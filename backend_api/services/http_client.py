# -*- coding: utf-8 -*-
"""Client HTTP unique pour les sources scientifiques accessibles publiquement.

Ce module effectue uniquement des requêtes HTTP standards. Il ne contient ni
proxy, ni navigateur automatisé, ni mécanisme de contournement de protection.
Les réponses refusées (403, 429, challenge, etc.) sont retournées telles quelles
pour être classifiées par le pipeline EnnoScholar.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Tuple

import requests


TIMEOUT = float(os.getenv("ENNOSMART_HTTP_TIMEOUT", "30"))
USER_AGENT = os.getenv(
    "ENNOSMART_HTTP_USER_AGENT",
    "EnnoScholar/1.0 (legal open-access full-text retrieval)",
)


class HTTPFetcher:
    """Session HTTP standard réutilisable, sans proxy ni stratégie furtive."""

    def __init__(self) -> None:
        self._session: Optional[requests.Session] = None
        self._closed = False

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            # Ignore aussi les variables d'environnement HTTP(S)_PROXY du
            # système : ce chemin doit rester une requête directe standard.
            self._session.trust_env = False
            self._session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
                }
            )
        return self._session

    @staticmethod
    def _read_limited(response: requests.Response, max_bytes: int) -> Tuple[bytes, bool]:
        if max_bytes <= 0:
            return response.content, False

        chunks = []
        total = 0
        truncated = False
        for chunk in response.iter_content(chunk_size=128 * 1024):
            if not chunk:
                continue
            remaining = max_bytes - total
            if remaining <= 0:
                truncated = True
                break
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                total += remaining
                truncated = True
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks), truncated

    def fetch_bytes(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        max_bytes: int = 0,
        referer: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any], bytes]:
        """Lit une URL publique et conserve le statut HTTP pour le diagnostic.

        ``referer`` est volontairement ignoré : le client ne simule pas une
        navigation humaine ou un accès depuis un autre site.
        """
        del referer
        started = time.monotonic()
        info: Dict[str, Any] = {
            "url": url,
            "final_url": url,
            "status": "requests_started",
            "elapsed_seconds": 0.0,
        }
        response: Optional[requests.Response] = None

        try:
            response = self._get_session().get(
                url,
                headers=headers or {},
                timeout=TIMEOUT,
                allow_redirects=True,
                stream=max_bytes > 0,
            )
            info.update(
                {
                    "final_url": response.url,
                    "http_status": response.status_code,
                    "content_type": response.headers.get("Content-Type", ""),
                }
            )

            if response.status_code >= 400:
                # Un petit corps HTML peut expliquer un refus (paywall,
                # challenge, maintenance). Il sert uniquement au diagnostic,
                # jamais à contourner la protection.
                content, truncated = self._read_limited(response, max_bytes)
                info.update(
                    {
                        "status": "requests_http_error",
                        "reason": f"HTTP {response.status_code}",
                        "content_bytes": len(content),
                        "truncated": truncated,
                    }
                )
                return False, info, content

            content, truncated = self._read_limited(response, max_bytes)
            info.update(
                {
                    "status": "requests_success",
                    "content_bytes": len(content),
                    "truncated": truncated,
                }
            )
            return True, info, content
        except requests.exceptions.SSLError as exc:
            # Ne jamais désactiver ``verify`` pour "faire passer" un site.
            # L'appelant peut classer proprement ce cas et proposer ensuite une
            # recherche de copie ouverte vérifiée, sans confondre l'erreur TLS
            # avec un paywall ou un anti-bot.
            info.update(
                {
                    "status": "requests_tls_failed",
                    "error_kind": "tls_certificate_verification_failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "content_bytes": 0,
                }
            )
            return False, info, b""
        except requests.RequestException as exc:
            info.update(
                {
                    "status": "requests_failed",
                    "error_kind": "network_request_failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "content_bytes": 0,
                }
            )
            return False, info, b""
        finally:
            if response is not None:
                response.close()
            info["elapsed_seconds"] = round(time.monotonic() - started, 3)

    def fetch_text(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        max_chars: int = 0,
        referer: str = "",
    ) -> Tuple[bool, Dict[str, Any], str]:
        max_bytes = max_chars * 4 if max_chars > 0 else 0
        ok, info, content = self.fetch_bytes(
            url=url,
            headers=headers,
            max_bytes=max_bytes,
            referer=referer,
        )
        if not ok or not content:
            return ok, info, ""

        encoding = "utf-8"
        content_type = str(info.get("content_type") or "")
        if "charset=" in content_type.lower():
            encoding = content_type.split("charset=", 1)[1].split(";", 1)[0].strip() or encoding
        try:
            text = content.decode(encoding, errors="replace")
        except LookupError:
            encoding = "utf-8"
            text = content.decode(encoding, errors="replace")
        info["encoding_used"] = encoding
        return True, info, text[:max_chars] if max_chars > 0 else text

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._session is not None:
            self._session.close()
            self._session = None

    def __del__(self) -> None:
        self.close()


GLOBAL_FETCHER = HTTPFetcher()
