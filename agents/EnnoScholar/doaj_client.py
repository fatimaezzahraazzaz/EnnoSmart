# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Client DOAJ v4.

DOAJ fournit une recherche publique, sans clé, sur des articles en accès ouvert.
Le client reste volontairement simple : cache local, un retry court et
normalisation vers le contrat commun EnnoScholar.
"""

import urllib.parse
from typing import Any, Dict, List

from .external_source_base import (
    cache_path,
    get_json,
    normalized_error,
    read_cache,
    safe,
    strip_html,
    write_cache,
)

DOAJ_ARTICLE_SEARCH = "https://doaj.org/api/search/articles"


class DoajClient:
    def __init__(
        self,
        timeout: int = 8,
        max_retries: int = 1,
        cache_ttl_days: int = 30,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_ttl_days = cache_ttl_days

    def search_articles(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = safe(query, 300)
        if not query:
            return []

        limit = max(1, min(int(limit or 20), 100))
        path = cache_path("doaj", query, limit)
        cached = read_cache(path, self.cache_ttl_days)
        if cached is not None:
            return cached

        encoded_query = urllib.parse.quote(query, safe="")
        url = f"{DOAJ_ARTICLE_SEARCH}/{encoded_query}?pageSize={limit}"
        try:
            data = get_json(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "EnnoSmart-EnnoScholar/3.2",
                },
                timeout=self.timeout,
                retries=self.max_retries,
                sleep_seconds=0.05,
            )
            results = data.get("results") or [] if isinstance(data, dict) else []
            out = [self.normalize(item, query) for item in results if isinstance(item, dict)]
            out = [item for item in out if item.get("title")]
            write_cache(path, out)
            return out
        except Exception as exc:
            stale = read_cache(path, 3650)
            return stale if stale is not None else [normalized_error("doaj", query, exc)]

    @staticmethod
    def normalize(item: Dict[str, Any], query: str) -> Dict[str, Any]:
        bib = item.get("bibjson") if isinstance(item.get("bibjson"), dict) else {}

        doi = ""
        for identifier in bib.get("identifier") or []:
            if not isinstance(identifier, dict):
                continue
            if safe(identifier.get("type"), 40).lower() == "doi":
                doi = safe(identifier.get("id"), 260)
                break

        authors = [
            safe(author.get("name"), 180)
            for author in (bib.get("author") or [])
            if isinstance(author, dict) and safe(author.get("name"), 180)
        ]

        fulltext_url = ""
        pdf_url = ""
        for link in bib.get("link") or []:
            if not isinstance(link, dict):
                continue
            candidate = safe(link.get("url"), 1000)
            if not candidate.startswith("http"):
                continue
            link_type = safe(link.get("type"), 80).lower()
            content_type = safe(link.get("content_type"), 120).lower()
            if not fulltext_url and (link_type == "fulltext" or content_type):
                fulltext_url = candidate
            if not pdf_url and ("pdf" in content_type or candidate.lower().endswith(".pdf")):
                pdf_url = candidate

        journal = bib.get("journal") if isinstance(bib.get("journal"), dict) else {}
        year_raw = safe(bib.get("year"), 12)
        year = int(year_raw[:4]) if len(year_raw) >= 4 and year_raw[:4].isdigit() else None

        subjects = []
        for subject in bib.get("subject") or []:
            if isinstance(subject, dict):
                term = safe(subject.get("term"), 180)
                if term:
                    subjects.append(term)
        subjects.extend(
            safe(keyword, 180)
            for keyword in (bib.get("keywords") or [])
            if safe(keyword, 180)
        )

        return {
            "source": "doaj",
            "source_type": "open_access_journal_article",
            "query": query,
            "paper_id": safe(item.get("id") or doi, 260),
            "title": safe(bib.get("title"), 500),
            "abstract": strip_html(bib.get("abstract"), 12000),
            "year": year,
            "venue": safe(journal.get("title") or "DOAJ", 300),
            "url": pdf_url or fulltext_url or (f"https://doi.org/{doi}" if doi else ""),
            "doi": doi,
            "authors": authors,
            "citation_count": 0,
            "publication_types": ["journal-article"],
            "fields_of_study": subjects,
            "pdf_url": pdf_url,
            "primary_pdf_url": pdf_url,
            "is_open_access": True,
            "open_access": True,
            "free_fulltext_available": bool(pdf_url),
            "fulltext_access_status": "open_access_pdf" if pdf_url else "open_access_landing",
            "oa_landing_url": fulltext_url,
        }
