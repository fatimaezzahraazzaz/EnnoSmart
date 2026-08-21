# -*- coding: utf-8 -*-
from __future__ import annotations
import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List
from urllib.parse import quote

try:
    import httpx
except Exception:
    httpx = None

API_BASE = os.getenv(
    "OPENCITATIONS_API_BASE",
    "https://api.opencitations.net/index/v2",
).rstrip("/")

def _clean_doi(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(
        r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)",
        "",
        text,
        flags=re.I,
    )
    return text.strip().rstrip("./")

def _doi_from_pid(value: Any) -> str:
    for token in re.split(r"\s+", str(value or "").strip()):
        if token.lower().startswith("doi:"):
            return _clean_doi(token[4:])
    return ""

class OpenCitationsClient:
    def __init__(self, timeout: float | None = None, max_retries: int | None = None):
        # Réglages V5 d'origine conservés : pas de nouvelle politique quota.
        self.timeout = float(timeout or os.getenv("OPENCITATIONS_TIMEOUT", "6"))
        self.max_retries = max(
            0,
            int(
                max_retries
                if max_retries is not None
                else os.getenv("OPENCITATIONS_MAX_RETRIES", "1")
            ),
        )
        self.token = str(os.getenv("OPENCITATIONS_ACCESS_TOKEN", "") or "").strip()
        self.cache_ttl = max(
            60,
            int(os.getenv("OPENCITATIONS_REDIS_CACHE_TTL_SECONDS", "604800")),
        )
        self._redis = self._build_redis()

    @property
    def available(self) -> bool:
        return httpx is not None

    def _build_redis(self):
        if str(os.getenv("ENNOSCHOLAR_REDIS_ENABLED", "1")).lower() not in {
            "1", "true", "yes", "on"
        }:
            return None
        try:
            import redis
            client = redis.Redis.from_url(
                os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
                socket_connect_timeout=0.4,
                socket_timeout=0.5,
                decode_responses=True,
            )
            client.ping()
            return client
        except Exception:
            return None

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "EnnoSmart-EnnoScholar/5-research-upgrade-v2",
        }
        if self.token:
            headers["authorization"] = self.token
        return headers

    def _cache_key(self, endpoint: str) -> str:
        return "ennoscholar:opencitations:v2:" + hashlib.sha1(
            endpoint.encode("utf-8")
        ).hexdigest()

    def _cache_get(self, endpoint: str):
        if self._redis is None:
            return None
        try:
            raw = self._redis.get(self._cache_key(endpoint))
            if not raw:
                return None
            data = json.loads(raw)
            return data if isinstance(data, list) else None
        except Exception:
            return None

    def _cache_set(self, endpoint: str, payload: list) -> None:
        if self._redis is None:
            return
        try:
            self._redis.setex(
                self._cache_key(endpoint),
                self.cache_ttl,
                json.dumps(payload, ensure_ascii=False),
            )
        except Exception:
            pass

    def _get(self, endpoint: str) -> List[Dict[str, Any]]:
        # Fresh-first sans modifier les quotas/retries V5.
        cached = self._cache_get(endpoint) or []
        if httpx is None:
            return [dict(row, retrieval_origin="cache_fallback") for row in cached if isinstance(row, dict)]

        url = f"{API_BASE}/{endpoint.lstrip('/')}"
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                    response = client.get(url, headers=self._headers())
                if response.status_code == 429 and attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                payload = response.json()
                fresh = payload if isinstance(payload, list) else []

                merged: List[Dict[str, Any]] = []
                seen: set[str] = set()
                for origin, rows in (("fresh_api", fresh), ("cache_supplement", cached)):
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        key = json.dumps(row, sort_keys=True, ensure_ascii=False)
                        if key in seen:
                            continue
                        seen.add(key)
                        item = dict(row)
                        item["retrieval_origin"] = origin
                        merged.append(item)
                self._cache_set(endpoint, merged)
                return merged
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(0.25 * (attempt + 1))

        out: List[Dict[str, Any]] = []
        for row in cached:
            if isinstance(row, dict):
                item = dict(row)
                item["retrieval_origin"] = "cache_fallback"
                if last_error is not None:
                    item["fresh_error"] = f"{type(last_error).__name__}: {last_error}"
                out.append(item)
        return out

    def references(self, doi: str, limit: int = 10) -> List[Dict[str, Any]]:
        doi = _clean_doi(doi)
        if not doi:
            return []
        rows = self._get(f"references/doi:{quote(doi, safe='/:._-()')}")
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            target = _doi_from_pid(row.get("cited"))
            if not target or target == doi:
                continue
            out.append({
                "doi": target,
                "relation": "reference",
                "seed_doi": doi,
                "oci": row.get("oci"),
            })
            if len(out) >= max(1, int(limit)):
                break
        return out

    def citations(self, doi: str, limit: int = 10) -> List[Dict[str, Any]]:
        doi = _clean_doi(doi)
        if not doi:
            return []
        rows = self._get(f"citations/doi:{quote(doi, safe='/:._-()')}")
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            target = _doi_from_pid(row.get("citing"))
            if not target or target == doi:
                continue
            out.append({
                "doi": target,
                "relation": "citation",
                "seed_doi": doi,
                "oci": row.get("oci"),
            })
            if len(out) >= max(1, int(limit)):
                break
        return out

    def neighbours(
        self,
        doi: str,
        *,
        references: int = 5,
        citations: int = 5,
    ) -> List[Dict[str, Any]]:
        merged = self.references(doi, references) + self.citations(doi, citations)
        out, seen = [], set()
        for item in merged:
            key = _clean_doi(item.get("doi"))
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out
