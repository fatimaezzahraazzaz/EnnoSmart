from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..domain.models import ArticleIdentity, FulltextCandidate
from ..domain.normalizers import normalize_doi
from ..infrastructure.http import HttpRequestError, ResilientHttpClient
from .base import FulltextProvider
from .source_utils import classify_public_source


def _identifier(bibjson: dict[str, Any], kind: str) -> str | None:
    for item in bibjson.get("identifier") or []:
        if (
            isinstance(item, dict)
            and str(item.get("type") or "").lower() == kind.lower()
            and item.get("id")
        ):
            return str(item["id"])
    return None


def _authors(bibjson: dict[str, Any]) -> list[str]:
    return [
        str(item["name"])
        for item in (bibjson.get("author") or [])
        if isinstance(item, dict) and item.get("name")
    ]


class DoajProvider(FulltextProvider):
    """Index OA DOAJ, interrogé uniquement par DOI ou titre scientifique."""

    name = "doaj"
    priority = 5

    async def _search(
        self,
        article: ArticleIdentity,
        http: ResilientHttpClient,
    ) -> list[dict[str, Any]]:
        doi = normalize_doi(article.doi)
        queries = [f'doi.exact:"{doi}"'] if doi else []
        queries.append(f'bibjson.title:"{article.title.replace(chr(34), " ")}"')

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        first_error: HttpRequestError | None = None
        for query in queries:
            try:
                data = await http.get_json(
                    f"https://doaj.org/api/search/articles/{quote(query, safe='')}",
                    params={"pageSize": 10},
                )
            except HttpRequestError as exc:
                first_error = first_error or exc
                continue
            for item in data.get("results") or []:
                if not isinstance(item, dict):
                    continue
                bibjson = item.get("bibjson") if isinstance(item.get("bibjson"), dict) else {}
                key = str(item.get("id") or _identifier(bibjson, "doi") or bibjson.get("title") or "").lower()
                if key and key not in seen:
                    seen.add(key)
                    results.append(item)
        if not results and first_error is not None:
            raise first_error
        return results

    async def search(
        self,
        article: ArticleIdentity,
        http: ResilientHttpClient,
    ) -> list[FulltextCandidate]:
        out: list[FulltextCandidate] = []
        seen: set[str] = set()
        for item in await self._search(article, http):
            bibjson = item.get("bibjson") if isinstance(item.get("bibjson"), dict) else {}
            candidate_doi = _identifier(bibjson, "doi")
            for link in bibjson.get("link") or []:
                if not isinstance(link, dict):
                    continue
                url = str(link.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    continue
                link_type = str(link.get("type") or "").lower()
                content_type = str(link.get("content_type") or "").lower()
                if not (
                    link_type in {"fulltext", "full_text"}
                    or "pdf" in content_type
                    or url.lower().endswith(".pdf")
                ):
                    continue
                key = url.lower()
                if key in seen:
                    continue
                seen.add(key)
                access_type, rights_status, domain = classify_public_source(
                    url,
                    license_value="DOAJ open-access journal",
                )
                out.append(
                    FulltextCandidate(
                        provider=self.name,
                        provider_priority=self.priority,
                        pdf_url=url,
                        landing_url=url,
                        license="DOAJ open-access journal",
                        host_type="publisher",
                        legal_access=True,
                        access_type=access_type,
                        rights_status=rights_status,
                        source_domain=domain,
                        discovered_via="doaj_fulltext_link",
                        candidate_doi=candidate_doi,
                        candidate_title=bibjson.get("title"),
                        candidate_authors=_authors(bibjson),
                        candidate_year=(
                            int(bibjson["year"])
                            if str(bibjson.get("year") or "").isdigit()
                            else None
                        ),
                        raw_metadata={
                            "doaj_id": item.get("id"),
                            "link_type": link_type,
                            "content_type": content_type,
                        },
                    )
                )
        return out
