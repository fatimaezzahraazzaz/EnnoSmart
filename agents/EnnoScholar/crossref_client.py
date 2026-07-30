# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Any, Dict, List

from .external_source_base import cache_path, encode_params, get_json, normalized_error, read_cache, safe, strip_html, write_cache

CROSSREF_WORKS = "https://api.crossref.org/works"


class CrossrefClient:
    def __init__(self, mailto: str | None = None, timeout: int = 10, max_retries: int = 1, cache_ttl_days: int = 30):
        self.mailto = mailto or os.getenv("CROSSREF_MAILTO") or os.getenv("OPENALEX_MAILTO", "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_ttl_days = cache_ttl_days

    def search_works(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = safe(query, 300)
        if not query:
            return []
        limit = max(1, min(int(limit or 20), 100))
        path = cache_path("crossref", query, limit)
        cached = read_cache(path, self.cache_ttl_days)
        if cached is not None:
            return cached
        params = {
            "query.bibliographic": query,
            "rows": limit,
            "select": "DOI,title,author,abstract,published,container-title,URL,type,link,is-referenced-by-count,subject",
        }
        if self.mailto:
            params["mailto"] = self.mailto
        url = encode_params(CROSSREF_WORKS, params)
        headers = {"Accept": "application/json", "User-Agent": f"EnnoSmart-EnnoScholar/3.2 ({self.mailto or 'no-mailto'})"}
        try:
            data = get_json(url, headers=headers, timeout=self.timeout, retries=self.max_retries)
            items = ((data.get("message") or {}).get("items") or []) if isinstance(data, dict) else []
            out = [self.normalize(item, query) for item in items if isinstance(item, dict)]
            out = [x for x in out if x.get("title")]
            write_cache(path, out)
            return out
        except Exception as exc:
            stale = read_cache(path, 3650)
            if stale is not None:
                return stale
            return [normalized_error("crossref", query, exc)]

    @staticmethod
    def normalize(item: Dict[str, Any], query: str) -> Dict[str, Any]:
        title = item.get("title") or []
        title = title[0] if isinstance(title, list) and title else title
        container = item.get("container-title") or []
        container = container[0] if isinstance(container, list) and container else container
        authors = []
        for a in item.get("author") or []:
            if not isinstance(a, dict):
                continue
            name = safe(" ".join([str(a.get("given") or ""), str(a.get("family") or "")]), 160)
            if name:
                authors.append(name)
        year = None
        parts = ((item.get("published") or {}).get("date-parts") or [])
        try:
            year = int(parts[0][0]) if parts and parts[0] else None
        except Exception:
            year = None
        pdf_url = ""
        for link in item.get("link") or []:
            if not isinstance(link, dict):
                continue
            content_type = safe(link.get("content-type"), 80).lower()
            candidate = safe(link.get("URL"), 1000)
            if candidate.startswith("http") and "pdf" in content_type:
                pdf_url = candidate
                break
        return {
            "source": "crossref",
            "source_type": "scientific_metadata",
            "query": query,
            "paper_id": safe(item.get("DOI"), 260),
            "title": safe(title, 500),
            "abstract": strip_html(item.get("abstract"), 12000),
            "year": year,
            "venue": safe(container, 300),
            "url": pdf_url or safe(item.get("URL"), 1000),
            "doi": safe(item.get("DOI"), 260),
            "authors": authors,
            "citation_count": int(item.get("is-referenced-by-count") or 0),
            "publication_types": [safe(item.get("type"), 100)] if item.get("type") else [],
            "fields_of_study": item.get("subject") or [],
            "pdf_url": pdf_url,
            "primary_pdf_url": pdf_url,
            "is_open_access": bool(pdf_url),
            "open_access": bool(pdf_url),
            "free_fulltext_available": bool(pdf_url),
            "fulltext_access_status": "open_access_pdf" if pdf_url else "metadata_only",
        }
