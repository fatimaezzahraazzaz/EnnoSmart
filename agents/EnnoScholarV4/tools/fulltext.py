from __future__ import annotations

import re
import time
from urllib.parse import urlsplit
from agents.EnnoScholarV2.models import Article as LegacyArticle
from agents.EnnoScholarV2.tools.article_reader import matches_article
from .extraction import pdf_chunks, html_document, xml_document
from ..network import fetch_public
from ..pdf_store import save_pdf


def document_urls(article, candidate_urls=None, pdf_only=False):
    explicit = list(candidate_urls or [])
    derived = []
    if article.arxiv_id:
        derived.extend(["https://arxiv.org/html/" + article.arxiv_id, "https://arxiv.org/pdf/" + article.arxiv_id])
    originals = [article.pdf_url, *article.work_urls, article.url]
    for url in [*explicit, *originals]:
        if not url:
            continue
        pmc = re.search(r"(?:pmc/)?articles/(?:PMC)?(\d+)", url, re.I)
        if pmc and (urlsplit(url).hostname or "").endswith("ncbi.nlm.nih.gov"):
            derived.append("https://www.ebi.ac.uk/europepmc/webservices/rest/PMC" + pmc.group(1) + "/fullTextXML")
        bio = re.search(r"(\d{4}\.\d{2}\.\d{2}\.\d+(?:v\d+)?)", url)
        if bio and "biorxiv.org" in (urlsplit(url).hostname or ""):
            derived.append("https://www.biorxiv.org/content/10.1101/" + bio.group(1) + ".full")
    urls = list(dict.fromkeys(u for u in [*explicit, *derived, *originals] if u))
    if pdf_only:
        pdfs = [u for u in urls if ".pdf" in urlsplit(u).path.lower() or "/pdf/" in urlsplit(u).path.lower()
                or any(c.get("url") == u and c.get("format") == "pdf" for c in article.document_candidates)]
        return pdfs or urls
    return urls


def get_fulltext(article, sources, deadline, check=lambda: None, candidate_urls=None, config=None, pdf_only=False):
    from ..config import ScholarV4Config
    config = config or ScholarV4Config.from_env()
    deadline = min(deadline, time.monotonic() + (config.max_pdf_download_seconds if pdf_only else config.max_download_seconds))
    attempts, seen, cooldowns, request_counts = [], set(), {}, {}
    urls = document_urls(article, candidate_urls, pdf_only)
    resolved, downloads = False, 0
    retries = []
    while True:
        for url in urls:
            check()
            if not url or url in seen or downloads >= config.max_download_attempts or time.monotonic() >= deadline:
                continue
            seen.add(url)
            host = urlsplit(url).hostname
            if cooldowns.get(host, 0) > time.monotonic():
                attempts.append({"url": url, "stage": "download", "outcome": "deferred_host_rate_limit"})
                continue
            started = time.monotonic()
            downloads += 1
            request_counts[url] = request_counts.get(url, 0) + 1
            stage = "download"
            try:
                # Large PDFs can require >18 seconds. Keep the total article deadline,
                # reserve time for alternatives when several candidates remain.
                remaining = deadline-time.monotonic()
                allowance = remaining if len(urls) == 1 else max(18, min(70 if pdf_only else 35, remaining-5))
                data, content_type, final_url = fetch_public(url, min(deadline, time.monotonic()+allowance), check, max_bytes=config.max_pdf_bytes)
                stage = "extraction"
                if pdf_only and not data.startswith(b"%PDF"):
                    raise ValueError("RequestedPDFUnavailable")
                source_title = ""
                if data.startswith(b"%PDF"):
                    chunks, truncated = pdf_chunks(data, config)
                    kind = "pdf"
                elif "html" in content_type:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(data, "html.parser")
                    page_title = soup.title.get_text(" ", strip=True).casefold() if soup.title else ""
                    if any(token in page_title for token in ("checking your browser", "verify you are human", "just a moment")):
                        raise ValueError("AccessRestricted")
                    row, chunks, truncated = html_document(data, final_url, config)
                    # A genuine article may load a CAPTCHA widget for its comment form.
                    # Reject actual challenge pages, not incidental script references.
                    if not chunks and any(token in soup.get_text(" ", strip=True).casefold() for token in ("verify you are human", "captcha", "just a moment")):
                        raise ValueError("AccessRestricted")
                    source_title, kind = row.title, "html"
                elif "xml" in content_type:
                    row, chunks, truncated = xml_document(data, final_url, config)
                    source_title, kind = row.title, "jats"
                else:
                    raise ValueError("UnsupportedFormat")
                text = "\n\n".join(chunk.text for chunk in chunks)
                identity_text = source_title + "\n" + (chunks[0].text[:7000] if chunks else "")
                if len(text) < 500 or not matches_article(LegacyArticle(title=article.title), identity_text):
                    raise ValueError("IdentityOrTextUnavailable")
                check()
                stage = "storage"
                filename = save_pdf(data) if kind == "pdf" else ""
                from ..provenance import document_identity
                version = document_identity(data, final_url, kind, chunks)
                attempts.append({"url": url, "final_url": final_url, "stage": "extraction", "outcome": "success",
                    "bytes": len(data), "characters": len(text), "seconds": round(time.monotonic()-started, 3)})
                return {"access_status": "fulltext_extracted", "access_level": "fulltext", "text": text,
                    "url": final_url, "pdf_file": filename, "source_kind": kind,
                    "document_version": version,
                    "chunks": [chunk.model_dump() for chunk in chunks], "truncated": truncated,
                    "attempts": attempts, "reason": "Texte accessible extrait ; lecture et analyse restent à effectuer."}
            except Exception as exc:
                check()
                response = getattr(exc, "response", None)
                status = getattr(response, "status_code", None)
                retry_after = response.headers.get("retry-after") if response is not None else None
                attempts.append({"url": url, "stage": stage, "http_status": status, "error": type(exc).__name__,
                    "reason": str(exc)[:160] if isinstance(exc, (ValueError, TimeoutError)) else "",
                    "retry_after": retry_after, "seconds": round(time.monotonic()-started, 3)})
                if status in {429, 500, 502, 503, 504} and request_counts[url] < config.max_retry_attempts:
                    try:
                        delay = max(1, float(retry_after or 2))
                    except ValueError:
                        delay = 2
                    cooldowns[host] = time.monotonic()+delay
                    retries.append(url)
        if not resolved and article.doi and time.monotonic()+2 < deadline and downloads < config.max_download_attempts:
            resolved = True
            try:
                rows = sources.metadata(article.doi, min(deadline, time.monotonic()+12), check)
                urls = [u for row in rows for u in document_urls(row, pdf_only=pdf_only)]
            except Exception as exc:
                check()
                attempts.append({"stage": "oa_resolution", "error": type(exc).__name__})
                urls = []
            continue
        if retries and downloads < config.max_download_attempts:
            url = retries.pop(0)
            wait = max(0, cooldowns.get(urlsplit(url).hostname, 0)-time.monotonic())
            if time.monotonic()+wait+2 < deadline:
                until = time.monotonic()+wait
                while time.monotonic() < until:
                    check()
                    time.sleep(min(.1, max(0, until-time.monotonic())))
                seen.discard(url)
                urls = [url]
                # At most one delayed retry of a candidate.
                retries = []
                resolved = True
                continue
        break
    blocked = any(a.get("http_status") == 403 or a.get("reason") == "AccessRestricted" for a in attempts)
    extraction_failed = any(a.get("stage") == "extraction" for a in attempts)
    return {"access_status": "access_blocked" if blocked else "extraction_failed" if extraction_failed else "abstract_only" if article.abstract else "metadata_only",
        "access_level": "abstract_only" if article.abstract else "metadata_only", "text": "", "chunks": [], "truncated": False,
        "attempts": attempts, "failure_stage": "extraction" if extraction_failed else "download" if downloads else "discovery",
        "reason": "Aucun texte intégral lisible récupéré ; les notices disponibles restent conservées."}


class FullTextResolver:
    def resolve(self, article, sources, deadline, check=lambda: None, candidate_urls=None, config=None):
        if article.fulltext.get("text"):
            return article.fulltext
        return get_fulltext(article, sources, deadline, check, candidate_urls, config)
