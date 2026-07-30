# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Any, Dict, List
from .external_source_base import cache_path, encode_params, get_json, normalized_error, read_cache, safe, write_cache

CORE_SEARCH = "https://api.core.ac.uk/v3/search/works"


class CoreClient:
    def __init__(self, api_key: str | None = None, timeout: int = 12, max_retries: int = 1, cache_ttl_days: int = 30):
        self.api_key = api_key or os.getenv("CORE_API_KEY", "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_ttl_days = cache_ttl_days

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def search_works(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = safe(query, 300)
        if not query:
            return []
        if not self.api_key:
            return [normalized_error("core", query, "CORE_API_KEY absente", skipped=True)]
        limit = max(1, min(int(limit or 20), 100))
        path = cache_path("core", query, limit)
        cached = read_cache(path, self.cache_ttl_days)
        if cached is not None:
            return cached
        url = encode_params(CORE_SEARCH, {"q": query, "limit": limit})
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json", "User-Agent": "EnnoSmart-EnnoScholar/3.2"}
        try:
            data = get_json(url, headers=headers, timeout=self.timeout, retries=self.max_retries)
            rows = data.get("results") or data.get("data") or []
            out = [self.normalize(x, query) for x in rows if isinstance(x, dict)]
            out = [x for x in out if x.get("title")]
            write_cache(path, out)
            return out
        except Exception as exc:
            stale = read_cache(path, 3650)
            return stale if stale is not None else [normalized_error("core", query, exc)]

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
