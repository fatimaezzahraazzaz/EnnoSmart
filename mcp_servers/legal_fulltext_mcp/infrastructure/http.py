from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import random
import socket
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from ..domain.normalizers import looks_like_download_url
from .landing_extractors import extract_script_pdf_urls, platform_pdf_candidates


_PDF_META_NAMES = {
    "citation_pdf_url",
    "bepress_citation_pdf_url",
    "eprints.document_url",
    "eprints.document_url_1",
    "wkhealth_pdf_url",
    "pdf_url",
    "og:pdf",
}


class _ScholarlyHtmlParser(HTMLParser):
    """Extrait uniquement des indices documentaires standards d'une page scientifique."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.pdf_urls: list[str] = []
        self.title: str | None = None
        self.doi: str | None = None
        self.authors: list[str] = []
        self.year: int | None = None
        self._in_script = False
        self._script_parts: list[str] = []

    def _add_url(self, value: str | None) -> None:
        if not value:
            return
        absolute = urljoin(self.base_url, value.strip())
        if absolute.startswith(("http://", "https://")) and absolute not in self.pdf_urls:
            self.pdf_urls.append(absolute)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(k).lower(): (v or "").strip() for k, v in attrs}
        if tag.lower() == "meta":
            key = (values.get("name") or values.get("property") or "").lower()
            content = values.get("content") or ""
            if key in _PDF_META_NAMES or key.endswith("pdf_url"):
                self._add_url(content)
            elif key in {"citation_title", "dc.title", "dcterms.title"} and content:
                self.title = self.title or content
            elif key in {"citation_doi", "dc.identifier", "dcterms.identifier"} and content:
                if "10." in content.lower() or "doi.org/" in content.lower():
                    self.doi = self.doi or content
            elif key in {"citation_author", "dc.creator", "dcterms.creator"} and content:
                if content not in self.authors:
                    self.authors.append(content)
            elif key in {"citation_publication_date", "citation_date", "dc.date", "dcterms.issued"}:
                raw_year = content[:4]
                if raw_year.isdigit():
                    self.year = self.year or int(raw_year)
            elif key in {"dc.relation", "dcterms.haspart"} and looks_like_download_url(content):
                self._add_url(content)
        elif tag.lower() in {"link", "iframe", "embed", "object", "source"}:
            candidate = values.get("href") or values.get("src") or values.get("data") or ""
            context = " ".join([values.get("rel", ""), values.get("type", ""), values.get("title", "")])
            if looks_like_download_url(candidate) or "pdf" in context.lower() or "application/pdf" in context.lower():
                self._add_url(candidate)
        elif tag.lower() == "a":
            href = values.get("href") or ""
            context = " ".join([
                values.get("class", ""), values.get("id", ""), values.get("title", ""),
                values.get("aria-label", ""), values.get("download", ""),
            ]).lower()
            if looks_like_download_url(href) or any(token in context for token in ("pdf", "download", "fulltext", "full-text")):
                self._add_url(href)
        elif tag.lower() == "script":
            self._in_script = True
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_script and data:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_script:
            script = "".join(self._script_parts)
            for url in extract_script_pdf_urls(script, self.base_url):
                self._add_url(url)
            self._in_script = False
            self._script_parts = []

    def finalize(self) -> None:
        self.pdf_urls = platform_pdf_candidates(self.base_url, self.pdf_urls)


@dataclass
class PdfProbe:
    ok: bool
    status: str
    final_url: str | None = None
    content_type: str | None = None
    bytes_checked: int = 0
    error: str | None = None
    http_status: int | None = None
    failure_kind: str | None = None
    candidate_urls: list[str] = field(default_factory=list)
    document_metadata: dict[str, Any] = field(default_factory=dict)
    discovered_from_landing: bool = False
    source_url: str | None = None
    content_head_sha256: str | None = None


class UnsafeUrlError(ValueError):
    pass


class HttpRequestError(RuntimeError):
    """Erreur HTTP structurée exploitable par les providers et le cache."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_text: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text
        self.retry_after_seconds = retry_after_seconds

    @property
    def transient(self) -> bool:
        # L'absence de statut correspond généralement à un timeout, une panne
        # DNS ou une coupure transport : on reste conservateur et on n'écrit
        # pas de cache négatif stable dans ce cas.
        return self.status_code is None or self.status_code in {408, 425, 429, 500, 502, 503, 504}


class ResilientHttpClient:
    TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_retries: int,
        user_agent: str,
        browser_user_agent: str | None = None,
        max_landing_pdf_links: int = 6,
        validate_public_network_urls: bool = True,
    ) -> None:
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max(1, int(max_retries))
        self.user_agent = user_agent
        self.browser_user_agent = browser_user_agent or user_agent
        self.max_landing_pdf_links = max(1, min(12, int(max_landing_pdf_links)))
        self.validate_public_network_urls = bool(validate_public_network_urls)
        self._client: httpx.AsyncClient | None = None
        self._safe_hosts: set[str] = set()

    async def __aenter__(self) -> "ResilientHttpClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds),
            follow_redirects=True,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            },
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("ResilientHttpClient doit être utilisé avec 'async with'.")
        return self._client

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float | None:
        raw = (response.headers.get("Retry-After") or "").strip()
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            try:
                target = parsedate_to_datetime(raw)
                now = parsedate_to_datetime(response.headers.get("Date")) if response.headers.get("Date") else None
                if now is None:
                    from datetime import datetime, timezone

                    now = datetime.now(timezone.utc)
                return max(0.0, (target - now).total_seconds())
            except Exception:
                return None

    @staticmethod
    def _response_excerpt(response: httpx.Response, limit: int = 800) -> str:
        try:
            text = response.text.strip()
        except Exception:
            return ""
        return text[:limit]

    async def _sleep_before_retry(self, attempt: int, retry_after: float | None = None) -> None:
        exponential = min(20.0, 0.8 * (2**attempt))
        jitter = random.uniform(0.05, 0.35)
        delay = max(exponential + jitter, retry_after or 0.0)
        await asyncio.sleep(min(delay, 60.0))

    @staticmethod
    def _public_ip(address: str) -> bool:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    async def _ensure_safe_public_url(self, url: str) -> None:
        if not self.validate_public_network_urls:
            return
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().strip(".")
        if parsed.scheme not in {"http", "https"} or not host:
            raise UnsafeUrlError("URL HTTP(S) invalide")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise UnsafeUrlError("Hôte local interdit")
        if host in self._safe_hosts:
            return
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = None
        last_dns_error: socket.gaierror | None = None
        # Une panne DNS très brève ne doit pas transformer tous les articles
        # suivants en « introuvables ». Trois résolutions courtes suffisent,
        # sans contourner la validation SSRF des adresses obtenues.
        for attempt in range(3):
            try:
                infos = await asyncio.to_thread(
                    socket.getaddrinfo,
                    host,
                    port,
                    type=socket.SOCK_STREAM,
                )
                break
            except socket.gaierror as exc:
                last_dns_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.25 * (2**attempt))
        if infos is None:
            raise HttpRequestError(
                f"Résolution DNS temporairement impossible : {host}",
                status_code=None,
            ) from last_dns_error
        addresses = {str(info[4][0]) for info in infos if info and info[4]}
        if not addresses or any(not self._public_ip(address) for address in addresses):
            raise UnsafeUrlError("Adresse locale, privée ou réservée interdite")
        self._safe_hosts.add(host)

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        await self._ensure_safe_public_url(url)
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = await self.client.get(url, params=params, headers=headers)
                if response.status_code in self.TRANSIENT_STATUS_CODES:
                    retry_after = self._retry_after_seconds(response)
                    error = HttpRequestError(
                        f"HTTP transitoire {response.status_code}",
                        status_code=response.status_code,
                        response_text=self._response_excerpt(response),
                        retry_after_seconds=retry_after,
                    )
                    if attempt + 1 >= self.max_retries:
                        raise error
                    await self._sleep_before_retry(attempt, retry_after)
                    continue
                if response.status_code >= 400:
                    excerpt = self._response_excerpt(response)
                    message = f"HTTP {response.status_code}"
                    if excerpt:
                        message += f" : {excerpt}"
                    raise HttpRequestError(message, status_code=response.status_code, response_text=excerpt)
                data = response.json()
                return data if isinstance(data, dict) else {"results": data}
            except HttpRequestError:
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt + 1 >= self.max_retries:
                    break
                await self._sleep_before_retry(attempt)
            except ValueError as exc:
                raise HttpRequestError(f"Réponse JSON invalide : {exc}") from exc
        raise HttpRequestError(str(last_error) if last_error else "Erreur HTTP inconnue")

    async def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        await self._ensure_safe_public_url(url)
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = await self.client.get(url, params=params, headers=headers)
                if response.status_code in self.TRANSIENT_STATUS_CODES:
                    retry_after = self._retry_after_seconds(response)
                    error = HttpRequestError(
                        f"HTTP transitoire {response.status_code}",
                        status_code=response.status_code,
                        response_text=self._response_excerpt(response),
                        retry_after_seconds=retry_after,
                    )
                    if attempt + 1 >= self.max_retries:
                        raise error
                    await self._sleep_before_retry(attempt, retry_after)
                    continue
                if response.status_code >= 400:
                    excerpt = self._response_excerpt(response)
                    message = f"HTTP {response.status_code}"
                    if excerpt:
                        message += f" : {excerpt}"
                    raise HttpRequestError(message, status_code=response.status_code, response_text=excerpt)
                return response.text
            except HttpRequestError:
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt + 1 >= self.max_retries:
                    break
                await self._sleep_before_retry(attempt)
        raise HttpRequestError(str(last_error) if last_error else "Erreur HTTP inconnue")

    @staticmethod
    def _host(url: str) -> str:
        try:
            return (urlparse(url).hostname or "").lower()
        except Exception:
            return ""

    def _document_headers(self, url: str, referer: str | None, *, use_range: bool, max_bytes: int) -> dict[str, str]:
        headers = {
            "Accept": "application/pdf,application/octet-stream,text/html;q=0.8,*/*;q=0.6",
            "User-Agent": self.browser_user_agent,
            "Cache-Control": "no-cache",
        }
        if use_range:
            headers["Range"] = f"bytes=0-{max_bytes - 1}"
        if referer:
            headers["Referer"] = referer
        return headers

    @staticmethod
    def _failure_kind_for_status(status_code: int) -> str:
        if status_code in {401, 403, 407, 451}:
            return "access_blocked"
        if status_code == 404:
            return "not_found"
        if status_code == 429:
            return "rate_limited"
        if status_code in {408, 425, 500, 502, 503, 504}:
            return "temporarily_unavailable"
        return "http_error"

    async def _probe_once(
        self,
        url: str,
        *,
        max_bytes: int,
        referer: str | None,
        use_range: bool,
        redirect_depth: int = 0,
    ) -> PdfProbe:
        try:
            await self._ensure_safe_public_url(url)
        except UnsafeUrlError as exc:
            return PdfProbe(ok=False, status="unsafe_url", error=str(exc), failure_kind="unsafe_url", source_url=url)

        headers = self._document_headers(url, referer, use_range=use_range, max_bytes=max_bytes)
        try:
            async with self.client.stream("GET", url, headers=headers, follow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        return PdfProbe(
                            ok=False,
                            status="redirect_without_location",
                            http_status=response.status_code,
                            failure_kind="http_error",
                            source_url=url,
                        )
                    if redirect_depth >= 6:
                        return PdfProbe(
                            ok=False,
                            status="too_many_redirects",
                            http_status=response.status_code,
                            failure_kind="http_error",
                            source_url=url,
                        )
                    next_url = urljoin(str(response.url), location)
                    return await self._probe_once(
                        next_url,
                        max_bytes=max_bytes,
                        referer=referer or url,
                        use_range=use_range,
                        redirect_depth=redirect_depth + 1,
                    )
                if response.status_code >= 400:
                    return PdfProbe(
                        ok=False,
                        status=f"http_{response.status_code}",
                        final_url=str(response.url),
                        content_type=response.headers.get("Content-Type"),
                        http_status=response.status_code,
                        failure_kind=self._failure_kind_for_status(response.status_code),
                        source_url=url,
                    )
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    remaining = max_bytes - total
                    chunks.append(chunk[:remaining])
                    total += min(len(chunk), remaining)
                    if total >= max_bytes:
                        break
                head = b"".join(chunks)
                content_type = (response.headers.get("Content-Type") or "").lower()
                stripped = head.lstrip()
                lower = stripped.lower()
                html_like = lower.startswith((b"<!doctype html", b"<html", b"<?xml")) or "text/html" in content_type
                magic_pdf = stripped.startswith(b"%PDF-")
                is_pdf = magic_pdf or ("application/pdf" in content_type and not html_like and len(head) > 16)
                digest = hashlib.sha256(head).hexdigest() if head else None
                if is_pdf:
                    return PdfProbe(
                        ok=True,
                        status="verified_pdf",
                        final_url=str(response.url),
                        content_type=content_type,
                        bytes_checked=len(head),
                        http_status=response.status_code,
                        source_url=url,
                        content_head_sha256=digest,
                    )

                candidate_urls: list[str] = []
                document_metadata: dict[str, Any] = {}
                if html_like and head:
                    parser = _ScholarlyHtmlParser(str(response.url))
                    try:
                        parser.feed(head.decode(response.encoding or "utf-8", errors="ignore"))
                    except Exception:
                        parser = _ScholarlyHtmlParser(str(response.url))
                    parser.finalize()
                    candidate_urls = parser.pdf_urls[: self.max_landing_pdf_links]
                    document_metadata = {
                        "title": parser.title,
                        "doi": parser.doi,
                        "authors": parser.authors,
                        "year": parser.year,
                    }
                    document_metadata = {
                        k: v
                        for k, v in document_metadata.items()
                        if v is not None and v != "" and v != []
                    }
                return PdfProbe(
                    ok=False,
                    status="landing_page_with_candidates" if candidate_urls else "not_pdf_response",
                    final_url=str(response.url),
                    content_type=content_type,
                    bytes_checked=len(head),
                    http_status=response.status_code,
                    failure_kind="landing_page" if html_like else "not_pdf",
                    candidate_urls=candidate_urls,
                    document_metadata=document_metadata,
                    source_url=url,
                    content_head_sha256=digest,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return PdfProbe(ok=False, status="probe_error", error=str(exc), failure_kind="temporarily_unavailable", source_url=url)
        except Exception as exc:
            return PdfProbe(ok=False, status="probe_error", error=str(exc), failure_kind="probe_error", source_url=url)

    @staticmethod
    def _should_retry_without_range(probe: PdfProbe) -> bool:
        return probe.status in {
            "http_403",
            "http_406",
            "http_416",
            "not_pdf_response",
            "landing_page_with_candidates",
        }

    async def _probe_with_range_fallback(
        self,
        url: str,
        *,
        max_bytes: int,
        referer: str | None,
    ) -> PdfProbe:
        async def probe_mode(use_range: bool) -> PdfProbe:
            last: PdfProbe | None = None
            for attempt in range(self.max_retries):
                last = await self._probe_once(
                    url,
                    max_bytes=max_bytes,
                    referer=referer,
                    use_range=use_range,
                )
                if last.failure_kind not in {"rate_limited", "temporarily_unavailable"}:
                    return last
                if attempt + 1 < self.max_retries:
                    await self._sleep_before_retry(attempt)
            return last or PdfProbe(
                ok=False,
                status="probe_error",
                failure_kind="temporarily_unavailable",
                source_url=url,
            )

        first = await probe_mode(True)
        if first.ok or not self._should_retry_without_range(first):
            return first
        second = await probe_mode(False)
        if second.ok or second.candidate_urls or second.status != "probe_error":
            return second
        return first

    async def probe_pdf(
        self,
        url: str,
        max_bytes: int = 524288,
        *,
        referer: str | None = None,
    ) -> PdfProbe:
        landing = await self._probe_with_range_fallback(url, max_bytes=max_bytes, referer=referer)
        if landing.ok:
            return landing

        queue = list(landing.candidate_urls)
        seen = {url.lower().strip()}
        tried = 0
        while queue and tried < self.max_landing_pdf_links:
            candidate_url = queue.pop(0)
            key = candidate_url.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            tried += 1
            candidate = await self._probe_with_range_fallback(
                candidate_url,
                max_bytes=max_bytes,
                referer=landing.final_url or url,
            )
            if candidate.ok:
                candidate.status = "verified_pdf_from_landing"
                candidate.discovered_from_landing = True
                candidate.source_url = url
                candidate.document_metadata = landing.document_metadata
                return candidate
            for nested in candidate.candidate_urls:
                if nested.lower().strip() not in seen and len(queue) < self.max_landing_pdf_links:
                    queue.append(nested)

        return landing
