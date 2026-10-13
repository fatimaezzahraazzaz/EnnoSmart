# -*- coding: utf-8 -*-
from __future__ import annotations

"""Limiteur de capacité partagé par tous les appels LLM d'EnnoSmart.

Le sémaphore Redis borne la concurrence entre FastAPI et les workers Celery.
Si Redis est momentanément indisponible, un sémaphore local conserve un mode
dégradé sûr au lieu de rendre tous les agents indisponibles.
"""

import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

try:
    import redis
except Exception:  # pragma: no cover - dépendance facultative hors backend
    redis = None  # type: ignore[assignment]

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover
    dotenv_values = None  # type: ignore[assignment]


class LLMCapacityTimeoutError(RuntimeError):
    """La file LLM n'a pas obtenu de créneau dans le délai configuré."""


_CONFIG: dict[str, str] | None = None


def _config() -> dict[str, str]:
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG
    root = Path(__file__).resolve().parents[2]
    values: dict[str, str] = {}
    if dotenv_values is not None:
        for path in (root / "backend_api" / ".env", root / ".env"):
            try:
                for key, value in (dotenv_values(path) or {}).items():
                    if value is not None:
                        values[str(key)] = str(value)
            except Exception:
                continue
    values.update({str(key): str(value) for key, value in os.environ.items()})
    _CONFIG = values
    return values


def _value(name: str, default: Any = "") -> str:
    return str(_config().get(name, default) or "").strip()


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(_value(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _env_bool(name: str, default: bool) -> bool:
    raw = _value(name, "1" if default else "0").casefold()
    return raw in {"1", "true", "yes", "oui", "on"}


@dataclass(frozen=True)
class _Lease:
    mode: str
    token: str


class LLMConcurrencyGate:
    """Sémaphore borné avec bail Redis renouvelé pendant les appels longs."""

    _ACQUIRE_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local expires = tonumber(ARGV[2])
local capacity = tonumber(ARGV[3])
local token = ARGV[4]
local ttl = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if redis.call('ZCARD', key) < capacity then
  redis.call('ZADD', key, expires, token)
  redis.call('EXPIRE', key, ttl)
  return 1
end
return 0
"""
    _REFRESH_SCRIPT = """
local key = KEYS[1]
local token = ARGV[1]
local expires = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
if redis.call('ZSCORE', key, token) then
  redis.call('ZADD', key, expires, token)
  redis.call('EXPIRE', key, ttl)
  return 1
end
return 0
"""
    _RELEASE_SCRIPT = """
return redis.call('ZREM', KEYS[1], ARGV[1])
"""

    def __init__(self) -> None:
        self.capacity = _env_int("ENNOSMART_LLM_MAX_CONCURRENCY", 8)
        self.queue_timeout = _env_int(
            "ENNOSMART_LLM_QUEUE_TIMEOUT_SECONDS",
            900,
        )
        self.lease_seconds = _env_int(
            "ENNOSMART_LLM_SLOT_LEASE_SECONDS",
            900,
            minimum=30,
        )
        self.distributed_enabled = _env_bool(
            "ENNOSMART_LLM_DISTRIBUTED_LIMITER",
            True,
        )
        self.redis_url = str(
            _value("ENNOSMART_LLM_REDIS_URL")
            or _value("REDIS_URL")
            or "redis://127.0.0.1:6379/0"
        ).strip()
        self.redis_key = str(
            _value("ENNOSMART_LLM_REDIS_SEMAPHORE_KEY")
            or "ennosmart:llm:capacity:v1"
        ).strip()
        self._local = threading.BoundedSemaphore(self.capacity)
        self._redis_client: Any | None = None
        self._redis_retry_after = 0.0
        self._stats_lock = threading.Lock()
        self._active = 0
        self._waiting = 0
        self._started = 0
        self._timeouts = 0
        self._last_mode = "not_used"

    def _client(self) -> Any | None:
        if not self.distributed_enabled or redis is None:
            return None
        if time.monotonic() < self._redis_retry_after:
            return None
        if self._redis_client is None:
            self._redis_client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.35,
                socket_timeout=0.75,
                health_check_interval=30,
            )
        return self._redis_client

    def _mark_redis_unavailable(self) -> None:
        self._redis_client = None
        self._redis_retry_after = time.monotonic() + 5.0

    def _try_distributed(self, token: str) -> bool | None:
        client = self._client()
        if client is None:
            return None
        now = time.time()
        try:
            return bool(
                client.eval(
                    self._ACQUIRE_SCRIPT,
                    1,
                    self.redis_key,
                    now,
                    now + self.lease_seconds,
                    self.capacity,
                    token,
                    self.lease_seconds * 2,
                )
            )
        except Exception:
            self._mark_redis_unavailable()
            return None

    def acquire(self, request_name: str) -> tuple[_Lease, float]:
        del request_name  # réservé à une future télémétrie sans contenu utilisateur
        token = str(uuid.uuid4())
        started = time.monotonic()
        deadline = started + self.queue_timeout
        with self._stats_lock:
            self._waiting += 1
        try:
            while True:
                distributed = self._try_distributed(token)
                if distributed is True:
                    lease = _Lease("redis", token)
                    break
                if distributed is None:
                    remaining = max(0.0, deadline - time.monotonic())
                    if self._local.acquire(timeout=remaining):
                        lease = _Lease("local", token)
                        break
                if time.monotonic() >= deadline:
                    with self._stats_lock:
                        self._timeouts += 1
                    raise LLMCapacityTimeoutError(
                        "La file d'attente LLM est saturée. Réessayez dans quelques instants."
                    )
                time.sleep(0.15)
        finally:
            with self._stats_lock:
                self._waiting = max(0, self._waiting - 1)

        waited = round(time.monotonic() - started, 3)
        with self._stats_lock:
            self._active += 1
            self._started += 1
            self._last_mode = lease.mode
        return lease, waited

    def refresh(self, lease: _Lease) -> bool:
        if lease.mode != "redis":
            return True
        client = self._client()
        if client is None:
            return False
        try:
            return bool(
                client.eval(
                    self._REFRESH_SCRIPT,
                    1,
                    self.redis_key,
                    lease.token,
                    time.time() + self.lease_seconds,
                    self.lease_seconds * 2,
                )
            )
        except Exception:
            self._mark_redis_unavailable()
            return False

    def release(self, lease: _Lease) -> None:
        try:
            if lease.mode == "local":
                self._local.release()
            else:
                client = self._client()
                if client is not None:
                    try:
                        client.eval(
                            self._RELEASE_SCRIPT,
                            1,
                            self.redis_key,
                            lease.token,
                        )
                    except Exception:
                        self._mark_redis_unavailable()
        finally:
            with self._stats_lock:
                self._active = max(0, self._active - 1)

    def status(self) -> dict[str, Any]:
        with self._stats_lock:
            return {
                "capacity": self.capacity,
                "active": self._active,
                "waiting": self._waiting,
                "started": self._started,
                "timeouts": self._timeouts,
                "queue_timeout_seconds": self.queue_timeout,
                "distributed_enabled": self.distributed_enabled,
                "last_mode": self._last_mode,
            }


_GATE: LLMConcurrencyGate | None = None
_GATE_LOCK = threading.Lock()


def get_llm_concurrency_gate() -> LLMConcurrencyGate:
    global _GATE
    if _GATE is None:
        with _GATE_LOCK:
            if _GATE is None:
                _GATE = LLMConcurrencyGate()
    return _GATE


@contextmanager
def llm_capacity_slot(request_name: str = "llm") -> Iterator[dict[str, Any]]:
    gate = get_llm_concurrency_gate()
    lease, waited = gate.acquire(request_name)
    stopped = threading.Event()

    def heartbeat() -> None:
        interval = max(10.0, gate.lease_seconds / 3)
        while not stopped.wait(interval):
            if not gate.refresh(lease):
                return

    refresher: threading.Thread | None = None
    if lease.mode == "redis":
        refresher = threading.Thread(
            target=heartbeat,
            name="ennosmart-llm-slot-heartbeat",
            daemon=True,
        )
        refresher.start()
    try:
        yield {
            "llm_queue_wait_seconds": waited,
            "llm_capacity_limit": gate.capacity,
            "llm_capacity_mode": lease.mode,
        }
    finally:
        stopped.set()
        if refresher is not None:
            refresher.join(timeout=0.2)
        gate.release(lease)


def llm_concurrency_status() -> dict[str, Any]:
    return get_llm_concurrency_gate().status()
