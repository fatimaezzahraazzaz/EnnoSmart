# -*- coding: utf-8 -*-
from __future__ import annotations

"""OpenAlex client - V161 priority/fresh-first.

Key properties:
- always attempts a fresh OpenAlex request before using query cache;
- sends OPENALEX_API_KEY when configured;
- serializes OpenAlex calls across concurrent lock searches;
- applies a conservative minimum interval plus exponential backoff on 429;
- cache is supplement/fallback only.
"""

import hashlib
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from modules.common.runtime_paths import cache_root
from typing import Any, Dict, List, Optional, Tuple

from .external_source_base import merge_fresh_with_cache, fallback_from_cache

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
    return cache_root() / "ennoscholar"


def _cache_key(source: str, query: str, limit: int) -> Path:
    # Preserve V5 cache key version so old query results remain usable only as
    # supplement/fallback after the new fresh request.
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
            out: List[Dict[str, Any]] = []
            for raw in data["items"]:
                if isinstance(raw, dict):
                    item = dict(raw)
                    item.setdefault("cache_hit", True)
                    item.setdefault("cache_source", str(path))
                    out.append(item)
            return out
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


def _open_json(url: str, headers: Dict[str, str], timeout: int = 12) -> Tuple[Dict[str, Any], Dict[str, str]]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        response_headers = {str(k): str(v) for k, v in resp.headers.items()}
    return json.loads(raw), response_headers


def _is_retryable_error(exc: Exception) -> Tuple[bool, str, int]:
    if isinstance(exc, urllib.error.HTTPError):
        code = int(getattr(exc, "code", 0) or 0)
        message = f"HTTP Error {code}: {getattr(exc, 'reason', '')}"
        return code in {408, 409, 425, 429, 500, 502, 503, 504}, message, code
    if isinstance(exc, (TimeoutError, socket.timeout, urllib.error.URLError)):
        return True, str(exc), 0
    return False, str(exc), 0


def _retry_after_seconds(exc: Exception) -> float | None:
    try:
        headers = getattr(exc, "headers", None)
        if headers is None:
            return None
        raw = headers.get("Retry-After")
        if raw is None:
            return None
        value = float(raw)
        # Never freeze an interactive run for a daily reset window.
        return value if 0 <= value <= 30 else None
    except Exception:
        return None


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
        timeout: int = 30,
        sleep_seconds: float | None = None,
        max_retries: int | None = None,
        cache_ttl_days: int | None = None,
    ):
        self.mailto = mailto or os.getenv("OPENALEX_MAILTO", "")
        self.api_key = str(os.getenv("OPENALEX_API_KEY", "") or "").strip()
        self.timeout = int(timeout)
        self.sleep_seconds = float(
            sleep_seconds
            if sleep_seconds is not None
            else os.getenv("ENNOSCHOLAR_OPENALEX_SLEEP", "0.35")
        )
        self.min_interval_seconds = max(
            0.0,
            float(os.getenv("ENNOSCHOLAR_OPENALEX_MIN_INTERVAL_SECONDS", str(max(self.sleep_seconds, 0.35)))),
        )
        self.max_retries = max(
            0,
            int(
                max_retries
                if max_retries is not None
                else os.getenv("ENNOSCHOLAR_OPENALEX_MAX_RETRIES", "3")
            ),
        )
        self.retry_max_delay = max(
            2.0,
            float(os.getenv("ENNOSCHOLAR_OPENALEX_RETRY_MAX_DELAY", "12")),
        )
        self.cache_ttl_days = int(
            cache_ttl_days
            if cache_ttl_days is not None
            else os.getenv("ENNOSCHOLAR_CACHE_TTL_DAYS", "30")
        )
        # One shared client instance is used by all lock workers. This lock makes
        # OpenAlex priority predictable and avoids concurrent bursts from the same run.
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0

    def headers(self) -> Dict[str, str]:
        return {
            "User-Agent": "EnnoSmart-EnnoScholar/5.0",
            "Accept": "application/json",
        }

    def _wait_for_slot(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def search_works(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = _safe(query)
        if not query:
            return []

        limit = max(1, min(int(limit or 20), 100))
        require_oa = _env_bool("ENNOSCHOLAR_REQUIRE_FREE_FULLTEXT", False)
        cache_query = query + ("|openalex_oa_only" if require_oa else "")
        cache_path = _cache_key("openalex", cache_query, limit)
        cached = _read_cache(cache_path, self.cache_ttl_days)

        params: Dict[str, Any] = {
            "search": query,
            "per-page": limit,
            "sort": "relevance_score:desc",
        }
        if require_oa:
            params["filter"] = "open_access.is_oa:true"
        # OpenAlex API authentication is key-based. The key is read from the
        # existing project environment and never logged or persisted in cache.
        if self.api_key:
            params["api_key"] = self.api_key

        url = OPENALEX_WORKS_URL + "?" + urllib.parse.urlencode(params)
        last_error = ""
        last_code = 0
        attempts = 0
        last_rate_headers: Dict[str, str] = {}

        # Serialize *only* OpenAlex calls. Other engines remain parallel.
        with self._request_lock:
            for attempt in range(self.max_retries + 1):
                attempts = attempt + 1
                self._wait_for_slot()
                try:
                    data, response_headers = _open_json(
                        url,
                        headers=self.headers(),
                        timeout=self.timeout,
                    )
                    self._last_request_at = time.monotonic()
                    last_rate_headers = {
                        key: value
                        for key, value in response_headers.items()
                        if key.lower().startswith("x-ratelimit-")
                    }
                    fresh: List[Dict[str, Any]] = []
                    for work in data.get("results") or []:
                        if isinstance(work, dict):
                            item = self.normalize(work, query)
                            item["openalex_authenticated"] = bool(self.api_key)
                            if last_rate_headers:
                                item["openalex_rate_limit"] = dict(last_rate_headers)
                            fresh.append(item)

                    combined = merge_fresh_with_cache(fresh, cached, limit, "openalex")
                    _write_cache(cache_path, combined)
                    return combined
                except Exception as exc:
                    self._last_request_at = time.monotonic()
                    retryable, message, code = _is_retryable_error(exc)
                    last_error = message
                    last_code = code
                    if not retryable or attempt >= self.max_retries:
                        break

                    retry_after = _retry_after_seconds(exc)
                    if retry_after is not None:
                        delay = retry_after
                    else:
                        # OpenAlex guidance recommends exponential backoff for 429.
                        delay = min((2.0 ** attempt) + self.min_interval_seconds, self.retry_max_delay)
                    time.sleep(max(self.min_interval_seconds, delay))

        stale = _read_cache(cache_path, max_age_days=3650)
        fallback = fallback_from_cache(cached or stale, "openalex", last_error)
        if fallback:
            for item in fallback:
                item["openalex_authenticated"] = bool(self.api_key)
                item["openalex_attempts"] = attempts
            return fallback

        return [{
            "source": "openalex",
            "query": query,
            "error": last_error or "OpenAlex request failed",
            "http_status": last_code,
            "normalized_error": True,
            "retryable": True,
            "attempts": attempts,
            "api_limited": last_code == 429 or "429" in last_error,
            "cache_path": str(cache_path),
            "openalex_authenticated": bool(self.api_key),
            "retrieval_origin": "fresh_api_error",
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
