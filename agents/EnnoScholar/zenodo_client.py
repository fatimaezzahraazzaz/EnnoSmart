# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List
from .external_source_base import cache_path, encode_params, get_json, normalized_error, read_cache, safe, strip_html, write_cache

ZENODO_RECORDS = "https://zenodo.org/api/records"


class ZenodoClient:
    def __init__(self, timeout: int = 12, max_retries: int = 1, cache_ttl_days: int = 30):
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_ttl_days = cache_ttl_days

    def search_records(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = safe(query, 300)
        if not query: return []
        limit = max(1, min(int(limit or 20), 100))
        path = cache_path("zenodo", query, limit)
        cached = read_cache(path, self.cache_ttl_days)
        if cached is not None: return cached
        url = encode_params(ZENODO_RECORDS, {"q": query, "size": limit, "sort": "bestmatch"})
        try:
            data = get_json(url, headers={"Accept": "application/json", "User-Agent": "EnnoSmart-EnnoScholar/3.2"}, timeout=self.timeout, retries=self.max_retries)
            hits = ((data.get("hits") or {}).get("hits") or []) if isinstance(data, dict) else []
            out = [self.normalize(x, query) for x in hits if isinstance(x, dict)]
            out = [x for x in out if x.get("title")]
            write_cache(path, out)
            return out
        except Exception as exc:
            stale = read_cache(path, 3650)
            return stale if stale is not None else [normalized_error("zenodo", query, exc)]

    @staticmethod
    def normalize(item: Dict[str, Any], query: str) -> Dict[str, Any]:
        md = item.get("metadata") or {}
        creators = md.get("creators") or []
        authors = [safe(x.get("name"), 180) for x in creators if isinstance(x, dict) and x.get("name")]
        files = item.get("files") or []
        pdf = ""
        for f in files:
            if not isinstance(f, dict): continue
            key = safe(f.get("key"), 300).lower()
            candidate = safe((f.get("links") or {}).get("self") or f.get("download"), 1000)
            if candidate.startswith("http") and (key.endswith(".pdf") or "pdf" in safe(f.get("type"), 80).lower()):
                pdf = candidate; break
        date = safe(md.get("publication_date") or item.get("created"), 40)
        year = int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None
        resource_type = md.get("resource_type") or {}
        rtype = safe(resource_type.get("type") if isinstance(resource_type, dict) else resource_type, 100)
        doi = safe(md.get("doi") or item.get("doi"), 260)
        landing = safe((item.get("links") or {}).get("html") or (item.get("links") or {}).get("self_html"), 1000)
        return {
            "source": "zenodo",
            "source_type": "research_output",
            "query": query,
            "paper_id": safe(item.get("id") or doi, 260),
            "title": safe(md.get("title"), 500),
            "abstract": strip_html(md.get("description"), 12000),
            "year": year,
            "venue": "Zenodo",
            "url": pdf or landing,
            "doi": doi,
            "authors": authors,
            "citation_count": 0,
            "publication_types": [rtype] if rtype else [],
            "fields_of_study": md.get("keywords") or [],
            "pdf_url": pdf,
            "primary_pdf_url": pdf,
            "is_open_access": True,
            "open_access": True,
            "free_fulltext_available": bool(pdf),
            "fulltext_access_status": "open_access_pdf" if pdf else "open_access_landing",
        }
