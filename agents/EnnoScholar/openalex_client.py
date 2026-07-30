# -*- coding: utf-8 -*-
from __future__ import annotations

"""
openalex_client.py — EnnoScholar V136 production

Client OpenAlex Works API robuste :
- retry avec backoff sur 429 / erreurs temporaires ;
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

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on", "oui"}


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


def _open_json(url: str, headers: Dict[str, str], timeout: int = 12) -> Dict[str, Any]:
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
    def __init__(
        self,
        mailto: str | None = None,
        timeout: int = 8,
        sleep_seconds: float | None = None,
        max_retries: int | None = None,
        cache_ttl_days: int | None = None,
    ):
        self.mailto = mailto or os.getenv("OPENALEX_MAILTO", "")
        self.timeout = timeout
        self.sleep_seconds = float(
            sleep_seconds
            if sleep_seconds is not None
            else os.getenv("ENNOSCHOLAR_OPENALEX_SLEEP", "0.05")
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
        return {"User-Agent": "EnnoSmart-EnnoScholar/3.2", "Accept": "application/json"}

    def search_works(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = _safe(query)
        if not query:
            return []

        limit = max(1, min(int(limit or 20), 100))
        require_oa = _env_bool("ENNOSCHOLAR_REQUIRE_FREE_FULLTEXT", True)
        cache_query = query + ("|openalex_oa_only" if require_oa else "")
        cache_path = _cache_key("openalex", cache_query, limit)

        cached = _read_cache(cache_path, self.cache_ttl_days)
        if cached is not None:
            return cached

        params = {
            "search": query,
            "per-page": limit,
            "sort": "relevance_score:desc",
        }
        if require_oa:
            # V142 — recherche OpenAlex limitée aux travaux Open Access.
            # Cela évite de proposer au consultant des articles payants ensuite bloquants.
            params["filter"] = "open_access.is_oa:true"
        if self.mailto:
            params["mailto"] = self.mailto

        url = OPENALEX_WORKS_URL + "?" + urllib.parse.urlencode(params)

        last_error = ""
        last_code = 0
        for attempt in range(self.max_retries + 1):
            try:
                data = _open_json(url, headers=self.headers(), timeout=self.timeout)
                out = []
                for w in data.get("results") or []:
                    if isinstance(w, dict):
                        out.append(self.normalize(w, query))
                _write_cache(cache_path, out)
                time.sleep(self.sleep_seconds)
                return out
            except Exception as exc:
                retryable, message, code = _is_retryable_error(exc)
                last_error = message
                last_code = code
                if not retryable or attempt >= self.max_retries:
                    break
                base = 2.0 if code == 429 else 1.0
                time.sleep(min(base * (attempt + 1) + self.sleep_seconds, float(os.getenv("ENNOSCHOLAR_BACKOFF_MAX_SECONDS", "2.0"))))

        stale = _read_cache(cache_path, max_age_days=3650)
        if stale is not None:
            for it in stale:
                if isinstance(it, dict):
                    it["cache_stale_used_after_error"] = True
                    it["api_error"] = last_error
            return stale

        return [{
            "source": "openalex",
            "query": query,
            "error": last_error,
            "http_status": last_code,
            "normalized_error": True,
            "retryable": True,
            "attempts": self.max_retries + 1,
            "api_limited": last_code == 429 or "429" in last_error,
            "cache_path": str(cache_path),
        }]

    def normalize(self, w: Dict[str, Any], query: str) -> Dict[str, Any]:
        authors = []
        for a in w.get("authorships") or []:
            if isinstance(a, dict):
                au = a.get("author") or {}
                if au.get("display_name"):
                    authors.append(_safe(au.get("display_name")))

        loc = w.get("primary_location") or {}
        best_oa = w.get("best_oa_location") or {}
        src = loc.get("source") or {}
        ids = w.get("ids") or {}
        doi = _safe(w.get("doi") or ids.get("doi"))
        if doi.startswith("https://doi.org/"):
            doi = doi.replace("https://doi.org/", "")

        oa = w.get("open_access") or {}
        is_oa = bool(oa.get("is_oa")) if isinstance(oa, dict) else False
        oa_status = _safe(oa.get("oa_status")) if isinstance(oa, dict) else ""
        pdf_url = ""
        if isinstance(best_oa, dict):
            pdf_url = _safe(best_oa.get("pdf_url"))
        if not pdf_url and isinstance(loc, dict):
            pdf_url = _safe(loc.get("pdf_url"))

        landing_url = ""
        if isinstance(best_oa, dict):
            landing_url = _safe(best_oa.get("landing_page_url"))
        if not landing_url:
            landing_url = _safe(loc.get("landing_page_url") or ids.get("openalex"))

        return {
            "source": "openalex",
            "query": query,
            "paper_id": _safe(w.get("id")),
            "title": _safe(w.get("title") or w.get("display_name")),
            "abstract": _abstract_from_inverted_index(w.get("abstract_inverted_index")),
            "year": w.get("publication_year"),
            "venue": _safe(src.get("display_name")),
            "url": pdf_url or landing_url,
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

            # V142 — statut Open Access / PDF exploitable.
            "is_open_access": is_oa,
            "open_access": is_oa,
            "open_access_status": oa_status,
            "pdf_url": pdf_url,
            "primary_pdf_url": pdf_url,
            "landing_page_url": landing_url,
            "free_fulltext_available": bool(is_oa and (pdf_url or landing_url)),
            "fulltext_access_status": "open_access_pdf" if pdf_url else ("open_access_landing" if is_oa else "unknown_or_paywalled"),
        }
