# -*- coding: utf-8 -*-
from __future__ import annotations

"""
arxiv_client.py — EnnoScholar V136 production

Client ArXiv robuste : retry, cache local, limite jusqu'à 100 résultats.
ArXiv reste activé seulement pour les profils où il est pertinent via scholar_agent.py.
"""

import hashlib
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from modules.common.runtime_paths import cache_root
from typing import Any, Dict, List, Optional, Tuple

from .external_source_base import merge_fresh_with_cache, fallback_from_cache

ARXIV_API_URL = "https://export.arxiv.org/api/query"


def _safe(x: Any) -> str:
    return str(x or "").strip()


def _strip_xml_text(x: str) -> str:
    return re.sub(r"\s+", " ", x or "").strip()


def _cache_root() -> Path:
    root = os.getenv("ENNOSCHOLAR_CACHE_DIR")
    if root:
        return Path(root)
    return cache_root() / "ennoscholar"


def _cache_key(source: str, query: str, limit: int) -> Path:
    raw = f"{source}|{query}|{limit}|v132".encode("utf-8", errors="replace")
    h = hashlib.sha256(raw).hexdigest()[:32]
    return _cache_root() / source / f"{h}.json"


def _read_cache(path: Path, max_age_days: int) -> Optional[List[Dict[str, Any]]]:
    try:
        if not path.exists():
            return None
        age_seconds = time.time() - path.stat().st_mtime
        if max_age_days > 0 and age_seconds > max_age_days * 86400:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            items = data["items"]
            for it in items:
                if isinstance(it, dict):
                    it.setdefault("cache_hit", True)
                    it.setdefault("cache_source", str(path))
            return items
    except Exception:
        return None
    return None


def _write_cache(path: Path, items: List[Dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _is_retryable_error(exc: Exception) -> Tuple[bool, str, int]:
    if isinstance(exc, urllib.error.HTTPError):
        code = int(getattr(exc, "code", 0) or 0)
        if code in {408, 409, 425, 429, 500, 502, 503, 504}:
            return True, f"HTTP Error {code}: {getattr(exc, 'reason', '')}", code
        return False, f"HTTP Error {code}: {getattr(exc, 'reason', '')}", code
    if isinstance(exc, (TimeoutError, socket.timeout, urllib.error.URLError)):
        return True, str(exc), 0
    return False, str(exc), 0


class ArxivClient:
    def __init__(self, timeout: int = 8, sleep_seconds: float | None = None, max_retries: int | None = None, cache_ttl_days: int | None = None):
        self.timeout = timeout
        self.sleep_seconds = float(sleep_seconds if sleep_seconds is not None else os.getenv("ENNOSCHOLAR_ARXIV_SLEEP", "0.05"))
        self.max_retries = int(max_retries if max_retries is not None else os.getenv("ENNOSCHOLAR_ARXIV_MAX_RETRIES", os.getenv("ENNOSCHOLAR_MAX_RETRIES", "1")))
        self.cache_ttl_days = int(cache_ttl_days if cache_ttl_days is not None else os.getenv("ENNOSCHOLAR_CACHE_TTL_DAYS", "30"))

    def search_papers(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = _safe(query)
        if not query:
            return []

        limit = max(1, min(int(limit or 20), 100))
        cache_path = _cache_key("arxiv", query, limit)
        cached = _read_cache(cache_path, self.cache_ttl_days)
        # Fresh-first: cache is kept only as a supplement/fallback.

        params = {
            "search_query": "all:" + query,
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        url = ARXIV_API_URL + "?" + urllib.parse.urlencode(params)

        raw = ""
        last_error = ""
        last_code = 0
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "EnnoSmart-EnnoScholar/3.2"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                time.sleep(self.sleep_seconds)
                break
            except Exception as exc:
                retryable, message, code = _is_retryable_error(exc)
                last_error = message
                last_code = code
                if not retryable or attempt >= self.max_retries:
                    stale = _read_cache(cache_path, max_age_days=3650)
                    fallback = fallback_from_cache(cached or stale, "arxiv", last_error)
                    if fallback:
                        return fallback
                    return [{"source": "arxiv", "query": query, "error": last_error, "http_status": last_code, "normalized_error": True, "api_limited": last_code == 429 or "429" in last_error, "attempts": self.max_retries + 1, "retrieval_origin": "fresh_api_error"}]
                time.sleep(min((2.0 if code == 429 else 1.0) * (attempt + 1) + self.sleep_seconds, float(os.getenv("ENNOSCHOLAR_ARXIV_RETRY_MAX_DELAY", os.getenv("ENNOSCHOLAR_BACKOFF_MAX_SECONDS", "8.0")))))

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
            year = int(published[:4]) if published[:4].isdigit() else None

            authors = []
            for au in entry.findall("a:author", ns):
                name = au.findtext("a:name", default="", namespaces=ns)
                if name:
                    authors.append(_safe(name))

            url_link = ""
            pdf_url = ""
            for link in entry.findall("a:link", ns):
                href = link.attrib.get("href") or ""
                title_attr = (link.attrib.get("title") or "").lower()
                link_type = (link.attrib.get("type") or "").lower()
                if href and not url_link:
                    url_link = href
                if href and ("pdf" in title_attr or "pdf" in link_type or href.endswith(".pdf")):
                    pdf_url = href
            paper_id = _safe(entry.findtext("a:id", default="", namespaces=ns))
            if not pdf_url and paper_id:
                pdf_url = paper_id.replace("/abs/", "/pdf/")

            out.append({
                "source": "arxiv",
                "query": query,
                "paper_id": paper_id,
                "title": title,
                "abstract": abstract,
                "year": year,
                "venue": "arXiv",
                "url": pdf_url or url_link or paper_id,
                "doi": "",
                "authors": authors,
                "citation_count": 0,
                "influential_citation_count": 0,
                "publication_types": ["preprint"],
                "fields_of_study": [],
                "tldr": "",

                # V142 — arXiv est librement accessible par définition.
                "is_open_access": True,
                "open_access": True,
                "pdf_url": pdf_url,
                "primary_pdf_url": pdf_url,
                "free_fulltext_available": bool(pdf_url),
                "fulltext_access_status": "open_access_pdf",
            })
        combined = merge_fresh_with_cache(out, cached, limit, "arxiv")
        _write_cache(cache_path, combined)
        return combined
