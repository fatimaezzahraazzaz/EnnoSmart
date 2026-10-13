from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class SQLiteTTLCache:
    def __init__(self, path: str, ttl_seconds: int, enabled: bool = True) -> None:
        self.path = Path(path)
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.enabled = enabled
        self._lock = threading.Lock()
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=20)

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache (cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL NOT NULL)"
            )
            conn.commit()

    def get(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload, created_at FROM cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if not row:
                return None
            payload, created_at = row
            if now - float(created_at) > self.ttl_seconds:
                conn.execute("DELETE FROM cache WHERE cache_key = ?", (key,))
                conn.commit()
                return None
            try:
                data = json.loads(payload)
                return data if isinstance(data, dict) else None
            except Exception:
                conn.execute("DELETE FROM cache WHERE cache_key = ?", (key,))
                conn.commit()
                return None

    def set(self, key: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        raw = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO cache(cache_key, payload, created_at) VALUES(?, ?, ?) "
                "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, created_at=excluded.created_at",
                (key, raw, time.time()),
            )
            conn.commit()

    def delete(self, key: str) -> None:
        if not self.enabled:
            return
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM cache WHERE cache_key = ?", (key,))
            conn.commit()

    def clear(self) -> None:
        if not self.enabled:
            return
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM cache")
            conn.commit()
