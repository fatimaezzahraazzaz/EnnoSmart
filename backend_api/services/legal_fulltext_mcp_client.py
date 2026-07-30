# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Passerelle HTTP légère vers le serveur MCP Legal Fulltext.

Pourquoi une passerelle HTTP ?
- le serveur reste un vrai serveur MCP sur /mcp ;
- EnnoAmel et les clients MCP peuvent utiliser le protocole MCP officiel ;
- le backend FastAPI EnnoSmart n'a pas besoin d'installer le SDK MCP dans sa venv ;
- on évite ainsi de coupler les dépendances Starlette/FastAPI du backend à celles du serveur MCP.
"""

import os
import time
from typing import Any

import requests


REST_URL = os.getenv(
    "ENNOSCHOLAR_LEGAL_MCP_REST_URL",
    "http://127.0.0.1:8010/api/resolve",
)
MCP_ENABLED = os.getenv("ENNOSCHOLAR_LEGAL_MCP_ENABLED", "1").lower() in {
    "1", "true", "yes", "on"
}
TIMEOUT_SECONDS = float(os.getenv("ENNOSCHOLAR_LEGAL_MCP_CLIENT_TIMEOUT_SECONDS", "300"))
MAX_RETRIES = max(1, int(os.getenv("ENNOSCHOLAR_LEGAL_MCP_CLIENT_MAX_RETRIES", "2")))


def _fallback(status: str, reason: str | None = None) -> dict[str, Any]:
    transient = status in {
        "mcp_unavailable",
        "mcp_client_error",
        "mcp_client_empty_result",
        "provider_temporarily_unavailable",
    }
    return {
        "ok": False,
        "found": False,
        "legal_access": False,
        "same_article": False,
        "status": status,
        "failure_code": status,
        "best_candidate": None,
        "locations": [],
        "attempts": [],
        "retry_recommended": transient,
        "needs_consultant_upload": not transient,
        "reason": reason,
    }


def resolve_article_fulltext(
    *,
    title: str,
    doi: str | None = None,
    authors: list[str] | None = None,
    year: int | None = None,
    known_urls: list[str] | None = None,
    article_id: int | str | None = None,
    source: str | None = None,
    search_all: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Résoudre un full text via la passerelle du serveur MCP autonome."""
    if not MCP_ENABLED:
        return _fallback("mcp_disabled")

    title = str(title or "").strip()
    if not title:
        return _fallback("invalid_article", "Le titre est obligatoire.")

    payload = {
        "title": title,
        "doi": doi,
        "authors": authors or [],
        "year": year,
        "known_urls": known_urls or [],
        "article_id": article_id,
        "source": source,
        "search_all": search_all,
        "force_refresh": force_refresh,
    }

    last_error: str | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(
                REST_URL,
                json=payload,
                timeout=TIMEOUT_SECONDS,
                headers={"Accept": "application/json"},
            )
            if response.status_code in {429, 500, 502, 503, 504}:
                last_error = f"HTTP transitoire {response.status_code}"
                if attempt + 1 < MAX_RETRIES:
                    time.sleep(min(3.0, 0.5 * (2**attempt)))
                    continue
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return data
            return _fallback("invalid_mcp_response", "Réponse JSON non objet.")
        except Exception as exc:
            last_error = str(exc)
            if attempt + 1 < MAX_RETRIES:
                time.sleep(min(3.0, 0.5 * (2**attempt)))

    return _fallback("mcp_unavailable", last_error)
