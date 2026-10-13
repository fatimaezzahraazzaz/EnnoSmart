from __future__ import annotations

from urllib.parse import urlparse


_REPOSITORY_HINTS = (
    "repository",
    "repositories",
    "archive",
    "archives",
    "hal.science",
    "arxiv.org",
    "zenodo.org",
    "europepmc.org",
    "pmc.ncbi.nlm.nih.gov",
    "core.ac.uk",
    "ssrn.com",
    "papers.ssrn.com",
    "deliverypdf.ssrn.com",
    "publica.fraunhofer.de",
)

_ACADEMIC_SUFFIXES = (
    ".edu",
    ".edu.au",
    ".ac.uk",
    ".ac.jp",
    ".ac.nz",
    ".ac.za",
    ".edu.cn",
    ".edu.tr",
    ".edu.br",
    ".edu.mx",
    ".edu.tw",
    ".ac.cn",
    ".edu.sg",
)

_BLOCKED_PUBLIC_WEB_DOMAINS = {
    "sci-hub.se",
    "sci-hub.st",
    "sci-hub.ru",
    "sci-hub.wf",
    "sci-hub.ee",
    "sci-hub.shop",
    "sci-hub.ren",
    "libgen.is",
    "libgen.rs",
    "library.lol",
}


def source_domain(url: str | None) -> str | None:
    try:
        host = (urlparse(url or "").hostname or "").lower().strip(".")
        return host or None
    except Exception:
        return None


def is_blocked_fulltext_domain(url: str | None) -> bool:
    host = source_domain(url)
    if not host:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in _BLOCKED_PUBLIC_WEB_DOMAINS)


def classify_public_source(url: str | None, *, license_value: str | None = None) -> tuple[str, str, str | None]:
    host = source_domain(url)
    if license_value:
        if host and any(hint in host for hint in _REPOSITORY_HINTS):
            return "repository_copy", "license_explicit", host
        return "publisher_open_access", "license_explicit", host

    if host and (host.endswith(_ACADEMIC_SUFFIXES) or any(hint in host for hint in _REPOSITORY_HINTS)):
        if "arxiv.org" in host:
            return "preprint", "repository_terms", host
        if any(hint in host for hint in _REPOSITORY_HINTS):
            return "repository_copy", "repository_terms", host
        return "public_author_copy", "publicly_accessible_license_unknown", host

    return "public_pdf", "publicly_accessible_license_unknown", host
