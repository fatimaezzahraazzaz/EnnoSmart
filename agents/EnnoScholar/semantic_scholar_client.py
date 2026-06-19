# -*- coding: utf-8 -*-
from __future__ import annotations

"""
semantic_scholar_client.py — EnnoScholar V2

Client Semantic Scholar robuste avec timeout court.
"""

import json
import os
import socket
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

FIELDS = ",".join([
    "paperId", "title", "abstract", "year", "venue", "url", "authors",
    "citationCount", "influentialCitationCount", "externalIds",
    "publicationTypes", "fieldsOfStudy", "tldr",
])


def _safe(x: Any) -> str:
    return str(x or "").strip()


def _open_json(url: str, headers: Dict[str, str], timeout: int = 8) -> Dict[str, Any]:
    socket.setdefaulttimeout(timeout)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


class SemanticScholarClient:
    def __init__(self, api_key: Optional[str] = None, timeout: int = 8, sleep_seconds: float = 0.2):
        self.api_key = api_key or os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
        self.timeout = timeout
        self.sleep_seconds = sleep_seconds

    def headers(self) -> Dict[str, str]:
        h = {"User-Agent": "EnnoSmart-EnnoScholar/2.0", "Accept": "application/json"}
        if self.api_key:
            h["x-api-key"] = self.api_key
        return h

    def search_papers(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        query = _safe(query)
        if not query:
            return []

        params = {"query": query, "limit": max(1, min(int(limit or 5), 20)), "fields": FIELDS}
        url = SEMANTIC_SCHOLAR_SEARCH_URL + "?" + urllib.parse.urlencode(params)

        try:
            data = _open_json(url, headers=self.headers(), timeout=self.timeout)
            time.sleep(self.sleep_seconds)
        except Exception as e:
            return [{"source": "semantic_scholar", "query": query, "error": str(e), "normalized_error": True}]

        out = []
        for p in data.get("data") or []:
            if isinstance(p, dict):
                out.append(self.normalize(p, query))
        return out

    def normalize(self, p: Dict[str, Any], query: str) -> Dict[str, Any]:
        external = p.get("externalIds") or {}
        authors = p.get("authors") or []
        tldr = p.get("tldr") or {}

        return {
            "source": "semantic_scholar",
            "query": query,
            "paper_id": _safe(p.get("paperId")),
            "title": _safe(p.get("title")),
            "abstract": _safe(p.get("abstract")),
            "year": p.get("year"),
            "venue": _safe(p.get("venue")),
            "url": _safe(p.get("url")),
            "doi": _safe(external.get("DOI")),
            "authors": [_safe(a.get("name")) for a in authors if isinstance(a, dict) and a.get("name")],
            "citation_count": int(p.get("citationCount") or 0),
            "influential_citation_count": int(p.get("influentialCitationCount") or 0),
            "publication_types": p.get("publicationTypes") or [],
            "fields_of_study": p.get("fieldsOfStudy") or [],
            "tldr": _safe(tldr.get("text")) if isinstance(tldr, dict) else "",
        }
