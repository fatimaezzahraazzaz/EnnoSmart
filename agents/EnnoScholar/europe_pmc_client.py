# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List
from .external_source_base import cache_path, encode_params, get_json, normalized_error, read_cache, safe, write_cache, merge_fresh_with_cache, fallback_from_cache

EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


class EuropePmcClient:
    def __init__(self, timeout: int = 10, max_retries: int = 1, cache_ttl_days: int = 30):
        self.timeout = timeout; self.max_retries = max_retries; self.cache_ttl_days = cache_ttl_days

    def search_papers(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = safe(query, 300)
        if not query: return []
        limit = max(1, min(int(limit or 20), 100))
        path = cache_path("europe_pmc", query, limit)
        cached = read_cache(path, self.cache_ttl_days)
        url = encode_params(EUROPE_PMC_SEARCH, {"query": query, "format": "json", "pageSize": limit, "resultType": "core"})
        try:
            data = get_json(url, headers={"Accept": "application/json", "User-Agent": "EnnoSmart-EnnoScholar/3.2"}, timeout=self.timeout, retries=self.max_retries)
            rows = ((data.get("resultList") or {}).get("result") or []) if isinstance(data, dict) else []
            out = [self.normalize(x, query) for x in rows if isinstance(x, dict)]
            out = [x for x in out if x.get("title")]
            combined = merge_fresh_with_cache(out, cached, limit, "europe_pmc")
            write_cache(path, combined)
            return combined
        except Exception as exc:
            stale = read_cache(path, 3650)
            fallback = fallback_from_cache(cached or stale, "europe_pmc", exc)
            return fallback if fallback else [normalized_error("europe_pmc", query, exc)]

    @staticmethod
    def normalize(item: Dict[str, Any], query: str) -> Dict[str, Any]:
        pmcid = safe(item.get("pmcid"), 100)
        doi = safe(item.get("doi"), 260)
        full = f"https://europepmc.org/articles/{pmcid}?pdf=render" if pmcid else ""
        landing = f"https://europepmc.org/article/{safe(item.get('source'),50)}/{safe(item.get('id'),100)}"
        authors = [safe(x, 180) for x in str(item.get("authorString") or "").split(",") if safe(x, 180)]
        return {
            "source": "europe_pmc",
            "source_type": "scientific_article",
            "query": query,
            "paper_id": pmcid or safe(item.get("id") or doi, 260),
            "title": safe(item.get("title"), 500),
            "abstract": safe(item.get("abstractText"), 12000),
            "year": int(item.get("pubYear")) if str(item.get("pubYear") or "").isdigit() else None,
            "venue": safe(item.get("journalTitle"), 300),
            "url": full or landing,
            "doi": doi,
            "authors": authors,
            "citation_count": int(item.get("citedByCount") or 0),
            "publication_types": [safe(item.get("pubType"),100)] if item.get("pubType") else [],
            "fields_of_study": ["Biomedical", "Life sciences"],
            "pdf_url": full,
            "primary_pdf_url": full,
            "is_open_access": bool(item.get("isOpenAccess") == "Y" or pmcid),
            "open_access": bool(item.get("isOpenAccess") == "Y" or pmcid),
            "free_fulltext_available": bool(full),
            "fulltext_access_status": "open_access_pdf" if full else "open_access_landing" if item.get("isOpenAccess") == "Y" else "metadata_only",
        }
