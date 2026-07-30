from __future__ import annotations

from urllib.parse import quote

from ..domain.models import ArticleIdentity, FulltextCandidate
from ..domain.normalizers import normalize_doi
from ..infrastructure.http import HttpRequestError, ResilientHttpClient
from .base import FulltextProvider
from .source_utils import classify_public_source


_FIELDS = "paperId,title,year,authors,externalIds,openAccessPdf,url"


class SemanticScholarProvider(FulltextProvider):
    name = "semantic_scholar"
    priority = 4

    def __init__(self, api_key: str = "") -> None:
        self.api_key = (api_key or "").strip()

    def _headers(self) -> dict[str, str] | None:
        return {"x-api-key": self.api_key} if self.api_key else None

    async def _by_doi(self, doi: str, http: ResilientHttpClient) -> dict | None:
        data = await http.get_json(
            f"https://api.semanticscholar.org/graph/v1/paper/{quote('DOI:' + doi, safe='')}",
            params={"fields": _FIELDS},
            headers=self._headers(),
        )
        return data if isinstance(data, dict) and data else None

    async def _by_title(self, title: str, http: ResilientHttpClient) -> list[dict]:
        data = await http.get_json(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": title, "limit": 10, "fields": _FIELDS},
            headers=self._headers(),
        )
        return [x for x in (data.get("data") or []) if isinstance(x, dict)]

    async def _load_papers(self, article: ArticleIdentity, http: ResilientHttpClient) -> list[dict]:
        doi = normalize_doi(article.doi)
        papers: list[dict] = []
        doi_error: HttpRequestError | None = None
        if doi:
            try:
                paper = await self._by_doi(doi, http)
                if paper:
                    papers.append(paper)
            except HttpRequestError as exc:
                doi_error = exc

        # Fallback systématique si le lookup DOI n'apporte pas de PDF OA.
        has_oa = any(isinstance(p.get("openAccessPdf"), dict) and p["openAccessPdf"].get("url") for p in papers)
        if not has_oa:
            try:
                papers.extend(await self._by_title(article.title, http))
            except HttpRequestError:
                if doi_error is not None:
                    raise doi_error
                raise

        out: list[dict] = []
        seen: set[str] = set()
        for paper in papers:
            key = str(paper.get("paperId") or paper.get("url") or paper.get("title") or "").lower()
            if key and key not in seen:
                seen.add(key)
                out.append(paper)
        return out

    async def search(self, article: ArticleIdentity, http: ResilientHttpClient) -> list[FulltextCandidate]:
        out: list[FulltextCandidate] = []
        for paper in await self._load_papers(article, http):
            oa = paper.get("openAccessPdf")
            if not isinstance(oa, dict):
                continue
            pdf_url = oa.get("url")
            if not isinstance(pdf_url, str) or not pdf_url.startswith(("http://", "https://")):
                continue
            authors = [
                str(author["name"])
                for author in (paper.get("authors") or [])
                if isinstance(author, dict) and author.get("name")
            ]
            external_ids = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
            license_value = oa.get("license") if isinstance(oa.get("license"), str) else None
            access_type, rights_status, domain = classify_public_source(pdf_url, license_value=license_value)
            out.append(
                FulltextCandidate(
                    provider=self.name,
                    provider_priority=self.priority,
                    pdf_url=pdf_url,
                    landing_url=paper.get("url"),
                    license=license_value,
                    host_type="aggregator",
                    legal_access=True,
                    access_type=access_type,
                    rights_status=rights_status,
                    source_domain=domain,
                    discovered_via="semantic_scholar_openAccessPdf",
                    candidate_doi=external_ids.get("DOI"),
                    candidate_title=paper.get("title"),
                    candidate_authors=authors,
                    candidate_year=paper.get("year"),
                    raw_metadata={"paperId": paper.get("paperId"), "oa_status": oa.get("status")},
                )
            )
        return out
