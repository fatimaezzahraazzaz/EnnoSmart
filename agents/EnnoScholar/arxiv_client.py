# -*- coding: utf-8 -*-
from __future__ import annotations

"""
arxiv_client.py — EnnoScholar V2

Client ArXiv API sans dépendance externe.
Retourne un format papier normalisé.
"""

import re
import socket
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

ARXIV_API_URL = "https://export.arxiv.org/api/query"


def _safe(x: Any) -> str:
    return str(x or "").strip()


def _strip_xml_text(x: str) -> str:
    return re.sub(r"\s+", " ", x or "").strip()


class ArxivClient:
    def __init__(self, timeout: int = 8, sleep_seconds: float = 0.5):
        self.timeout = timeout
        self.sleep_seconds = sleep_seconds

    def search_papers(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        query = _safe(query)
        if not query:
            return []

        params = {
            "search_query": "all:" + query,
            "start": 0,
            "max_results": max(1, min(int(limit or 5), 20)),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        url = ARXIV_API_URL + "?" + urllib.parse.urlencode(params)

        try:
            socket.setdefaulttimeout(self.timeout)
            req = urllib.request.Request(url, headers={"User-Agent": "EnnoSmart-EnnoScholar/2.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            time.sleep(self.sleep_seconds)
        except Exception as e:
            return [{"source": "arxiv", "query": query, "error": str(e), "normalized_error": True}]

        try:
            root = ET.fromstring(raw)
        except Exception as e:
            return [{"source": "arxiv", "query": query, "error": f"XML parse error: {e}", "normalized_error": True}]

        ns = {"a": "http://www.w3.org/2005/Atom"}
        out = []
        for entry in root.findall("a:entry", ns):
            title = _strip_xml_text(entry.findtext("a:title", default="", namespaces=ns))
            abstract = _strip_xml_text(entry.findtext("a:summary", default="", namespaces=ns))
            published = _safe(entry.findtext("a:published", default="", namespaces=ns))
            year = None
            if published[:4].isdigit():
                year = int(published[:4])

            authors = []
            for au in entry.findall("a:author", ns):
                name = au.findtext("a:name", default="", namespaces=ns)
                if name:
                    authors.append(_safe(name))

            url_link = ""
            for link in entry.findall("a:link", ns):
                if link.attrib.get("href"):
                    url_link = link.attrib["href"]
                    break

            paper_id = _safe(entry.findtext("a:id", default="", namespaces=ns))

            out.append({
                "source": "arxiv",
                "query": query,
                "paper_id": paper_id,
                "title": title,
                "abstract": abstract,
                "year": year,
                "venue": "arXiv",
                "url": url_link or paper_id,
                "doi": "",
                "authors": authors,
                "citation_count": 0,
                "influential_citation_count": 0,
                "publication_types": ["preprint"],
                "fields_of_study": [],
                "tldr": "",
            })

        return out
