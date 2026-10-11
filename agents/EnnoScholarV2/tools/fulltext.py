from __future__ import annotations

import re
import time
import logging

import httpx
from urllib.parse import urlparse

from ..models import Article, FulltextResult
from .article_reader import IdentityMismatchError, read_fulltext
from .scientific_search import ScientificSearch


logger = logging.getLogger("uvicorn.error")


def valid_fulltext(result: FulltextResult | None) -> bool:
    return bool(result and result.access_level == "fulltext" and result.text.strip())


def unavailable_fulltext(article: Article, *, incomplete=False) -> FulltextResult:
    return FulltextResult(access_level="abstract_only" if article.abstract else "metadata_only",
        text=article.abstract, reason=("Fulltext non récupéré dans le temps disponible; résolution incomplète. " if incomplete else
        "Aucun texte intégral légal récupéré. ") + ("Analyse limitée à l'abstract disponible." if article.abstract else
        "Métadonnées uniquement; aucun abstract disponible."))


def candidate_sources(article: Article, candidate_urls: list[str] | None):
    direct, preprints, public, pages, doi_urls = [], [], [], [], []
    if article.pdf_url:
        direct.append((article.pdf_url, "direct_pdf"))
    if article.arxiv_id:
        preprints.append(("https://arxiv.org/pdf/" + article.arxiv_id, "arxiv"))
    for url in [*(candidate_urls or []), *article.work_urls, article.url]:
        if not url:
            continue
        parsed = urlparse(url)
        arxiv = re.search(r"arxiv\.org/(?:abs|html|pdf)/([^?#]+)", url, re.I)
        if arxiv:
            preprints.append(("https://arxiv.org/pdf/" + arxiv.group(1).removesuffix(".pdf"), "arxiv"))
        elif parsed.hostname in {"doi.org", "dx.doi.org"}:
            doi_urls.append((url, "doi"))
        elif parsed.path.lower().endswith(".pdf") or "/pdf/" in parsed.path.lower():
            direct.append((url, "direct_pdf"))
        elif url != article.url:
            public.append((url, "oa"))  # Known public version candidate; access is still verified.
        else:
            pages.append((url, "article_page"))
    return [*direct, *preprints, *public, *pages], doi_urls


def get_fulltext(article: Article, search: ScientificSearch, candidate_urls: list[str] | None = None, *, deadline: float | None = None) -> FulltextResult:
    started = time.monotonic()
    config = search.config
    deadline = min(deadline, started + config.fulltext_article_timeout) if deadline is not None else started + config.fulltext_article_timeout
    known, doi_urls = candidate_sources(article, candidate_urls)
    attempted = set()
    attempts = 0

    def public_urls():
        nonlocal attempts
        yield from known
        if article.doi and deadline - time.monotonic() > 0.5:
            lookup_started = time.monotonic()
            attempts += 1
            lookup_status = "not_pdf"
            try:
                found_articles = search.lookup_doi(article.doi, deadline=min(deadline, lookup_started + config.fulltext_attempt_timeout))
                lookup_status = "success" if found_articles else "not_pdf"
            except (httpx.TimeoutException, TimeoutError):
                found_articles, lookup_status = [], "timeout"
            except Exception:
                found_articles, lookup_status = [], "http_error"
            logger.info("EnnoScholar V2 fulltext article_id=%s source_candidate=doi stage=metadata_resolution attempt=%s elapsed=%.3f status=%s",
                article.id, attempts, time.monotonic() - lookup_started, lookup_status)
            for found in found_articles:
                yield found.pdf_url, "doi"
                yield found.url, "doi"
            doi_urls.append(("https://doi.org/" + article.doi, "doi"))
        yield from doi_urls

    try:
        for url, source in public_urls():
            if deadline - time.monotonic() <= 0.1:
                break
            if not url or url in attempted:
                continue
            if len(attempted) >= 8:
                break
            attempted.add(url)
            host = (urlparse(url).hostname or "").lower()
            if host.startswith("scholar.google."):
                continue
            attempts += 1
            attempt_started = time.monotonic()
            attempt_deadline = min(deadline, attempt_started + config.fulltext_attempt_timeout)
            remaining = max(0.1, attempt_deadline - attempt_started)
            timeout = httpx.Timeout(connect=min(config.fulltext_connect_timeout, remaining),
                read=min(config.fulltext_read_timeout, remaining), write=min(5, remaining), pool=min(5, remaining))
            status = "not_pdf"
            try:
                result = read_fulltext(url, article, timeout=timeout, deadline=attempt_deadline,
                    headers_timeout=min(config.fulltext_headers_timeout, remaining))
                if valid_fulltext(result):
                    status = "success"
                    return result
            except (httpx.TimeoutException, TimeoutError):
                status = "timeout"
            except httpx.HTTPError:
                status = "http_error"
            except IdentityMismatchError:
                status = "identity_mismatch"
            except Exception:
                # Do not leak text, credential-bearing URLs or source exception messages.
                status = "not_pdf"
            finally:
                logger.info("EnnoScholar V2 fulltext article_id=%s source_candidate=%s attempt=%s elapsed=%.3f status=%s",
                    article.id, source, attempts, time.monotonic() - attempt_started, status)
        return unavailable_fulltext(article, incomplete=deadline - time.monotonic() <= 0.1)
    finally:
        logger.info("EnnoScholar V2 fulltext article_id=%s total_elapsed=%.3f attempts=%s cache_hit=false",
            article.id, time.monotonic() - started, attempts)
