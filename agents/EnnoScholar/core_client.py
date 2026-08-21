# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import urllib.error
from typing import Any, Dict, List
from .external_source_base import cache_path, encode_params, get_json, normalized_error, read_cache, safe, write_cache, merge_fresh_with_cache, fallback_from_cache

CORE_SEARCH = "https://api.core.ac.uk/v3/search/works"


class CoreClient:
    """CORE search client with authenticated-first, public-fallback behaviour.

    CORE currently offers free public API access with lower rate limits.  When a
    configured API key is rejected (401/403), EnnoScholar retries the same
    request without Authorization and keeps public mode for the remaining
    queries handled by this client instance/run.
    """

    def __init__(
        self,
        api_key: str | None = None,
        timeout: int = 12,
        max_retries: int = 1,
        cache_ttl_days: int = 30,
    ):
        self.api_key = (api_key if api_key is not None else os.getenv("CORE_API_KEY", "")) or ""
        self.api_key = str(self.api_key).strip()
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_ttl_days = cache_ttl_days
        # After one rejected credential, do not resend the invalid key for every
        # query of the same run. Public CORE remains available.
        self._auth_failed = False

    @property
    def available(self) -> bool:
        # CORE documents a free public access tier, therefore absence/rejection
        # of a key must not remove the provider from discovery.
        return True

    def _headers(self, *, authenticated: bool) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "EnnoSmart-EnnoScholar/3.2",
        }
        if authenticated and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _fetch(self, url: str, *, authenticated: bool) -> Dict[str, Any]:
        return get_json(
            url,
            headers=self._headers(authenticated=authenticated),
            timeout=self.timeout,
            retries=self.max_retries,
        )

    def _normalize_response(
        self,
        data: Dict[str, Any],
        query: str,
        *,
        auth_mode: str,
    ) -> List[Dict[str, Any]]:
        rows = data.get("results") or data.get("data") or []
        out = [self.normalize(x, query) for x in rows if isinstance(x, dict)]
        out = [x for x in out if x.get("title")]
        for item in out:
            item["core_auth_mode"] = auth_mode
        return out

    def search_works(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = safe(query, 300)
        if not query:
            return []

        limit = max(1, min(int(limit or 20), 100))
        path = cache_path("core", query, limit)
        cached = read_cache(path, self.cache_ttl_days)
        url = encode_params(CORE_SEARCH, {"q": query, "limit": limit})

        # Try the configured credential first only while it has not already been
        # rejected during this client/run.
        use_authenticated = bool(self.api_key) and not self._auth_failed

        if use_authenticated:
            try:
                data = self._fetch(url, authenticated=True)
                fresh = self._normalize_response(data, query, auth_mode="api_key")
                combined = merge_fresh_with_cache(fresh, cached, limit, "core")
                write_cache(path, combined)
                return combined
            except Exception as exc:
                status = int(getattr(exc, "code", 0) or 0)
                if not (isinstance(exc, urllib.error.HTTPError) and status in {401, 403}):
                    stale = read_cache(path, 3650)
                    fallback = fallback_from_cache(cached or stale, "core", exc)
                    return fallback if fallback else [normalized_error("core", query, exc)]

                # Credential rejected: switch to public tier for this and all
                # subsequent requests from the same client instance.
                self._auth_failed = True

        # Public mode: either there is no key, or the key has just been rejected.
        try:
            data = self._fetch(url, authenticated=False)
            fresh = self._normalize_response(data, query, auth_mode="public")
            combined = merge_fresh_with_cache(fresh, cached, limit, "core")
            write_cache(path, combined)
            return combined
        except Exception as public_exc:
            stale = read_cache(path, 3650)
            fallback = fallback_from_cache(cached or stale, "core", public_exc)
            if fallback:
                for item in fallback:
                    if isinstance(item, dict):
                        item.setdefault("core_auth_mode", "public_fallback_cache")
                return fallback

            err = normalized_error("core", query, public_exc)
            err["core_auth_mode"] = "public"
            err["auth_fallback_attempted"] = bool(self.api_key)
            return [err]

    @staticmethod
    def normalize(item: Dict[str, Any], query: str) -> Dict[str, Any]:
        authors = []
        for a in item.get("authors") or []:
            if isinstance(a, dict):
                name = safe(a.get("name") or a.get("displayName"), 180)
            else:
                name = safe(a, 180)
            if name: authors.append(name)
        links = item.get("downloadUrl") or item.get("fullTextLink") or item.get("links") or []
        pdf = ""
        if isinstance(links, str):
            pdf = links
        elif isinstance(links, list):
            for x in links:
                candidate = safe(x.get("url") if isinstance(x, dict) else x, 1000)
                if candidate.startswith("http"):
                    pdf = candidate
                    break
        doi = safe(item.get("doi"), 260)
        year = item.get("yearPublished") or item.get("year") or item.get("publishedDate")
        try:
            if isinstance(year, str) and len(year) >= 4: year = int(year[:4])
        except Exception: pass
        return {
            "source": "core",
            "source_type": "scientific_article",
            "query": query,
            "paper_id": safe(item.get("id") or doi, 260),
            "title": safe(item.get("title"), 500),
            "abstract": safe(item.get("abstract"), 12000),
            "year": year,
            "venue": safe(item.get("journals") or item.get("publisher") or item.get("repositoryName") or "CORE", 300),
            "url": pdf or safe(item.get("documentPageUrl") or item.get("sourceFulltextUrls"), 1000),
            "doi": doi,
            "authors": authors,
            "citation_count": int(item.get("citationCount") or 0),
            "publication_types": [safe(item.get("documentType"), 100)] if item.get("documentType") else [],
            "fields_of_study": item.get("fieldOfStudy") or item.get("topics") or [],
            "pdf_url": pdf,
            "primary_pdf_url": pdf,
            "is_open_access": True,
            "open_access": True,
            "free_fulltext_available": bool(pdf),
            "fulltext_access_status": "open_access_pdf" if pdf else "open_access_landing",
        }
