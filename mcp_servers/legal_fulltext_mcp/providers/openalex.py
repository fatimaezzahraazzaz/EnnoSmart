from __future__ import annotations

from urllib.parse import quote

from ..domain.models import ArticleIdentity, FulltextCandidate
from ..domain.normalizers import normalize_doi
from ..infrastructure.http import HttpRequestError, ResilientHttpClient
from .base import FulltextProvider


class OpenAlexProvider(FulltextProvider):
    name = "openalex"
    priority = 3

    def __init__(self, api_key: str) -> None:
        self.api_key = (api_key or "").strip()

    def enabled(self) -> bool:
        return bool(self.api_key)

    def disabled_reason(self) -> str | None:
        return None if self.enabled() else "OPENALEX_API_KEY manquant"

    @staticmethod
    def _has_oa_location(work: dict) -> bool:
        locations = [work.get("best_oa_location"), work.get("primary_location"), *(work.get("locations") or [])]
        return any(
            isinstance(location, dict)
            and location.get("is_oa")
            and (location.get("pdf_url") or location.get("landing_page_url"))
            for location in locations
        )

    async def _load_works(self, article: ArticleIdentity, http: ResilientHttpClient) -> list[dict]:
        base_params = {"api_key": self.api_key}
        doi = normalize_doi(article.doi)
        works: list[dict] = []
        doi_error: HttpRequestError | None = None
        if doi:
            try:
                work_id = quote(f"https://doi.org/{doi}", safe="")
                work = await http.get_json(f"https://api.openalex.org/works/{work_id}", params=base_params)
                if work:
                    works.append(work)
            except HttpRequestError as exc:
                doi_error = exc

        # Le DOI peut pointer vers une notice sans localisation OA. Dans ce
        # cas, la recherche bibliographique peut retrouver une autre notice.
        if not any(self._has_oa_location(work) for work in works):
            try:
                data = await http.get_json(
                    "https://api.openalex.org/works",
                    params={"api_key": self.api_key, "search": article.title, "per_page": 5},
                )
                works.extend(item for item in (data.get("results") or []) if isinstance(item, dict))
            except HttpRequestError:
                if doi_error is not None:
                    raise doi_error
                raise

        out: list[dict] = []
        seen: set[str] = set()
        for work in works:
            key = str(work.get("id") or work.get("doi") or work.get("title") or "").lower()
            if key and key not in seen:
                seen.add(key)
                out.append(work)
        return out

    async def search(self, article: ArticleIdentity, http: ResilientHttpClient) -> list[FulltextCandidate]:
        out: list[FulltextCandidate] = []
        seen: set[str] = set()
        for work in await self._load_works(article, http):
            authors: list[str] = []
            for authorship in work.get("authorships") or []:
                if isinstance(authorship, dict):
                    author = authorship.get("author") or {}
                    name = author.get("display_name") if isinstance(author, dict) else None
                    if name:
                        authors.append(name)

            raw_locations: list[dict] = []
            for name in ["best_oa_location", "primary_location"]:
                value = work.get(name)
                if isinstance(value, dict):
                    raw_locations.append(value)
            raw_locations.extend(item for item in (work.get("locations") or []) if isinstance(item, dict))

            for location in raw_locations:
                if not location.get("is_oa"):
                    continue
                direct_pdf = location.get("pdf_url")
                landing_url = location.get("landing_page_url")
                candidate_url = direct_pdf or landing_url
                key = str(candidate_url or "").lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                source = location.get("source") or {}
                out.append(
                    FulltextCandidate(
                        provider=self.name,
                        provider_priority=self.priority,
                        pdf_url=candidate_url,
                        landing_url=landing_url,
                        license=location.get("license"),
                        version=location.get("version"),
                        host_type=source.get("type") if isinstance(source, dict) else None,
                        legal_access=True,
                        discovered_via="openalex_oa_location",
                        candidate_doi=work.get("doi"),
                        candidate_title=work.get("title") or work.get("display_name"),
                        candidate_authors=authors,
                        candidate_year=work.get("publication_year"),
                        raw_metadata={
                            "openalex_id": work.get("id"),
                            "source_name": source.get("display_name") if isinstance(source, dict) else None,
                            "direct_pdf_available": bool(direct_pdf),
                        },
                    )
                )
        return out
