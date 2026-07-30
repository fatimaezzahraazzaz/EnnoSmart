# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Pont robuste entre les articles EnnoScholar et le serveur MCP Legal Fulltext.

Objectifs :
- envoyer systématiquement au MCP le titre, le DOI, les auteurs, l'année et les URLs connues ;
- accepter une correction d'identité explicite, traçable et validée humainement ;
- ne jamais coder en dur une correction dans le service Python ;
- conserver dans les diagnostics l'identité réellement envoyée au MCP ;
- ne réinjecter dans le pipeline qu'un PDF légal, vérifié et correspondant au même article.

Le fichier d'overrides est, par défaut :
    C:/EnnoSmart/config/ennoscholar_article_identity_overrides.json

Il peut être remplacé avec :
    ENNOSCHOLAR_ARTICLE_IDENTITY_OVERRIDES_FILE
"""

import json
import os
import re
from pathlib import Path
from typing import Any, MutableSequence

from services.legal_fulltext_mcp_client import resolve_article_fulltext


_URL_KEYS = {
    "url",
    "pdf",
    "pdf_url",
    "pdfurl",
    "url_for_pdf",
    "download_url",
    "downloadurl",
    "fulltext_url",
    "full_text_url",
    "fulltextidentifier",
    "oa_url",
    "open_access_url",
    "landing_page_url",
    "source_url",
    "external_url",
    "homepage_url",
    "primary_location",
    "best_oa_location",
    "openaccesspdf",
    "open_access_pdf",
    "locations",
}

_OVERRIDE_OBJECT_KEYS = (
    "article_identity_override",
    "identity_override",
    "selected_article_identity",
    "consultant_identity",
    "canonical_identity",
)

_TITLE_KEYS = (
    "selected_title",
    "consultant_title",
    "canonical_title",
    "original_title",
    "display_title",
    "title",
)

_AUTHOR_KEYS = (
    "selected_authors",
    "consultant_authors",
    "canonical_authors",
    "original_authors",
    "authors",
    "authorships",
)

_YEAR_KEYS = (
    "selected_year",
    "consultant_year",
    "canonical_year",
    "publication_year",
    "publicationYear",
    "year",
)

_DOI_KEYS = (
    "selected_doi",
    "consultant_doi",
    "canonical_doi",
    "original_doi",
    "doi",
)


# ---------------------------------------------------------------------------
# Helpers génériques
# ---------------------------------------------------------------------------

def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return []


def _safe_text(value: Any, max_chars: int = 2000) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].strip()
    return text


def _clean_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""

    text = value.strip().replace("\\/", "/")
    if text.startswith("//"):
        text = "https:" + text

    if not text.startswith(("http://", "https://")):
        return ""

    return text


def _safe_year(value: Any) -> int | None:
    try:
        year = int(value)
        return year if 1800 <= year <= 2200 else None
    except Exception:
        return None


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> tuple[bool, Any]:
    for key in keys:
        if key in mapping:
            return True, mapping.get(key)
    return False, None


def _first_nonempty(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple, set, dict)) and not value:
            continue
        return value
    return None


def _dedupe_strings(values: list[str], *, limit: int = 100) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _safe_text(value, 4000)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Auteurs / URLs
# ---------------------------------------------------------------------------

def _authors_from_value(values: Any) -> list[str]:
    if isinstance(values, str):
        return _dedupe_strings(
            [item.strip() for item in re.split(r"[;,|]", values) if item.strip()],
            limit=30,
        )

    out: list[str] = []
    for item in _as_list(values):
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
            continue

        if not isinstance(item, dict):
            continue

        author = item.get("author") if isinstance(item.get("author"), dict) else item
        name = (
            author.get("display_name")
            or author.get("name")
            or author.get("full_name")
            or author.get("author_name")
        )
        if isinstance(name, str) and name.strip():
            out.append(name.strip())

    return _dedupe_strings(out, limit=30)


def _extract_authors(article: Any, source_json: dict[str, Any]) -> list[str]:
    direct = getattr(article, "authors", None)
    if direct:
        parsed = _authors_from_value(direct)
        if parsed:
            return parsed

    for key in _AUTHOR_KEYS:
        if key in source_json:
            parsed = _authors_from_value(source_json.get(key))
            if parsed:
                return parsed

    return []


def collect_first_phase_urls(
    article: Any,
    *,
    source_json: dict[str, Any] | None = None,
    extra_urls: list[str] | None = None,
    max_urls: int = 80,
) -> list[str]:
    """Collecte toutes les URLs utiles déjà récupérées pendant la phase 1."""
    source_json = source_json or _as_dict(getattr(article, "source_json", None))
    collected: list[str] = []

    def add(value: Any) -> None:
        url = _clean_url(value)
        if url:
            collected.append(url)

    for value in extra_urls or []:
        add(value)

    for attr in (
        "url",
        "pdf_url",
        "open_access_url",
        "oa_url",
        "landing_page_url",
        "source_url",
        "external_url",
        "download_url",
    ):
        add(getattr(article, attr, None))

    def walk(value: Any, *, key_hint: str = "", depth: int = 0) -> None:
        if depth > 8 or len(collected) >= max_urls * 3:
            return

        if isinstance(value, str):
            key = key_hint.lower().replace("-", "_")
            url = _clean_url(value)
            if not url:
                return

            low = url.lower()
            if (
                key in _URL_KEYS
                or "url" in key
                or "pdf" in key
                or low.endswith(".pdf")
                or ".pdf?" in low
                or "/pdf?" in low
                or "viewcontent.cgi" in low
                or "/viewfile/" in low
                or "doi.org/" in low
                or "arxiv.org/" in low
                or "hal.science/" in low
                or "theses.hal.science/" in low
                or "core.ac.uk/" in low
                or "zenodo.org/" in low
            ):
                collected.append(url)
            return

        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, key_hint=str(key), depth=depth + 1)
            return

        if isinstance(value, (list, tuple, set)):
            for child in value:
                walk(child, key_hint=key_hint, depth=depth + 1)

    walk(source_json)

    out: list[str] = []
    seen: set[str] = set()
    for url in collected:
        normalized = url.strip()
        key = normalized.casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(normalized)
        if len(out) >= max_urls:
            break

    return out


# ---------------------------------------------------------------------------
# Overrides d'identité validés humainement
# ---------------------------------------------------------------------------

def _override_file_path() -> Path:
    configured = os.getenv(
        "ENNOSCHOLAR_ARTICLE_IDENTITY_OVERRIDES_FILE",
        "C:/EnnoSmart/config/ennoscholar_article_identity_overrides.json",
    )
    return Path(configured)


def _read_override_file() -> dict[str, Any]:
    path = _override_file_path()
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _external_override_for_article(article_id: Any) -> dict[str, Any]:
    data = _read_override_file()
    article_key = str(article_id) if article_id is not None else ""

    candidates = data.get("articles") if isinstance(data.get("articles"), dict) else data
    value = candidates.get(article_key) if isinstance(candidates, dict) else None

    if not isinstance(value, dict):
        return {}
    if value.get("enabled") is False:
        return {}

    return value


def _embedded_override(source_json: dict[str, Any]) -> dict[str, Any]:
    for key in _OVERRIDE_OBJECT_KEYS:
        value = source_json.get(key)
        if isinstance(value, dict) and value.get("enabled") is not False:
            return value
    return {}


def build_article_identity(article: Any) -> dict[str, Any]:
    """
    Construit l'identité effectivement envoyée au MCP.

    Priorité :
    1. override externe validé humainement ;
    2. override explicite présent dans source_json ;
    3. colonnes ORM ;
    4. métadonnées source_json.
    """
    source_json = _as_dict(getattr(article, "source_json", None))
    article_id = getattr(article, "id", None)

    external_override = _external_override_for_article(article_id)
    embedded_override = _embedded_override(source_json)
    override = external_override or embedded_override

    warnings: list[str] = []
    origin = "database"
    if external_override:
        origin = "human_validated_override_file"
        reason = _safe_text(external_override.get("reason"), 1000)
        if reason:
            warnings.append(f"Override d'identité appliqué : {reason}")
    elif embedded_override:
        origin = "source_json_identity_override"

    # Titre
    override_has_title, override_title = _first_present(override, _TITLE_KEYS)
    if override_has_title:
        title = _safe_text(override_title, 2000)
    else:
        title = _safe_text(
            getattr(article, "title", None)
            or _first_nonempty(source_json, _TITLE_KEYS),
            2000,
        )

    # DOI : la présence explicite de null dans l'override signifie "supprimer le DOI".
    override_has_doi, override_doi = _first_present(override, _DOI_KEYS)
    if override_has_doi:
        doi = _safe_text(override_doi, 500) or None
    else:
        doi = _safe_text(
            getattr(article, "doi", None)
            or _first_nonempty(source_json, _DOI_KEYS),
            500,
        ) or None

    # Auteurs
    override_has_authors, override_authors = _first_present(override, _AUTHOR_KEYS)
    if override_has_authors:
        authors = _authors_from_value(override_authors)
    else:
        authors = _extract_authors(article, source_json)

    # Année
    override_has_year, override_year = _first_present(override, _YEAR_KEYS)
    if override_has_year:
        year = _safe_year(override_year)
    else:
        year = _safe_year(
            getattr(article, "year", None)
            or _first_nonempty(source_json, _YEAR_KEYS)
        )

    # Source
    if "source" in override:
        source = _safe_text(override.get("source"), 500) or None
    else:
        source = _safe_text(
            getattr(article, "source", None) or source_json.get("source"),
            500,
        ) or None

    override_urls = []
    for key in ("known_urls", "urls", "pdf_urls"):
        value = override.get(key)
        if isinstance(value, str):
            override_urls.append(value)
        elif isinstance(value, list):
            override_urls.extend(str(item) for item in value if item)

    if override and override.get("inherit_database_urls") is not True:
        known_urls = []
        seen_urls: set[str] = set()
        for raw_url in override_urls:
            url = _clean_url(raw_url)
            key = url.casefold()
            if url and key not in seen_urls:
                seen_urls.add(key)
                known_urls.append(url)
    else:
        known_urls = collect_first_phase_urls(
            article,
            source_json=source_json,
            extra_urls=override_urls,
        )

    if not title:
        warnings.append("Le titre de l'article est vide.")
    if not authors:
        warnings.append("Aucun auteur n'est disponible dans la métadonnée backend.")
    if year is None:
        warnings.append("L'année est absente de la métadonnée backend.")

    return {
        "article_id": article_id,
        "title": title,
        "doi": doi,
        "authors": authors,
        "year": year,
        "known_urls": known_urls,
        "source": source,
        "identity_origin": origin,
        "identity_warnings": warnings,
        "override_file": str(_override_file_path()) if external_override else None,
    }


# ---------------------------------------------------------------------------
# Appel MCP
# ---------------------------------------------------------------------------

def resolve_mcp_for_article(
    article: Any,
    *,
    force: bool = False,
    search_all: bool | None = None,
) -> dict[str, Any]:
    identity = build_article_identity(article)

    if search_all is None:
        search_all = os.getenv("ENNOSCHOLAR_LEGAL_MCP_SEARCH_ALL", "1").lower() in {
            "1", "true", "yes", "on"
        }

    result = resolve_article_fulltext(
        article_id=identity.get("article_id"),
        title=identity.get("title") or "",
        doi=identity.get("doi"),
        authors=list(identity.get("authors") or []),
        year=identity.get("year"),
        known_urls=list(identity.get("known_urls") or []),
        source=identity.get("source"),
        search_all=bool(search_all),
        force_refresh=force,
    )

    if not isinstance(result, dict):
        result = {
            "ok": False,
            "found": False,
            "legal_access": False,
            "same_article": False,
            "status": "invalid_mcp_response",
            "best_candidate": None,
            "locations": [],
            "attempts": [],
            "needs_consultant_upload": True,
        }

    # Champs privés au pont backend, utilisés pour la traçabilité des diagnostics.
    result["_backend_identity_sent"] = identity
    result["_backend_search_all"] = bool(search_all)
    return result


# ---------------------------------------------------------------------------
# Diagnostic compact
# ---------------------------------------------------------------------------

def _compact_provider_attempt(item: Any) -> dict[str, Any]:
    value = _as_dict(item)
    return {
        "provider": value.get("provider"),
        "enabled": value.get("enabled"),
        "ok": value.get("ok"),
        "status": value.get("status"),
        "candidates_count": value.get("candidates_count"),
        "elapsed_seconds": value.get("elapsed_seconds"),
        "error": value.get("error"),
        "http_status": value.get("http_status"),
        "transient": value.get("transient"),
        "identity_rejected_count": value.get("identity_rejected_count"),
        "access_blocked_count": value.get("access_blocked_count"),
        "landing_only_count": value.get("landing_only_count"),
        "verified_count": value.get("verified_count"),
    }


def _compact_location(item: Any) -> dict[str, Any]:
    value = _as_dict(item)
    return {
        "provider": value.get("provider"),
        "pdf_url": value.get("pdf_url"),
        "landing_url": value.get("landing_url"),
        "legal_access": value.get("legal_access"),
        "license": value.get("license"),
        "access_type": value.get("access_type"),
        "rights_status": value.get("rights_status"),
        "same_article": value.get("same_article"),
        "identity_score": value.get("identity_score"),
        "identity_method": value.get("identity_method"),
        "verified_pdf": value.get("verified_pdf"),
        "probe_status": value.get("probe_status"),
        "probe_http_status": value.get("probe_http_status"),
        "probe_failure_kind": value.get("probe_failure_kind"),
        "resolution_status": value.get("resolution_status"),
        "final_url": value.get("final_url"),
        "content_type": value.get("content_type"),
        "source_domain": value.get("source_domain"),
        "discovered_via": value.get("discovered_via"),
        "candidate_doi": value.get("candidate_doi"),
        "candidate_title": value.get("candidate_title"),
        "candidate_authors": value.get("candidate_authors"),
        "candidate_year": value.get("candidate_year"),
        "warnings": [
            _safe_text(warning, 500)
            for warning in _as_list(value.get("warnings"))[:8]
            if _safe_text(warning, 500)
        ],
    }


def _compact_best_candidate(item: Any) -> dict[str, Any] | None:
    value = _as_dict(item)
    if not value:
        return None
    return _compact_location(value)


def build_mcp_diagnostic(
    result: Any,
    *,
    max_attempts: int = 20,
    max_locations: int = 20,
) -> dict[str, Any]:
    payload = _as_dict(result)
    locations = _as_list(payload.get("locations"))
    attempts = _as_list(payload.get("attempts"))
    backend_identity = _as_dict(payload.get("_backend_identity_sent"))

    verified_count = sum(
        1
        for location in locations
        if isinstance(location, dict)
        and location.get("verified_pdf") is True
        and location.get("same_article") is True
    )

    legal_same_article_count = sum(
        1
        for location in locations
        if isinstance(location, dict)
        and location.get("legal_access") is True
        and location.get("same_article") is True
    )

    return {
        "status": "mcp_resolution",
        "candidate_source": "legal_mcp",
        "candidate_kind": "diagnostic",
        "mcp_called": True,
        "mcp_ok": payload.get("ok"),
        "mcp_found": bool(payload.get("found")),
        "mcp_status": payload.get("status") or "mcp_unknown_status",
        "mcp_failure_code": payload.get("failure_code"),
        "mcp_reason": _safe_text(payload.get("reason"), 2000) or None,
        "mcp_resolver_version": payload.get("resolver_version"),
        "mcp_provenance": _as_dict(payload.get("provenance")),
        "mcp_cache_hit": bool(payload.get("cache_hit")),
        "mcp_legal_access": bool(payload.get("legal_access")),
        "mcp_same_article": bool(payload.get("same_article")),
        "mcp_needs_consultant_upload": payload.get("needs_consultant_upload"),
        "mcp_retry_recommended": bool(payload.get("retry_recommended")),
        "mcp_locations_count": len(locations),
        "mcp_verified_candidates_count": verified_count,
        "mcp_legal_same_article_locations_count": legal_same_article_count,
        "mcp_search_all": bool(payload.get("_backend_search_all")),
        "article_identity_sent": backend_identity,
        "article_identity_origin": backend_identity.get("identity_origin"),
        "article_identity_warnings": list(
            backend_identity.get("identity_warnings") or []
        ),
        "mcp_article": _as_dict(payload.get("article")),
        "mcp_best_candidate": _compact_best_candidate(payload.get("best_candidate")),
        "mcp_provider_attempts": [
            _compact_provider_attempt(item)
            for item in attempts[:max_attempts]
        ],
        "mcp_locations": [
            _compact_location(item)
            for item in locations[:max_locations]
        ],
    }


# ---------------------------------------------------------------------------
# Candidat réinjecté dans les phases 2A / 2B
# ---------------------------------------------------------------------------

def build_mcp_candidates_for_article(
    article: Any,
    *,
    force: bool = False,
    search_all: bool | None = None,
    diagnostics: MutableSequence[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    result = resolve_mcp_for_article(
        article,
        force=force,
        search_all=search_all,
    )

    diagnostic = build_mcp_diagnostic(result)
    if diagnostics is not None:
        diagnostics.append(diagnostic)

    best = result.get("best_candidate") if isinstance(result, dict) else None
    if not isinstance(best, dict):
        return []

    pdf_url = best.get("final_url") or best.get("pdf_url")
    if not isinstance(pdf_url, str) or not pdf_url.strip():
        return []

    if not best.get("same_article") or not best.get("verified_pdf"):
        return []

    source = f"legal_mcp:{best.get('provider') or 'unknown'}"
    final_url = str(best.get("final_url") or pdf_url).strip()
    original_url = str(best.get("pdf_url") or final_url).strip()

    return [
        {
            "kind": "pdf",
            "source": source,
            "resolver": source,
            "url": final_url,
            "pdf_url": original_url,
            "final_url": final_url,
            "legal_access": bool(best.get("legal_access")),
            "license": best.get("license"),
            "version": best.get("version"),
            "host_type": best.get("host_type"),
            "access_type": best.get("access_type"),
            "rights_status": best.get("rights_status"),
            "source_domain": best.get("source_domain"),
            "discovered_via": best.get("discovered_via"),
            "identity_score": best.get("identity_score"),
            "identity_method": best.get("identity_method"),
            "same_article": bool(best.get("same_article")),
            "verified_pdf": bool(best.get("verified_pdf")),
            "retrieved_via_mcp": True,
            "mcp_called": True,
            "mcp_found": bool(result.get("found")),
            "mcp_status": result.get("status"),
            "mcp_cache_hit": bool(result.get("cache_hit")),
            "article_identity_sent": diagnostic.get("article_identity_sent"),
            "article_identity_origin": diagnostic.get("article_identity_origin"),
        }
    ]
