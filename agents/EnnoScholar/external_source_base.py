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

CLIENT_VERSION = "v147"


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
            for item in items:
                if isinstance(item, dict):
                    item.setdefault("cache_hit", True)
                    item.setdefault("cache_source", str(path))
            return items
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


def is_retryable(exc: Exception) -> Tuple[bool, str, int]:
    if isinstance(exc, urllib.error.HTTPError):
        code = int(getattr(exc, "code", 0) or 0)
        return code in {408, 409, 425, 429, 500, 502, 503, 504}, f"HTTP {code}: {getattr(exc, 'reason', '')}", code
    if isinstance(exc, (TimeoutError, socket.timeout, urllib.error.URLError)):
        return True, str(exc), 0
    return False, str(exc), 0


def get_json(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 10,
    retries: int = 1,
    sleep_seconds: float = 0.1,
) -> Dict[str, Any]:
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
    }


def encode_params(base_url: str, params: Dict[str, Any]) -> str:
    clean = {k: v for k, v in params.items() if v is not None and v != ""}
    return base_url + "?" + urllib.parse.urlencode(clean, doseq=True)
