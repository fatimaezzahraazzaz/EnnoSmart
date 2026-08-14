from __future__ import annotations
import os
from typing import Any

def env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}

def redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

def redis_ready(timeout_seconds: float = 0.7) -> bool:
    if not env_bool("ENNOSCHOLAR_REDIS_ENABLED", True):
        return False
    try:
        import redis
        client = redis.Redis.from_url(
            redis_url(),
            socket_connect_timeout=timeout_seconds,
            socket_timeout=timeout_seconds,
            decode_responses=True,
        )
        return bool(client.ping())
    except Exception:
        return False

def celery_preflight_enabled() -> bool:
    return env_bool("ENNOSCHOLAR_PREFLIGHT_ASYNC", True) and redis_ready()

def runtime_status() -> dict[str, Any]:
    return {
        "redis_ready": redis_ready(),
        "celery_preflight_enabled": celery_preflight_enabled(),
        "redis_url": redis_url(),
    }
