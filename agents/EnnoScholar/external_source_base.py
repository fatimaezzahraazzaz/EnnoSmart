# -*- coding: utf-8 -*-
from __future__ import annotations

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

CLIENT_VERSION = "v147"  # preserve existing V5 query-cache keys; semantics are fresh-first


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on", "oui"}


def safe(value: Any, max_chars: int = 4000) -> str:
    import re
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_chars].strip()


def strip_html(value: Any, max_chars: int = 10000) -> str:
    import html, re
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return safe(text, max_chars)


def cache_root() -> Path:
    custom = os.getenv("ENNOSCHOLAR_CACHE_DIR")
    if custom:
        return Path(custom)
    return Path.cwd() / "storage" / "ennoscholar_cache"


def cache_path(source: str, query: str, limit: int) -> Path:
    raw = f"{source}|{query}|{limit}|{CLIENT_VERSION}".encode("utf-8", errors="replace")
    digest = hashlib.sha256(raw).hexdigest()[:32]
    return cache_root() / source / f"{digest}.json"


def read_cache(path: Path, max_age_days: int) -> Optional[List[Dict[str, Any]]]:
    try:
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if max_age_days > 0 and age > max_age_days * 86400:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        if isinstance(items, list):
            out: List[Dict[str, Any]] = []
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                item.setdefault("cache_hit", True)
                item.setdefault("cache_source", str(path))
                out.append(item)
            return out
    except Exception:
        return None
    return None


def write_cache(path: Path, items: List[Dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "items": items}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _item_identity(item: Dict[str, Any]) -> str:
    import re
    doi = safe(item.get("doi"), 300).lower()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi).strip()
    if doi:
        return "doi:" + doi
    paper_id = safe(
        item.get("paper_id") or item.get("paperId") or item.get("id") or item.get("artifact_id"),
        400,
    ).lower()
    if paper_id:
        return "id:" + paper_id
    title = re.sub(r"[^a-z0-9]+", " ", safe(item.get("title"), 600).casefold()).strip()
    year = safe(item.get("year"), 10)
    if title:
        return f"title:{title}:{year}"
    url = safe(item.get("url"), 1000).lower()
    return "url:" + url if url else ""


def mark_fresh(items: List[Dict[str, Any]] | None, source: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if not item.get("normalized_error"):
            item["cache_hit"] = False
            item["cache_supplement"] = False
            item["retrieval_origin"] = "fresh_api"
            item["retrieval_source"] = source
        out.append(item)
    return out


def merge_fresh_with_cache(
    fresh: List[Dict[str, Any]] | None,
    cached: List[Dict[str, Any]] | None,
    limit: int,
    source: str,
) -> List[Dict[str, Any]]:
    """Fresh results always lead; cache only fills missing unique slots."""
    limit = max(1, int(limit or 1))
    fresh_items = mark_fresh(fresh, source)
    out: List[Dict[str, Any]] = []
    seen = set()

    for item in fresh_items:
        if item.get("normalized_error"):
            continue
        key = _item_identity(item)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(item)
        if len(out) >= limit:
            return out[:limit]

    for raw in cached or []:
        if not isinstance(raw, dict) or raw.get("normalized_error"):
            continue
        item = dict(raw)
        key = _item_identity(item)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        item["cache_hit"] = True
        item["cache_supplement"] = True
        item["retrieval_origin"] = "cache_supplement"
        item["retrieval_source"] = source
        out.append(item)
        if len(out) >= limit:
            break
    return out[:limit]


def fallback_from_cache(
    cached: List[Dict[str, Any]] | None,
    source: str,
    error: Exception | str | None = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in cached or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["cache_hit"] = True
        item["cache_supplement"] = False
        item["cache_fallback_after_error"] = True
        item["retrieval_origin"] = "cache_fallback"
        item["retrieval_source"] = source
        if error is not None:
            item["api_error"] = str(error)
        out.append(item)
    return out


def is_retryable(exc: Exception) -> Tuple[bool, str, int]:
    if isinstance(exc, urllib.error.HTTPError):
        code = int(getattr(exc, "code", 0) or 0)
        return code in {408, 409, 425, 429, 500, 502, 503, 504}, f"HTTP {code}: {getattr(exc, 'reason', '')}", code
    if isinstance(exc, (TimeoutError, socket.timeout, urllib.error.URLError)):
        return True, str(exc), 0
    return False, str(exc), 0


def _retry_after(exc: Exception) -> float | None:
    try:
        headers = getattr(exc, "headers", None)
        if headers is None:
            return None
        raw = headers.get("Retry-After")
        if raw is None:
            return None
        return max(0.0, float(raw))
    except Exception:
        return None


def get_json(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 10,
    retries: int = 1,
    sleep_seconds: float = 0.1,
) -> Dict[str, Any]:
    # Politique de retry V5 conservée volontairement : aucun nouveau quota/provider policy.
    last: Exception | None = None
    for attempt in range(max(0, retries) + 1):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw)
        except Exception as exc:
            last = exc
            retryable, _, code = is_retryable(exc)
            if not retryable or attempt >= retries:
                raise
            time.sleep(min((2.0 if code == 429 else 1.0) * (attempt + 1) + sleep_seconds, 4.0))
    if last:
        raise last
    return {}

def normalized_error(source: str, query: str, exc: Exception | str, *, skipped: bool = False) -> Dict[str, Any]:
    retryable, message, code = is_retryable(exc) if isinstance(exc, Exception) else (False, str(exc), 0)
    return {
        "source": source,
        "query": query,
        "error": message,
        "http_status": code,
        "normalized_error": True,
        "api_limited": code == 429 or "429" in message,
        "skipped": bool(skipped),
        "retryable": bool(retryable),
        "retrieval_origin": "fresh_api_error",
    }


def encode_params(base_url: str, params: Dict[str, Any]) -> str:
    clean = {k: v for k, v in params.items() if v is not None and v != ""}
    return base_url + "?" + urllib.parse.urlencode(clean, doseq=True)
