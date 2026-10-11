from __future__ import annotations

import ipaddress
import asyncio
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..models import Article, FulltextResult, TextChunk
from .scientific_search import normalized_title

MAX_BYTES = 15 * 1024 * 1024
MAX_TEXT = 100000
SELECTED_TEXT = 24000
SECTION = re.compile(r"^(?:\d+(?:\.\d+)*[.\s]*)?(abstract|introduction|related work|background|method(?:s|ology)?|experiments?|results?|discussion|limitations?|conclusions?|future work)\b", re.I)
PRIORITY = re.compile(r"abstract|introduction|results|discussion|limitation|conclusion|future", re.I)
_DNS_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="scholar-v2-dns")


class IdentityMismatchError(ValueError):
    pass


class FulltextUnavailableError(ValueError):
    pass


def validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        raise ValueError("URL publique HTTP(S) requise.")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
        raise ValueError("Les adresses locales ou privées ne sont pas accessibles.")


def fetch_url(url: str, timeout: float | httpx.Timeout = 15, *, deadline: float | None = None,
    headers_timeout: float | None = None) -> tuple[bytes, str, str]:
    """Bounded public downloads. Validate every redirect; never solve a challenge."""
    total = float(timeout) if isinstance(timeout, (int, float)) else timeout.read or 15
    if deadline is None:
        deadline = time.monotonic() + total
    elif isinstance(timeout, (int, float)):
        deadline = min(deadline, time.monotonic() + total)

    async def download():
        nonlocal url
        async with asyncio.timeout(max(0.001, deadline - time.monotonic())):
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                for _ in range(5):
                    # DNS validation cannot monopolize the article worker after its deadline.
                    await asyncio.get_running_loop().run_in_executor(_DNS_POOL, validate_public_url, url)
                    request = client.build_request("GET", url, headers={"User-Agent": "EnnoScholarV2/0.1"})
                    async with asyncio.timeout(max(0.001, min(headers_timeout or total, deadline - time.monotonic()))):
                        response = await client.send(request, stream=True)
                    try:
                        if response.is_redirect:
                            url = urljoin(url, response.headers.get("location", ""))
                            continue
                        response.raise_for_status()
                        if int(response.headers.get("content-length", "0") or 0) > MAX_BYTES:
                            raise ValueError("Document trop volumineux.")
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > MAX_BYTES:
                                raise ValueError("Document trop volumineux.")
                        return bytes(data), response.headers.get("content-type", ""), str(response.url)
                    finally:
                        await response.aclose()
                raise ValueError("Trop de redirections.")

    # A private loop in this synchronous worker permits cancellation of an entire
    # download (headers + redirects + trickling body), unlike a per-read timeout.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(download())
    except TimeoutError as exc:
        raise httpx.ReadTimeout("Délai global de téléchargement atteint.") from exc
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        # Do not wait for an OS DNS resolver after cancellation; its worker is bounded.
        loop.close()


def select_chunks(chunks: list[TextChunk], budget: int = SELECTED_TEXT) -> list[TextChunk]:
    # Give the introduction and the final pages a chance even without headings.
    indices = list(dict.fromkeys([0, *[i for i, c in enumerate(chunks) if PRIORITY.search(c.section)],
        *range(max(0, len(chunks) - 3), len(chunks)), *range(len(chunks))]))
    selected = {}
    per_chunk = max(1200, budget // max(1, min(len(indices), 10)))
    for index in indices:
        if index >= len(chunks) or budget <= 0:
            continue
        text = chunks[index].text[:min(per_chunk, budget)]
        selected[index] = chunks[index].model_copy(update={"text": text})
        budget -= len(text)
    return [selected[i] for i in sorted(selected)]


def pdf_chunks(data: bytes) -> tuple[list[TextChunk], bool]:
    import fitz
    chunks, used = [], 0
    with fitz.open(stream=data, filetype="pdf") as document:
        if document.needs_pass:
            raise ValueError("PDF protégé par un mot de passe.")
        page_count = len(document)
        page_indices = list(range(min(page_count, 30)))
        page_indices += list(range(max(30, page_count - 10), page_count))
        for index in page_indices:
            text = document[index].get_text()[:MAX_TEXT - used]
            used += len(text)
            section = next((line.strip() for line in text.splitlines() if SECTION.match(line.strip())), "")
            if text.strip():
                chunks.append(TextChunk(page=index + 1, section=section, text=text.strip()))
            if used >= MAX_TEXT:
                break
    selected = select_chunks(chunks)
    return selected, len(page_indices) < page_count or sum(len(c.text) for c in selected) < used


def html_document(data: bytes, url: str) -> tuple[Article, list[TextChunk], bool]:
    soup = BeautifulSoup(data, "html.parser")
    def meta(*names: str) -> str:
        for name in names:
            node = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
            if node and node.get("content"):
                return str(node["content"]).strip()
        return ""
    title = meta("citation_title", "dc.title", "og:title") or (soup.title.get_text(" ", strip=True) if soup.title else "")
    abstract_node = soup.select_one("#abstract, .abstract, [role='doc-abstract']")
    abstract = abstract_node.get_text(" ", strip=True) if abstract_node else meta("citation_abstract", "dc.description", "description")
    article = Article(title=title[:1000] or url, abstract=abstract[:12000], url=url,
        doi=meta("citation_doi", "dc.identifier"), pdf_url=urljoin(url, meta("citation_pdf_url")) if meta("citation_pdf_url") else "",
        authors=[str(n.get("content", "")) for n in soup.find_all("meta", attrs={"name": "citation_author"})],
        source="article_url", sources=["article_url"])
    for node in soup.select("script, style, nav, footer, header, form"):
        node.decompose()
    body = soup.select_one("article, .fulltext, #fulltext, .ltx_document, [role='doc-body']")
    if body is None:
        return article, [], False
    chunks, section, buffer = [], "", []
    for node in body.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        text = node.get_text(" ", strip=True)
        if node.name.startswith("h"):
            if buffer:
                chunks.append(TextChunk(section=section, text="\n".join(buffer)))
                buffer = []
            section = text
        elif text:
            buffer.append(text)
    if buffer:
        chunks.append(TextChunk(section=section, text="\n".join(buffer)))
    scientific_sections = sum(bool(SECTION.match(c.section)) for c in chunks)
    if scientific_sections < 2 or sum(len(c.text) for c in chunks) < 1000:
        return article, [], False
    bounded, budget = [], MAX_TEXT
    for chunk in chunks:
        if budget <= 0:
            break
        bounded.append(chunk.model_copy(update={"text": chunk.text[:budget]}))
        budget -= len(bounded[-1].text)
    selected = select_chunks(bounded)
    return article, selected, sum(len(c.text) for c in selected) < sum(len(c.text) for c in chunks)


def matches_article(article: Article, text: str) -> bool:
    title = normalized_title(article.title)
    normalized = normalized_title(text)
    if title and title in normalized:
        return True
    words = {w for w in title.split() if len(w) > 3}
    return len(words) >= 3 and len(words.intersection(normalized.split())) / len(words) >= 0.8


def read_fulltext(url: str, article: Article, timeout: float | httpx.Timeout = 15, *,
    deadline: float | None = None, headers_timeout: float | None = None) -> FulltextResult:
    options = {"deadline": deadline, "headers_timeout": headers_timeout} if deadline is not None else {}
    data, content_type, final_url = fetch_url(url, timeout, **options)
    html_identity = ""
    if data.startswith(b"%PDF"):
        chunks, truncated = pdf_chunks(data)
    elif "html" in content_type or data.lstrip().startswith((b"<!DOCTYPE", b"<html", b"<!doctype")):
        html = data.decode("utf-8", errors="replace").lower()
        if any(token in html for token in ("cf-chl-", "verify you are human", "captcha", "just a moment...")):
            raise ValueError("Page protégée par une vérification anti-bot.")
        html_article, chunks, truncated = html_document(data, final_url)
        html_identity = html_article.title
    else:
        raise FulltextUnavailableError("Format de document non pris en charge.")
    text = "\n\n".join(c.text for c in chunks)
    if len(text) < 500:
        raise FulltextUnavailableError("Texte intégral absent ou identité de l'article non confirmée.")
    if not matches_article(article, html_identity + "\n" + text):
        raise IdentityMismatchError("Texte intégral absent ou identité de l'article non confirmée.")
    return FulltextResult(access_level="fulltext", fulltext_source=urlparse(final_url).hostname or "",
        fulltext_url=final_url, text=text, chunks=chunks, truncated=truncated,
        reason="Texte accessible extrait; analyse de passages sélectionnés." if truncated else "Texte accessible extrait.")
