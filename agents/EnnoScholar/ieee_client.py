# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Any, Dict, List
from .external_source_base import cache_path, encode_params, get_json, normalized_error, read_cache, safe, write_cache, merge_fresh_with_cache, fallback_from_cache

IEEE_SEARCH = "https://ieeexploreapi.ieee.org/api/v1/search/articles"


class IeeeClient:
    def __init__(self, api_key: str | None = None, timeout: int = 12, max_retries: int = 1, cache_ttl_days: int = 30):
        self.api_key = api_key or os.getenv("IEEE_XPLORE_API_KEY", "")
        self.timeout = timeout; self.max_retries = max_retries; self.cache_ttl_days = cache_ttl_days

    @property
    def available(self) -> bool: return bool(self.api_key)

    def search_papers(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = safe(query, 300)
        if not query: return []
        if not self.api_key: return [normalized_error("ieee", query, "IEEE_XPLORE_API_KEY absente", skipped=True)]
        limit = max(1, min(int(limit or 20), 200))
        path = cache_path("ieee", query, limit)
        cached = read_cache(path, self.cache_ttl_days)
        url = encode_params(IEEE_SEARCH, {"querytext": query, "max_records": limit, "start_record": 1, "sort_field": "article_title", "sort_order": "asc", "apikey": self.api_key, "format": "json"})
        try:
            data = get_json(url, headers={"Accept": "application/json", "User-Agent": "EnnoSmart-EnnoScholar/3.2"}, timeout=self.timeout, retries=self.max_retries)
            rows = data.get("articles") or []
            out = [self.normalize(x, query) for x in rows if isinstance(x, dict)]
            out = [x for x in out if x.get("title")]
            combined = merge_fresh_with_cache(out, cached, limit, "ieee")
            write_cache(path, combined)
            return combined
        except Exception as exc:
            stale = read_cache(path, 3650)
            fallback = fallback_from_cache(cached or stale, "ieee", exc)
            return fallback if fallback else [normalized_error("ieee", query, exc)]

    @staticmethod
    def normalize(item: Dict[str, Any], query: str) -> Dict[str, Any]:
        authors = []
        for a in ((item.get("authors") or {}).get("authors") or []):
            if isinstance(a, dict) and a.get("full_name"): authors.append(safe(a.get("full_name"),180))
        pdf = safe(item.get("pdf_url"),1000) if safe(item.get("access_type"),80).lower() == "open access" else ""
        return {
            "source":"ieee", "source_type":"scientific_article", "query":query,
            "paper_id":safe(item.get("article_number") or item.get("doi"),260),
            "title":safe(item.get("title"),500), "abstract":safe(item.get("abstract"),12000),
            "year":int(item.get("publication_year")) if str(item.get("publication_year") or "").isdigit() else None,
            "venue":safe(item.get("publication_title"),300), "url":pdf or safe(item.get("html_url"),1000),
            "doi":safe(item.get("doi"),260), "authors":authors,
            "citation_count":int(item.get("citing_paper_count") or 0),
            "publication_types":[safe(item.get("content_type"),100)] if item.get("content_type") else [],
            "fields_of_study": list(dict.fromkeys((item.get("author_terms") or {}).get("terms") or [])) if isinstance(item.get("author_terms"),dict) else [],
            "pdf_url":pdf, "primary_pdf_url":pdf,
            "is_open_access":bool(pdf), "open_access":bool(pdf), "free_fulltext_available":bool(pdf),
            "fulltext_access_status":"open_access_pdf" if pdf else "metadata_only",
        }
