# -*- coding: utf-8 -*-
from __future__ import annotations

"""
openalex_client.py — EnnoScholar V2

Client OpenAlex Works API robuste.
"""

import json
import os
import socket
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


def _safe(x: Any) -> str:
    return str(x or "").strip()


def _open_json(url: str, headers: Dict[str, str], timeout: int = 8) -> Dict[str, Any]:
    socket.setdefaulttimeout(timeout)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _abstract_from_inverted_index(inv: Any) -> str:
    if not isinstance(inv, dict):
        return ""
    positions = []
    for word, indexes in inv.items():
        if not isinstance(indexes, list):
            continue
        for i in indexes:
            try:
                positions.append((int(i), str(word)))
            except Exception:
                pass
    positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in positions)


class OpenAlexClient:
    def __init__(self, mailto: str | None = None, timeout: int = 8, sleep_seconds: float = 0.1):
        self.mailto = mailto or os.getenv("OPENALEX_MAILTO", "")
        self.timeout = timeout
        self.sleep_seconds = sleep_seconds

    def headers(self) -> Dict[str, str]:
        return {"User-Agent": "EnnoSmart-EnnoScholar/2.0", "Accept": "application/json"}

    def search_works(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        query = _safe(query)
        if not query:
            return []

        params = {
            "search": query,
            "per-page": max(1, min(int(limit or 5), 25)),
            "sort": "relevance_score:desc",
        }
        if self.mailto:
            params["mailto"] = self.mailto

        url = OPENALEX_WORKS_URL + "?" + urllib.parse.urlencode(params)

        try:
            data = _open_json(url, headers=self.headers(), timeout=self.timeout)
            time.sleep(self.sleep_seconds)
        except Exception as e:
            return [{"source": "openalex", "query": query, "error": str(e), "normalized_error": True}]

        out = []
        for w in data.get("results") or []:
            if isinstance(w, dict):
                out.append(self.normalize(w, query))
        return out

    def normalize(self, w: Dict[str, Any], query: str) -> Dict[str, Any]:
        authors = []
        for a in w.get("authorships") or []:
            if isinstance(a, dict):
                au = a.get("author") or {}
                if au.get("display_name"):
                    authors.append(_safe(au.get("display_name")))

        loc = w.get("primary_location") or {}
        src = loc.get("source") or {}
        ids = w.get("ids") or {}
        doi = _safe(w.get("doi") or ids.get("doi"))
        if doi.startswith("https://doi.org/"):
            doi = doi.replace("https://doi.org/", "")

        return {
            "source": "openalex",
            "query": query,
            "paper_id": _safe(w.get("id")),
            "title": _safe(w.get("title") or w.get("display_name")),
            "abstract": _abstract_from_inverted_index(w.get("abstract_inverted_index")),
            "year": w.get("publication_year"),
            "venue": _safe(src.get("display_name")),
            "url": _safe(loc.get("landing_page_url") or ids.get("openalex")),
            "doi": doi,
            "authors": authors,
            "citation_count": int(w.get("cited_by_count") or 0),
            "influential_citation_count": 0,
            "publication_types": [w.get("type")] if w.get("type") else [],
            "fields_of_study": [
                _safe(c.get("display_name"))
                for c in (w.get("concepts") or [])[:8]
                if isinstance(c, dict) and c.get("display_name")
            ],
            "tldr": "",
        }
