# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Generic publisher full-text discovery for EnnoScholar MCP.

Fonctions couvertes :
1. Ouvre une page éditeur/DOI.
2. Détecte les PDF directs.
3. Analyse les métadonnées citation_pdf_url / DC / JSON-LD.
4. Analyse les liens, boutons, formulaires et attributs data-*.
5. Suit les redirections et valide réellement les PDF.
6. À défaut de PDF, détecte et extrait un article scientifique complet en HTML.
7. Vérifie génériquement l'identité par DOI, titre, auteurs et année.
8. Ne contient aucun article_id, DOI ou domaine éditeur codé en dur.

Dépendances :
    pip install requests beautifulsoup4 lxml

Version :
    1.9.0-generic-html-and-download-discovery
"""

import html as html_lib
import json
import logging
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from hashlib import sha256
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, FeatureNotFound, Tag


LOGGER = logging.getLogger(__name__)

RESOLVER_VERSION = "1.9.1-safe-beautifulsoup-cleanup"

DEFAULT_TIMEOUT = 25.0
DEFAULT_MAX_HTML_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_PDF_PROBE_BYTES = 256 * 1024
DEFAULT_MAX_CANDIDATES = 60
DEFAULT_MIN_HTML_WORDS = 900
DEFAULT_MIN_HTML_SCORE = 7.0
DEFAULT_MIN_TITLE_SIMILARITY = 0.72

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 EnnoSmart/1.9"
)

BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/pdf,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "Connection": "keep-alive",
}


def _parse_html(html_text: str) -> BeautifulSoup:
    """Utilise lxml lorsqu'il existe, sinon le parseur Python standard.

    La découverte légale ne doit pas échouer entièrement parce que le serveur
    MCP a été lancé dans un environnement où l'extension optionnelle ``lxml``
    n'est pas installée.
    """
    try:
        return BeautifulSoup(html_text, "lxml")
    except FeatureNotFound:
        return BeautifulSoup(html_text, "html.parser")

DOWNLOAD_TERMS = {
    "pdf",
    "download",
    "download pdf",
    "download article",
    "article pdf",
    "full text",
    "full-text",
    "view pdf",
    "open pdf",
    "read pdf",
    "get pdf",
    "télécharger",
    "telecharger",
    "texte intégral",
    "texte integral",
}

NEGATIVE_LINK_TERMS = {
    "supplement",
    "supplementary",
    "supporting information",
    "cover",
    "poster",
    "presentation",
    "slides",
    "dataset",
    "data set",
    "appendix only",
    "citation",
    "bibtex",
    "ris",
    "xml",
    "epub",
}

SCIENTIFIC_HEADINGS = {
    "abstract",
    "summary",
    "introduction",
    "background",
    "related work",
    "materials and methods",
    "material and methods",
    "methods",
    "methodology",
    "experimental",
    "experiments",
    "results",
    "discussion",
    "results and discussion",
    "conclusion",
    "conclusions",
    "references",
    "bibliography",
    "acknowledgements",
    "acknowledgments",
}

PAYWALL_MARKERS = {
    "purchase this article",
    "buy this article",
    "rent this article",
    "institutional access",
    "sign in to access",
    "subscribe to access",
    "access through your institution",
    "get access",
    "article purchase",
    "you do not have access",
    "login to view full text",
}

ABSTRACT_ONLY_MARKERS = {
    "abstract only",
    "preview only",
    "show abstract",
}

ANTI_BOT_MARKERS = {
    "captcha",
    "cloudflare",
    "verify you are human",
    "are you a robot",
    "not a robot",
    "access denied",
    "security check",
    "enable javascript and cookies",
}


@dataclass(slots=True)
class ArticleIdentity:
    title: str
    doi: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    known_urls: list[str] = field(default_factory=list)
    article_id: str | int | None = None


@dataclass(slots=True)
class IdentityCheck:
    same_article: bool
    score: float
    method: str
    title_similarity: float = 0.0
    title_token_coverage: float = 0.0
    doi_match: bool = False
    author_overlap: float = 0.0
    year_match: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Candidate:
    url: str
    source_url: str
    discovered_via: str
    label: str = ""
    score: float = 0.0
    method: str = "GET"
    form_data: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class DiscoveryResult:
    ok: bool
    found: bool
    status: str
    content_kind: str | None
    source_url: str | None
    final_url: str | None
    content_type: str | None
    legal_access: bool
    same_article: bool
    identity_score: float
    identity_method: str | None
    verified_pdf: bool
    html_fulltext: bool
    full_text: str | None = None
    title: str | None = None
    doi: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    word_count: int = 0
    html_score: float = 0.0
    discovered_via: str | None = None
    source_domain: str | None = None
    response_bytes_sha256: str | None = None
    candidates_count: int = 0
    landing_only_count: int = 0
    verified_count: int = 0
    warnings: list[str] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_text(value: Any, max_chars: int = 0) -> str:
    text = "" if value is None else str(value)
    text = html_lib.unescape(text)
    text = text.replace("\x00", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if max_chars and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _norm(value: Any) -> str:
    text = _strip_accents(_safe_text(value).lower())
    text = re.sub(r"https?://(?:dx\.)?doi\.org/", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_doi(value: Any) -> str:
    text = _safe_text(value).lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi\s*:\s*", "", text)
    match = re.search(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", text, flags=re.I)
    return match.group(0).rstrip(".,;:)") if match else ""


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().split("@")[-1]
    except Exception:
        return ""


def _dedupe(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = _safe_text(item, 5000)
        if not value:
            continue
        key = value.rstrip("/")
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _token_set(value: str) -> set[str]:
    stop = {
        "a", "an", "the", "of", "for", "and", "or", "on", "in", "to", "with",
        "using", "based", "from", "via", "by", "new", "method", "study", "analysis",
    }
    return {x for x in _norm(value).split() if len(x) > 1 and x not in stop}


def _title_metrics(expected: str, candidate: str) -> tuple[float, float]:
    a = _norm(expected)
    b = _norm(candidate)
    if not a or not b:
        return 0.0, 0.0
    similarity = SequenceMatcher(None, a, b).ratio()
    expected_tokens = _token_set(a)
    candidate_tokens = _token_set(b)
    coverage = (
        len(expected_tokens & candidate_tokens) / max(1, len(expected_tokens))
    )
    return similarity, coverage


def _author_surnames(authors: Iterable[str]) -> set[str]:
    surnames: set[str] = set()
    for author in authors:
        parts = _norm(author).split()
        if parts:
            surname = parts[-1]
            if len(surname) >= 3:
                surnames.add(surname)
    return surnames


def _identity_check(
    expected: ArticleIdentity,
    *,
    candidate_title: str = "",
    candidate_doi: str = "",
    candidate_authors: Iterable[str] = (),
    candidate_year: int | None = None,
    document_text: str = "",
) -> IdentityCheck:
    expected_doi = _normalize_doi(expected.doi)
    found_doi = _normalize_doi(candidate_doi)

    text_head = _safe_text(document_text[:30000])
    norm_head = _norm(text_head)

    doi_match = bool(expected_doi and (
        expected_doi == found_doi or expected_doi in text_head.lower()
    ))

    if expected_doi and found_doi and expected_doi != found_doi:
        return IdentityCheck(
            same_article=False,
            score=0.0,
            method="doi_mismatch",
            doi_match=False,
            reasons=["Le DOI candidat est différent du DOI attendu."],
        )

    similarity, coverage = _title_metrics(expected.title, candidate_title)
    if not candidate_title and expected.title:
        title_probe = _safe_text(text_head[:5000])
        expected_norm = _norm(expected.title)
        coverage = (
            len(_token_set(expected.title) & _token_set(title_probe))
            / max(1, len(_token_set(expected.title)))
        )
        similarity = 0.0
        if expected_norm and expected_norm in _norm(title_probe):
            similarity = 1.0
            coverage = 1.0

    expected_surnames = _author_surnames(expected.authors)
    candidate_surnames = _author_surnames(candidate_authors)
    if not candidate_surnames and expected_surnames:
        candidate_surnames = {x for x in expected_surnames if x in norm_head}
    author_overlap = (
        len(expected_surnames & candidate_surnames) / max(1, len(expected_surnames))
        if expected_surnames else 0.0
    )

    year_match = bool(
        expected.year
        and (
            candidate_year == expected.year
            or str(expected.year) in text_head[:12000]
        )
    )

    if doi_match:
        return IdentityCheck(
            same_article=True,
            score=1.0,
            method="same_doi",
            title_similarity=similarity,
            title_token_coverage=coverage,
            doi_match=True,
            author_overlap=author_overlap,
            year_match=year_match,
        )

    score = (
        0.55 * max(similarity, coverage)
        + 0.25 * author_overlap
        + 0.10 * float(year_match)
    )
    if similarity >= 0.90 or coverage >= 0.90:
        score += 0.10

    same = bool(
        (similarity >= 0.78 and coverage >= 0.72)
        or (coverage >= 0.86 and (author_overlap >= 0.20 or year_match))
        or (similarity >= 0.72 and author_overlap >= 0.50)
    )

    reasons: list[str] = []
    if not same:
        reasons.append("L'identité de l'article n'est pas suffisamment confirmée.")

    return IdentityCheck(
        same_article=same,
        score=round(min(0.99, score), 6),
        method="metadata_match" if same else "title_mismatch",
        title_similarity=round(similarity, 6),
        title_token_coverage=round(coverage, 6),
        doi_match=False,
        author_overlap=round(author_overlap, 6),
        year_match=year_match,
        reasons=reasons,
    )


def _make_session(session: requests.Session | None = None) -> requests.Session:
    sess = session or requests.Session()
    sess.headers.update(BASE_HEADERS)
    return sess


def _is_pdf(content_type: str, content: bytes, url: str = "") -> bool:
    ctype = (content_type or "").lower()
    return (
        content.startswith(b"%PDF-")
        or "application/pdf" in ctype
        or (
            urlparse(url).path.lower().endswith(".pdf")
            and not content.lstrip().lower().startswith(b"<!doctype html")
            and not content.lstrip().lower().startswith(b"<html")
        )
    )


def _is_html(content_type: str, content: bytes) -> bool:
    ctype = (content_type or "").lower()
    head = content[:500].lstrip().lower()
    return (
        "text/html" in ctype
        or "application/xhtml+xml" in ctype
        or head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
    )


def _decode_response(response: requests.Response, max_bytes: int) -> str:
    raw = response.content[:max_bytes]
    encoding = response.encoding or response.apparent_encoding or "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _meta_content(soup: BeautifulSoup, names: Iterable[str]) -> str:
    lowered = {x.lower() for x in names}
    for meta in soup.find_all("meta"):
        key = _safe_text(meta.get("name") or meta.get("property")).lower()
        if key in lowered:
            content = _safe_text(meta.get("content"), 10000)
            if content:
                return content
    return ""


def _all_meta_contents(soup: BeautifulSoup, names: Iterable[str]) -> list[str]:
    lowered = {x.lower() for x in names}
    values: list[str] = []
    for meta in soup.find_all("meta"):
        key = _safe_text(meta.get("name") or meta.get("property")).lower()
        if key in lowered:
            content = _safe_text(meta.get("content"), 10000)
            if content:
                values.append(content)
    return _dedupe(values)


def _extract_json_ld(soup: BeautifulSoup) -> list[Any]:
    objects: list[Any] = []
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = script.string or script.get_text(" ", strip=True)
        raw = _safe_text(raw, 2_000_000)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, list):
            objects.extend(data)
        else:
            objects.append(data)
    return objects


def _walk_json(value: Any, path: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield child_path, child
            yield from _walk_json(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            yield child_path, child
            yield from _walk_json(child, child_path)


def _candidate_score(url: str, label: str, discovered_via: str) -> float:
    path = unquote(urlparse(url).path).lower()
    query = unquote(urlparse(url).query).lower()
    text = _norm(f"{label} {path} {query} {discovered_via}")

    score = 0.0
    if path.endswith(".pdf"):
        score += 10.0
    if "pdf" in text:
        score += 5.0
    if any(term in text for term in DOWNLOAD_TERMS):
        score += 4.0
    if "citation pdf url" in _norm(discovered_via):
        score += 8.0
    if "json ld" in _norm(discovered_via):
        score += 3.0
    if any(term in text for term in NEGATIVE_LINK_TERMS):
        score -= 10.0
    if url.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
        score -= 100.0
    return score


def _add_candidate(
    candidates: list[Candidate],
    *,
    base_url: str,
    raw_url: Any,
    source_url: str,
    discovered_via: str,
    label: str = "",
    method: str = "GET",
    form_data: dict[str, str] | None = None,
) -> None:
    raw = _safe_text(raw_url, 10000)
    if not raw:
        return
    raw = html_lib.unescape(raw).strip("\"' ")
    if raw.startswith(("javascript:", "mailto:", "tel:", "data:")):
        match = re.search(r"""['"]((?:https?:)?//[^'"]+|/[^'"]+)['"]""", raw)
        if not match:
            return
        raw = match.group(1)

    url = urljoin(base_url, raw)
    if urlparse(url).scheme not in {"http", "https"}:
        return

    score = _candidate_score(url, label, discovered_via)
    if score < -20:
        return

    candidates.append(
        Candidate(
            url=url,
            source_url=source_url,
            discovered_via=discovered_via,
            label=_safe_text(label, 500),
            score=score,
            method=method.upper(),
            form_data=form_data or {},
        )
    )


def discover_download_candidates(
    html_text: str,
    page_url: str,
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> list[Candidate]:
    soup = _parse_html(html_text)
    candidates: list[Candidate] = []

    # 1. Métadonnées standard.
    meta_names = {
        "citation_pdf_url",
        "dc.identifier",
        "dc.identifier.uri",
        "eprints.document_url",
        "pdf_url",
        "wkhealth_pdf_url",
        "bepress_citation_pdf_url",
    }
    for meta in soup.find_all("meta"):
        key = _safe_text(meta.get("name") or meta.get("property")).lower()
        content = _safe_text(meta.get("content"), 10000)
        if not content:
            continue
        if key in meta_names or ("pdf" in key and content.startswith(("http", "/"))):
            _add_candidate(
                candidates,
                base_url=page_url,
                raw_url=content,
                source_url=page_url,
                discovered_via=f"meta:{key}",
                label=key,
            )

    # 2. Canonical/alternate/item links.
    for link in soup.find_all("link"):
        href = link.get("href")
        rel = " ".join(link.get("rel") or [])
        link_type = _safe_text(link.get("type")).lower()
        title = _safe_text(link.get("title"))
        marker = _norm(f"{rel} {link_type} {title} {href}")
        if "pdf" in marker or "application pdf" in marker:
            _add_candidate(
                candidates,
                base_url=page_url,
                raw_url=href,
                source_url=page_url,
                discovered_via="link_tag",
                label=f"{rel} {title}",
            )

    # 3. JSON-LD générique.
    for obj in _extract_json_ld(soup):
        for path, value in _walk_json(obj):
            if not isinstance(value, str):
                continue
            key = _norm(path)
            value_norm = _norm(value)
            if (
                "pdf" in key
                or key.endswith("contenturl")
                or key.endswith("downloadurl")
                or "encoding" in key
                or value.lower().split("?")[0].endswith(".pdf")
                or "application pdf" in value_norm
            ):
                _add_candidate(
                    candidates,
                    base_url=page_url,
                    raw_url=value,
                    source_url=page_url,
                    discovered_via=f"json_ld:{path}",
                    label=path,
                )

    # 4. Liens HTML.
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        label = _safe_text(anchor.get_text(" ", strip=True), 500)
        title = _safe_text(anchor.get("title"), 500)
        aria = _safe_text(anchor.get("aria-label"), 500)
        download = _safe_text(anchor.get("download"), 500)
        classes = " ".join(anchor.get("class") or [])
        identifier = _safe_text(anchor.get("id"), 500)
        marker = _norm(
            f"{label} {title} {aria} {download} {classes} {identifier} {href}"
        )

        if (
            href
            and (
                "pdf" in marker
                or any(term in marker for term in DOWNLOAD_TERMS)
            )
            and not any(term in marker for term in NEGATIVE_LINK_TERMS)
        ):
            _add_candidate(
                candidates,
                base_url=page_url,
                raw_url=href,
                source_url=page_url,
                discovered_via="anchor",
                label=f"{label} {title} {aria}",
            )

        for attr in (
            "data-url",
            "data-href",
            "data-download",
            "data-download-url",
            "data-pdf",
            "data-pdf-url",
            "data-file",
            "data-file-url",
            "data-target-url",
        ):
            value = anchor.get(attr)
            if value and (
                "pdf" in _norm(value)
                or any(term in marker for term in DOWNLOAD_TERMS)
            ):
                _add_candidate(
                    candidates,
                    base_url=page_url,
                    raw_url=value,
                    source_url=page_url,
                    discovered_via=f"anchor:{attr}",
                    label=f"{label} {title} {aria}",
                )

    # 5. Boutons et contrôles.
    for element in soup.find_all(["button", "input"]):
        element_type = _safe_text(element.get("type")).lower()
        label = _safe_text(
            element.get_text(" ", strip=True)
            or element.get("value")
            or element.get("aria-label")
            or element.get("title"),
            500,
        )
        classes = " ".join(element.get("class") or [])
        marker = _norm(f"{label} {classes} {element.get('id')}")
        if not (
            any(term in marker for term in DOWNLOAD_TERMS)
            or "pdf" in marker
        ):
            continue

        for attr in (
            "href",
            "formaction",
            "data-url",
            "data-href",
            "data-download",
            "data-download-url",
            "data-pdf",
            "data-pdf-url",
            "data-file",
            "data-file-url",
            "onclick",
        ):
            value = element.get(attr)
            if value:
                _add_candidate(
                    candidates,
                    base_url=page_url,
                    raw_url=value,
                    source_url=page_url,
                    discovered_via=f"button:{attr}",
                    label=label,
                )

        parent_form = element.find_parent("form")
        if isinstance(parent_form, Tag):
            _add_form_candidate(candidates, parent_form, page_url, label)

    # 6. Formulaires pouvant lancer un téléchargement.
    for form in soup.find_all("form"):
        marker = _norm(
            f"{form.get('action')} {form.get('id')} "
            f"{' '.join(form.get('class') or [])} "
            f"{form.get_text(' ', strip=True)}"
        )
        if "pdf" in marker or any(term in marker for term in DOWNLOAD_TERMS):
            _add_form_candidate(candidates, form, page_url, marker)

    # 7. Scripts et attributs onclick : extraire uniquement des URL plausibles.
    script_text = "\n".join(
        script.get_text(" ", strip=True)
        for script in soup.find_all("script")
        if script.get_text(" ", strip=True)
    )
    patterns = [
        r"""(?i)(?:pdfUrl|pdf_url|downloadUrl|download_url|fileUrl|file_url)\s*[:=]\s*['"]([^'"]+)['"]""",
        r"""(?i)['"]((?:https?:)?//[^'"]+?\.pdf(?:\?[^'"]*)?|/[^'"]+?\.pdf(?:\?[^'"]*)?)['"]""",
        r"""(?i)(?:window\.open|location(?:\.href)?\s*=)\s*\(\s*['"]([^'"]+)['"]""",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, script_text):
            _add_candidate(
                candidates,
                base_url=page_url,
                raw_url=match.group(1),
                source_url=page_url,
                discovered_via="embedded_script",
                label="script PDF/download URL",
            )

    # 8. URL encodées dans des paramètres.
    expanded: list[Candidate] = list(candidates)
    for candidate in candidates:
        query = parse_qs(urlparse(candidate.url).query)
        for key, values in query.items():
            if _norm(key) in {
                "url", "uri", "file", "pdf", "download", "redirect",
                "target", "resource", "document",
            }:
                for value in values:
                    decoded = unquote(value)
                    if decoded.startswith(("http://", "https://", "/")):
                        _add_candidate(
                            expanded,
                            base_url=candidate.url,
                            raw_url=decoded,
                            source_url=page_url,
                            discovered_via=f"query_parameter:{key}",
                            label=candidate.label,
                        )

    # Dédoublonnage en gardant le meilleur score.
    best: dict[tuple[str, str, str], Candidate] = {}
    for candidate in expanded:
        key = (
            candidate.method,
            candidate.url.rstrip("/"),
            json.dumps(candidate.form_data, sort_keys=True),
        )
        previous = best.get(key)
        if previous is None or candidate.score > previous.score:
            best[key] = candidate

    return sorted(best.values(), key=lambda x: x.score, reverse=True)[:max_candidates]


def _add_form_candidate(
    candidates: list[Candidate],
    form: Tag,
    page_url: str,
    label: str,
) -> None:
    action = form.get("action") or page_url
    method = _safe_text(form.get("method") or "GET").upper()
    data: dict[str, str] = {}
    for field in form.find_all(["input", "button", "select", "textarea"]):
        name = _safe_text(field.get("name"), 500)
        if not name:
            continue
        if field.name == "select":
            selected = field.find("option", selected=True) or field.find("option")
            value = _safe_text(selected.get("value") if selected else "", 5000)
        elif field.name == "textarea":
            value = _safe_text(field.get_text(" ", strip=True), 5000)
        else:
            value = _safe_text(field.get("value"), 5000)
        field_type = _safe_text(field.get("type")).lower()
        if field_type in {"checkbox", "radio"} and not field.has_attr("checked"):
            continue
        data[name] = value

    _add_candidate(
        candidates,
        base_url=page_url,
        raw_url=action,
        source_url=page_url,
        discovered_via="download_form",
        label=label,
        method=method,
        form_data=data,
    )


def _extract_page_metadata(
    soup: BeautifulSoup,
    page_url: str,
) -> dict[str, Any]:
    title = (
        _meta_content(
            soup,
            [
                "citation_title",
                "dc.title",
                "dcterms.title",
                "og:title",
                "twitter:title",
            ],
        )
        or _safe_text(soup.title.get_text(" ", strip=True) if soup.title else "", 1000)
    )

    doi = _meta_content(
        soup,
        [
            "citation_doi",
            "dc.identifier",
            "dc.identifier.doi",
            "prism.doi",
            "bepress_citation_doi",
        ],
    )
    doi = _normalize_doi(doi)

    authors = _all_meta_contents(
        soup,
        [
            "citation_author",
            "dc.creator",
            "dcterms.creator",
            "author",
        ],
    )

    date_value = _meta_content(
        soup,
        [
            "citation_publication_date",
            "citation_date",
            "dc.date",
            "prism.publicationdate",
            "article:published_time",
        ],
    )
    year_match = re.search(r"\b(19|20)\d{2}\b", date_value)
    year = int(year_match.group(0)) if year_match else None

    canonical = ""
    canonical_tag = soup.find("link", rel=lambda value: value and "canonical" in value)
    if canonical_tag:
        canonical = urljoin(page_url, _safe_text(canonical_tag.get("href")))

    return {
        "title": title,
        "doi": doi,
        "authors": authors,
        "year": year,
        "canonical_url": canonical,
    }


def _tag_attr(tag: Tag | None, name: str, default: Any = None) -> Any:
    """
    Lecture défensive des attributs BeautifulSoup.

    Après ``decompose()``, BeautifulSoup peut conserver temporairement dans une
    liste des descendants dont ``attrs`` vaut désormais None. Appeler
    directement ``tag.get(...)`` sur ces objets provoque alors :
    AttributeError: 'NoneType' object has no attribute 'get'.
    """
    if tag is None:
        return default
    attrs = getattr(tag, "attrs", None)
    if not isinstance(attrs, dict):
        return default
    value = attrs.get(name, default)
    return default if value is None else value


def _remove_noise(soup: BeautifulSoup) -> None:
    removable_names = {
        "script", "style", "noscript", "svg", "canvas", "iframe",
        "nav", "footer", "header", "aside",
    }

    # Une seule passe sur un instantané. On traite les nœuds les plus profonds
    # d'abord afin de ne pas invalider leurs descendants avant lecture.
    tags = list(soup.find_all(True))
    tags.sort(
        key=lambda tag: len(list(getattr(tag, "parents", []))),
        reverse=True,
    )

    noise_pattern = re.compile(
        r"(cookie|consent|breadcrumb|toolbar|social|share|related|recommend|"
        r"advert|banner|navigation|sidebar|metrics|citation-download|"
        r"references-export|author-information|supplementary)",
        re.I,
    )

    for tag in tags:
        # Le tag peut déjà avoir été détruit indirectement.
        if not isinstance(getattr(tag, "attrs", None), dict):
            continue

        tag_name = _safe_text(getattr(tag, "name", "")).lower()
        classes = _tag_attr(tag, "class", [])
        if isinstance(classes, str):
            classes_text = classes
        elif isinstance(classes, (list, tuple, set)):
            classes_text = " ".join(_safe_text(x) for x in classes)
        else:
            classes_text = _safe_text(classes)

        marker = " ".join(
            [
                _safe_text(_tag_attr(tag, "id", "")),
                classes_text,
                _safe_text(_tag_attr(tag, "role", "")),
            ]
        )

        if tag_name in removable_names or noise_pattern.search(marker):
            try:
                tag.decompose()
            except (AttributeError, TypeError):
                # Un autre parent a pu disparaître entre-temps. Le contenu est
                # déjà inutilisable et ne doit pas bloquer toute la résolution.
                continue


def _heading_names(container: Tag) -> list[str]:
    headings: list[str] = []
    for heading in container.find_all(["h1", "h2", "h3", "h4", "strong"]):
        text = _norm(heading.get_text(" ", strip=True))
        if 1 <= len(text.split()) <= 10:
            headings.append(text)
    return headings


def _select_article_container(soup: BeautifulSoup) -> Tag:
    selectors = [
        "article",
        "[itemprop='articleBody']",
        ".article-body",
        ".article__body",
        ".fulltext",
        ".full-text",
        ".article-content",
        ".articleContent",
        ".body",
        "main",
    ]
    candidates: list[Tag] = []
    for selector in selectors:
        candidates.extend(soup.select(selector))

    if not candidates:
        body = soup.body or soup
        return body

    def score(tag: Tag) -> tuple[int, int]:
        text = _safe_text(tag.get_text(" ", strip=True))
        headings = _heading_names(tag)
        scientific = sum(
            1
            for heading in headings
            if any(name in heading for name in SCIENTIFIC_HEADINGS)
        )
        return scientific, len(text)

    return max(candidates, key=score)


def extract_html_fulltext(
    html_text: str,
    page_url: str,
    expected: ArticleIdentity,
) -> dict[str, Any]:
    soup = _parse_html(html_text)
    metadata = _extract_page_metadata(soup, page_url)

    clean_soup = _parse_html(html_text)
    _remove_noise(clean_soup)
    container = _select_article_container(clean_soup)

    paragraphs: list[str] = []
    for element in container.find_all(
        ["h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote"]
    ):
        text = _safe_text(element.get_text(" ", strip=True), 10000)
        if len(text) < 20:
            continue
        if paragraphs and text == paragraphs[-1]:
            continue
        paragraphs.append(text)

    full_text = "\n\n".join(paragraphs)
    word_count = len(re.findall(r"\b\w+\b", full_text, flags=re.UNICODE))
    headings = _heading_names(container)
    scientific_headings = sorted({
        heading
        for heading in headings
        if any(name in heading for name in SCIENTIFIC_HEADINGS)
    })

    lower_text = full_text.lower()
    page_lower = _safe_text(
        _parse_html(html_text).get_text(" ", strip=True)
    ).lower()

    paywall_hits = sorted(marker for marker in PAYWALL_MARKERS if marker in page_lower)
    abstract_only_hits = sorted(
        marker for marker in ABSTRACT_ONLY_MARKERS if marker in page_lower
    )

    identity = _identity_check(
        expected,
        candidate_title=metadata["title"],
        candidate_doi=metadata["doi"],
        candidate_authors=metadata["authors"],
        candidate_year=metadata["year"],
        document_text=full_text,
    )

    score = 0.0
    score += min(4.0, word_count / 700.0)
    score += min(4.0, len(scientific_headings) * 0.8)
    if "references" in " ".join(scientific_headings):
        score += 1.0
    if "introduction" in " ".join(scientific_headings):
        score += 1.0
    if any(
        key in " ".join(scientific_headings)
        for key in ("methods", "methodology", "experimental", "results", "discussion")
    ):
        score += 1.5
    if identity.same_article:
        score += 2.0
    if identity.doi_match:
        score += 1.0
    if paywall_hits and word_count < 1800:
        score -= 4.0
    if abstract_only_hits:
        score -= 3.0

    has_fulltext = bool(
        identity.same_article
        and word_count >= DEFAULT_MIN_HTML_WORDS
        and len(scientific_headings) >= 3
        and score >= DEFAULT_MIN_HTML_SCORE
        and not (paywall_hits and word_count < 1800)
    )

    return {
        "has_fulltext": has_fulltext,
        "full_text": full_text if has_fulltext else None,
        "word_count": word_count,
        "html_score": round(score, 3),
        "scientific_headings": scientific_headings,
        "paywall_markers": paywall_hits,
        "abstract_only_markers": abstract_only_hits,
        "metadata": metadata,
        "identity": identity,
    }


def _request_candidate(
    session: requests.Session,
    candidate: Candidate,
    *,
    timeout: float,
) -> requests.Response:
    headers = {
        "Referer": candidate.source_url,
        "Accept": "application/pdf,text/html,application/xhtml+xml,*/*;q=0.8",
    }
    if candidate.method == "POST":
        return session.post(
            candidate.url,
            data=candidate.form_data,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        )

    if candidate.form_data:
        return session.get(
            candidate.url,
            params=candidate.form_data,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        )

    return session.get(
        candidate.url,
        headers=headers,
        timeout=timeout,
        allow_redirects=True,
        stream=True,
    )


def _read_probe(
    response: requests.Response,
    max_bytes: int = DEFAULT_MAX_PDF_PROBE_BYTES,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        remaining = max_bytes - total
        chunks.append(chunk[:remaining])
        total += len(chunks[-1])
        if total >= max_bytes:
            break
    return b"".join(chunks)


def resolve_publisher_fulltext(
    article: ArticleIdentity | dict[str, Any],
    *,
    session: requests.Session | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_html_bytes: int = DEFAULT_MAX_HTML_BYTES,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> dict[str, Any]:
    """
    Résout le texte intégral depuis les URLs connues.

    Retourne :
    - legal_pdf_found si un PDF public vérifié est trouvé ;
    - legal_html_fulltext_found si le texte intégral est disponible en HTML ;
    - no_verified_publisher_fulltext sinon.
    """
    if isinstance(article, dict):
        article = ArticleIdentity(
            title=_safe_text(article.get("title"), 2000),
            doi=_normalize_doi(article.get("doi")) or None,
            authors=[
                _safe_text(x, 500)
                for x in (article.get("authors") or [])
                if _safe_text(x)
            ],
            year=article.get("year"),
            known_urls=[
                _safe_text(x, 5000)
                for x in (article.get("known_urls") or [])
                if _safe_text(x)
            ],
            article_id=article.get("article_id"),
        )

    if not article.title:
        return DiscoveryResult(
            ok=False,
            found=False,
            status="invalid_article",
            content_kind=None,
            source_url=None,
            final_url=None,
            content_type=None,
            legal_access=False,
            same_article=False,
            identity_score=0.0,
            identity_method=None,
            verified_pdf=False,
            html_fulltext=False,
            warnings=["Le titre est obligatoire."],
        ).to_dict()

    urls = list(article.known_urls)
    if article.doi:
        urls.insert(0, f"https://doi.org/{_normalize_doi(article.doi)}")
    urls = _dedupe(urls)

    if not urls:
        return DiscoveryResult(
            ok=True,
            found=False,
            status="no_known_url",
            content_kind=None,
            source_url=None,
            final_url=None,
            content_type=None,
            legal_access=False,
            same_article=False,
            identity_score=0.0,
            identity_method=None,
            verified_pdf=False,
            html_fulltext=False,
        ).to_dict()

    sess = _make_session(session)
    attempts: list[dict[str, Any]] = []
    all_candidates: list[Candidate] = []
    landing_only_count = 0
    html_fallbacks: list[dict[str, Any]] = []

    for initial_url in urls:
        started = time.monotonic()
        try:
            response = sess.get(
                initial_url,
                timeout=timeout,
                allow_redirects=True,
                headers={"Referer": initial_url},
            )
            content = response.content[:max_html_bytes]
            content_type = response.headers.get("Content-Type", "")
            final_url = response.url

            attempts.append({
                "stage": "open_known_url",
                "url": initial_url,
                "final_url": final_url,
                "http_status": response.status_code,
                "content_type": content_type,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            })

            if response.status_code >= 400:
                continue

            # Cas URL connue directement PDF.
            if _is_pdf(content_type, content, final_url):
                identity = _identity_check(
                    article,
                    candidate_title="",
                    candidate_doi=article.doi or "",
                    document_text="",
                )
                if identity.same_article or bool(article.doi):
                    return DiscoveryResult(
                        ok=True,
                        found=True,
                        status="legal_pdf_found",
                        content_kind="pdf",
                        source_url=initial_url,
                        final_url=final_url,
                        content_type=content_type or "application/pdf",
                        legal_access=True,
                        same_article=True,
                        identity_score=max(identity.score, 0.95),
                        identity_method=identity.method,
                        verified_pdf=True,
                        html_fulltext=False,
                        discovered_via="known_url_direct_pdf",
                        source_domain=_domain(final_url),
                        response_bytes_sha256=sha256(content).hexdigest(),
                        candidates_count=1,
                        verified_count=1,
                        attempts=attempts,
                    ).to_dict()

            if not _is_html(content_type, content):
                continue

            html_text = _decode_response(response, max_html_bytes)
            page_text = _safe_text(
                _parse_html(html_text).get_text(" ", strip=True),
                10000,
            ).lower()
            if any(marker in page_text for marker in ANTI_BOT_MARKERS):
                attempts.append({
                    "stage": "publisher_page",
                    "url": final_url,
                    "status": "access_blocked_or_antibot",
                })
                continue

            candidates = discover_download_candidates(
                html_text,
                final_url,
                max_candidates=max_candidates,
            )
            all_candidates.extend(candidates)

            html_result = extract_html_fulltext(html_text, final_url, article)
            html_fallbacks.append({
                "source_url": initial_url,
                "final_url": final_url,
                "content_type": content_type,
                "result": html_result,
            })

            if not candidates:
                landing_only_count += 1

        except requests.RequestException as exc:
            attempts.append({
                "stage": "open_known_url",
                "url": initial_url,
                "status": "request_error",
                "error": _safe_text(exc, 1000),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            })

    # Dédoublonner tous les candidats issus de toutes les pages.
    candidate_map: dict[tuple[str, str, str], Candidate] = {}
    for candidate in all_candidates:
        key = (
            candidate.method,
            candidate.url.rstrip("/"),
            json.dumps(candidate.form_data, sort_keys=True),
        )
        previous = candidate_map.get(key)
        if previous is None or candidate.score > previous.score:
            candidate_map[key] = candidate

    candidates = sorted(
        candidate_map.values(),
        key=lambda item: item.score,
        reverse=True,
    )[:max_candidates]

    # Tester les candidats PDF/download.
    for candidate in candidates:
        started = time.monotonic()
        try:
            response = _request_candidate(sess, candidate, timeout=timeout)
            probe = _read_probe(response)
            content_type = response.headers.get("Content-Type", "")
            final_url = response.url

            attempt = {
                "stage": "probe_download_candidate",
                "url": candidate.url,
                "final_url": final_url,
                "method": candidate.method,
                "discovered_via": candidate.discovered_via,
                "label": candidate.label,
                "candidate_score": candidate.score,
                "http_status": response.status_code,
                "content_type": content_type,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }

            if response.status_code >= 400:
                attempt["status"] = "http_error"
                attempts.append(attempt)
                continue

            if not _is_pdf(content_type, probe, final_url):
                attempt["status"] = "not_pdf"
                attempts.append(attempt)
                continue

            # La page source porte l'identité. Le PDF sera ensuite vérifié une
            # seconde fois par l'extracteur PDF principal du MCP.
            source_html = next(
                (
                    x["result"]
                    for x in html_fallbacks
                    if x["final_url"] == candidate.source_url
                    or x["source_url"] == candidate.source_url
                ),
                None,
            )
            if source_html:
                identity = source_html["identity"]
            else:
                identity = _identity_check(
                    article,
                    candidate_doi=article.doi or "",
                )

            if not identity.same_article:
                attempt["status"] = "identity_rejected"
                attempt["identity"] = asdict(identity)
                attempts.append(attempt)
                continue

            attempt["status"] = "verified_pdf"
            attempts.append(attempt)

            return DiscoveryResult(
                ok=True,
                found=True,
                status="legal_pdf_found",
                content_kind="pdf",
                source_url=candidate.url,
                final_url=final_url,
                content_type=content_type or "application/pdf",
                legal_access=True,
                same_article=True,
                identity_score=identity.score,
                identity_method=identity.method,
                verified_pdf=True,
                html_fulltext=False,
                title=article.title,
                doi=_normalize_doi(article.doi) or None,
                authors=article.authors,
                year=article.year,
                discovered_via=candidate.discovered_via,
                source_domain=_domain(final_url),
                response_bytes_sha256=sha256(probe).hexdigest(),
                candidates_count=len(candidates),
                landing_only_count=landing_only_count,
                verified_count=1,
                attempts=attempts,
            ).to_dict()

        except requests.RequestException as exc:
            attempts.append({
                "stage": "probe_download_candidate",
                "url": candidate.url,
                "method": candidate.method,
                "discovered_via": candidate.discovered_via,
                "status": "request_error",
                "error": _safe_text(exc, 1000),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            })

    # Aucun PDF : accepter un texte intégral HTML vérifié.
    valid_html = [
        item
        for item in html_fallbacks
        if item["result"]["has_fulltext"]
    ]
    if valid_html:
        best = max(
            valid_html,
            key=lambda item: (
                item["result"]["html_score"],
                item["result"]["word_count"],
            ),
        )
        html_result = best["result"]
        metadata = html_result["metadata"]
        identity = html_result["identity"]
        text = html_result["full_text"] or ""

        return DiscoveryResult(
            ok=True,
            found=True,
            status="legal_html_fulltext_found",
            content_kind="html",
            source_url=best["source_url"],
            final_url=best["final_url"],
            content_type=best["content_type"] or "text/html",
            legal_access=True,
            same_article=True,
            identity_score=identity.score,
            identity_method=identity.method,
            verified_pdf=False,
            html_fulltext=True,
            full_text=text,
            title=metadata["title"] or article.title,
            doi=metadata["doi"] or _normalize_doi(article.doi) or None,
            authors=metadata["authors"] or article.authors,
            year=metadata["year"] or article.year,
            word_count=html_result["word_count"],
            html_score=html_result["html_score"],
            discovered_via="publisher_html_fulltext",
            source_domain=_domain(best["final_url"]),
            response_bytes_sha256=sha256(
                text.encode("utf-8", errors="ignore")
            ).hexdigest(),
            candidates_count=len(candidates),
            landing_only_count=landing_only_count,
            verified_count=1,
            warnings=[
                "Texte intégral extrait depuis la page HTML ; aucun PDF requis."
            ],
            attempts=attempts,
        ).to_dict()

    return DiscoveryResult(
        ok=True,
        found=False,
        status="no_verified_publisher_fulltext",
        content_kind=None,
        source_url=None,
        final_url=None,
        content_type=None,
        legal_access=False,
        same_article=False,
        identity_score=0.0,
        identity_method=None,
        verified_pdf=False,
        html_fulltext=False,
        candidates_count=len(candidates),
        landing_only_count=landing_only_count,
        verified_count=0,
        warnings=[
            "Aucun PDF public vérifié ni texte intégral HTML vérifié n'a été trouvé."
        ],
        attempts=attempts,
    ).to_dict()


def to_mcp_location(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Convertit le résultat du module au format d'une location du resolver MCP.
    """
    if not isinstance(result, dict) or not result.get("found"):
        return None

    content_kind = result.get("content_kind")
    is_pdf = content_kind == "pdf"

    return {
        "provider": "known_url_publisher_discovery",
        "pdf_url": result.get("final_url") if is_pdf else None,
        "landing_url": (
            result.get("source_url")
            if is_pdf
            else result.get("final_url")
        ),
        "fulltext_url": result.get("final_url"),
        "legal_access": True,
        "license": None,
        "access_type": (
            "publisher_open_access"
            if is_pdf
            else "publisher_html_fulltext"
        ),
        "rights_status": "publicly_accessible_license_unknown",
        "same_article": bool(result.get("same_article")),
        "identity_score": float(result.get("identity_score") or 0.0),
        "identity_method": result.get("identity_method"),
        "verified_pdf": bool(result.get("verified_pdf")),
        "verified_html_fulltext": bool(result.get("html_fulltext")),
        "content_kind": content_kind,
        "full_text": result.get("full_text") if not is_pdf else None,
        "word_count": int(result.get("word_count") or 0),
        "probe_status": (
            "verified_pdf"
            if is_pdf
            else "verified_html_fulltext"
        ),
        "probe_http_status": 200,
        "probe_failure_kind": None,
        "resolution_status": "verified",
        "final_url": result.get("final_url"),
        "content_type": result.get("content_type"),
        "source_domain": result.get("source_domain"),
        "discovered_via": result.get("discovered_via"),
        "candidate_doi": result.get("doi"),
        "candidate_title": result.get("title"),
        "candidate_authors": result.get("authors") or [],
        "candidate_year": result.get("year"),
        "warnings": result.get("warnings") or [],
    }


def to_provider_attempt(result: dict[str, Any] | None, elapsed_seconds: float) -> dict[str, Any]:
    """
    Produit l'entrée mcp_provider_attempts attendue par les rapports existants.
    """
    result = result if isinstance(result, dict) else {}
    found = bool(result.get("found"))
    return {
        "provider": "known_url_publisher_discovery",
        "enabled": True,
        "ok": bool(result.get("ok")),
        "status": (
            "verified_candidate_found"
            if found
            else "searched_no_verified_candidate"
        ),
        "candidates_count": int(result.get("candidates_count") or 0),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "error": None,
        "http_status": None,
        "transient": False,
        "identity_rejected_count": sum(
            1
            for item in result.get("attempts") or []
            if item.get("status") == "identity_rejected"
        ),
        "access_blocked_count": sum(
            1
            for item in result.get("attempts") or []
            if item.get("status") == "access_blocked_or_antibot"
        ),
        "landing_only_count": int(result.get("landing_only_count") or 0),
        "verified_count": int(result.get("verified_count") or 0),
        "verified_pdf_count": int(
            bool(found and result.get("content_kind") == "pdf")
        ),
        "verified_html_count": int(
            bool(found and result.get("content_kind") == "html")
        ),
    }


if __name__ == "__main__":
    # Test manuel :
    # python generic_publisher_discovery.py "Titre" "DOI"
    import sys

    logging.basicConfig(level=logging.INFO)

    title_arg = sys.argv[1] if len(sys.argv) > 1 else ""
    doi_arg = sys.argv[2] if len(sys.argv) > 2 else None

    test_article = ArticleIdentity(
        title=title_arg,
        doi=doi_arg,
        known_urls=[f"https://doi.org/{doi_arg}"] if doi_arg else [],
    )
    print(
        json.dumps(
            resolve_publisher_fulltext(test_article),
            ensure_ascii=False,
            indent=2,
        )
    )
