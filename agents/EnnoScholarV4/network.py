"""Public, bounded downloads with a pinned DNS result on every redirect."""
from __future__ import annotations

import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit
from concurrent.futures import ThreadPoolExecutor

import httpx

_dns_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="scholar-v4-dns")


class DownloadTooLarge(ValueError):
    """A bounded refusal, retaining the facts needed to explain/reconsider it."""
    def __init__(self, limit, observed, source, url, content_type=""):
        super().__init__("Publication trop volumineuse.")
        self.max_bytes, self.observed_bytes = limit, observed
        self.size_source, self.url, self.content_type = source, url, content_type


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


def fetch_public(url: str, deadline: float, check=lambda: None, max_bytes=None) -> tuple[bytes, str, str]:
    if max_bytes is None:
        from .config import ScholarV4Config
        max_bytes = ScholarV4Config.from_env().max_pdf_bytes
    if max_bytes <= 0:
        raise ValueError("La limite de téléchargement doit être positive.")
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        for _ in range(5):
            check()
            if time.monotonic() >= deadline:
                raise TimeoutError("Délai de téléchargement atteint.")
            pinned, host = resolve_public(url, deadline)
            # Connect to the validated IP, preserve HTTP Host and TLS SNI/certificate validation.
            with client.stream("GET", pinned, headers={"Host": urlsplit(url).netloc,
                    "User-Agent": "EnnoScholarV4/1.0 (public scientific literature)"},
                    extensions={"sni_hostname": host}, timeout=min(15, max(0.1, deadline - time.monotonic()))) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                response.raise_for_status()
                try:
                    announced = int(response.headers.get("content-length", "0") or 0)
                except ValueError:
                    announced = 0  # Unknown length: the streamed limit still applies.
                if announced > max_bytes:
                    raise DownloadTooLarge(max_bytes, announced, "content_length", url,
                        response.headers.get("content-type", ""))
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=min(65536, max_bytes+1)):
                    check()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Délai de téléchargement atteint.")
                    if len(chunk) > max_bytes-len(data):
                        raise DownloadTooLarge(max_bytes, len(data)+len(chunk), "stream", url,
                            response.headers.get("content-type", ""))
                    data.extend(chunk)
                return bytes(data), response.headers.get("content-type", ""), url
    raise ValueError("Trop de redirections.")


def fetch_document(url, deadline, check=lambda: None, max_bytes=None, *, config=None, pdf_to_disk=True):
    """PDF bytes go to bounded staging; HTML/XML retain the small-response API.

    No Range retry: an interrupted transfer is discarded, never a complete PDF.
    DNS pinning, redirects and deadlines are identical to fetch_public.
    """
    import hashlib
    import os
    from .config import ScholarV4Config
    from .pdf_store import StagedDocument
    config = config or ScholarV4Config.from_env()
    memory_limit = config.max_pdf_bytes if max_bytes is None else max_bytes
    disk_limit = config.max_pdf_disk_bytes
    if not 0 < memory_limit <= disk_limit:
        raise ValueError('Limites documentaires invalides.')
    staged = None
    try:
        with httpx.Client(follow_redirects=False, trust_env=False) as client:
            for _ in range(5):
                check()
                if time.monotonic() >= deadline:
                    raise TimeoutError('Délai de téléchargement atteint.')
                pinned, host = resolve_public(url, deadline)
                with client.stream('GET', pinned, headers={'Host': urlsplit(url).netloc,
                        'User-Agent': 'EnnoScholarV4/1.0 (public scientific literature)'},
                        extensions={'sni_hostname': host}, timeout=min(15, max(.1, deadline-time.monotonic()))) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get('location', ''))
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get('content-type', '')
                    try:
                        announced = max(0, int(response.headers.get('content-length', '0') or 0))
                    except ValueError:
                        announced = 0
                    if announced > disk_limit:
                        raise DownloadTooLarge(disk_limit, announced, 'content_length', url, content_type)
                    memory, received, pdf, stream = bytearray(), 0, None, None
                    digest = hashlib.sha256()
                    try:
                        for block in response.iter_bytes(chunk_size=65536):
                            check()
                            if time.monotonic() >= deadline:
                                raise TimeoutError('Délai de téléchargement atteint.')
                            if pdf is None:
                                pdf = pdf_to_disk and (block.startswith(b'%PDF') or 'application/pdf' in content_type.lower())
                                limit = disk_limit if pdf else memory_limit
                                if announced > limit:
                                    error = DownloadTooLarge(limit, announced, 'content_length', url, content_type)
                                    error.storage_mode = 'disk' if pdf else 'memory'
                                    raise error
                                if pdf:
                                    staged = StagedDocument(config)
                                    stream = staged.path.open('wb')
                            limit = disk_limit if pdf else memory_limit
                            received += len(block)
                            if received > limit:
                                error = DownloadTooLarge(limit, received, 'stream', url, content_type)
                                error.storage_mode = 'disk' if pdf else 'memory'
                                raise error
                            if pdf:
                                stream.write(block)
                                digest.update(block)
                            else:
                                memory.extend(block)
                        # A length mismatch is not a resumed/complete transfer.
                        if announced and not response.headers.get('content-encoding') and announced != received:
                            raise ValueError('ContentLengthMismatch')
                        if stream is not None:
                            stream.flush()
                            os.fsync(stream.fileno())
                    finally:
                        if stream is not None:
                            stream.close()
                    if staged is not None:
                        staged.size, staged.sha256 = received, digest.hexdigest()
                        return staged, content_type, url
                    return bytes(memory), content_type, url
        raise ValueError('Trop de redirections.')
    except BaseException:
        if staged is not None:
            staged.close()
        raise
