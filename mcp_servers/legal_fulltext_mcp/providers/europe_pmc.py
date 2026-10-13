from __future__ import annotations

from ..domain.models import ArticleIdentity, FulltextCandidate
from ..domain.normalizers import normalize_doi
from ..infrastructure.http import HttpRequestError, ResilientHttpClient
from .base import FulltextProvider
from .source_utils import classify_public_source


class EuropePmcProvider(FulltextProvider):
    name = "europe_pmc"
    priority = 7

    async def search(self, article: ArticleIdentity, http: ResilientHttpClient) -> list[FulltextCandidate]:
        doi = normalize_doi(article.doi)
        title_query = f'TITLE:"{article.title.replace(chr(34), " ")}"'
        queries = [f'DOI:"{doi}"', title_query] if doi else [title_query]
        results: list[dict] = []
        seen_results: set[str] = set()
        first_error: HttpRequestError | None = None
        for query in queries:
            try:
                data = await http.get_json(
                    "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                    params={"query": query, "format": "json", "pageSize": 10, "resultType": "core"},
                )
            except HttpRequestError as exc:
                first_error = first_error or exc
                continue
            raw_results = ((data.get("resultList") or {}).get("result") or []) if isinstance(data.get("resultList"), dict) else []
            for item in raw_results:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("pmcid") or item.get("pmid") or item.get("doi") or item.get("title") or "").lower()
                if key and key not in seen_results:
                    seen_results.add(key)
                    results.append(item)
        if not results and first_error is not None:
            raise first_error
        out: list[FulltextCandidate] = []
        for item in results:
            pmcid = item.get("pmcid")
            if not pmcid:
                continue
            is_open = str(item.get("isOpenAccess") or "").upper() == "Y"
            in_epmc = str(item.get("inEPMC") or "").upper() == "Y"
            if not (is_open or in_epmc):
                continue
            pdf_url = f"https://europepmc.org/articles/{pmcid}?pdf=render"
            access_type, rights_status, domain = classify_public_source(pdf_url)
            author_string = str(item.get("authorString") or "")
            authors = [x.strip() for x in author_string.split(",") if x.strip()]
            year_raw = item.get("pubYear")
            year = int(year_raw) if str(year_raw or "").isdigit() else None
            out.append(
                FulltextCandidate(
                    provider=self.name,
                    provider_priority=self.priority,
                    pdf_url=pdf_url,
                    landing_url=f"https://europepmc.org/article/MED/{item.get('pmid')}" if item.get("pmid") else None,
                    host_type="repository",
                    legal_access=True,
                    access_type=access_type,
                    rights_status=rights_status,
                    source_domain=domain,
                    discovered_via="europe_pmc",
                    candidate_doi=item.get("doi"),
                    candidate_title=item.get("title"),
                    candidate_authors=authors,
                    candidate_year=year,
                    raw_metadata={"pmcid": pmcid, "pmid": item.get("pmid")},
                )
            )
        return out
