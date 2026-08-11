# -*- coding: utf-8 -*-
from __future__ import annotations

"""Recherche complémentaire générique pour le chat EnnoScholar.

Les articles sont recherchés dans plusieurs index scientifiques. Les
documentations d'outils peuvent aussi être proposées depuis le Web public.
Chaque résultat conserve sa requête, son fournisseur et son périmètre de
preuve. Une documentation officielle peut expliquer un outil ou une procédure,
mais ne prouve jamais seule une performance scientifique.
"""

import hashlib
import html
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs, unquote, urlparse

import requests

from modules.LLM.llm_client import LLMClient


def _clean(value: Any, limit: int = 8000) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()[:limit]


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value).casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9+#./-]+", " ", text).strip()


def _tokens(value: Any) -> set[str]:
    stop = {
        "avec", "dans", "des", "une", "pour", "par", "sur", "les", "the",
        "and", "for", "from", "with", "state", "art", "etat", "article",
        "method", "methode", "outil", "tool", "resultat", "result",
        "http", "https", "www", "com", "org", "net",
    }
    output: set[str] = set()
    for raw_token in _norm(value).split():
        token = raw_token.strip("./-")
        variants = {token}
        variants.update(
            part
            for part in re.split(r"[./-]+", token)
            if part
        )
        output.update(
            variant
            for variant in variants
            if len(variant) >= 3 and variant not in stop
        )
    return output


def _stable_id(prefix: str, *values: Any) -> str:
    raw = "|".join(_norm(value) for value in values)
    return f"{prefix}-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12].upper()


def _year(value: Any) -> int | None:
    try:
        result = int(str(value or "")[:4])
    except Exception:
        return None
    return result if 1800 <= result <= 2200 else None


def _authors(value: Any) -> list[str]:
    rows = value if isinstance(value, list) else [value] if value else []
    output: list[str] = []
    for row in rows:
        name = _clean(
            (row.get("name") or row.get("display_name"))
            if isinstance(row, Mapping)
            else row,
            200,
        )
        if name and name not in output:
            output.append(name)
    return output[:20]


def _domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").casefold()
    except Exception:
        return ""


def _decode_ddg_url(value: str) -> str:
    url = html.unescape(value or "")
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if "duckduckgo.com" in (parsed.hostname or ""):
        target = (parse_qs(parsed.query).get("uddg") or [""])[0]
        if target:
            return unquote(target)
    return url


def _looks_like_official_documentation(entity_name: Any, url: str) -> bool:
    domain = re.sub(r"[^a-z0-9]+", "", _domain(url))
    entity_tokens = [
        re.sub(r"[^a-z0-9]+", "", token)
        for token in _tokens(entity_name)
    ]
    distinctive = [
        token
        for token in entity_tokens
        if len(token) >= 3
        and token not in {"official", "documentation", "software", "tool"}
    ]
    return bool(domain and any(token in domain for token in distinctive))


def _looks_like_scientific_web_source(url: str, title: Any = "") -> bool:
    domain = _domain(url)
    path = (urlparse(url).path or "").casefold()
    scientific_domains = {
        "arxiv.org",
        "doi.org",
        "dl.acm.org",
        "ieeexplore.ieee.org",
        "link.springer.com",
        "sciencedirect.com",
        "hal.science",
        "zenodo.org",
        "openreview.net",
        "pubmed.ncbi.nlm.nih.gov",
        "pmc.ncbi.nlm.nih.gov",
    }
    if any(domain == item or domain.endswith("." + item) for item in scientific_domains):
        return True
    title_text = _norm(title)
    title_tokens = _tokens(title)
    return bool(
        "/publication" in path
        or "/pubs/" in path
        or "/paper" in path
        or "/article" in path
        or "/abstract" in path
        or (
            domain.startswith("research.")
            and path.endswith(".pdf")
        )
        or {"paper", "study"} & title_tokens
        or re.search(
            r"\b(?:journal|proceedings|conference paper|thesis|dissertation|"
            r"volume\s+\d+)\b",
            title_text,
        )
    )


def _canonical_source_key(row: Mapping[str, Any]) -> str:
    url = _clean(row.get("url"), 2000)
    parsed = urlparse(url)
    domain = (parsed.hostname or "").casefold()
    path = unquote(parsed.path or "").rstrip("/").casefold()
    if domain.endswith("arxiv.org"):
        match = re.search(r"/(?:abs|pdf)/([^/?]+)", path)
        if match:
            identifier = re.sub(r"(?:v\d+)?(?:\.pdf)?$", "", match.group(1))
            return f"arxiv:{identifier}"
    doi = _norm(row.get("doi"))
    if doi:
        return f"doi:{doi}"
    if domain == "doi.org":
        return f"doi:{path.lstrip('/')}"
    if domain.endswith("github.com"):
        parts = [value for value in path.split("/") if value]
        if len(parts) >= 2:
            return f"github:{parts[0]}/{parts[1]}"
    if domain:
        return f"url:{domain}{path}"
    return f"title:{_norm(row.get('title'))}:{row.get('year') or ''}"


def _extract_json_object(value: Any) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


class WebResearchService:
    SCIENTIFIC_PROVIDERS = {
        "semantic_scholar": ("semantic_scholar_client", "SemanticScholarClient", "search_papers"),
        "openalex": ("openalex_client", "OpenAlexClient", "search_works"),
        "crossref": ("crossref_client", "CrossrefClient", "search_works"),
        "arxiv": ("arxiv_client", "ArxivClient", "search_papers"),
        "hal": ("hal_client", "HalClient", "search_works"),
        "doaj": ("doaj_client", "DoajClient", "search_articles"),
        "zenodo": ("zenodo_client", "ZenodoClient", "search_records"),
    }
    TECHNICAL_PROVIDERS = {
        "github": ("github_client", "GitHubClient", "search_repositories"),
    }
    # Machine-readable values used by the structured conversation contract.
    # These identifiers are routing data, not consultant-message vocabulary.
    DOCUMENTATION_QUERY_KINDS = frozenset({
        "official_documentation",
    })
    SCIENTIFIC_QUERY_KINDS = frozenset({
        "scientific_evidence",
        "direct_scientific_evidence",
    })
    DOCUMENTATION_ENTITY_TYPES = frozenset({
        "tool",
        "scientific_software",
        "software_library",
        "protocol",
        "standard",
        "documentation",
    })
    DOCUMENTATION_SOURCE_PREFERENCES = frozenset({
        "documentation",
        "official_documentation",
        "official_website",
        "software_documentation",
    })
    SCIENTIFIC_SOURCE_PREFERENCES = frozenset({
        "scientific_article",
        "scientific_articles",
        "scientific_evidence",
        "scientific_publication",
        "scientific_publications",
    })

    def __init__(
        self,
        *,
        timeout: int = 25,
        per_provider_limit: int = 7,
        llm: LLMClient | None = None,
        enable_openai_web: bool = True,
        enable_llm_rerank: bool = True,
    ) -> None:
        self.timeout = max(5, min(int(timeout), 90))
        self.per_provider_limit = max(3, min(int(per_provider_limit), 20))
        self._clients: dict[str, Any] = {}
        self.llm = llm
        self.enable_openai_web = bool(enable_openai_web and llm is not None)
        self.enable_llm_rerank = bool(enable_llm_rerank and llm is not None)

    @staticmethod
    def _contract_identifier(value: Any) -> str:
        """Normalise an enum-like value without interpreting natural language."""
        raw = getattr(value, "value", value)
        text = unicodedata.normalize("NFKD", _clean(raw, 160).casefold())
        text = "".join(
            character
            for character in text
            if not unicodedata.combining(character)
        )
        return re.sub(r"[^a-z0-9]+", "_", text).strip("_")

    @classmethod
    def _structured_source_modes(
        cls,
        request: Mapping[str, Any],
    ) -> tuple[bool, bool] | None:
        """Return ``(scientific, documentation)`` for a structured request.

        ``query_kind`` is authoritative when present. Canonical
        ``source_preferences`` are used only when no authoritative query kind
        was supplied. Unknown values deliberately fall through to the legacy
        compatibility path instead of being guessed from the query text.
        """
        query_kind = cls._contract_identifier(request.get("query_kind"))
        if query_kind in cls.DOCUMENTATION_QUERY_KINDS:
            return False, True
        if query_kind in cls.SCIENTIFIC_QUERY_KINDS:
            return True, False

        preferences = {
            cls._contract_identifier(value)
            for value in (request.get("source_preferences") or [])
            if cls._contract_identifier(value)
        }
        scientific = bool(preferences & cls.SCIENTIFIC_SOURCE_PREFERENCES)
        documentation = bool(
            preferences & cls.DOCUMENTATION_SOURCE_PREFERENCES
        )
        if scientific or documentation:
            return scientific, documentation
        if request.get("require_direct_evidence") is True:
            return True, False
        if "target_context_dimensions" in request:
            entity_type = cls._contract_identifier(
                request.get("entity_type")
            )
            if entity_type == "documentation":
                return False, True
            return True, False
        return None

    @classmethod
    def _is_structured_request(
        cls,
        request: Mapping[str, Any],
    ) -> bool:
        """Whether the new interpreter supplied an actionable search contract."""
        return bool(
            cls._contract_identifier(request.get("query_kind"))
            in (
                cls.DOCUMENTATION_QUERY_KINDS
                | cls.SCIENTIFIC_QUERY_KINDS
            )
            or "target_context_dimensions" in request
            or "require_direct_evidence" in request
        )

    def search(
        self,
        requests_payload: Iterable[Mapping[str, Any]],
        *,
        excluded_ids: Iterable[str] | None = None,
        max_candidates: int = 30,
        auto_refine: bool = True,
    ) -> dict[str, Any]:
        seed_requests = [
            dict(row) for row in requests_payload
            if isinstance(row, Mapping) and _clean(row.get("query"))
        ][:8]
        seed_requests = self._expand_documentation_entities(seed_requests)[:8]
        requests_list = self._plan_provider_requests(seed_requests)[:8]
        excluded = {str(value) for value in (excluded_ids or [])}
        jobs: list[tuple[str, dict[str, Any]]] = []
        for request in requests_list:
            wants_documentation = self._wants_documentation(request)
            wants_scientific = self._wants_scientific(request)
            if wants_scientific:
                for provider in self.SCIENTIFIC_PROVIDERS:
                    jobs.append((provider, request))
            if wants_documentation:
                if not self.enable_openai_web:
                    jobs.append(("public_web", request))
                jobs.append(("readthedocs", request))
                for provider in self.TECHNICAL_PROVIDERS:
                    jobs.append((provider, request))
        if self.enable_openai_web and callable(
            getattr(self.llm, "web_search", None)
        ):
            seen_web_entities: set[str] = set()
            for request in seed_requests:
                entity_tokens = _tokens(
                    request.get("entity_name") or request.get("query")
                )
                focused_entity = bool(
                    self._contract_identifier(request.get("entity_type"))
                    in self.DOCUMENTATION_ENTITY_TYPES
                    or len(entity_tokens) <= 4
                )
                use_web_discovery = bool(
                    self._wants_documentation(request)
                    or (
                        focused_entity
                        and request.get("refinement_reason")
                        in {
                            "missing_scientific_articles",
                            "missing_direct_scientific_evidence",
                            "missing_scientific_breadth",
                        }
                    )
                )
                if not use_web_discovery:
                    continue
                web_key = _norm(
                    request.get("entity_name") or request.get("query")
                )
                if not web_key or web_key in seen_web_entities:
                    continue
                seen_web_entities.add(web_key)
                jobs.append(("openai_web", request))

        raw: list[dict[str, Any]] = []
        executions: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(jobs)))) as pool:
            futures = {
                pool.submit(self._run_job, provider, request): (provider, request)
                for provider, request in jobs
            }
            for future in as_completed(futures):
                provider, request = futures[future]
                try:
                    rows = future.result()
                    raw.extend(rows)
                    executions.append({
                        "provider": provider,
                        "query": request.get("query"),
                        "ok": True,
                        "results": len(rows),
                    })
                except Exception as exc:
                    executions.append({
                        "provider": provider,
                        "query": request.get("query"),
                        "ok": False,
                        "results": 0,
                        "error": f"{type(exc).__name__}: {exc}",
                    })

        merged = self._deduplicate(
            row
            for row in raw
            if row.get("candidate_id") not in excluded
            and self._candidate_matches_request(row)
        )
        merged = self._classify_and_filter_candidates(
            merged,
            seed_requests,
        )
        year_ceilings = [
            _year(request.get("publication_year_max"))
            for request in seed_requests
            if request.get("publication_year_max") is not None
        ]
        year_ceilings = [year for year in year_ceilings if year is not None]
        if year_ceilings:
            # Une contrainte temporelle explicite est stricte : une source sans
            # année vérifiable ne doit pas être présentée comme conforme.
            publication_year_max = min(year_ceilings)
            merged = [
                row
                for row in merged
                if _year(row.get("year") or row.get("publication_year")) is not None
                and _year(row.get("year") or row.get("publication_year"))
                <= publication_year_max
            ]
        role_priority = {
            "direct_evidence": 4,
            "official_documentation": 3,
            "connected_evidence": 2,
            "implementation": 1,
        }
        ranked_all = sorted(
            merged,
            key=lambda row: (
                role_priority.get(str(row.get("relevance_role") or ""), 0),
                float(row.get("relevance_score") or 0.0),
                float(row.get("source_authority") or 0.0),
                bool(row.get("open_access")),
            ),
            reverse=True,
        )
        candidate_limit = max(1, min(int(max_candidates), 60))
        ranked = ranked_all[:candidate_limit]
        documentation_requested = any(
            self._wants_documentation(request)
            for request in requests_list
        )
        if documentation_requested:
            documentation = [
                row
                for row in ranked_all
                if row.get("candidate_kind") == "documentation"
            ][: min(5, candidate_limit)]
            ranked_ids = {
                str(row.get("candidate_id") or "") for row in ranked
            }
            for source in documentation:
                source_id = str(source.get("candidate_id") or "")
                if source_id in ranked_ids:
                    continue
                if len(ranked) >= candidate_limit:
                    removed = ranked.pop()
                    ranked_ids.discard(str(removed.get("candidate_id") or ""))
                ranked.append(source)
                ranked_ids.add(source_id)
            ranked.sort(
                key=lambda row: (
                    role_priority.get(
                        str(row.get("relevance_role") or ""),
                        0,
                    ),
                    float(row.get("relevance_score") or 0.0),
                    float(row.get("source_authority") or 0.0),
                ),
                reverse=True,
            )
        completeness = self._research_completeness(
            requests_list,
            ranked,
        )
        refinement_rounds: list[dict[str, Any]] = []
        missing_for_refinement = list(
            completeness["missing_source_types"]
        )
        if (
            "direct_scientific_evidence" in missing_for_refinement
            and int(
                (completeness.get("found") or {}).get(
                    "scientific_articles", 0
                )
            )
            >= 6
        ):
            # Le portefeuille contient déjà assez de travaux pour exposer
            # honnêtement l'absence de preuve directe. Une seconde recherche
            # automatique coûteuse et quasi identique n'améliore généralement
            # pas la couverture ; le consultant pourra demander un affinage.
            missing_for_refinement.remove("direct_scientific_evidence")
        if auto_refine and missing_for_refinement:
            refinements = self._build_refinement_requests(
                requests_list,
                missing_for_refinement,
            )
            if refinements:
                retry = self.search(
                    refinements,
                    excluded_ids=[
                        *excluded,
                        *[
                            str(row.get("candidate_id") or "")
                            for row in ranked
                        ],
                    ],
                    max_candidates=max_candidates,
                    auto_refine=False,
                )
                refinement_rounds.append({
                    "round": 1,
                    "queries": refinements,
                    "candidates_found": len(retry.get("candidates") or []),
                    "remaining_gaps": (
                        retry.get("completeness") or {}
                    ).get("missing_source_types") or [],
                })
                executions.extend(retry.get("executions") or [])
                ranked = sorted(
                    self._deduplicate([
                        *ranked,
                        *list(retry.get("candidates") or []),
                    ]),
                    key=lambda row: (
                        role_priority.get(
                            str(row.get("relevance_role") or ""),
                            0,
                        ),
                        float(row.get("relevance_score") or 0.0),
                        float(row.get("source_authority") or 0.0),
                        bool(row.get("open_access")),
                    ),
                    reverse=True,
                )[:candidate_limit]
                completeness = self._research_completeness(
                    requests_list,
                    ranked,
                )
        return {
            "ok": True,
            "payload_type": "guided_multisource_web_research_v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "seed_queries": seed_requests,
            "queries": requests_list,
            "executions": executions,
            "candidates": ranked,
            "completeness": completeness,
            "refinement_rounds": refinement_rounds,
            "policy": {
                "consultant_validation_required": True,
                "scientific_indexes": list(self.SCIENTIFIC_PROVIDERS),
                "technical_indexes": [
                    "readthedocs",
                    *self.TECHNICAL_PROVIDERS,
                    "public_web",
                    "openai_web_search",
                ],
                "query_decomposition_enabled": True,
                "semantic_web_discovery_enabled": bool(self.enable_openai_web),
                "llm_relevance_reranking_enabled": bool(
                    self.enable_llm_rerank
                ),
                "official_documentation_supported": True,
                "official_documentation_scope": [
                    "definition",
                    "architecture",
                    "procedure",
                    "configuration",
                ],
                "official_documentation_never_proves_scientific_performance_alone": True,
                "no_paywall_bypass": True,
                "mcp_reserved_for_legal_fulltext_recovery": True,
            },
        }

    @staticmethod
    def _preference_tokens(request: Mapping[str, Any]) -> set[str]:
        """Legacy natural-language preferences kept for older callers."""
        return {
            _norm(value)
            for value in (request.get("source_preferences") or [])
            if _clean(value)
        }

    @classmethod
    def _wants_documentation(cls, request: Mapping[str, Any]) -> bool:
        structured_modes = cls._structured_source_modes(request)
        if structured_modes is not None:
            return structured_modes[1]

        # Compatibility for payloads created before source preferences became
        # enum-like contract values. This branch is never used to reinterpret
        # an authoritative structured query_kind.
        preferences = cls._preference_tokens(request)
        documentation_preferences = {
            "documentation officielle",
            "official documentation",
            "documentation",
            "site officiel",
            "official website",
            "logiciel",
            "software",
        }
        scientific_preferences = {
            "articles scientifiques",
            "article scientifique",
            "scientific articles",
            "scientific article",
            "publications scientifiques",
            "scientific publications",
        }
        if (
            preferences & scientific_preferences
            and not preferences & documentation_preferences
        ):
            return False
        return bool(
            preferences
            & documentation_preferences
        ) or cls._contract_identifier(
            request.get("entity_type")
        ) in cls.DOCUMENTATION_ENTITY_TYPES

    @classmethod
    def _wants_scientific(cls, request: Mapping[str, Any]) -> bool:
        structured_modes = cls._structured_source_modes(request)
        if structured_modes is not None:
            return structured_modes[0]

        # Legacy compatibility only; structured requests are handled above.
        preferences = cls._preference_tokens(request)
        explicitly_scientific = bool(
            preferences
            & {
                "articles scientifiques",
                "article scientifique",
                "scientific articles",
                "scientific article",
                "publications scientifiques",
                "scientific publications",
            }
        )
        if explicitly_scientific:
            return True
        if preferences and cls._wants_documentation(request):
            return False
        return True

    @classmethod
    def _plan_provider_requests(
        cls,
        seed_requests: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Décompose une demande riche en requêtes courtes par type de source."""
        output: list[dict[str, Any]] = []
        meta_dimensions = {
            "definition",
            "method",
            "methods",
            "procedure",
            "results",
            "result",
            "performance",
            "transferability",
            "limitations",
            "limitation",
            "scientific validation",
            "validation",
            "implementation",
            "architecture",
            "configuration",
        }
        for seed in seed_requests:
            entity = _clean(
                seed.get("entity_name") or seed.get("query"),
                300,
            )
            structured_modes = cls._structured_source_modes(seed)
            if cls._is_structured_request(seed) and structured_modes is not None:
                wants_scientific, wants_documentation = structured_modes
                query_kind = cls._contract_identifier(
                    seed.get("query_kind")
                )
                if wants_scientific:
                    scientific = dict(seed)
                    scientific["query_kind"] = (
                        query_kind
                        if query_kind in cls.SCIENTIFIC_QUERY_KINDS
                        else "scientific_evidence"
                    )
                    scientific["source_preferences"] = [
                        "scientific_articles"
                    ]
                    output.append(scientific)
                if wants_documentation:
                    documentation = dict(seed)
                    documentation["query_kind"] = "official_documentation"
                    documentation["entity_type"] = "documentation"
                    documentation["source_preferences"] = [
                        "official_documentation"
                    ]
                    output.append(documentation)
                continue

            dimensions = [
                _clean(value, 160)
                for value in (seed.get("requested_dimensions") or [])
                if _clean(value, 160)
            ]
            scientific_dimensions = [
                value
                for value in dimensions
                if _norm(value) not in meta_dimensions
            ]
            if not scientific_dimensions:
                scientific_dimensions = dimensions[:2]

            if cls._wants_scientific(seed):
                parts = [entity]
                word_count = len(_norm(entity).split())
                for dimension in scientific_dimensions:
                    dimension_words = _norm(dimension).split()
                    if word_count + len(dimension_words) > 9:
                        continue
                    parts.append(dimension)
                    word_count += len(dimension_words)
                scientific = dict(seed)
                scientific.update({
                    "query": _clean(" ".join(parts), 500),
                    "original_query": seed.get("query"),
                    "query_kind": "scientific_evidence",
                    "source_preferences": ["articles scientifiques"],
                })
                output.append(scientific)

            if cls._wants_documentation(seed):
                documentation = dict(seed)
                documentation.update({
                    "query": _clean(
                        f"{entity} official documentation",
                        500,
                    ),
                    "original_query": seed.get("query"),
                    "query_kind": "official_documentation",
                    "entity_type": "documentation",
                    "source_preferences": ["documentation officielle"],
                })
                output.append(documentation)

            if not cls._wants_scientific(seed) and not cls._wants_documentation(seed):
                output.append(dict(seed))

        unique: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in output:
            key = (_norm(row.get("query")), str(row.get("query_kind") or ""))
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return unique

    @staticmethod
    def _target_context_dimensions(
        request: Mapping[str, Any],
    ) -> list[str]:
        explicit = [
            _clean(value, 160)
            for value in (request.get("target_context_dimensions") or [])
            if _clean(value, 160)
        ]
        # Presence is meaningful in the structured contract: an explicit empty
        # list means that no target context was requested. Only legacy payloads
        # may derive this field from the broader requested_dimensions list.
        if "target_context_dimensions" in request:
            return explicit
        meta = {
            "definition",
            "method",
            "methods",
            "procedure",
            "results",
            "result",
            "performance",
            "transferability",
            "limitations",
            "limitation",
            "scientific validation",
            "validation",
            "implementation",
            "architecture",
            "configuration",
        }
        dimensions = [
            _clean(value, 160)
            for value in (request.get("requested_dimensions") or [])
            if _clean(value, 160) and _norm(value) not in meta
        ]
        return dimensions[1:] if len(dimensions) > 1 else dimensions

    @classmethod
    def _deterministic_relevance_role(
        cls,
        candidate: Mapping[str, Any],
        requests_list: list[dict[str, Any]],
    ) -> tuple[str, float, str]:
        kind = str(candidate.get("candidate_kind") or "")
        if (
            kind in {"documentation", "official_documentation"}
            and bool(candidate.get("official_source"))
        ):
            return (
                "official_documentation",
                0.96,
                "Page issue d'un domaine officiel lié à l'entité étudiée.",
            )
        if kind in {"software_repository", "research_output"}:
            return (
                "implementation",
                0.88,
                "Artefact d'implémentation : utile techniquement, non probant seul.",
            )

        text = " ".join(
            _clean(candidate.get(key), 8000)
            for key in ("title", "abstract", "venue", "url")
        )
        found = _tokens(text)
        identity_found = found | _tokens(
            candidate.get("web_citation_context")
        )
        matching_request: Mapping[str, Any] = {}
        candidate_query = _norm(candidate.get("query"))
        if candidate_query:
            matching_request = next(
                (
                    request
                    for request in requests_list
                    if _norm(request.get("query")) == candidate_query
                ),
                {},
            )
        for request in requests_list:
            if matching_request:
                break
            entity_tokens = _tokens(request.get("entity_name"))
            entity_hits = len(entity_tokens & identity_found)
            minimum_hits = min(2, len(entity_tokens))
            if not entity_tokens or entity_hits >= minimum_hits:
                matching_request = request
                break
        if kind == "scientific_article":
            context_dimensions = cls._target_context_dimensions(
                matching_request
            )
            direct_dimensions = [
                dimension
                for dimension in context_dimensions
                if (
                    (dimension_tokens := _tokens(dimension))
                    and len(dimension_tokens & found)
                    >= max(1, (len(dimension_tokens) + 1) // 2)
                )
            ]
            required_dimension_matches = min(
                2,
                len(context_dimensions),
            )
            direct_specificity_ok = True
            if matching_request.get("require_direct_evidence") is True:
                entity_tokens = _tokens(
                    matching_request.get("entity_name")
                )
                discriminating_groups = [
                    tokens
                    for tokens in (
                        _tokens(value)
                        for value in (
                            matching_request.get("required_terms") or []
                        )
                    )
                    if tokens and not tokens.issubset(entity_tokens)
                ]
                if discriminating_groups:
                    direct_specificity_ok = any(
                        tokens.issubset(found)
                        for tokens in discriminating_groups
                    )
            if (
                direct_dimensions
                and len(direct_dimensions) >= required_dimension_matches
                and direct_specificity_ok
            ):
                return (
                    "direct_evidence",
                    0.88,
                    "L'article étudie l'entité dans au moins un contexte cible demandé : "
                    + ", ".join(direct_dimensions[:3]),
                )
            return (
                "connected_evidence",
                0.76,
                "Article scientifique lié à l'entité, sans validation directe du contexte cible.",
            )
        return (
            "implementation",
            0.65,
            "Source contextuelle ou technique ne constituant pas une preuve scientifique directe.",
        )

    def _classify_and_filter_candidates(
        self,
        candidates: list[dict[str, Any]],
        requests_list: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        annotated: list[dict[str, Any]] = []
        for candidate in candidates:
            role, confidence, reason = self._deterministic_relevance_role(
                candidate,
                requests_list,
            )
            row = dict(candidate)
            row.update({
                "relevance_role": role,
                "role_confidence": confidence,
                "role_reason": reason,
                "direct_evidence": role == "direct_evidence",
            })
            annotated.append(row)

        if not self.enable_llm_rerank or self.llm is None:
            for row in annotated:
                row["selection_priority_score"] = (
                    self._selection_priority_score(row)
                )
            return annotated
        prompt_candidates = [
            {
                "candidate_id": row.get("candidate_id"),
                "title": row.get("title"),
                "abstract": _clean(row.get("abstract"), 1200),
                "discovery_context": _clean(
                    row.get("web_citation_context"),
                    500,
                ),
                "url": row.get("url"),
                "candidate_kind": row.get("candidate_kind"),
                "official_source": bool(row.get("official_source")),
                "deterministic_role": row.get("relevance_role"),
            }
            for row in annotated[:40]
        ]
        prompt_requests = [
            {
                "entity_name": row.get("entity_name"),
                "query": row.get("query"),
                "requested_dimensions": row.get("requested_dimensions") or [],
                "target_context_dimensions": self._target_context_dimensions(row),
                "section_titles": row.get("section_titles") or [],
                "target_verrous": row.get("target_verrous") or [],
            }
            for row in requests_list
        ]
        prompt = f"""
Tu classes des sources candidates pour une recherche scientifique ciblée.

DEMANDE
{json.dumps(prompt_requests, ensure_ascii=False)}

CANDIDATS
{json.dumps(prompt_candidates, ensure_ascii=False)}

Retourne uniquement ce JSON :
{{
  "decisions": [
    {{
      "candidate_id": "identifiant exact",
      "role": "direct_evidence|connected_evidence|official_documentation|implementation|irrelevant",
      "confidence": 0.0,
      "reason": "justification factuelle courte"
    }}
  ]
}}

Règles :
- direct_evidence : article scientifique étudiant directement l'entité dans le contexte cible demandé.
- connected_evidence : article scientifique utile par comparaison ou transférabilité, sans preuve directe.
- official_documentation : page officielle servant aux capacités, définitions ou procédures, jamais aux performances scientifiques seule.
- implementation : dépôt, logiciel, exemple ou artefact technique.
- irrelevant : aucun lien démontrable avec l'entité et le contexte de recherche.
- discovery_context peut aider à identifier la source, mais provient du commentaire
  de découverte Web : ne l'utilise jamais pour déclarer une preuve directe.
- Utilise uniquement le titre, le résumé, l'URL et le type fournis. N'invente rien.
""".strip()
        try:
            raw = self.llm.generate(
                prompt,
                temperature=0.0,
                max_output_tokens=2600,
                json_mode=True,
                request_name="ennoscholar:guided_research:source_rerank",
            )
            parsed = _extract_json_object(raw)
        except Exception:
            return annotated

        decisions = {
            str(row.get("candidate_id") or ""): row
            for row in (parsed.get("decisions") or [])
            if isinstance(row, Mapping) and row.get("candidate_id")
        }
        output: list[dict[str, Any]] = []
        allowed_roles = {
            "direct_evidence",
            "connected_evidence",
            "official_documentation",
            "implementation",
            "irrelevant",
        }
        for candidate in annotated:
            decision = decisions.get(str(candidate.get("candidate_id") or ""))
            if not decision:
                output.append(candidate)
                continue
            role = str(decision.get("role") or "")
            if role not in allowed_roles:
                output.append(candidate)
                continue
            try:
                confidence = max(
                    0.0,
                    min(1.0, float(decision.get("confidence") or 0.0)),
                )
            except Exception:
                confidence = 0.0
            kind = str(candidate.get("candidate_kind") or "")
            deterministic_role = str(
                candidate.get("relevance_role") or ""
            )
            if kind in {"software_repository", "research_output"}:
                role = "implementation"
            elif (
                kind in {"documentation", "official_documentation"}
                and bool(candidate.get("official_source"))
            ):
                role = "official_documentation"
            elif kind == "scientific_article":
                # Un article reste une preuve scientifique : il ne devient
                # jamais une page de documentation ou un artefact. De plus,
                # le rôle direct déterministe ne peut être ni supprimé ni
                # dégradé par une seconde interprétation LLM.
                if deterministic_role == "direct_evidence":
                    role = "direct_evidence"
                elif role in {
                    "official_documentation",
                    "implementation",
                }:
                    role = deterministic_role
                elif role == "irrelevant":
                    # Une publication peut être scientifique tout en étant
                    # hors du domaine demandé. L'ancien code la reclassait
                    # systématiquement en preuve connexe dès que le nom de la
                    # méthode apparaissait, ce qui faisait remonter des
                    # applications médicales, faciales ou affectives dans une
                    # recherche SAR.
                    if confidence >= 0.70:
                        continue
                    role = deterministic_role
                elif (
                    role == "direct_evidence"
                    and deterministic_role != "direct_evidence"
                ):
                    role = "connected_evidence"
            elif kind != "scientific_article" and role in {
                "direct_evidence",
                "connected_evidence",
            }:
                role = "implementation"
            if role == "irrelevant" and confidence >= 0.78:
                continue
            if role != "irrelevant":
                candidate["relevance_role"] = role
                candidate["direct_evidence"] = role == "direct_evidence"
                candidate["role_confidence"] = confidence
                candidate["role_reason"] = _clean(
                    decision.get("reason"),
                    500,
                )
                candidate["selection_priority_score"] = (
                    self._selection_priority_score(candidate)
                )
            if "selection_priority_score" not in candidate:
                candidate["selection_priority_score"] = (
                    self._selection_priority_score(candidate)
                )
            output.append(candidate)
        return output

    @staticmethod
    def _selection_priority_score(
        candidate: Mapping[str, Any],
    ) -> float:
        """Score d'aide à la sélection, distinct du simple chevauchement lexical."""
        relevance = max(
            0.0,
            min(1.0, float(candidate.get("relevance_score") or 0.0)),
        )
        authority = max(
            0.0,
            min(1.0, float(candidate.get("source_authority") or 0.0)),
        )
        role = str(candidate.get("relevance_role") or "")
        if role == "direct_evidence":
            score = 0.78 + 0.17 * relevance
        elif role == "official_documentation":
            score = 0.70 + 0.18 * authority
        elif role == "connected_evidence":
            score = 0.35 + 0.25 * relevance
        else:
            score = 0.20 + 0.20 * relevance
        return round(max(0.0, min(0.99, score)), 4)

    @classmethod
    def _expand_documentation_entities(
        cls,
        requests_list: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for request in requests_list:
            entity = _clean(request.get("entity_name"), 300)
            if not entity or not cls._wants_documentation(request):
                output.append(request)
                continue
            explicit_entities = [
                _clean(
                    value.get("name") if isinstance(value, Mapping) else value,
                    160,
                )
                for value in (
                    request.get("entity_names")
                    or request.get("entities")
                    or []
                )
                if _clean(
                    value.get("name") if isinstance(value, Mapping) else value,
                    160,
                )
            ]
            if explicit_entities:
                for explicit_entity in explicit_entities[:8]:
                    expanded = dict(request)
                    expanded["entity_name"] = explicit_entity
                    expanded["query"] = explicit_entity
                    output.append(expanded)
                continue
            if cls._is_structured_request(request):
                # The structured interpreter emits one request per entity. Do
                # not parse a consultant-authored entity label with language-
                # specific conjunction rules.
                output.append(request)
                continue
            parts = [
                _clean(value, 160)
                for value in re.split(
                    r"\s+(?:et|and)\s+|\s*&\s*|[,;]",
                    entity,
                    flags=re.I,
                )
                if _clean(value, 160)
            ]
            if not (
                2 <= len(parts) <= 4
                and all(1 <= len(_tokens(part)) <= 5 for part in parts)
            ):
                output.append(request)
                continue
            for part in parts:
                expanded = dict(request)
                expanded["entity_name"] = part
                expanded["query"] = _clean(
                    " ".join([
                        part,
                        *[
                            str(value)
                            for value in (
                                request.get("requested_dimensions") or []
                            )
                        ],
                    ]),
                    800,
                ) or part
                output.append(expanded)
        return output

    @classmethod
    def _research_completeness(
        cls,
        requests_list: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        scientific_requested = any(
            cls._wants_scientific(request)
            for request in requests_list
        )
        documentation_requested = any(
            cls._wants_documentation(request)
            for request in requests_list
        )
        scientific_count = sum(
            row.get("candidate_kind") == "scientific_article"
            for row in candidates
        )
        documentation_count = sum(
            row.get("candidate_kind")
            in {"documentation", "official_documentation", "software_repository"}
            for row in candidates
        )
        official_documentation_count = sum(
            row.get("candidate_kind")
            in {"documentation", "official_documentation"}
            and bool(row.get("official_source"))
            for row in candidates
        )
        direct_evidence_requested = any(
            cls._wants_scientific(request)
            and request.get("require_direct_evidence") is True
            for request in requests_list
        )
        direct_evidence_count = sum(
            row.get("candidate_kind") == "scientific_article"
            and row.get("relevance_role") == "direct_evidence"
            for row in candidates
        )
        missing: list[str] = []
        if scientific_requested and scientific_count == 0:
            missing.append("scientific_articles")
        if documentation_requested and official_documentation_count == 0:
            missing.append("official_documentation_pages")
        if direct_evidence_requested and direct_evidence_count == 0:
            missing.append("direct_scientific_evidence")
        return {
            "complete": not missing,
            "requested": {
                "scientific_articles": scientific_requested,
                "official_documentation": documentation_requested,
                "direct_scientific_evidence": direct_evidence_requested,
            },
            "found": {
                "scientific_articles": scientific_count,
                "documentation_candidates": documentation_count,
                "official_documentation_pages": official_documentation_count,
                "direct_scientific_evidence": direct_evidence_count,
            },
            "missing_source_types": missing,
        }

    @classmethod
    def _build_refinement_requests(
        cls,
        requests_list: list[dict[str, Any]],
        missing_source_types: list[str],
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for request in requests_list:
            entity = _clean(
                request.get("entity_name") or request.get("query"),
                300,
            )
            if (
                "official_documentation_pages" in missing_source_types
                and cls._wants_documentation(request)
            ):
                refined = dict(request)
                refined.update({
                    "query": _clean(
                        f"{entity} official documentation",
                        800,
                    ),
                    "entity_name": entity,
                    "entity_type": "documentation",
                    "query_kind": "official_documentation",
                    "source_preferences": ["official_documentation"],
                    "refinement_reason": "missing_official_documentation",
                })
                output.append(refined)
            if (
                "scientific_articles" in missing_source_types
                and cls._wants_scientific(request)
            ):
                refined = dict(request)
                refined.update({
                    "query_kind": "scientific_evidence",
                    "source_preferences": ["scientific_articles"],
                    "refinement_reason": "missing_scientific_articles",
                })
                output.append(refined)
            if (
                "direct_scientific_evidence" in missing_source_types
                and cls._wants_scientific(request)
            ):
                target_dimensions = cls._target_context_dimensions(request)
                if not target_dimensions:
                    continue
                refined = dict(request)
                refined.update({
                    "query": _clean(
                        " ".join([
                            entity,
                            *target_dimensions[:3],
                            "direct application validation",
                        ]),
                        800,
                    ),
                    "query_kind": "direct_scientific_evidence",
                    "source_preferences": ["scientific_articles"],
                    "require_direct_evidence": True,
                    "refinement_reason": "missing_direct_scientific_evidence",
                })
                output.append(refined)
        unique: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in output:
            key = (
                _norm(row.get("query")),
                str(row.get("query_kind") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return unique[:8]

    def fetch_public_content(self, url: str, *, max_chars: int = 24000) -> dict[str, Any]:
        """Lit une page publique validée sans contourner d'accès restreint."""
        target = _clean(url, 2000)
        if not target.startswith(("http://", "https://")):
            return {"ok": False, "error": "invalid_public_url", "text": ""}
        response = requests.get(
            target,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                )
            },
            timeout=self.timeout,
            allow_redirects=True,
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").casefold()
        if "text/html" not in content_type:
            return {
                "ok": False,
                "error": "non_html_content_requires_existing_fulltext_pipeline",
                "content_type": content_type,
                "text": "",
            }
        page = re.sub(
            r"<(?:script|style|nav|footer|noscript)\b[^>]*>.*?</(?:script|style|nav|footer|noscript)>",
            " ",
            response.text,
            flags=re.I | re.S,
        )
        page = re.sub(r"<[^>]+>", " ", page)
        text = _clean(html.unescape(page), max_chars)
        return {
            "ok": len(text) >= 120,
            "url": response.url,
            "content_type": content_type,
            "text": text,
        }

    @classmethod
    def _web_discovery_prompt(cls, request: Mapping[str, Any]) -> str:
        entity = _clean(
            request.get("entity_name") or request.get("query"),
            300,
        )
        dimensions = [
            _clean(value, 180)
            for value in (request.get("requested_dimensions") or [])
            if _clean(value, 180)
        ]
        source_types: list[str] = []
        if cls._wants_scientific(request):
            source_types.append(
                "articles scientifiques ou actes de conférence originaux"
            )
        if cls._wants_documentation(request):
            source_types.append(
                "documentation officielle de l'éditeur ou du projet"
            )
        direct_requirement = ""
        if (
            request.get("require_direct_evidence") is True
            or request.get("refinement_reason")
            == "missing_direct_scientific_evidence"
        ):
            direct_requirement = (
                "\nPriorité absolue : cherche une publication originale qui "
                "applique explicitement l'entité au contexte cible indiqué. "
                "Une application voisine, une documentation ou un dépôt ne "
                "compte pas comme preuve directe. Si elle existe, ouvre sa "
                "notice originale, son DOI ou son PDF public.\n"
            )
        return f"""
Recherche sur le Web des sources exactes et autoritatives concernant : {entity}.

Axes à vérifier : {", ".join(dimensions) or "définition, méthode, résultats et limites"}.
Types attendus : {", ".join(source_types) or "sources scientifiques et techniques fiables"}.
{direct_requirement}

Effectue plusieurs recherches courtes si nécessaire, ouvre les pages prometteuses et distingue :
1. les preuves scientifiques directes dans le contexte demandé ;
2. les travaux connexes utiles seulement par comparaison ou transférabilité ;
3. les documentations officielles pour les capacités et procédures ;
4. les limites, conditions et preuves manquantes.

Priorise les pages officielles, les publications originales, les DOI et les dépôts institutionnels.
Écarte les agrégateurs faibles, les pages sans rapport et les simples listes de résultats.
Retourne une liste concise avec titre, type de source, pertinence directe ou connexe et limite principale.
Chaque source mentionnée doit avoir une citation Web vérifiable.
""".strip()

    def _run_job(
        self,
        provider: str,
        request: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        query = _clean(request.get("query"), 800)
        entity_name = _clean(request.get("entity_name"), 300)
        documentation_request = (
            self._contract_identifier(request.get("query_kind"))
            in self.DOCUMENTATION_QUERY_KINDS
        )
        documentation_query = (
            f"{entity_name} official documentation"
            if entity_name
            and documentation_request
            else query + " official documentation"
        )
        if provider == "openai_web":
            if self.llm is None or not callable(
                getattr(self.llm, "web_search", None)
            ):
                return []
            result = self.llm.web_search(
                self._web_discovery_prompt(request),
                blocked_domains=[
                    "reddit.com",
                    "wikipedia.org",
                    "quora.com",
                    "pinterest.com",
                    "facebook.com",
                ],
                max_output_tokens=1800,
                request_name="ennoscholar:guided_research:web_discovery",
            )
            if not result.get("ok"):
                return []
            return [
                self._normalize_openai_web_source(
                    row,
                    request,
                    rank,
                    answer=result.get("answer"),
                    model=result.get("model"),
                )
                for rank, row in enumerate(
                    result.get("sources") or [],
                    start=1,
                )
                if isinstance(row, Mapping) and row.get("url")
            ]
        if provider == "public_web":
            return [
                self._normalize_web(row, request, rank)
                for rank, row in enumerate(
                    self._search_public_web(documentation_query),
                    start=1,
                )
                if row
            ]
        if provider == "readthedocs":
            return [
                self._normalize_documentation_registry(row, request, rank)
                for rank, row in enumerate(
                    self._search_readthedocs(request),
                    start=1,
                )
                if row
            ]
        client, method = self._client(provider)
        provider_query = (
            entity_name
            if entity_name
            and documentation_request
            and provider in self.TECHNICAL_PROVIDERS
            else query
        )
        rows = method(provider_query, self.per_provider_limit)
        output = []
        for rank, row in enumerate(rows or [], start=1):
            if not isinstance(row, Mapping) or row.get("normalized_error"):
                continue
            candidate = (
                self._normalize_technical(
                    row, provider=provider, request=request, rank=rank
                )
                if provider in self.TECHNICAL_PROVIDERS
                else self._normalize_scientific(
                    row, provider=provider, request=request, rank=rank
                )
            )
            if candidate:
                output.append(candidate)
        return output

    def _normalize_openai_web_source(
        self,
        row: Mapping[str, Any],
        request: Mapping[str, Any],
        rank: int,
        *,
        answer: Any = "",
        model: Any = "",
    ) -> dict[str, Any]:
        url = _clean(row.get("url"), 2000)
        domain = _domain(url)
        title = _clean(row.get("title"), 600) or domain or url
        source_excerpt = _clean(row.get("source_excerpt"), 2600)
        citation_context = _clean(
            row.get("citation_context") or row.get("snippet"),
            2600,
        )
        official_source = _looks_like_official_documentation(
            request.get("entity_name"),
            url,
        )
        repository = domain.endswith("github.com")
        scientific = _looks_like_scientific_web_source(url, title)
        if repository:
            candidate_kind = "software_repository"
            evidence_scope = [
                "implementation",
                "configuration",
                "reproducibility",
            ]
        elif scientific:
            candidate_kind = "scientific_article"
            evidence_scope = [
                "method",
                "result",
                "limitation",
                "comparison",
            ]
        else:
            candidate_kind = "documentation"
            evidence_scope = [
                "definition",
                "architecture",
                "procedure",
                "configuration",
            ]
        authority = (
            0.95
            if official_source
            else 0.92
            if scientific
            else 0.82
            if bool(row.get("cited"))
            else 0.68
        )
        score = self._score(
            request.get("query"),
            f"{title} {source_excerpt} {citation_context} {domain}",
            authority=authority,
            rank=rank,
        )
        public_fulltext = bool(
            url.casefold().endswith(".pdf")
            or domain.endswith("arxiv.org")
            or (
                official_source
                and candidate_kind == "documentation"
            )
        )
        return {
            "candidate_id": _stable_id("WEB", title, url),
            "candidate_kind": candidate_kind,
            "title": title,
            "authors": [],
            "year": None,
            "doi": (
                urlparse(url).path.lstrip("/")
                if domain == "doi.org"
                else None
            ),
            "url": url,
            "pdf_url": url if url.casefold().endswith(".pdf") else None,
            "abstract": source_excerpt or None,
            "web_citation_context": citation_context or None,
            "venue": domain or None,
            "open_access": public_fulltext,
            "official_source": official_source,
            "official_status": (
                "official_domain_candidate"
                if official_source
                else "consultant_validation_required"
            ),
            "source_providers": ["openai_web_search"],
            "source_authority": authority,
            "scientific_evidence_eligible": scientific,
            "context_evidence_eligible": True,
            "evidence_scope": evidence_scope,
            "section_ids": list(request.get("section_ids") or []),
            "section_titles": list(request.get("section_titles") or []),
            "target_verrous": list(request.get("target_verrous") or []),
            "requested_dimensions": list(
                request.get("requested_dimensions") or []
            ),
            "target_context_dimensions": list(
                request.get("target_context_dimensions") or []
            ),
            "require_direct_evidence": bool(
                request.get("require_direct_evidence")
            ),
            "query": request.get("query"),
            "entity_name": request.get("entity_name"),
            "entity_type": request.get("entity_type"),
            "query_kind": request.get("query_kind"),
            "discovery_kind": "semantic_web_discovery",
            "required_terms": list(request.get("required_terms") or []),
            "excluded_terms": list(request.get("excluded_terms") or []),
            "relevance_score": score,
            "consultant_decision": "proposed",
            "retrieval": [{
                "provider": "openai_web_search",
                "rank": rank,
                "query": request.get("query"),
                "model": _clean(model, 100),
                "cited": bool(row.get("cited")),
            }],
            "raw_payloads": [{
                **dict(row),
                "web_answer_excerpt": _clean(answer, 1800),
            }],
        }

    def _client(self, provider: str) -> tuple[Any, Any]:
        if provider in self._clients:
            return self._clients[provider]
        provider_spec = (
            self.SCIENTIFIC_PROVIDERS.get(provider)
            or self.TECHNICAL_PROVIDERS[provider]
        )
        module_name, class_name, method_name = provider_spec
        module = __import__(
            f"agents.EnnoScholar.{module_name}",
            fromlist=[class_name],
        )
        client = getattr(module, class_name)()
        method = getattr(client, method_name)
        self._clients[provider] = (client, method)
        return client, method

    def _normalize_scientific(
        self,
        row: Mapping[str, Any],
        *,
        provider: str,
        request: Mapping[str, Any],
        rank: int,
    ) -> dict[str, Any] | None:
        title = _clean(row.get("title") or row.get("name"), 600)
        if len(title) < 4:
            return None
        abstract = _clean(
            row.get("abstract") or row.get("summary") or row.get("description"),
            5000,
        )
        url = _clean(
            row.get("url") or row.get("landing_url") or row.get("html_url"),
            2000,
        )
        pdf_url = _clean(
            row.get("pdf_url") or row.get("open_access_pdf_url"),
            2000,
        )
        doi = _clean(row.get("doi"), 300)
        score = self._score(
            request.get("query"),
            f"{title} {abstract}",
            authority=0.88,
            rank=rank,
        )
        publication_types = [
            _norm(value)
            for value in (row.get("publication_types") or [])
            if _clean(value)
        ]
        scholarly_types = {
            "article",
            "journal article",
            "journal-article",
            "conference paper",
            "conference-paper",
            "proceedings article",
            "proceedings-article",
            "preprint",
            "book chapter",
            "book-chapter",
            "thesis",
            "dissertation",
            "publication",
        }
        is_scholarly = (
            provider != "zenodo"
            or any(value in scholarly_types for value in publication_types)
        )
        candidate_kind = "scientific_article" if is_scholarly else "research_output"
        evidence_scope = (
            ["method", "result", "limitation", "comparison"]
            if is_scholarly
            else ["implementation", "dataset", "configuration", "reproducibility"]
        )
        return {
            "candidate_id": _stable_id("SRC", doi, title, row.get("year")),
            "candidate_kind": candidate_kind,
            "title": title,
            "authors": _authors(row.get("authors")),
            "year": _year(row.get("year") or row.get("publication_year")),
            "doi": doi or None,
            "url": url or pdf_url or None,
            "pdf_url": pdf_url or None,
            "abstract": abstract or None,
            "venue": _clean(
                row.get("venue") or row.get("journal") or row.get("publisher"),
                500,
            ) or None,
            "open_access": bool(
                row.get("open_access")
                or row.get("is_open_access")
                or row.get("is_oa")
                or pdf_url
                or provider in {"arxiv", "hal"}
            ),
            "source_providers": [provider],
            "source_authority": 0.88,
            "scientific_evidence_eligible": is_scholarly,
            "context_evidence_eligible": True,
            "evidence_scope": evidence_scope,
            "publication_types": publication_types,
            "section_ids": list(request.get("section_ids") or []),
            "section_titles": list(request.get("section_titles") or []),
            "target_verrous": list(request.get("target_verrous") or []),
            "requested_dimensions": list(request.get("requested_dimensions") or []),
            "target_context_dimensions": list(
                request.get("target_context_dimensions") or []
            ),
            "require_direct_evidence": bool(
                request.get("require_direct_evidence")
            ),
            "query": request.get("query"),
            "entity_name": request.get("entity_name"),
            "entity_type": request.get("entity_type"),
            "query_kind": request.get("query_kind"),
            "required_terms": list(request.get("required_terms") or []),
            "excluded_terms": list(request.get("excluded_terms") or []),
            "relevance_score": score,
            "consultant_decision": "proposed",
            "retrieval": [{"provider": provider, "rank": rank, "query": request.get("query")}],
            "raw_payloads": [dict(row)],
        }

    def _normalize_technical(
        self,
        row: Mapping[str, Any],
        *,
        provider: str,
        request: Mapping[str, Any],
        rank: int,
    ) -> dict[str, Any] | None:
        title = _clean(row.get("title"), 600)
        if len(title) < 2:
            return None
        repository_url = _clean(row.get("url"), 2000)
        documentation_url = _clean(row.get("documentation_url"), 2000)
        summary = _clean(row.get("abstract") or row.get("description"), 5000)
        score = self._score(
            request.get("entity_name") or request.get("query"),
            f"{title} {summary}",
            authority=0.76,
            rank=rank,
        )
        return {
            "candidate_id": _stable_id("TOOL", provider, title, repository_url),
            "candidate_kind": "software_repository",
            "title": title,
            "authors": _authors(row.get("authors")),
            "year": _year(row.get("year")),
            "doi": None,
            "url": documentation_url or repository_url or None,
            "repository_url": repository_url or None,
            "documentation_url": documentation_url or None,
            "abstract": summary or None,
            "venue": "GitHub" if provider == "github" else provider,
            "open_access": True,
            "official_source": False,
            "official_status": "consultant_validation_required",
            "source_providers": [provider],
            "source_authority": 0.76,
            "scientific_evidence_eligible": False,
            "context_evidence_eligible": True,
            "evidence_scope": [
                "definition",
                "architecture",
                "implementation",
                "installation",
                "configuration",
                "reproducibility",
            ],
            "section_ids": list(request.get("section_ids") or []),
            "section_titles": list(request.get("section_titles") or []),
            "target_verrous": list(request.get("target_verrous") or []),
            "requested_dimensions": list(request.get("requested_dimensions") or []),
            "target_context_dimensions": list(
                request.get("target_context_dimensions") or []
            ),
            "require_direct_evidence": bool(
                request.get("require_direct_evidence")
            ),
            "query": request.get("query"),
            "entity_name": request.get("entity_name"),
            "entity_type": request.get("entity_type"),
            "query_kind": request.get("query_kind"),
            "required_terms": list(request.get("required_terms") or []),
            "excluded_terms": list(request.get("excluded_terms") or []),
            "relevance_score": score,
            "consultant_decision": "proposed",
            "retrieval": [{
                "provider": provider,
                "rank": rank,
                "query": request.get("query"),
            }],
            "raw_payloads": [dict(row)],
        }

    def _search_readthedocs(
        self,
        request: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        entity = _clean(
            request.get("entity_name")
            or request.get("topic")
            or request.get("query"),
            300,
        )
        slugs: list[str] = []
        normalized = re.sub(r"[^a-z0-9]+", "-", _norm(entity)).strip("-")
        if normalized:
            slugs.append(normalized)
        first_token = next(iter(_tokens(entity)), "")
        if first_token and first_token not in slugs:
            slugs.append(first_token)
        output: list[dict[str, Any]] = []
        for slug in slugs[:2]:
            response = requests.get(
                "https://readthedocs.org/api/v2/project/",
                params={"slug": slug},
                headers={"User-Agent": "EnnoSmart-EnnoScholar/3.2"},
                timeout=self.timeout,
            )
            if response.status_code in {404, 410}:
                continue
            response.raise_for_status()
            payload = response.json()
            for row in (payload.get("results") or [])[:3]:
                if isinstance(row, Mapping):
                    output.append(dict(row))
        return output

    def _normalize_documentation_registry(
        self,
        row: Mapping[str, Any],
        request: Mapping[str, Any],
        rank: int,
    ) -> dict[str, Any]:
        title = _clean(row.get("name") or row.get("slug"), 600)
        url = _clean(row.get("canonical_url"), 2000)
        description = _clean(row.get("description"), 5000)
        official_source = _looks_like_official_documentation(
            request.get("entity_name"),
            url,
        )
        score = self._score(
            request.get("entity_name") or request.get("query"),
            f"{title} {description} {row.get('repo') or ''}",
            authority=0.82,
            rank=rank,
        )
        return {
            "candidate_id": _stable_id("DOC", title, url),
            "candidate_kind": "documentation",
            "title": title,
            "authors": [],
            "year": None,
            "doi": None,
            "url": url or None,
            "repository_url": _clean(row.get("repo"), 2000) or None,
            "abstract": description or f"Documentation technique de {title}.",
            "venue": "Read the Docs",
            "open_access": True,
            "official_source": official_source,
            "official_status": (
                "official_domain_candidate"
                if official_source
                else "consultant_validation_required"
            ),
            "source_providers": ["readthedocs"],
            "source_authority": 0.82,
            "scientific_evidence_eligible": False,
            "context_evidence_eligible": True,
            "evidence_scope": [
                "definition",
                "architecture",
                "procedure",
                "installation",
                "configuration",
            ],
            "section_ids": list(request.get("section_ids") or []),
            "section_titles": list(request.get("section_titles") or []),
            "target_verrous": list(request.get("target_verrous") or []),
            "requested_dimensions": list(request.get("requested_dimensions") or []),
            "query": request.get("query"),
            "entity_name": request.get("entity_name"),
            "entity_type": request.get("entity_type"),
            "query_kind": request.get("query_kind"),
            "required_terms": list(request.get("required_terms") or []),
            "excluded_terms": list(request.get("excluded_terms") or []),
            "relevance_score": score,
            "consultant_decision": "proposed",
            "retrieval": [{
                "provider": "readthedocs",
                "rank": rank,
                "query": request.get("query"),
            }],
            "raw_payloads": [dict(row)],
        }

    def _search_public_web(self, query: str) -> list[dict[str, str]]:
        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                )
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        page = response.text
        links = re.findall(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            page,
            flags=re.I | re.S,
        )
        snippets = re.findall(
            r'<(?:a|div)[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div)>',
            page,
            flags=re.I | re.S,
        )
        output: list[dict[str, str]] = []
        for index, (href, raw_title) in enumerate(links[:10]):
            title = _clean(re.sub(r"<[^>]+>", " ", html.unescape(raw_title)), 600)
            url = _decode_ddg_url(href)
            if not url.startswith(("http://", "https://")):
                continue
            snippet = ""
            if index < len(snippets):
                snippet = _clean(
                    re.sub(r"<[^>]+>", " ", html.unescape(snippets[index])),
                    1800,
                )
            output.append({"title": title, "url": url, "snippet": snippet})
        return output

    def _normalize_web(
        self,
        row: Mapping[str, Any],
        request: Mapping[str, Any],
        rank: int,
    ) -> dict[str, Any]:
        title = _clean(row.get("title"), 600)
        url = _clean(row.get("url"), 2000)
        snippet = _clean(row.get("snippet"), 2400)
        domain = _domain(url)
        official_source = _looks_like_official_documentation(
            request.get("entity_name"),
            url,
        )
        authority = 0.78 if domain else 0.45
        score = self._score(
            request.get("query"),
            f"{title} {snippet} {domain}",
            authority=authority,
            rank=rank,
        )
        return {
            "candidate_id": _stable_id("WEB", title, url),
            "candidate_kind": "documentation",
            "title": title,
            "authors": [],
            "year": None,
            "doi": None,
            "url": url,
            "pdf_url": url if url.casefold().endswith(".pdf") else None,
            "abstract": snippet or None,
            "venue": domain or None,
            "open_access": True,
            "official_source": official_source,
            "official_status": (
                "official_domain_candidate"
                if official_source
                else "consultant_validation_required"
            ),
            "source_providers": ["public_web"],
            "source_authority": authority,
            "scientific_evidence_eligible": False,
            "context_evidence_eligible": True,
            "evidence_scope": ["definition", "architecture", "procedure", "configuration"],
            "section_ids": list(request.get("section_ids") or []),
            "section_titles": list(request.get("section_titles") or []),
            "target_verrous": list(request.get("target_verrous") or []),
            "requested_dimensions": list(request.get("requested_dimensions") or []),
            "query": request.get("query"),
            "entity_name": request.get("entity_name"),
            "entity_type": request.get("entity_type"),
            "query_kind": request.get("query_kind"),
            "required_terms": list(request.get("required_terms") or []),
            "excluded_terms": list(request.get("excluded_terms") or []),
            "relevance_score": score,
            "consultant_decision": "proposed",
            "retrieval": [{"provider": "public_web", "rank": rank, "query": request.get("query")}],
            "raw_payloads": [dict(row)],
        }

    @staticmethod
    def _candidate_matches_request(candidate: Mapping[str, Any]) -> bool:
        candidate_text = " ".join(
            _clean(candidate.get(key), 8000)
            for key in (
                "title",
                "abstract",
                "venue",
                "url",
                "web_citation_context",
            )
        )
        found = _tokens(candidate_text)
        if not found:
            return False

        entity_tokens = _tokens(candidate.get("entity_name"))
        required_groups = [
            term_tokens
            for term_tokens in (
                _tokens(term)
                for term in (candidate.get("required_terms") or [])
                if _clean(term, 300)
            )
            if term_tokens
        ]
        entity_type = WebResearchService._contract_identifier(
            candidate.get("entity_type")
        )
        named_entity = bool(
            entity_type
            in {
                "tool",
                "scientific_software",
                "software_library",
                "dataset",
                "protocol",
                "standard",
            }
            or (
                len(required_groups) == 1
                and required_groups[0] == entity_tokens
            )
        )
        entity_minimum_hits = (
            max(1, len(entity_tokens) - 1)
            if named_entity and entity_tokens
            else min(2, len(entity_tokens))
            if entity_tokens
            else 0
        )
        if (
            entity_tokens
            and len(entity_tokens & found) < entity_minimum_hits
        ):
            return False

        context_groups = [
            tokens
            for tokens in (
                _tokens(value)
                for value in (
                    candidate.get("target_context_dimensions") or []
                )
            )
            if tokens
        ]
        context_matched = any(
            len(tokens & found)
            >= max(1, (len(tokens) + 1) // 2)
            for tokens in context_groups
        )
        if required_groups:
            matched_groups = sum(
                len(term_tokens & found)
                >= max(1, (len(term_tokens) + 1) // 2)
                for term_tokens in required_groups
            )
            # Un contexte cible effectivement observé constitue déjà un second
            # ancrage. Sans ce contexte, deux groupes conceptuels sont exigés
            # pour éviter les homonymes et applications hors domaine.
            minimum_groups = (
                1
                if context_matched or len(required_groups) < 3
                else 2
            )
            if matched_groups < minimum_groups:
                return False
            if (
                candidate.get("require_direct_evidence")
                and context_groups
                and not context_matched
                and matched_groups < 2
            ):
                return False

        for term in candidate.get("excluded_terms") or []:
            term_tokens = _tokens(term)
            if term_tokens and term_tokens.issubset(found):
                return False

        wanted = _tokens(candidate.get("query"))
        if not wanted:
            return False
        hits = len(wanted & found)
        minimum_hits = 1 if len(wanted) <= 4 else 2
        return hits >= minimum_hits and hits / len(wanted) >= 0.08

    @staticmethod
    def _score(
        query: Any,
        candidate_text: Any,
        *,
        authority: float,
        rank: int,
    ) -> float:
        wanted = _tokens(query)
        found = _tokens(candidate_text)
        overlap = len(wanted & found) / max(1, len(wanted))
        rank_score = 1.0 / max(1, rank)
        return round(
            max(0.0, min(1.0, 0.68 * overlap + 0.22 * authority + 0.10 * rank_score)),
            4,
        )

    @staticmethod
    def _deduplicate(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        output: dict[str, dict[str, Any]] = {}
        scientific_title_keys: dict[str, str] = {}

        def title_key(row: Mapping[str, Any]) -> str:
            if row.get("candidate_kind") != "scientific_article":
                return ""
            value = _norm(row.get("title"))
            value = re.sub(
                r"(?:\s+(?:request\s+pdf|full\s+text|view\s+article|pdf))+$",
                "",
                value,
            ).strip()
            return value if len(_tokens(value)) >= 5 else ""

        def url_quality(value: Any) -> int:
            url = _clean(value, 2000)
            domain = _domain(url)
            if any(
                domain == item or domain.endswith("." + item)
                for item in {
                    "arxiv.org",
                    "doi.org",
                    "pmc.ncbi.nlm.nih.gov",
                    "pubmed.ncbi.nlm.nih.gov",
                }
            ):
                return 5
            if domain.endswith(
                (
                    "researchgate.net",
                    "semanticscholar.org",
                    "academia.edu",
                )
            ):
                return 1
            if _looks_like_scientific_web_source(
                url,
                "",
            ):
                return 4
            return 3 if domain else 0

        for row in rows:
            key = _canonical_source_key(row)
            normalized_title = title_key(row)
            if normalized_title and normalized_title in scientific_title_keys:
                key = scientific_title_keys[normalized_title]
            existing = output.get(key)
            if existing is None:
                output[key] = row
                if normalized_title:
                    scientific_title_keys[normalized_title] = key
                continue
            existing["source_providers"] = sorted(
                set(existing.get("source_providers") or [])
                | set(row.get("source_providers") or [])
            )
            existing["relevance_score"] = max(
                float(existing.get("relevance_score") or 0.0),
                float(row.get("relevance_score") or 0.0),
            )
            existing["open_access"] = bool(
                existing.get("open_access") or row.get("open_access")
            )
            existing_title = _clean(existing.get("title"), 600)
            incoming_title = _clean(row.get("title"), 600)
            if (
                incoming_title
                and (
                    not existing_title
                    or existing_title.casefold()
                    in {
                        _clean(existing.get("venue"), 600).casefold(),
                        _domain(_clean(existing.get("url"), 2000)),
                    }
                )
            ):
                existing["title"] = incoming_title
            if not existing.get("abstract") and row.get("abstract"):
                existing["abstract"] = row["abstract"]
            if not existing.get("pdf_url") and row.get("pdf_url"):
                existing["pdf_url"] = row["pdf_url"]
            if not existing.get("doi") and row.get("doi"):
                existing["doi"] = row["doi"]
            if (
                row.get("url")
                and url_quality(row.get("url"))
                > url_quality(existing.get("url"))
            ):
                existing["url"] = row["url"]
                if row.get("venue"):
                    existing["venue"] = row["venue"]
            if (
                not existing.get("web_citation_context")
                and row.get("web_citation_context")
            ):
                existing["web_citation_context"] = row[
                    "web_citation_context"
                ]
            if (
                existing.get("candidate_kind") != "scientific_article"
                and row.get("candidate_kind") == "scientific_article"
            ):
                existing["candidate_kind"] = "scientific_article"
            existing["official_source"] = bool(
                existing.get("official_source") or row.get("official_source")
            )
            existing["scientific_evidence_eligible"] = bool(
                existing.get("scientific_evidence_eligible")
                or row.get("scientific_evidence_eligible")
            )
            existing["retrieval"] = [
                *list(existing.get("retrieval") or []),
                *list(row.get("retrieval") or []),
            ]
        return list(output.values())


__all__ = ["WebResearchService"]
