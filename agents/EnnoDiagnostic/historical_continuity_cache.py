# -*- coding: utf-8 -*-
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

CACHE_VERSION = "historical_performance_cache_safe_v1_20260916"
CACHE_FILENAME = "latest_success.json.gz"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(k): _json_safe(v)
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, set):
        return sorted((_json_safe(v) for v in value), key=lambda x: repr(x))
    return {
        "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
        "__repr__": repr(value)[:4000],
    }


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", errors="replace")


def _sha256_value(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Any) -> str:
    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return "missing"
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return "unavailable"


def _safe_env_snapshot() -> Dict[str, str]:
    exact_names = {
        "ENNOSMART_CIR_MEMORY_MAX_PREVIOUS_YEARS",
        "ENNOSMART_DIAG_HISTORICAL_PREFLIGHT",
        "ENNOSMART_DIAG_TRUST_LOCAL_YEAR_PREFLIGHT",
        "ENNOSMART_DIAG_CURRENT_PROJECT_ONLY",
        "OPENAI_MODEL",
        "OPENAI_DEFAULT_MODEL",
        "LLM_MODEL",
        "LLM_DEFAULT_MODEL",
    }
    allowed_prefixes = (
        "ENNOSMART_HISTORICAL_",
        "ENNOSMART_CIR_MEMORY_",
        "ENNOSMART_DIAG_HISTORICAL_",
        "ENNOSMART_LLM_",
    )
    blocked = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")

    out: Dict[str, str] = {}
    for name, value in os.environ.items():
        if any(fragment in name.upper() for fragment in blocked):
            continue
        if name in exact_names or name.startswith(allowed_prefixes):
            out[name] = str(value)
    return dict(sorted(out.items()))


def _llm_identity(llm: Any) -> Dict[str, Any]:
    if llm is None:
        return {"present": False}
    out: Dict[str, Any] = {
        "present": True,
        "type": f"{type(llm).__module__}.{type(llm).__qualname__}",
    }
    for name in (
        "provider", "default_model", "model", "model_name",
        "writer_model", "cross_provider",
    ):
        try:
            value = getattr(llm, name)
        except Exception:
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            out[name] = value
        else:
            out[name] = repr(value)[:500]
    return out


def build_historical_cache_key(
    *,
    current_verrous: Any,
    current_sections: Any,
    previous_memory: Any,
    reconciler_file: Any,
    llm: Any = None,
    project_identity: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    material = {
        "cache_version": CACHE_VERSION,
        "project_identity": _json_safe(project_identity or {}),
        "current_verrous_sha256": _sha256_value(current_verrous),
        "current_sections_sha256": _sha256_value(current_sections),
        "previous_memory_sha256": _sha256_value(previous_memory),
        "reconciler_file_sha256": _file_sha256(reconciler_file),
        "llm_identity": _llm_identity(llm),
        "behavior_env": _safe_env_snapshot(),
    }
    material["key"] = _sha256_value(material)
    return material


def _cache_path(cache_root: Any) -> Path:
    return Path(cache_root) / CACHE_FILENAME


def load_historical_continuity_cache(
    *,
    cache_root: Any,
    expected_key: Mapping[str, Any],
) -> Dict[str, Any]:
    path = _cache_path(cache_root)
    try:
        if not path.exists():
            return {"hit": False, "reason": "not_found", "path": str(path)}

        with gzip.open(path, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)

        if not isinstance(payload, dict):
            return {"hit": False, "reason": "invalid_payload", "path": str(path)}
        if payload.get("cache_version") != CACHE_VERSION:
            return {"hit": False, "reason": "version_changed", "path": str(path)}
        if payload.get("key") != expected_key.get("key"):
            return {"hit": False, "reason": "input_changed", "path": str(path)}

        report = payload.get("report")
        if not isinstance(report, dict) or not report.get("ok"):
            return {"hit": False, "reason": "cached_report_not_successful", "path": str(path)}

        return {
            "hit": True,
            "reason": "exact_match",
            "path": str(path),
            "report": report,
        }
    except Exception as exc:
        return {
            "hit": False,
            "reason": "read_error",
            "error": str(exc),
            "path": str(path),
        }


def save_historical_continuity_cache(
    *,
    cache_root: Any,
    key_material: Mapping[str, Any],
    report: Any,
) -> Dict[str, Any]:
    if not isinstance(report, dict) or not report.get("ok"):
        return {"saved": False, "reason": "report_not_successful"}

    root = Path(cache_root)
    path = _cache_path(root)
    tmp = root / (CACHE_FILENAME + ".tmp")

    try:
        root.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_version": CACHE_VERSION,
            "key": key_material.get("key"),
            "report": _json_safe(report),
        }
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
            json.dump(
                payload,
                fh,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        os.replace(tmp, path)
        return {
            "saved": True,
            "path": str(path),
            "size_bytes": path.stat().st_size,
        }
    except Exception as exc:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return {"saved": False, "reason": "write_error", "error": str(exc)}
