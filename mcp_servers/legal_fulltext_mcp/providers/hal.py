from __future__ import annotations

from typing import Any

from ..domain.models import ArticleIdentity, FulltextCandidate
from ..domain.normalizers import normalize_doi
from ..infrastructure.http import ResilientHttpClient
from .base import FulltextProvider


def _add_urls(value: Any, urls: list[str]) -> None:
    if isinstance(value, str):
        if value.startswith("http"):
            urls.append(value)
        elif value.startswith("/"):
            urls.append("https://hal.science" + value)
    elif isinstance(value, list):
        for item in value:
            _add_urls(item, urls)
    elif isinstance(value, dict):
        for key in ["url", "href", "file", "downloadUrl", "download_url"]:
            _add_urls(value.get(key), urls)


class HalProvider(FulltextProvider):
    name = "hal"
    priority = 4

    async def search(self, article: ArticleIdentity, http: ResilientHttpClient) -> list[FulltextCandidate]:
        doi = normalize_doi(article.doi)
        title = article.title.replace('"', " ")
        queries = []
        if doi:
            queries.extend([f'doiId_s:"{doi}"', doi])
        queries.extend([f'title_t:"{title}"', f'"{title}"'])

        out: list[FulltextCandidate] = []
        seen: set[str] = set()
        for query in queries:
            data = await http.get_json(
                "https://api.archives-ouvertes.fr/search/",
                params={
                    "wt": "json",
                    "rows": "8",
                    "q": query,
                    "fl": "docid,halId_s,title_s,uri_s,fileMain_s,files_s,linkExtUrl_s,doiId_s,submittedDate_s,authFullName_s,publicationDateY_i",
                },
            )
            docs = data.get("response", {}).get("docs", []) if isinstance(data.get("response"), dict) else []
            for doc in docs:
                if not isinstance(doc, dict):
                    continue
                urls: list[str] = []
                for key in ["fileMain_s", "files_s", "linkExtUrl_s"]:
                    _add_urls(doc.get(key), urls)
                uri = doc.get("uri_s")
                hal_id = doc.get("halId_s")
                if isinstance(uri, str) and uri.startswith("http"):
                    urls.append(uri.rstrip("/") + "/document")
                if hal_id:
                    urls.extend(
                        [
                            f"https://hal.science/{hal_id}/document",
                            f"https://theses.hal.science/{hal_id}/document",
                            f"https://tel.archives-ouvertes.fr/{hal_id}/document",
                        ]
                    )
                for url in urls:
                    key = url.lower().strip()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    title_value = doc.get("title_s")
                    if isinstance(title_value, list):
                        title_value = title_value[0] if title_value else None
                    out.append(
                        FulltextCandidate(
                            provider=self.name,
                            provider_priority=self.priority,
                            pdf_url=url,
                            landing_url=uri,
                            host_type="repository",
                            legal_access=True,
                            candidate_doi=doc.get("doiId_s"),
                            candidate_title=title_value,
                            candidate_authors=doc.get("authFullName_s") or [],
                            candidate_year=doc.get("publicationDateY_i"),
                            raw_metadata={"hal_id": hal_id, "docid": doc.get("docid")},
                        )
                    )
            if out:
                break
        return out
