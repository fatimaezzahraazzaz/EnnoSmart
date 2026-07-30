# -*- coding: utf-8 -*-
from __future__ import annotations

"""Validation d'identité documentaire commune aux extractions directes, MCP et uploads.

La validation bibliographique du résolveur ne suffit pas à elle seule : une URL peut
pointer vers un PDF générique, des consignes éditoriales, un programme de conférence
ou un autre article. Ce module vérifie le contenu réellement extrait avant de déclarer
``full_text_status=text_extracted``.
"""

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Iterable


_GENERIC_DOCUMENT_MARKERS = (
    "guidelines for ethical publishing",
    "publication ethics",
    "instructions for authors",
    "author guidelines",
    "submission guidelines",
    "copyright transfer agreement",
    "permission request form",
    "how to find a publisher s contact information",
    "how to contact the publisher",
    "conference program",
    "conference programme",
    "table of contents",
    "terms and conditions",
    "privacy policy",
    "cookie policy",
)

_STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "or", "to", "in", "on", "with", "by",
    "from", "using", "based", "study", "analysis", "method", "methods", "approach",
    "article", "paper", "new", "towards", "toward", "via", "sur", "de", "des", "du",
    "la", "le", "les", "un", "une", "et", "pour", "par", "avec", "dans",
}


def _safe_text(value: Any, max_chars: int = 0) -> str:
    text = str(value or "").replace("\x00", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars].strip() if max_chars and len(text) > max_chars else text


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _safe_text(value).lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").replace("`", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_doi(value: Any) -> str:
    text = _safe_text(value, 500).lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi\s*:\s*", "", text)
    return text.strip().rstrip(".,;)")


def extract_article_authors(article: Any) -> list[str]:
    direct = getattr(article, "authors", None)
    source_json = getattr(article, "source_json", None)
    source_json = source_json if isinstance(source_json, dict) else {}
    values = direct or source_json.get("authors") or source_json.get("authorships") or []
    if isinstance(values, str):
        values = [x.strip() for x in re.split(r"[;,|]", values) if x.strip()]
    out: list[str] = []
    for item in values if isinstance(values, list) else []:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            obj = item.get("author") if isinstance(item.get("author"), dict) else item
            name = obj.get("display_name") or obj.get("name") or obj.get("full_name") or ""
        else:
            name = ""
        name = _safe_text(name, 180)
        if name and name.casefold() not in {x.casefold() for x in out}:
            out.append(name)
    return out[:30]


def extract_text_from_payload(payload: dict[str, Any]) -> str:
    pages = payload.get("pages")
    if isinstance(pages, list):
        chunks = []
        for page in pages:
            if isinstance(page, dict):
                text = page.get("text") or page.get("content")
                if isinstance(text, str) and text.strip():
                    chunks.append(text)
        if chunks:
            return _safe_text("\n\n".join(chunks))
    for key in ("clean_text", "full_text", "text", "content", "full_text_preview"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _safe_text(value)
    return ""


def _title_tokens(title: str) -> list[str]:
    tokens = [x for x in normalize_text(title).split() if len(x) >= 3 and x not in _STOPWORDS]
    # Conserver l'ordre, supprimer les doublons.
    return list(dict.fromkeys(tokens))


def _author_surnames(authors: Iterable[str]) -> list[str]:
    out: list[str] = []
    for author in authors or []:
        tokens = [x for x in normalize_text(author).split() if len(x) >= 3]
        if tokens:
            surname = tokens[-1]
            if surname not in out:
                out.append(surname)
    return out


def _first_page_candidates(payload: dict[str, Any], full_text: str) -> list[str]:
    lines: list[str] = []
    pages = payload.get("pages")
    if isinstance(pages, list) and pages:
        first = pages[0] if isinstance(pages[0], dict) else {}
        first_text = _safe_text(first.get("text") or first.get("content"), 7000)
    else:
        first_text = _safe_text(full_text, 7000)
    raw_lines = [re.sub(r"\s+", " ", line).strip() for line in first_text.splitlines()]
    raw_lines = [line for line in raw_lines if 8 <= len(line) <= 500]
    for i, line in enumerate(raw_lines[:30]):
        lines.append(line)
        if i + 1 < len(raw_lines):
            lines.append(line + " " + raw_lines[i + 1])
        if i + 2 < len(raw_lines):
            lines.append(line + " " + raw_lines[i + 1] + " " + raw_lines[i + 2])
    return lines


def verify_extracted_document_identity(
    *,
    expected_title: str,
    expected_doi: str | None,
    expected_authors: list[str] | None,
    expected_year: int | None,
    extraction_payload: dict[str, Any],
    resolver_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    full_text = extract_text_from_payload(extraction_payload)
    normalized_text = normalize_text(full_text[:60000])
    normalized_head = normalize_text(full_text[:16000])
    expected_title_norm = normalize_text(expected_title)
    expected_doi_norm = normalize_doi(expected_doi)
    title_tokens = _title_tokens(expected_title)

    generic_markers = [marker for marker in _GENERIC_DOCUMENT_MARKERS if normalize_text(marker) in normalized_head]
    doi_match = bool(expected_doi_norm and expected_doi_norm in full_text.lower().replace("https://doi.org/", ""))

    present_tokens = [token for token in title_tokens if re.search(rf"\b{re.escape(token)}\b", normalized_head)]
    title_token_coverage = len(present_tokens) / max(1, len(title_tokens))

    line_scores: list[float] = []
    for candidate_line in _first_page_candidates(extraction_payload, full_text):
        candidate_norm = normalize_text(candidate_line)
        if candidate_norm:
            line_scores.append(SequenceMatcher(None, expected_title_norm, candidate_norm).ratio())
    title_line_similarity = max(line_scores or [0.0])

    surnames = _author_surnames(expected_authors or [])
    matched_surnames = [name for name in surnames if re.search(rf"\b{re.escape(name)}\b", normalized_head)]
    author_overlap = len(matched_surnames) / max(1, len(surnames)) if surnames else 0.5

    year_match = None
    if expected_year:
        year_match = bool(re.search(rf"\b{int(expected_year)}\b", normalized_head))

    resolver_candidate = resolver_candidate if isinstance(resolver_candidate, dict) else {}
    resolver_verified = bool(
        resolver_candidate.get("same_article") is True
        and resolver_candidate.get("verified_pdf") is True
        and float(resolver_candidate.get("identity_score") or 0.0) >= 0.90
    )

    same_article = False
    method = "content_identity_mismatch"
    reasons: list[str] = []

    # Une publication OA légitime peut contenir « terms and conditions » dans
    # son bloc de licence. Ce marqueur ne doit donc pas annuler un DOI, un
    # titre et des auteurs qui correspondent au document extrait. Il reste
    # bloquant lorsque les preuves bibliographiques du contenu sont faibles.
    strong_content_identity = bool(
        (
            doi_match
            and title_line_similarity >= 0.82
            and title_token_coverage >= 0.68
        )
        or (
            title_line_similarity >= 0.94
            and title_token_coverage >= 0.85
            and author_overlap >= 0.34
        )
    )

    if generic_markers and not strong_content_identity:
        reasons.append("generic_publisher_document_detected")
    elif doi_match:
        same_article = True
        method = "doi_found_in_extracted_document"
    elif title_line_similarity >= 0.88 and title_token_coverage >= 0.68:
        same_article = True
        method = "first_page_title_match"
    elif resolver_verified and title_token_coverage >= 0.72:
        same_article = True
        method = "resolver_identity_plus_content_title_coverage"
    elif resolver_verified and title_token_coverage >= 0.60 and author_overlap >= 0.34:
        same_article = True
        method = "resolver_identity_plus_title_and_author_content"
    else:
        if title_token_coverage < 0.60:
            reasons.append("title_tokens_missing_from_document")
        if title_line_similarity < 0.70:
            reasons.append("first_page_title_similarity_low")
        if surnames and author_overlap < 0.20:
            reasons.append("authors_missing_from_document_head")

    # Les titres très courts sont plus ambigus : exiger un signal secondaire.
    if same_article and len(title_tokens) <= 3 and not doi_match and author_overlap < 0.34:
        same_article = False
        method = "short_title_insufficient_secondary_evidence"
        reasons.append("short_title_requires_doi_or_author_match")

    score = min(
        1.0,
        (1.0 if doi_match else 0.0) * 0.45
        + title_token_coverage * 0.30
        + title_line_similarity * 0.20
        + author_overlap * 0.05,
    )
    if resolver_verified:
        score = max(score, min(0.99, float(resolver_candidate.get("identity_score") or 0.0))) if same_article else score

    return {
        "verified": bool(same_article),
        "same_article": bool(same_article),
        "method": method,
        "score": round(score, 4),
        "expected_title": expected_title,
        "expected_doi": expected_doi_norm or None,
        "expected_year": expected_year,
        "title_token_coverage": round(title_token_coverage, 4),
        "title_line_similarity": round(title_line_similarity, 4),
        "doi_match_in_content": doi_match,
        "author_overlap_in_head": round(author_overlap, 4),
        "matched_author_surnames": matched_surnames,
        "year_match_in_head": year_match,
        "generic_document_markers": generic_markers,
        "resolver_identity_verified": resolver_verified,
        "resolver_identity_score": resolver_candidate.get("identity_score"),
        "resolver_identity_method": resolver_candidate.get("identity_method"),
        "reasons": reasons,
    }


def verify_article_extraction(article: Any, extraction_payload: dict[str, Any], resolver_candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    return verify_extracted_document_identity(
        expected_title=_safe_text(getattr(article, "title", ""), 2000),
        expected_doi=_safe_text(getattr(article, "doi", ""), 500) or None,
        expected_authors=extract_article_authors(article),
        expected_year=getattr(article, "year", None),
        extraction_payload=extraction_payload,
        resolver_candidate=resolver_candidate,
    )
