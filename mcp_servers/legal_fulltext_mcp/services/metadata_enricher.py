from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

from ..domain.identity import author_similarity, title_similarity
from ..domain.models import ArticleIdentity, ProviderAttempt
from ..domain.normalizers import normalize_doi
from ..infrastructure.http import HttpRequestError, ResilientHttpClient


def _first_title(message: dict[str, Any]) -> str | None:
    title = message.get("title")
    if isinstance(title, list) and title:
        return str(title[0]).strip() or None
    if isinstance(title, str):
        return title.strip() or None
    return None


def _authors(message: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for author in message.get("author") or []:
        if not isinstance(author, dict):
            continue
        name = f"{author.get('given') or ''} {author.get('family') or ''}".strip()
        if name:
            out.append(name)
    return out


def _year(message: dict[str, Any]) -> int | None:
    for key in ["published-print", "published-online", "issued", "created"]:
        value = message.get(key)
        if not isinstance(value, dict):
            continue
        parts = value.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                continue
    return None


class ArticleMetadataEnricher:
    """Vérifie un DOI puis le réconcilie par métadonnées en cas de besoin.

    Un DOI d'entrée incohérent est conservé dans ``input_doi`` mais retiré de
    ``doi``. Un DOI de remplacement n'est accepté que si le titre est presque
    exact, ou si titre, auteurs et année forment un ensemble cohérent.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        crossref_mailto: str = "",
        title_accept_score: float = 0.72,
        title_conflict_score: float = 0.55,
        title_reconcile_score: float = 0.94,
        exact_title_reconcile_score: float = 0.985,
    ) -> None:
        self.enabled = bool(enabled)
        self.crossref_mailto = (crossref_mailto or "").strip()
        self.title_accept_score = float(title_accept_score)
        self.title_conflict_score = float(title_conflict_score)
        self.title_reconcile_score = float(title_reconcile_score)
        self.exact_title_reconcile_score = float(exact_title_reconcile_score)

    def _params(self) -> dict[str, str] | None:
        return {"mailto": self.crossref_mailto} if self.crossref_mailto else None

    @staticmethod
    def _complete(article: ArticleIdentity, message: dict[str, Any]) -> None:
        if not article.authors:
            article.authors = _authors(message)
        if article.year is None:
            article.year = _year(message)
        if "crossref" not in article.metadata_sources:
            article.metadata_sources.append("crossref")

    def _reconciliation_score(
        self,
        article: ArticleIdentity,
        message: dict[str, Any],
    ) -> tuple[bool, float, float, bool]:
        t_score = title_similarity(article.title, _first_title(message))
        candidate_authors = _authors(message)
        a_score = author_similarity(article.authors, candidate_authors)
        candidate_year = _year(message)
        year_conflict = bool(
            article.year and candidate_year and abs(article.year - candidate_year) > 1
        )
        author_conflict = bool(
            article.authors and candidate_authors and a_score < 0.25
        )
        exact = t_score >= self.exact_title_reconcile_score
        supported = t_score >= self.title_reconcile_score and a_score >= 0.5
        return bool((exact or supported) and not author_conflict and not year_conflict), t_score, a_score, year_conflict

    async def _title_candidates(
        self,
        article: ArticleIdentity,
        http: ResilientHttpClient,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "query.title": article.title,
            "rows": 5,
            "select": "DOI,title,author,published-print,published-online,issued,created",
        }
        if self.crossref_mailto:
            params["mailto"] = self.crossref_mailto
        payload = await http.get_json("https://api.crossref.org/works", params=params)
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        return [item for item in (message.get("items") or []) if isinstance(item, dict)]

    async def _reconcile_by_title(
        self,
        article: ArticleIdentity,
        http: ResilientHttpClient,
    ) -> tuple[dict[str, Any] | None, int]:
        items = await self._title_candidates(article, http)
        ranked: list[tuple[float, float, dict[str, Any]]] = []
        for item in items:
            doi = normalize_doi(item.get("DOI"))
            accepted, title_score, author_score, _ = self._reconciliation_score(article, item)
            if doi and accepted:
                ranked.append((title_score, author_score, item))
        ranked.sort(key=lambda value: (value[0], value[1]), reverse=True)
        return (ranked[0][2] if ranked else None), len(items)

    async def enrich(
        self,
        article: ArticleIdentity,
        http: ResilientHttpClient,
    ) -> tuple[ArticleIdentity, ProviderAttempt]:
        started = time.perf_counter()
        enriched = article.model_copy(deep=True)
        original_doi = normalize_doi(article.doi)
        enriched.input_doi = enriched.input_doi or article.doi
        enriched.doi_status = "provided" if original_doi else "missing"

        if not self.enabled:
            return enriched, ProviderAttempt(
                provider="metadata_enrichment",
                enabled=False,
                ok=True,
                status="disabled",
                elapsed_seconds=round(time.perf_counter() - started, 3),
            )

        conflict_detected = False
        try:
            if original_doi:
                try:
                    payload = await http.get_json(
                        f"https://api.crossref.org/works/{quote(original_doi, safe='')}",
                        params=self._params(),
                    )
                except HttpRequestError as direct_error:
                    if direct_error.transient:
                        raise
                    payload = {}
                    enriched.doi = None
                    enriched.doi_status = "lookup_failed"
                    enriched.metadata_warnings.append(
                        f"Le DOI fourni n'a pas de notice Crossref utilisable ({direct_error})."
                    )
                message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
                fetched_title = _first_title(message)
                score = title_similarity(enriched.title, fetched_title)

                if message and fetched_title and score >= self.title_accept_score:
                    enriched.doi = original_doi
                    enriched.doi_status = "verified"
                    self._complete(enriched, message)
                    return enriched, ProviderAttempt(
                        provider="metadata_enrichment",
                        enabled=True,
                        ok=True,
                        status="metadata_enriched" if enriched.authors or enriched.year else "doi_verified",
                        candidates_count=1,
                        elapsed_seconds=round(time.perf_counter() - started, 3),
                    )

                if fetched_title and score < self.title_conflict_score:
                    conflict_detected = True
                    enriched.doi = None
                    enriched.doi_status = "conflict_ignored"
                    if "crossref" not in enriched.metadata_sources:
                        enriched.metadata_sources.append("crossref")
                    enriched.metadata_warnings.append(
                        "Le DOI fourni a été ignoré : son titre Crossref est incompatible "
                        f"avec l'article sélectionné (score={score:.3f})."
                    )
                else:
                    enriched.doi = None
                    enriched.doi_status = "lookup_failed"
                    enriched.metadata_warnings.append(
                        "Le DOI fourni n'a pas pu être confirmé par les métadonnées Crossref."
                    )

            replacement, count = await self._reconcile_by_title(enriched, http)
            if replacement:
                replacement_doi = normalize_doi(replacement.get("DOI"))
                enriched.doi = replacement_doi
                enriched.doi_status = "reconciled"
                self._complete(enriched, replacement)
                enriched.metadata_warnings.append(
                    "Le DOI effectif a été réconcilié par titre, auteurs et année ; "
                    "le DOI d'entrée reste disponible dans input_doi."
                )
                return enriched, ProviderAttempt(
                    provider="metadata_enrichment",
                    enabled=True,
                    ok=True,
                    status="doi_reconciled",
                    candidates_count=count,
                    elapsed_seconds=round(time.perf_counter() - started, 3),
                )

            if not original_doi:
                enriched.doi_status = "missing"
            status = "doi_title_conflict_no_replacement" if conflict_detected else "metadata_not_reconciled"
            return enriched, ProviderAttempt(
                provider="metadata_enrichment",
                enabled=True,
                ok=True,
                status=status,
                candidates_count=count,
                elapsed_seconds=round(time.perf_counter() - started, 3),
            )
        except HttpRequestError as exc:
            if not conflict_detected:
                enriched.doi_status = "lookup_failed" if original_doi else "missing"
            enriched.metadata_warnings.append(f"Enrichissement Crossref indisponible : {exc}")
            return enriched, ProviderAttempt(
                provider="metadata_enrichment",
                enabled=True,
                ok=False,
                status="provider_temporarily_unavailable" if exc.transient else "provider_error",
                elapsed_seconds=round(time.perf_counter() - started, 3),
                error=str(exc),
                http_status=exc.status_code,
                transient=exc.transient,
            )
        except Exception as exc:
            if not conflict_detected:
                enriched.doi_status = "lookup_failed" if original_doi else "missing"
            enriched.metadata_warnings.append(f"Enrichissement Crossref échoué : {exc}")
            return enriched, ProviderAttempt(
                provider="metadata_enrichment",
                enabled=True,
                ok=False,
                status="provider_error",
                elapsed_seconds=round(time.perf_counter() - started, 3),
                error=str(exc),
            )
