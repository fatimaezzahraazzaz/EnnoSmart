from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Iterable

from ..config import Settings, get_settings
from ..domain.identity import validate_identity
from ..domain.models import (
    ArticleIdentity,
    FulltextCandidate,
    FulltextProvenance,
    HealthResult,
    LegalFulltextResult,
    ProviderAttempt,
    ProviderHealth,
)
from ..domain.normalizers import normalize_doi, normalize_title
from ..domain.ranking import sort_candidates
from ..infrastructure.audit import JsonlAuditLogger
from ..infrastructure.cache import SQLiteTTLCache
from ..infrastructure.http import HttpRequestError, PdfProbe, ResilientHttpClient
from ..providers import (
    ArxivProvider,
    CoreProvider,
    CrossrefProvider,
    EuropePmcProvider,
    HalProvider,
    OpenAlexProvider,
    SemanticScholarProvider,
    UnpaywallProvider,
    ZenodoProvider,
)
from ..providers.base import FulltextProvider
from ..providers.source_utils import classify_public_source, is_blocked_fulltext_domain
from .metadata_enricher import ArticleMetadataEnricher

from .generic_publisher_discovery import (
    RESOLVER_VERSION as PUBLISHER_DISCOVERY_VERSION,
    resolve_publisher_fulltext,
    to_mcp_location,
    to_provider_attempt,
)
RESOLVER_VERSION = "1.11.0-fast-preflight-no-duplicate-probes"


class LegalFulltextResolver:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.cache = SQLiteTTLCache(
            self.settings.cache_db,
            self.settings.cache_ttl_seconds,
            enabled=self.settings.cache_enabled,
        )
        self.audit = JsonlAuditLogger(self.settings.audit_log)
        self.metadata_enricher = ArticleMetadataEnricher(
            enabled=self.settings.metadata_enrichment_enabled,
            crossref_mailto=self.settings.effective_crossref_mailto,
            title_accept_score=self.settings.metadata_title_accept_score,
            title_conflict_score=self.settings.metadata_title_conflict_score,
            title_reconcile_score=self.settings.metadata_title_reconcile_score,
            exact_title_reconcile_score=self.settings.metadata_exact_title_reconcile_score,
        )
        self.provider_map: dict[str, FulltextProvider] = {
            "unpaywall": UnpaywallProvider(self.settings.unpaywall_email),
            "crossref": CrossrefProvider(self.settings.effective_crossref_mailto),
            "core": CoreProvider(self.settings.core_api_key, detail_limit=self.settings.core_detail_limit),
            "openalex": OpenAlexProvider(self.settings.openalex_api_key),
            "hal": HalProvider(),
            "arxiv": ArxivProvider(),
            "europe_pmc": EuropePmcProvider(),
            "zenodo": ZenodoProvider(),
        }
        # Semantic Scholar est volontairement désactivé par défaut : ses 429
        # ne doivent plus rendre toute la résolution indéterminée. Il ne sera
        # réactivé que par une configuration explicite.
        if self.settings.semantic_scholar_enabled:
            self.provider_map["semantic_scholar"] = SemanticScholarProvider(
                self.settings.semantic_scholar_api_key
            )
        ordered_names: list[str] = []
        for name in self.settings.provider_order:
            if name in self.provider_map and name not in ordered_names:
                ordered_names.append(name)
        self.configured_provider_names = ordered_names
        self.excluded_provider_names = [name for name in self.provider_map if name not in ordered_names]
        self.providers = [self.provider_map[name] for name in ordered_names]
        self._provider_locks = {name: asyncio.Lock() for name in self.provider_map}
        self._provider_last_call: dict[str, float] = {}
        self._provider_runtime: dict[str, dict[str, object]] = {
            name: {
                "status": "not_tested",
                "last_http_status": None,
                "last_error": None,
                "consecutive_failures": 0,
                "cooldown_until_epoch": 0.0,
            }
            for name in self.provider_map
        }

    def _providers_for_request(
        self,
        search_all: bool,
        article: ArticleIdentity,
    ) -> list[FulltextProvider]:
        if not search_all or not article.deterministic_oa_checked:
            return self.providers
        names = set(self.settings.deep_provider_order)
        return [provider for provider in self.providers if provider.name in names]

    def _cache_key(self, article: ArticleIdentity, search_all: bool) -> str:
        payload = {
            "resolver_version": RESOLVER_VERSION,
            "doi": normalize_doi(article.doi),
            "title": normalize_title(article.title),
            "authors": sorted(article.authors),
            "year": article.year,
            "known_urls": sorted(article.known_urls),
            "search_all": search_all,
            "deterministic_oa_checked": article.deterministic_oa_checked,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _dedupe(candidates: Iterable[FulltextCandidate]) -> list[FulltextCandidate]:
        out: list[FulltextCandidate] = []
        positions: dict[str, int] = {}
        for candidate in candidates:
            key = (candidate.final_url or candidate.pdf_url or candidate.landing_url or "").strip().lower()
            if not key:
                continue
            if key not in positions:
                positions[key] = len(out)
                out.append(candidate)
                continue
            previous = out[positions[key]]
            previous_quality = (
                LegalFulltextResolver._is_verified_fulltext(previous),
                previous.verified_pdf,
                previous.same_article,
                -previous.provider_priority,
            )
            candidate_quality = (
                LegalFulltextResolver._is_verified_fulltext(candidate),
                candidate.verified_pdf,
                candidate.same_article,
                -candidate.provider_priority,
            )
            if candidate_quality > previous_quality:
                out[positions[key]] = candidate
        return out

    def _provider_interval(self, provider_name: str) -> float:
        if provider_name == "semantic_scholar":
            return max(
                self.settings.provider_min_interval_seconds,
                self.settings.semantic_scholar_min_interval_seconds,
            )
        return max(0.0, self.settings.provider_min_interval_seconds)

    async def _provider_search(
        self,
        provider: FulltextProvider,
        article: ArticleIdentity,
        http: ResilientHttpClient,
    ) -> list[FulltextCandidate]:
        runtime = self._provider_runtime[provider.name]
        async with self._provider_locks[provider.name]:
            now_epoch = time.time()
            cooldown_until = float(runtime.get("cooldown_until_epoch") or 0.0)
            if cooldown_until > now_epoch:
                remaining = round(cooldown_until - now_epoch, 1)
                raise HttpRequestError(
                    f"Provider en délai de récupération ({remaining}s)",
                    status_code=int(runtime.get("last_http_status") or 503),
                    retry_after_seconds=remaining,
                )
            interval = self._provider_interval(provider.name)
            previous = self._provider_last_call.get(provider.name, 0.0)
            wait_for = interval - (time.monotonic() - previous)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._provider_last_call[provider.name] = time.monotonic()
            return await provider.search(article, http)

    def _record_provider_success(self, provider_name: str) -> None:
        runtime = self._provider_runtime[provider_name]
        runtime.update(
            status="ok",
            last_http_status=None,
            last_error=None,
            consecutive_failures=0,
            cooldown_until_epoch=0.0,
        )

    def _record_provider_error(self, provider_name: str, exc: Exception) -> None:
        runtime = self._provider_runtime[provider_name]
        failures = int(runtime.get("consecutive_failures") or 0) + 1
        runtime["consecutive_failures"] = failures
        runtime["last_error"] = str(exc)
        if isinstance(exc, HttpRequestError):
            runtime["last_http_status"] = exc.status_code
            runtime["status"] = "temporarily_unavailable" if exc.transient else "error"
            # Un 429/5xx distant justifie un cooldown partagé. Une panne DNS
            # locale n'en justifie pas : bloquer le provider pour les articles
            # suivants amplifierait un incident réseau de quelques secondes.
            if exc.transient and exc.status_code is not None:
                cooldown = max(
                    self.settings.provider_cooldown_seconds,
                    float(exc.retry_after_seconds or 0.0),
                )
                runtime["cooldown_until_epoch"] = time.time() + min(cooldown, 300.0)
            elif exc.status_code is None:
                runtime["cooldown_until_epoch"] = 0.0
        else:
            runtime["status"] = "error"

    def _validate(self, article: ArticleIdentity, candidate: FulltextCandidate):
        return validate_identity(
            article,
            candidate,
            min_identity_score=self.settings.min_identity_score,
            min_title_score=self.settings.min_title_score,
            allow_title_match=self.settings.allow_title_match,
            doi_title_conflict_score=self.settings.doi_title_conflict_score,
            exact_title_repository_score=self.settings.exact_title_repository_score,
            allow_exact_title_repository_match=self.settings.allow_exact_title_repository_match,
        )

    @staticmethod
    def _apply_probe(candidate: FulltextCandidate, probe: PdfProbe) -> None:
        candidate.verified_pdf = probe.ok
        candidate.probe_status = probe.status
        candidate.probe_http_status = probe.http_status
        candidate.probe_failure_kind = probe.failure_kind
        candidate.final_url = probe.final_url
        candidate.content_type = probe.content_type
        candidate.bytes_checked = probe.bytes_checked
        candidate.discovered_from_landing = probe.discovered_from_landing
        candidate.content_head_sha256 = probe.content_head_sha256
        candidate.resolution_status = "verified" if probe.ok else (probe.failure_kind or probe.status)
        if probe.error:
            candidate.warnings.append(probe.error)

    @staticmethod
    def _is_verified_fulltext(candidate: FulltextCandidate) -> bool:
        """
        Un résultat peut être :
        - un PDF public vérifié ;
        - un texte intégral HTML public vérifié par le module générique.

        Le texte HTML est conservé dans raw_metadata pour rester compatible
        avec le modèle FulltextCandidate existant.
        """
        metadata = candidate.raw_metadata or {}
        html_verified = bool(
            metadata.get("verified_html_fulltext")
            and metadata.get("content_kind") == "html"
            and str(metadata.get("full_text") or "").strip()
            and int(metadata.get("word_count") or 0) >= 900
        )
        return bool(
            candidate.legal_access
            and candidate.same_article
            and (candidate.verified_pdf or html_verified)
        )

    @staticmethod
    def _is_html_fulltext(candidate: FulltextCandidate | None) -> bool:
        if candidate is None:
            return False
        metadata = candidate.raw_metadata or {}
        return bool(
            metadata.get("verified_html_fulltext")
            and metadata.get("content_kind") == "html"
            and str(metadata.get("full_text") or "").strip()
        )

    def _candidate_from_generic_location(
        self,
        article: ArticleIdentity,
        location: dict,
    ) -> FulltextCandidate:
        """
        Convertit la sortie de generic_publisher_discovery vers le modèle
        FulltextCandidate déjà utilisé par le resolver.
        """
        content_kind = str(location.get("content_kind") or "").strip().lower()
        is_pdf = bool(location.get("verified_pdf") or content_kind == "pdf")
        is_html = bool(
            location.get("verified_html_fulltext")
            or content_kind == "html"
        )

        final_url = (
            location.get("final_url")
            or location.get("fulltext_url")
            or location.get("pdf_url")
            or location.get("landing_url")
        )
        landing_url = location.get("landing_url") or final_url
        pdf_url = (
            location.get("pdf_url")
            or (final_url if is_pdf else None)
        )

        raw_metadata = {
            "original_candidate_url": (
                location.get("pdf_url")
                or location.get("fulltext_url")
                or landing_url
            ),
            "publisher_discovery_version": PUBLISHER_DISCOVERY_VERSION,
            "content_kind": "html" if is_html and not is_pdf else "pdf",
            "verified_html_fulltext": bool(is_html and not is_pdf),
            "full_text": location.get("full_text") if is_html and not is_pdf else None,
            "word_count": int(location.get("word_count") or 0),
            "generic_location": {
                key: value
                for key, value in location.items()
                if key != "full_text"
            },
        }

        candidate = FulltextCandidate(
            provider="known_url_publisher_discovery",
            provider_priority=4,
            pdf_url=pdf_url,
            landing_url=landing_url,
            legal_access=bool(location.get("legal_access", True)),
            license=location.get("license"),
            version=location.get("version"),
            access_type=(
                location.get("access_type")
                or (
                    "publisher_html_fulltext"
                    if is_html and not is_pdf
                    else "publisher_open_access"
                )
            ),
            rights_status=(
                location.get("rights_status")
                or "publicly_accessible_license_unknown"
            ),
            source_domain=location.get("source_domain"),
            discovered_via=(
                location.get("discovered_via")
                or "generic_publisher_discovery"
            ),
            candidate_doi=location.get("candidate_doi") or article.doi,
            candidate_title=location.get("candidate_title") or article.title,
            candidate_authors=location.get("candidate_authors") or list(article.authors),
            candidate_year=location.get("candidate_year") or article.year,
            raw_metadata=raw_metadata,
        )

        candidate.final_url = final_url
        candidate.content_type = location.get("content_type")
        candidate.verified_pdf = bool(is_pdf and location.get("verified_pdf"))
        candidate.probe_status = (
            "verified_pdf"
            if candidate.verified_pdf
            else "verified_html_fulltext"
            if is_html
            else location.get("probe_status")
        )
        candidate.probe_http_status = location.get("probe_http_status")
        candidate.probe_failure_kind = location.get("probe_failure_kind")
        candidate.resolution_status = (
            "verified"
            if candidate.verified_pdf or is_html
            else location.get("resolution_status")
        )
        candidate.identity_score = float(location.get("identity_score") or 0.0)
        candidate.identity_method = location.get("identity_method") or "generic_publisher_identity"
        candidate.same_article = bool(location.get("same_article"))
        candidate.warnings.extend(location.get("warnings") or [])

        # Sécurité supplémentaire : la sortie générique doit encore passer
        # par la validation bibliographique centrale du projet.
        validation = self._validate(article, candidate)
        if not validation.same_article:
            candidate.same_article = False
            candidate.identity_method = validation.method
            candidate.identity_score = validation.score
            candidate.warnings.extend(validation.warnings)
            candidate.resolution_status = "identity_rejected"
        elif validation.score >= candidate.identity_score:
            candidate.same_article = True
            candidate.identity_method = validation.method
            candidate.identity_score = validation.score
            candidate.warnings.extend(validation.warnings)

        return candidate

    def _revalidate_document_metadata(
        self,
        article: ArticleIdentity,
        candidate: FulltextCandidate,
        probe: PdfProbe,
    ) -> bool:
        metadata = probe.document_metadata
        if not metadata:
            return True
        document = candidate.model_copy(deep=True)
        document.candidate_title = metadata.get("title") or document.candidate_title
        document.candidate_doi = metadata.get("doi") or document.candidate_doi
        document.candidate_authors = metadata.get("authors") or document.candidate_authors
        document.candidate_year = metadata.get("year") or document.candidate_year
        validation = self._validate(article, document)
        if not validation.same_article:
            candidate.same_article = False
            candidate.identity_method = validation.method
            candidate.identity_score = validation.score
            candidate.warnings.extend(validation.warnings)
            candidate.warnings.append("Les métadonnées de la page d'atterrissage ne correspondent pas à l'article.")
            candidate.resolution_status = "identity_rejected_after_landing"
            return False
        if validation.score >= candidate.identity_score:
            candidate.candidate_title = document.candidate_title
            candidate.candidate_doi = document.candidate_doi
            candidate.candidate_authors = document.candidate_authors
            candidate.candidate_year = document.candidate_year
            candidate.identity_method = validation.method
            candidate.identity_score = validation.score
            candidate.same_article = True
        return True

    @staticmethod
    def _failure(all_candidates: list[FulltextCandidate], attempts: list[ProviderAttempt]) -> tuple[str, str]:
        if any(attempt.transient for attempt in attempts) or any(
            candidate.probe_failure_kind in {"rate_limited", "temporarily_unavailable"}
            for candidate in all_candidates
        ):
            return (
                "provider_temporarily_unavailable",
                "Au moins une source était temporairement indisponible ou limitée ; relancer avec force_refresh=true.",
            )
        if any(candidate.probe_failure_kind == "access_blocked" for candidate in all_candidates):
            return (
                "remote_access_blocked",
                "Une copie candidate existe, mais son serveur refuse l'accès automatisé public.",
            )
        if any(candidate.probe_failure_kind == "landing_page" for candidate in all_candidates):
            return (
                "landing_page_without_verified_pdf",
                "Une page scientifique a été trouvée, sans lien PDF public vérifiable.",
            )
        if any(candidate.resolution_status and "identity_rejected" in candidate.resolution_status for candidate in all_candidates) or (
            all_candidates and all(not candidate.same_article for candidate in all_candidates)
        ):
            return (
                "identity_rejected",
                "Des documents ont été trouvés, mais leur identité bibliographique n'est pas suffisamment sûre.",
            )
        if all_candidates:
            return (
                "candidate_not_verified_as_pdf",
                "Des URLs candidates ont été trouvées, mais aucune n'a livré un PDF public vérifiable.",
            )
        return (
            "legal_copy_not_found",
            "Aucune copie Open Access ou publiquement accessible n'a été découverte dans les sources configurées.",
        )

    @staticmethod
    def _provenance(best: FulltextCandidate | None) -> FulltextProvenance | None:
        if not best:
            return None
        final_url = best.final_url or best.pdf_url
        if not final_url:
            return None
        original_url = best.raw_metadata.get("original_candidate_url") or best.pdf_url
        return FulltextProvenance(
            resolver_version=RESOLVER_VERSION,
            provider=best.provider,
            original_url=str(original_url) if original_url else None,
            final_url=final_url,
            discovered_via=best.discovered_via,
            identity_method=best.identity_method or "unknown",
            identity_score=best.identity_score,
            verified_pdf=best.probe_status != "probe_disabled" and best.verified_pdf,
            access_type=best.access_type,
            rights_status=best.rights_status,
            source_domain=best.source_domain,
            license=best.license,
            version=best.version,
            content_type=best.content_type,
            content_head_sha256=best.content_head_sha256,
        )

    async def resolve(
        self,
        article: ArticleIdentity,
        *,
        search_all: bool = False,
        force_refresh: bool = False,
    ) -> LegalFulltextResult:
        input_article = article.model_copy(deep=True)
        cache_key = self._cache_key(input_article, search_all)
        if force_refresh:
            self.cache.delete(cache_key)
        else:
            cached = self.cache.get(cache_key)
            if cached:
                if bool(cached.get("found")) or self.settings.cache_negative_results:
                    result = LegalFulltextResult.model_validate(cached)
                    result.cache_hit = True
                    return result
                self.cache.delete(cache_key)

        attempts: list[ProviderAttempt] = []
        accepted: list[FulltextCandidate] = []
        all_candidates: list[FulltextCandidate] = []
        effective_article = input_article

        async with ResilientHttpClient(
            timeout_seconds=self.settings.timeout_seconds,
            max_retries=self.settings.max_retries,
            user_agent=self.settings.user_agent,
            browser_user_agent=self.settings.browser_user_agent,
            max_landing_pdf_links=self.settings.max_landing_pdf_links,
            validate_public_network_urls=self.settings.validate_public_network_urls,
        ) as http:
            identity_is_complete = bool(
                normalize_doi(input_article.doi)
                and normalize_title(input_article.title)
                and input_article.authors
                and input_article.year
            )
            if identity_is_complete:
                effective_article = input_article
                enrichment_attempt = ProviderAttempt(
                    provider="metadata_enrichment",
                    enabled=True,
                    ok=True,
                    status="skipped_identity_already_complete",
                    candidates_count=0,
                    elapsed_seconds=0.0,
                )
            else:
                effective_article, enrichment_attempt = await self.metadata_enricher.enrich(
                    input_article,
                    http,
                )
            attempts.append(enrichment_attempt)

            # Étape générique prioritaire :
            # - suit le DOI et les URLs connues ;
            # - découvre les liens PDF, boutons Download, formulaires et data-* ;
            # - accepte aussi un texte intégral scientifique HTML vérifié ;
            # - n'utilise aucun domaine, DOI ou article_id codé en dur.
            generic_started = time.perf_counter()
            generic_candidates: list[FulltextCandidate] = []
            generic_verified_count = 0
            generic_error: str | None = None
            generic_transient = False
            generic_result: dict = {}

            # Le backend positionne ``deterministic_oa_checked`` seulement
            # apres avoir sonde les URLs connues et les fournisseurs OA. Les
            # refaire ici doublait le cout (jusqu'a 40 s/article) sans gain.
            # Le MCP passe alors directement aux depots profonds restants.
            if effective_article.deterministic_oa_checked:
                attempts.append(
                    ProviderAttempt(
                        provider="known_url_publisher_discovery",
                        enabled=False,
                        ok=True,
                        status="skipped_already_checked_by_backend",
                        candidates_count=0,
                        elapsed_seconds=0.0,
                    )
                )
            else:
                try:
                    generic_result = await asyncio.to_thread(
                        resolve_publisher_fulltext,
                        {
                            "article_id": effective_article.article_id,
                            "title": effective_article.title,
                            "doi": effective_article.doi,
                            "authors": list(effective_article.authors),
                            "year": effective_article.year,
                            "known_urls": list(effective_article.known_urls),
                            "source": getattr(effective_article, "source", None),
                        },
                        timeout=self.settings.timeout_seconds,
                    )

                    generic_location = to_mcp_location(generic_result)
                    if generic_location:
                        generic_candidate = self._candidate_from_generic_location(
                            effective_article,
                            generic_location,
                        )
                        generic_candidates.append(generic_candidate)
                        all_candidates.append(generic_candidate)

                        if self._is_verified_fulltext(generic_candidate):
                            accepted.append(generic_candidate)
                            generic_verified_count = 1

                    generic_transient = bool(
                        generic_result.get("retry_recommended")
                        or any(
                            item.get("status") in {
                                "timeout",
                                "temporary_error",
                                "rate_limited",
                                "request_error",
                            }
                            for item in (generic_result.get("attempts") or [])
                        )
                    )
                except Exception as exc:
                    generic_error = f"{type(exc).__name__}: {exc}"

                generic_attempt_data = to_provider_attempt(
                    generic_result,
                    elapsed_seconds=time.perf_counter() - generic_started,
                ) if generic_result else {}

                attempts.append(
                    ProviderAttempt(
                        provider="known_url_publisher_discovery",
                        enabled=True,
                        ok=bool(generic_result.get("ok")) and not generic_error,
                        status=(
                            "verified_candidate_found"
                            if generic_verified_count
                            else "provider_error"
                            if generic_error
                            else "searched_no_verified_candidate"
                        ),
                        candidates_count=int(
                            generic_attempt_data.get("candidates_count")
                            or len(generic_candidates)
                        ),
                        elapsed_seconds=round(
                            time.perf_counter() - generic_started,
                            3,
                        ),
                        error=generic_error,
                        identity_rejected_count=sum(
                            1
                            for candidate in generic_candidates
                            if candidate.resolution_status == "identity_rejected"
                        ),
                        access_blocked_count=int(
                            generic_attempt_data.get("access_blocked_count") or 0
                        ),
                        landing_only_count=int(
                            generic_attempt_data.get("landing_only_count") or 0
                        ),
                        verified_count=generic_verified_count,
                        transient=generic_transient,
                    )
                )

            if not (
                generic_verified_count
                and self.settings.stop_on_first_verified
            ):
                for provider in self._providers_for_request(search_all, effective_article):
                    started = time.perf_counter()
                    if not provider.enabled():
                        attempts.append(
                            ProviderAttempt(
                                provider=provider.name,
                                enabled=False,
                                ok=False,
                                status="disabled",
                                error=provider.disabled_reason(),
                            )
                        )
                        continue

                    try:
                        candidates = await self._provider_search(provider, effective_article, http)
                        self._record_provider_success(provider.name)
                        candidates = candidates[: self.settings.max_candidates_per_provider]
                        all_candidates.extend(candidates)
                        valid_from_provider: list[FulltextCandidate] = []
                        identity_rejected = 0
                        access_blocked = 0
                        landing_only = 0

                        for candidate in candidates:
                            original_url = candidate.pdf_url or candidate.landing_url
                            candidate.raw_metadata.setdefault("original_candidate_url", original_url)
                            if is_blocked_fulltext_domain(original_url):
                                candidate.legal_access = False
                                candidate.resolution_status = "domain_excluded"
                                candidate.warnings.append("domaine_exclu_par_politique_legal_fulltext")

                            if not candidate.access_type or not candidate.rights_status or not candidate.source_domain:
                                access_type, rights_status, domain = classify_public_source(
                                    original_url,
                                    license_value=candidate.license,
                                )
                                candidate.access_type = candidate.access_type or access_type
                                candidate.rights_status = candidate.rights_status or rights_status
                                candidate.source_domain = candidate.source_domain or domain
                            candidate.discovered_via = candidate.discovered_via or provider.name

                            validation = self._validate(effective_article, candidate)
                            candidate.identity_score = validation.score
                            candidate.identity_method = validation.method
                            candidate.same_article = validation.same_article
                            candidate.warnings.extend(validation.warnings)
                            if not validation.same_article:
                                candidate.resolution_status = "identity_rejected"
                                identity_rejected += 1
                                continue
                            if not candidate.legal_access or not candidate.pdf_url:
                                candidate.resolution_status = "not_eligible"
                                continue

                            if self.settings.verify_pdf:
                                probe = await http.probe_pdf(candidate.pdf_url, referer=candidate.landing_url)
                                self._apply_probe(candidate, probe)
                                if probe.discovered_from_landing:
                                    candidate.landing_url = candidate.landing_url or probe.source_url or original_url
                                    candidate.pdf_url = probe.final_url or candidate.pdf_url
                                if probe.failure_kind == "access_blocked":
                                    access_blocked += 1
                                if probe.failure_kind == "landing_page":
                                    landing_only += 1
                                if probe.ok and not self._revalidate_document_metadata(effective_article, candidate, probe):
                                    identity_rejected += 1
                                    continue
                            else:
                                candidate.verified_pdf = True
                                candidate.probe_status = "probe_disabled"
                                candidate.resolution_status = "accepted_without_http_probe"

                            if candidate.verified_pdf:
                                valid_from_provider.append(candidate)
                                accepted.append(candidate)

                        attempts.append(
                            ProviderAttempt(
                                provider=provider.name,
                                enabled=True,
                                ok=True,
                                status="verified_candidate_found" if valid_from_provider else "searched_no_verified_candidate",
                                candidates_count=len(candidates),
                                elapsed_seconds=round(time.perf_counter() - started, 3),
                                identity_rejected_count=identity_rejected,
                                access_blocked_count=access_blocked,
                                landing_only_count=landing_only,
                                verified_count=len(valid_from_provider),
                            )
                        )
                        if valid_from_provider and self.settings.stop_on_first_verified:
                            break
                    except HttpRequestError as exc:
                        self._record_provider_error(provider.name, exc)
                        attempts.append(
                            ProviderAttempt(
                                provider=provider.name,
                                enabled=True,
                                ok=False,
                                status="provider_temporarily_unavailable" if exc.transient else "provider_error",
                                elapsed_seconds=round(time.perf_counter() - started, 3),
                                error=str(exc),
                                http_status=exc.status_code,
                                transient=exc.transient,
                            )
                        )
                    except Exception as exc:
                        self._record_provider_error(provider.name, exc)
                        attempts.append(
                            ProviderAttempt(
                                provider=provider.name,
                                enabled=True,
                                ok=False,
                                status="provider_error",
                                elapsed_seconds=round(time.perf_counter() - started, 3),
                                error=str(exc),
                            )
                        )

        all_candidates = self._dedupe(all_candidates)
        accepted = self._dedupe(accepted)
        ranked = sort_candidates(accepted)
        best = ranked[0] if ranked else None
        transient_signal = any(attempt.transient for attempt in attempts) or any(
            candidate.probe_failure_kind in {"rate_limited", "temporarily_unavailable"}
            for candidate in all_candidates
        )
        successful_provider_search = any(
            attempt.enabled and attempt.ok
            for attempt in attempts
            if attempt.provider != "metadata_enrichment"
        )
        # Une seule panne transitoire ne doit pas annuler les recherches déjà
        # terminées par les autres fournisseurs. On recommande un retry MCP
        # uniquement lorsque la recherche utile n'a pas réellement pu aboutir.
        transient_failure = bool(
            best is None and transient_signal and not successful_provider_search
        )
        failure_code, reason = (None, None) if best else self._failure(all_candidates, attempts)
        provenance = self._provenance(best)

        result = LegalFulltextResult(
            resolver_version=RESOLVER_VERSION,
            ok=True,
            found=best is not None,
            legal_access=bool(best and best.legal_access),
            same_article=bool(best and best.same_article),
            status=(
                "legal_html_fulltext_found"
                if self._is_html_fulltext(best)
                else "legal_pdf_found"
                if best
                else "legal_fulltext_not_found"
            ),
            article=effective_article,
            best_candidate=best,
            locations=sort_candidates(all_candidates),
            attempts=attempts,
            needs_consultant_upload=bool(best is None and not transient_failure),
            retry_recommended=bool(best is None and transient_failure),
            cache_policy="positive_only" if not self.settings.cache_negative_results else "positive_and_stable_negative",
            failure_code=failure_code,
            reason=reason,
            provenance=provenance,
        )

        should_cache = bool(best) or bool(
            self.settings.cache_negative_results and not transient_failure
        )
        result.cache_write = should_cache
        payload = result.model_dump(mode="json")
        if should_cache:
            self.cache.set(cache_key, payload)
        else:
            self.cache.delete(cache_key)

        self.audit.write(
            "legal_fulltext_resolution",
            {
                "resolver_version": RESOLVER_VERSION,
                "article_id": input_article.article_id,
                "input_doi": input_article.doi,
                "effective_doi": effective_article.doi,
                "doi_status": effective_article.doi_status,
                "title": effective_article.title,
                "authors": effective_article.authors,
                "year": effective_article.year,
                "metadata_sources": effective_article.metadata_sources,
                "metadata_warnings": effective_article.metadata_warnings,
                "found": result.found,
                "failure_code": failure_code,
                "provider": best.provider if best else None,
                "pdf_url": best.pdf_url if best else None,
                "fulltext_url": (
                    (best.final_url or best.landing_url)
                    if best and self._is_html_fulltext(best)
                    else None
                ),
                "content_kind": (
                    "html"
                    if best and self._is_html_fulltext(best)
                    else "pdf"
                    if best
                    else None
                ),
                "identity_method": best.identity_method if best else None,
                "provenance": provenance.model_dump(mode="json") if provenance else None,
                "transient_failure": transient_failure,
                "cache_write": should_cache,
                "attempts": [x.model_dump(mode="json") for x in attempts],
            },
        )
        return result

    def health(self) -> HealthResult:
        enabled = [p.name for p in self.providers if p.enabled()]
        disabled = [p.name for p in self.providers if not p.enabled()]
        now = time.time()
        statuses: list[ProviderHealth] = []
        for name, provider in self.provider_map.items():
            runtime = self._provider_runtime[name]
            configured = name in self.configured_provider_names
            cooldown_epoch = float(runtime.get("cooldown_until_epoch") or 0.0)
            cooldown_until = None
            if cooldown_epoch > now:
                cooldown_until = datetime.fromtimestamp(cooldown_epoch, tz=timezone.utc).isoformat()
            status = str(runtime.get("status") or "not_tested")
            if not configured:
                status = "excluded_by_configuration"
            elif not provider.enabled():
                status = "disabled_missing_configuration"
            statuses.append(
                ProviderHealth(
                    provider=name,
                    configured=configured,
                    enabled=configured and provider.enabled(),
                    status=status,
                    last_http_status=runtime.get("last_http_status"),
                    last_error=str(runtime.get("last_error")) if runtime.get("last_error") else provider.disabled_reason(),
                    consecutive_failures=int(runtime.get("consecutive_failures") or 0),
                    cooldown_until=cooldown_until,
                )
            )
        return HealthResult(
            ok=self.settings.enabled,
            server="EnnoScholar Legal Fulltext MCP",
            version=RESOLVER_VERSION,
            enabled_providers=enabled,
            disabled_providers=disabled,
            configured_providers=self.configured_provider_names,
            excluded_providers=self.excluded_provider_names,
            provider_statuses=statuses,
        )
