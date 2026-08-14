# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List
from urllib.parse import quote

try:
    import httpx
except Exception:
    httpx = None

API_BASE = os.getenv("OPENCITATIONS_API_BASE", "https://api.opencitations.net/index/v2").rstrip("/")


def _clean_doi(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)", "", text, flags=re.I)
    return text.strip().rstrip("./")


def _doi_from_pid(value: Any) -> str:
    for token in re.split(r"\s+", str(value or "").strip()):
        if token.lower().startswith("doi:"):
            return _clean_doi(token[4:])
    return ""


class OpenCitationsClient:
    """Bounded citation-neighbour discovery for EnnoScholar."""

    def __init__(self, timeout: float | None = None, max_retries: int | None = None):
        self.timeout = float(timeout or os.getenv("OPENCITATIONS_TIMEOUT", "6"))
        self.max_retries = max(0, int(max_retries if max_retries is not None else os.getenv("OPENCITATIONS_MAX_RETRIES", "1")))
        self.token = str(os.getenv("OPENCITATIONS_ACCESS_TOKEN", "") or "").strip()

    @property
    def available(self) -> bool:
        return httpx is not None

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "EnnoSmart-EnnoScholar/5-research-upgrade"}
        if self.token:
            headers["authorization"] = self.token
        return headers

    def _get(self, endpoint: str) -> List[Dict[str, Any]]:
        if httpx is None:
            return []
        url = f"{API_BASE}/{endpoint.lstrip('/')}"
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                    response = client.get(url, headers=self._headers())
                if response.status_code == 429 and attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, list) else []
            except Exception:
                if attempt < self.max_retries:
                    time.sleep(0.25 * (attempt + 1))
        return []

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
            out.append({"doi": target, "relation": "reference", "seed_doi": doi, "oci": row.get("oci")})
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
            out.append({"doi": target, "relation": "citation", "seed_doi": doi, "oci": row.get("oci")})
            if len(out) >= max(1, int(limit)):
                break
        return out

    def neighbours(self, doi: str, *, references: int = 5, citations: int = 5) -> List[Dict[str, Any]]:
        merged = self.references(doi, references) + self.citations(doi, citations)
        out, seen = [], set()
        for item in merged:
            key = _clean_doi(item.get("doi"))
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out
