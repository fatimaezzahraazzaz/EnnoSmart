# -*- coding: utf-8 -*-
from __future__ import annotations

"""
semantic_scholar_client.py — EnnoScholar V136 production

Client Semantic Scholar robuste :
- retry avec backoff sur 429 / timeout / erreurs temporaires ;
- cache JSON local par requête ;
- fallback sur cache si l'API limite les requêtes ;
- limite configurable jusqu'à 100 résultats par requête.
"""

import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

FIELDS = ",".join([
    "paperId", "title", "abstract", "year", "venue", "url", "authors",
    "citationCount", "influentialCitationCount", "externalIds",
    "publicationTypes", "fieldsOfStudy", "tldr", "openAccessPdf", "isOpenAccess",
])


def _safe(x: Any) -> str:
    return str(x or "").strip()


def _cache_root() -> Path:
    root = os.getenv("ENNOSCHOLAR_CACHE_DIR")
    if root:
        return Path(root)
    return Path.cwd() / "storage" / "ennoscholar_cache"


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
        payload = {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "items": items,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _open_json(url: str, headers: Dict[str, str], timeout: int = 10) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _is_retryable_error(exc: Exception) -> Tuple[bool, str, int]:
    if isinstance(exc, urllib.error.HTTPError):
        code = int(getattr(exc, "code", 0) or 0)
        if code in {408, 409, 425, 429, 500, 502, 503, 504}:
            return True, f"HTTP Error {code}: {getattr(exc, 'reason', '')}", code
        return False, f"HTTP Error {code}: {getattr(exc, 'reason', '')}", code
    if isinstance(exc, (TimeoutError, socket.timeout, urllib.error.URLError)):
        return True, str(exc), 0
    return False, str(exc), 0


class SemanticScholarClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = 8,
        sleep_seconds: float | None = None,
        max_retries: int | None = None,
        cache_ttl_days: int | None = None,
    ):
        self.api_key = api_key or os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
        self.timeout = timeout
        self.sleep_seconds = float(
            sleep_seconds
            if sleep_seconds is not None
            else os.getenv("ENNOSCHOLAR_SEMANTIC_SLEEP", "0.05")
        )
        self.max_retries = int(
            max_retries
            if max_retries is not None
            else os.getenv("ENNOSCHOLAR_MAX_RETRIES", "1")
        )
        self.cache_ttl_days = int(
            cache_ttl_days
            if cache_ttl_days is not None
            else os.getenv("ENNOSCHOLAR_CACHE_TTL_DAYS", "30")
        )

    def headers(self) -> Dict[str, str]:
        h = {
            "User-Agent": "EnnoSmart-EnnoScholar/3.2",
            "Accept": "application/json",
        }
        if self.api_key:
            h["x-api-key"] = self.api_key
        return h

    def search_papers(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = _safe(query)
        if not query:
            return []

        limit = max(1, min(int(limit or 20), 100))
        cache_path = _cache_key("semantic_scholar", query, limit)

        cached = _read_cache(cache_path, self.cache_ttl_days)
        if cached is not None:
            return cached

        params = {"query": query, "limit": limit, "fields": FIELDS}
        url = SEMANTIC_SCHOLAR_SEARCH_URL + "?" + urllib.parse.urlencode(params)

        last_error = ""
        last_code = 0
        for attempt in range(self.max_retries + 1):
            try:
                data = _open_json(url, headers=self.headers(), timeout=self.timeout)
                out = []
                for p in data.get("data") or []:
                    if isinstance(p, dict):
                        out.append(self.normalize(p, query))
                _write_cache(cache_path, out)
                time.sleep(self.sleep_seconds)
                return out
            except Exception as exc:
                retryable, message, code = _is_retryable_error(exc)
                last_error = message
                last_code = code
                if not retryable or attempt >= self.max_retries:
                    break
                # Backoff prudent. 429 demande souvent quelques secondes.
                base = 2.0 if code == 429 else 1.0
                time.sleep(min(base * (attempt + 1) + self.sleep_seconds, float(os.getenv("ENNOSCHOLAR_BACKOFF_MAX_SECONDS", "2.0"))))

        # Fallback cache expiré si l'API est limitée mais qu'on a une ancienne réponse.
        stale = _read_cache(cache_path, max_age_days=3650)
        if stale is not None:
            for it in stale:
                if isinstance(it, dict):
                    it["cache_stale_used_after_error"] = True
                    it["api_error"] = last_error
            return stale

        return [{
            "source": "semantic_scholar",
            "query": query,
            "error": last_error,
            "http_status": last_code,
            "normalized_error": True,
            "retryable": True,
            "attempts": self.max_retries + 1,
            "api_limited": last_code == 429 or "429" in last_error,
            "cache_path": str(cache_path),
        }]

    def normalize(self, p: Dict[str, Any], query: str) -> Dict[str, Any]:
        external = p.get("externalIds") or {}
        authors = p.get("authors") or []
        tldr = p.get("tldr") or {}
        open_access_pdf = p.get("openAccessPdf") or {}
        pdf_url = ""
        if isinstance(open_access_pdf, dict):
            pdf_url = _safe(open_access_pdf.get("url"))

        is_open_access = bool(p.get("isOpenAccess") or pdf_url)

        return {
            "source": "semantic_scholar",
            "query": query,
            "paper_id": _safe(p.get("paperId")),
            "title": _safe(p.get("title")),
            "abstract": _safe(p.get("abstract")),
            "year": p.get("year"),
            "venue": _safe(p.get("venue")),
            "url": pdf_url or _safe(p.get("url")),
            "doi": _safe(external.get("DOI")),
            "authors": [_safe(a.get("name")) for a in authors if isinstance(a, dict) and a.get("name")],
            "citation_count": int(p.get("citationCount") or 0),
            "influential_citation_count": int(p.get("influentialCitationCount") or 0),
            "publication_types": p.get("publicationTypes") or [],
            "fields_of_study": p.get("fieldsOfStudy") or [],
            "tldr": _safe(tldr.get("text")) if isinstance(tldr, dict) else "",

            # V142 — filtre articles gratuits / exploitables fulltext.
            # Ces champs sont utilisés par scholar_agent.py pour éviter de proposer
            # au consultant des articles payants ou sans PDF exploitable.
            "is_open_access": is_open_access,
            "open_access": is_open_access,
            "pdf_url": pdf_url,
            "primary_pdf_url": pdf_url,
            "free_fulltext_available": bool(pdf_url),
            "fulltext_access_status": "open_access_pdf" if pdf_url else ("open_access_no_pdf" if is_open_access else "unknown_or_paywalled"),
            "open_access_pdf": open_access_pdf if isinstance(open_access_pdf, dict) else {},
        }
