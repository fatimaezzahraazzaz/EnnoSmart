"""Public, bounded downloads with a pinned DNS result on every redirect."""
from __future__ import annotations

import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit
from concurrent.futures import ThreadPoolExecutor

import httpx

MAX_BYTES = 30 * 1024 * 1024
_dns_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="scholar-v3-1-dns")


def resolve_public(url: str, deadline: float) -> tuple[str, str]:
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.port not in {None, 80, 443}):
        raise ValueError("URL publique HTTP(S) requise.")
    host = parsed.hostname.encode("idna").decode("ascii")
    future = _dns_pool.submit(socket.getaddrinfo, host, parsed.port or (443 if parsed.scheme == "https" else 80),
                              0, socket.SOCK_STREAM)
    rows = future.result(timeout=max(0.001, min(5, deadline - time.monotonic())))
    addresses = list(dict.fromkeys(row[4][0] for row in rows))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Les adresses internes, privées ou réservées sont interdites.")
    address = addresses[0]
    authority = f"[{address}]" if ":" in address else address
    if parsed.port:
        authority += f":{parsed.port}"
    return parsed._replace(netloc=authority, fragment="").geturl(), host


def fetch_public(url: str, deadline: float, check=lambda: None) -> tuple[bytes, str, str]:
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        for _ in range(5):
            check()
            if time.monotonic() >= deadline:
                raise TimeoutError("Délai de téléchargement atteint.")
            pinned, host = resolve_public(url, deadline)
            # Connect to the validated IP, preserve HTTP Host and TLS SNI/certificate validation.
            with client.stream("GET", pinned, headers={"Host": urlsplit(url).netloc,
                    "User-Agent": "EnnoScholarV31/1.0 (public scientific literature)"},
                    extensions={"sni_hostname": host}, timeout=min(15, max(0.1, deadline - time.monotonic()))) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                response.raise_for_status()
                if int(response.headers.get("content-length", "0") or 0) > MAX_BYTES:
                    raise ValueError("Publication trop volumineuse.")
                data = bytearray()
                for chunk in response.iter_bytes():
                    check()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Délai de téléchargement atteint.")
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise ValueError("Publication trop volumineuse.")
                return bytes(data), response.headers.get("content-type", ""), url
    raise ValueError("Trop de redirections.")
