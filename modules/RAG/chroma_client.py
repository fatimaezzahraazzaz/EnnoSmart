# -*- coding: utf-8 -*-
"""Fabrique unique des clients Chroma EnnoSmart.

En développement natif, Chroma peut rester embarqué dans un dossier local.
En production Docker, ``ENNOSMART_CHROMA_MODE=http`` force tous les processus
API/workers à utiliser le service Chroma partagé sur le réseau privé Docker.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import chromadb


HTTP_MODES = {"http", "server", "remote"}
LOCAL_MODES = {"persistent", "local", "embedded"}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def chroma_mode() -> str:
    configured = os.getenv("ENNOSMART_CHROMA_MODE", "auto").strip().lower()
    if configured in HTTP_MODES:
        return "http"
    if configured in LOCAL_MODES:
        return "persistent"
    if configured not in {"", "auto"}:
        raise ValueError(
            "ENNOSMART_CHROMA_MODE doit valoir http, persistent ou auto."
        )
    return "http" if os.getenv("ENNOSMART_CHROMA_HOST", "").strip() else "persistent"


def chroma_http_enabled() -> bool:
    return chroma_mode() == "http"


def chroma_scope_enforced() -> bool:
    # En mode serveur, l'isolation est sûre par défaut. Une valeur explicite à
    # 0 reste possible uniquement pour une migration contrôlée.
    return _env_bool(
        "ENNOSMART_CHROMA_ENFORCE_SCOPE",
        default=chroma_http_enabled(),
    )


def _http_settings() -> tuple[str, int, bool, str, str]:
    host = os.getenv("ENNOSMART_CHROMA_HOST", "chroma").strip() or "chroma"
    port = int(os.getenv("ENNOSMART_CHROMA_PORT", "8000"))
    ssl = _env_bool("ENNOSMART_CHROMA_SSL", False)
    tenant = os.getenv("ENNOSMART_CHROMA_TENANT", "default_tenant").strip()
    database = os.getenv("ENNOSMART_CHROMA_DATABASE", "default_database").strip()
    return host, port, ssl, tenant or "default_tenant", database or "default_database"


@lru_cache(maxsize=4)
def _cached_http_client(
    host: str,
    port: int,
    ssl: bool,
    tenant: str,
    database: str,
):
    return chromadb.HttpClient(
        host=host,
        port=port,
        ssl=ssl,
        tenant=tenant,
        database=database,
    )


def create_chroma_client(persist_dir: str | Path | None = None):
    """Retourne le client imposé par l'environnement d'exécution."""

    if chroma_http_enabled():
        return _cached_http_client(*_http_settings())

    if persist_dir is None:
        raise ValueError("persist_dir est obligatoire pour Chroma embarqué.")
    path = Path(persist_dir)
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path))


def chroma_connection_info(persist_dir: str | Path | None = None) -> Dict[str, Any]:
    mode = chroma_mode()
    if mode == "http":
        host, port, ssl, tenant, database = _http_settings()
        scheme = "https" if ssl else "http"
        return {
            "mode": mode,
            "location": f"{scheme}://{host}:{port}",
            "host": host,
            "port": port,
            "ssl": ssl,
            "tenant": tenant,
            "database": database,
            "scope_enforced": chroma_scope_enforced(),
        }
    return {
        "mode": mode,
        "location": str(Path(persist_dir).resolve()) if persist_dir else None,
        "scope_enforced": chroma_scope_enforced(),
    }

