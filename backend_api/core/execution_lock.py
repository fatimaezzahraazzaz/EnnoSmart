from __future__ import annotations

"""Verrous courts par conversation pour éviter les doubles mutations.

Deux conversations différentes ont toujours deux clés différentes et peuvent
donc avancer en parallèle. Redis étend cette garantie à plusieurs processus ;
le verrou local sert de repli lorsque Redis n'est pas disponible.
"""

import hashlib
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from core.config import settings

try:
    import redis
except Exception:  # pragma: no cover
    redis = None  # type: ignore[assignment]


class SessionBusyError(RuntimeError):
    pass


@dataclass
class _LocalEntry:
    lock: threading.Lock
    references: int = 0


_REGISTRY: dict[str, _LocalEntry] = {}
_REGISTRY_LOCK = threading.Lock()
_REDIS_CLIENT: Any | None = None
_REDIS_RETRY_AFTER = 0.0

_REFRESH_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def _normalized_key(namespace: str, resource_id: str) -> str:
    raw = f"{namespace}:{resource_id}".encode("utf-8", errors="ignore")
    digest = hashlib.sha256(raw).hexdigest()
    return f"ennosmart:session-execution:{digest}"


def _local_entry(key: str) -> _LocalEntry:
    with _REGISTRY_LOCK:
        entry = _REGISTRY.get(key)
        if entry is None:
            entry = _LocalEntry(lock=threading.Lock())
            _REGISTRY[key] = entry
        entry.references += 1
        return entry


def _release_local_entry(key: str, entry: _LocalEntry) -> None:
    with _REGISTRY_LOCK:
        entry.references = max(0, entry.references - 1)
        if entry.references == 0 and not entry.lock.locked():
            _REGISTRY.pop(key, None)


def _redis_client() -> Any | None:
    global _REDIS_CLIENT
    if not settings.SESSION_LOCK_DISTRIBUTED or redis is None:
        return None
    if time.monotonic() < _REDIS_RETRY_AFTER:
        return None
    if _REDIS_CLIENT is None:
        _REDIS_CLIENT = redis.Redis.from_url(
            settings.SESSION_LOCK_REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.35,
            socket_timeout=0.75,
            health_check_interval=30,
        )
    return _REDIS_CLIENT


def _mark_redis_unavailable() -> None:
    global _REDIS_CLIENT, _REDIS_RETRY_AFTER
    _REDIS_CLIENT = None
    _REDIS_RETRY_AFTER = time.monotonic() + 5.0


@contextmanager
def session_execution_lock(
    namespace: str,
    resource_id: str,
    *,
    ttl_seconds: int | None = None,
) -> Iterator[None]:
    """Refuse une seconde mutation de la même session pendant son traitement."""

    key = _normalized_key(str(namespace), str(resource_id))
    entry = _local_entry(key)
    if not entry.lock.acquire(blocking=False):
        _release_local_entry(key, entry)
        raise SessionBusyError(
            "Une action est déjà en cours dans cette conversation. "
            "Attendez sa fin avant d'envoyer une nouvelle demande."
        )

    token = str(uuid.uuid4())
    ttl = max(
        60,
        int(ttl_seconds or settings.SESSION_LOCK_TTL_SECONDS),
    )
    client = _redis_client()
    distributed_acquired = False
    try:
        if client is not None:
            try:
                distributed_acquired = bool(client.set(key, token, nx=True, ex=ttl))
            except Exception:
                _mark_redis_unavailable()
                client = None
            if client is not None and not distributed_acquired:
                raise SessionBusyError(
                    "Une action est déjà en cours dans cette conversation. "
                    "Attendez sa fin avant d'envoyer une nouvelle demande."
                )

        stopped = threading.Event()

        def heartbeat() -> None:
            interval = max(15.0, ttl / 3)
            while not stopped.wait(interval):
                current = _redis_client()
                if current is None:
                    return
                try:
                    current.eval(_REFRESH_SCRIPT, 1, key, token, ttl)
                except Exception:
                    _mark_redis_unavailable()
                    return

        refresher: threading.Thread | None = None
        if distributed_acquired:
            refresher = threading.Thread(
                target=heartbeat,
                name="ennosmart-session-lock-heartbeat",
                daemon=True,
            )
            refresher.start()
        try:
            yield
        finally:
            stopped.set()
            if refresher is not None:
                refresher.join(timeout=0.2)
    finally:
        if distributed_acquired:
            current = _redis_client()
            if current is not None:
                try:
                    current.eval(_RELEASE_SCRIPT, 1, key, token)
                except Exception:
                    _mark_redis_unavailable()
        entry.lock.release()
        _release_local_entry(key, entry)

