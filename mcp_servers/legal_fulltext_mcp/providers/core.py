from __future__ import annotations

from typing import Any

from ..domain.models import ArticleIdentity, FulltextCandidate
from ..domain.normalizers import looks_like_pdf_url, normalize_doi
from ..infrastructure.http import HttpRequestError, ResilientHttpClient
from .base import FulltextProvider


_URL_KEYS = {
    "downloadUrl",
    "fullTextIdentifier",
    "fullTextLink",
    "pdfUrl",
    "sourceFulltextUrls",
    "urls",
    "links",
    "url",
    "href",
}


def _collect_urls(value: Any, *, key_name: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        if key_name in _URL_KEYS or value.startswith(("http://", "https://")):
            out.append(value)
    elif isinstance(value, list):
        for item in value:
            out.extend(_collect_urls(item, key_name=key_name))
    elif isinstance(value, dict):
        for key, nested in value.items():
            if key in _URL_KEYS or isinstance(nested, (dict, list)):
                out.extend(_collect_urls(nested, key_name=key))
    return out


def _repository_landing(work: dict[str, Any]) -> str | None:
    repo = work.get("repositoryDocument")
    if isinstance(repo, dict):
        for key in ["url", "oai", "repositoryUrl"]:
            value = repo.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
    for key in ["sourceFulltextUrls", "urls"]:
        value = work.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.startswith(("http://", "https://")) and not looks_like_pdf_url(item):
                    return item
    work_id = work.get("id")
    return f"https://core.ac.uk/outputs/{work_id}" if work_id else None


def _pdf_urls_from_work(work: dict[str, Any]) -> list[str]:
    urls = _collect_urls(work)
    work_id = work.get("id")
    if work_id:
        urls.extend(
            [
                f"https://core.ac.uk/download/{work_id}.pdf",
                f"https://files.core.ac.uk/download/{work_id}.pdf",
            ]
        )
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        url = raw.strip()
        key = url.lower()
        if not url.startswith(("http://", "https://")):
            continue
        # Les champs de l'API CORE sont déjà des emplacements documentaires.
        # Une URL opaque est conservée et sera qualifiée par le probe HTTP.
        if "/works/" in key and not looks_like_pdf_url(url):
            continue
        if key not in seen:
            seen.add(key)
            out.append(url)
    return out


def _authors(work: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for author in work.get("authors") or []:
        if isinstance(author, str):
            out.append(author)
        elif isinstance(author, dict):
            name = author.get("name") or author.get("displayName")
            if name:
                out.append(str(name))
    return out


class CoreProvider(FulltextProvider):
    name = "core"
    priority = 2

    def __init__(self, api_key: str, *, detail_limit: int = 3) -> None:
        self.api_key = (api_key or "").strip()
        self.detail_limit = max(0, min(10, int(detail_limit)))

    def enabled(self) -> bool:
        return bool(self.api_key)

    def disabled_reason(self) -> str | None:
        return None if self.enabled() else "CORE_API_KEY manquant"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def _search_works(self, query: str, http: ResilientHttpClient) -> list[dict[str, Any]]:
        data = await http.get_json(
            "https://api.core.ac.uk/v3/search/works/",
            params={"q": query, "limit": 10},
            headers=self._headers(),
        )
        return [x for x in (data.get("results") or []) if isinstance(x, dict)]

    async def _load_detail(self, work_id: Any, http: ResilientHttpClient) -> dict[str, Any] | None:
        if work_id in {None, ""}:
            return None
        try:
            data = await http.get_json(
                f"https://api.core.ac.uk/v3/works/{work_id}",
                headers=self._headers(),
            )
            return data if isinstance(data, dict) else None
        except HttpRequestError:
            return None

    async def search(self, article: ArticleIdentity, http: ResilientHttpClient) -> list[FulltextCandidate]:
        doi = normalize_doi(article.doi)
        queries = [f'doi:"{doi}"'] if doi else []
        queries.append(f'title:"{article.title.replace(chr(34), " ")}"')

        works: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        first_error: HttpRequestError | None = None
        for query in queries:
            try:
                query_works = await self._search_works(query, http)
            except HttpRequestError as exc:
                first_error = first_error or exc
                continue
            for work in query_works:
                key = str(work.get("id") or work.get("doi") or work.get("title") or "").lower()
                if key and key not in seen_ids:
                    seen_ids.add(key)
                    works.append(work)
            # Continuer avec le titre même si la notice DOI existe : la notice
            # exacte peut ne pas exposer de fichier tandis qu'un dépôt le fait.
        if not works and first_error is not None:
            raise first_error

        for index, work in enumerate(list(works)[: self.detail_limit]):
            detail = await self._load_detail(work.get("id"), http)
            if detail:
                merged = dict(work)
                merged.update(detail)
                works[index] = merged

        out: list[FulltextCandidate] = []
        seen_urls: set[str] = set()
        for work in works:
            landing_url = _repository_landing(work)
            for url in _pdf_urls_from_work(work):
                key = url.lower().strip()
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                out.append(
                    FulltextCandidate(
                        provider=self.name,
                        provider_priority=self.priority,
                        pdf_url=url,
                        landing_url=landing_url,
                        license=work.get("license"),
                        version=work.get("version"),
                        host_type="repository",
                        legal_access=True,
                        access_type="repository_copy",
                        rights_status="repository_terms",
                        source_domain="core.ac.uk",
                        discovered_via="core_api_v3",
                        candidate_doi=work.get("doi"),
                        candidate_title=work.get("title"),
                        candidate_authors=_authors(work),
                        candidate_year=work.get("yearPublished") or work.get("year"),
                        raw_metadata={"core_id": work.get("id")},
                    )
                )
        return out
