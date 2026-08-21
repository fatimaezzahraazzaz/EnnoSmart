# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List
from .external_source_base import cache_path, encode_params, get_json, normalized_error, read_cache, safe, write_cache, merge_fresh_with_cache, fallback_from_cache

HAL_SEARCH = "https://api.archives-ouvertes.fr/search/"


class HalClient:
    def __init__(self, timeout: int = 10, max_retries: int = 1, cache_ttl_days: int = 30):
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_ttl_days = cache_ttl_days

    def search_works(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = safe(query, 300)
        if not query:
            return []
        limit = max(1, min(int(limit or 20), 100))
        path = cache_path("hal", query, limit)
        cached = read_cache(path, self.cache_ttl_days)
        params = {
            "q": query,
            "wt": "json",
            "rows": limit,
            "fl": "halId_s,title_s,abstract_s,producedDateY_i,authFullName_s,uri_s,fileMain_s,doiId_s,docType_s,journalTitle_s,conferenceTitle_s,keyword_s,language_s",
        }
        try:
            data = get_json(encode_params(HAL_SEARCH, params), headers={"User-Agent": "EnnoSmart-EnnoScholar/3.2"}, timeout=self.timeout, retries=self.max_retries)
            docs = ((data.get("response") or {}).get("docs") or []) if isinstance(data, dict) else []
            out = [self.normalize(x, query) for x in docs if isinstance(x, dict)]
            out = [x for x in out if x.get("title")]
            combined = merge_fresh_with_cache(out, cached, limit, "hal")
            write_cache(path, combined)
            return combined
        except Exception as exc:
            stale = read_cache(path, 3650)
            fallback = fallback_from_cache(cached or stale, "hal", exc)
            return fallback if fallback else [normalized_error("hal", query, exc)]

    @staticmethod
    def normalize(item: Dict[str, Any], query: str) -> Dict[str, Any]:
        title = item.get("title_s") or ""
        if isinstance(title, list): title = title[0] if title else ""
        abstract = item.get("abstract_s") or ""
        if isinstance(abstract, list): abstract = " ".join(map(str, abstract))
        authors = item.get("authFullName_s") or []
        if not isinstance(authors, list): authors = [str(authors)] if authors else []
        keywords = item.get("keyword_s") or []
        if not isinstance(keywords, list): keywords = [str(keywords)] if keywords else []
        pdf = safe(item.get("fileMain_s"), 1000)
        uri = safe(item.get("uri_s"), 1000)
        venue = safe(item.get("journalTitle_s") or item.get("conferenceTitle_s") or "HAL", 300)
        return {
            "source": "hal",
            "source_type": "scientific_article_or_thesis",
            "query": query,
            "paper_id": safe(item.get("halId_s"), 260),
            "title": safe(title, 500),
            "abstract": safe(abstract, 12000),
            "year": item.get("producedDateY_i"),
            "venue": venue,
            "url": pdf or uri,
            "doi": safe(item.get("doiId_s"), 260),
            "authors": [safe(x, 180) for x in authors if safe(x, 180)],
            "citation_count": 0,
            "publication_types": [safe(item.get("docType_s"), 100)] if item.get("docType_s") else [],
            "fields_of_study": keywords,
            "pdf_url": pdf,
            "primary_pdf_url": pdf,
            "is_open_access": bool(pdf or uri),
            "open_access": bool(pdf or uri),
            "free_fulltext_available": bool(pdf),
            "fulltext_access_status": "open_access_pdf" if pdf else "open_access_landing",
            "language": item.get("language_s"),
        }
