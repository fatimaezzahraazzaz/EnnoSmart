# -*- coding: utf-8 -*-
from __future__ import annotations

"""Prépare les publications acceptées depuis la recherche guidée.

La recherche guidée et le pipeline scientifique historique utilisent deux
persistances différentes. Ce service fait le raccordement générique :

1. transforme une publication acceptée en ``Article`` du ScholarRun courant ;
2. tente les URL/PDF déjà connus ;
3. en cas d'échec, lance obligatoirement la récupération légale via le MCP ;
4. ne rend la source éligible à la rédaction qu'après extraction vérifiée ;
5. reconstruit la sélection et les fiches extractives afin que les passages
   utiles soient disponibles pour la rédaction.

Aucun titre, verrou, outil ou projet n'est codé en dur.
"""

import re
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from db.models import Article, DiagnosticRun, Project, ScholarRun, Verrou
from services.article_card_builder import build_article_cards_for_selected_articles
from services.scholar_direct_fulltext_service import (
    resolve_and_extract_fulltext_for_article,
)
from services.scholar_legal_recovery_service import recover_legal_fulltext_for_article
from services.scholar_selection_scope import get_current_scholar_run
from services.scholar_state_of_art_payload_service import (
    build_state_of_art_selection_payload,
)


_TECHNICAL_KINDS = {
    "documentation",
    "official_documentation",
    "software_repository",
}
_SCIENTIFIC_PUBLICATION_TYPES = {
    "article",
    "journal article",
    "journal-article",
    "conference paper",
    "conference-paper",
    "proceedings article",
    "proceedings-article",
    "preprint",
    "book chapter",
    "book-chapter",
    "thesis",
    "dissertation",
    "publication",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any, max_chars: int = 0) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if max_chars and len(text) > max_chars:
        return text[:max_chars].strip()
    return text


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value).casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_doi(value: Any) -> str:
    doi = _clean(value, 500).casefold()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    return doi.strip().rstrip(".,;")


def _publication_types(source: dict[str, Any]) -> set[str]:
    return {
        _norm(value)
        for value in (source.get("publication_types") or [])
        if _clean(value)
    }


def is_scientific_publication_source(source: dict[str, Any]) -> bool:
    """Distingue une publication scientifique d'une documentation technique.

    ``query_kind=scientific_evidence`` et les types bibliographiques permettent
    de récupérer les dépôts (par exemple Zenodo) qui ont été initialement
    classés ``research_output`` malgré la présence d'une publication.
    """
    kind = _norm(source.get("candidate_kind")).replace(" ", "_")
    if kind in _TECHNICAL_KINDS:
        return False
    if kind == "scientific_article":
        return True

    publication_types = _publication_types(source)
    has_publication_identity = bool(
        _normalize_doi(source.get("doi"))
        or publication_types & _SCIENTIFIC_PUBLICATION_TYPES
    )
    return bool(
        _norm(source.get("query_kind")) == "scientific evidence"
        and _clean(source.get("title"))
        and has_publication_identity
    )


def _year(value: Any) -> int | None:
    match = re.search(r"\b(19|20)\d{2}\b", _clean(value))
    return int(match.group(0)) if match else None


def _score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= score <= 1.0:
        score *= 100.0
    return round(score, 4)


def _provider(source: dict[str, Any]) -> str:
    providers = source.get("source_providers") or []
    if isinstance(providers, list) and providers:
        return _clean(providers[0], 100) or "guided_research"
    return _clean(source.get("venue"), 100) or "guided_research"


def _known_source_urls(source: dict[str, Any]) -> dict[str, Any]:
    """Expose les liens connus sous les clés comprises par le pipeline direct."""
    payload = deepcopy(source)
    pdf_url = _clean(source.get("pdf_url"), 4000)
    url = _clean(source.get("url"), 4000)
    if pdf_url:
        payload["pdf_url"] = pdf_url
    if url:
        payload["url"] = url
    payload["guided_candidate_id"] = _clean(source.get("candidate_id"), 200)
    payload["guided_research_source"] = True
    payload["covered_verrou_ids"] = [
        _clean(value, 100)
        for value in (source.get("target_verrous") or [])
        if _clean(value)
    ]
    return payload


def _valid_target_verrou_ids(
    db: Session,
    project: Project,
    values: Iterable[Any],
) -> list[int]:
    requested: list[int] = []
    for value in values:
        try:
            requested.append(int(value))
        except (TypeError, ValueError):
            continue
    if not requested:
        return []
    rows = (
        db.query(Verrou.id)
        .join(DiagnosticRun, Verrou.diagnostic_run_id == DiagnosticRun.id)
        .filter(DiagnosticRun.project_id == project.id)
        .filter(Verrou.id.in_(requested))
        .all()
    )
    valid = {int(row[0]) for row in rows}
    return [value for value in requested if value in valid]


def _find_existing_article(
    db: Session,
    scholar_run: ScholarRun,
    source: dict[str, Any],
) -> Article | None:
    source_doi = _normalize_doi(source.get("doi"))
    source_title = _norm(source.get("title"))
    rows = (
        db.query(Article)
        .filter(Article.scholar_run_id == scholar_run.id)
        .all()
    )
    for article in rows:
        article_doi = _normalize_doi(article.doi)
        if source_doi and article_doi and source_doi == article_doi:
            return article
        if source_title and _norm(article.title) == source_title:
            return article
        source_json = article.source_json if isinstance(article.source_json, dict) else {}
        if (
            _clean(source.get("candidate_id"))
            and _clean(source_json.get("guided_candidate_id"))
            == _clean(source.get("candidate_id"))
        ):
            return article
    return None


def _upsert_selected_article(
    db: Session,
    project: Project,
    scholar_run: ScholarRun,
    source: dict[str, Any],
) -> tuple[Article, bool]:
    article = _find_existing_article(db, scholar_run, source)
    created = article is None
    valid_verrous = _valid_target_verrou_ids(
        db,
        project,
        source.get("target_verrous") or [],
    )
    source_json = _known_source_urls(source)

    if article is None:
        article = Article(
            scholar_run_id=scholar_run.id,
            verrou_id=valid_verrous[0] if valid_verrous else None,
            title=_clean(source.get("title"), 4000),
            year=_year(source.get("year")),
            source=_provider(source),
            tag_article="Connexe",
            score=_score(source.get("relevance_score")),
            url=_clean(source.get("url") or source.get("pdf_url"), 4000) or None,
            doi=_normalize_doi(source.get("doi")) or None,
            consultant_status="garde",
            source_json=source_json,
        )
    else:
        previous_json = (
            dict(article.source_json)
            if isinstance(article.source_json, dict)
            else {}
        )
        previous_json.update(source_json)
        article.source_json = previous_json
        article.consultant_status = "garde"
        if article.verrou_id is None and valid_verrous:
            article.verrou_id = valid_verrous[0]
        if not article.url:
            article.url = _clean(source.get("url") or source.get("pdf_url"), 4000) or None
        if not article.doi:
            article.doi = _normalize_doi(source.get("doi")) or None
        if not article.year:
            article.year = _year(source.get("year"))
        if not article.source:
            article.source = _provider(source)
        if not article.tag_article:
            article.tag_article = "Connexe"
        if article.score is None:
            article.score = _score(source.get("relevance_score"))

    db.add(article)
    db.commit()
    db.refresh(article)
    return article, created


def _text_ready(result: dict[str, Any]) -> bool:
    return bool(
        result.get("ok") is True
        and _norm(result.get("full_text_status")) == "text extracted"
    )


def prepare_article_fulltext_with_mcp_fallback(
    db: Session,
    project: Project,
    article: Article,
) -> dict[str, Any]:
    """Essaie le direct puis appelle le MCP à chaque échec de texte intégral."""
    try:
        direct = resolve_and_extract_fulltext_for_article(
            db,
            project,
            int(article.id),
            force=False,
        )
    except Exception as exc:
        direct = {
            "ok": False,
            "status": "direct_preparation_exception",
            "full_text_status": "missing_or_blocked_fulltext",
            "error": str(exc),
        }

    if _text_ready(direct):
        return {
            "ok": True,
            "status": "fulltext_ready",
            "retrieval_stage": "direct",
            "mcp_called": False,
            "direct": direct,
            "legal_mcp": None,
        }

    try:
        legal = recover_legal_fulltext_for_article(
            db,
            project,
            int(article.id),
            force_refresh=True,
            search_all=False,
        )
    except Exception as exc:
        legal = {
            "ok": False,
            "status": "legal_mcp_preparation_exception",
            "full_text_status": "missing_or_blocked_fulltext",
            "mcp_called": True,
            "error": str(exc),
        }

    ready = _text_ready(legal)
    return {
        "ok": ready,
        "status": "fulltext_ready" if ready else "fulltext_unavailable_after_mcp",
        "retrieval_stage": "legal_mcp" if ready else "unavailable",
        "mcp_called": True,
        "direct": direct,
        "legal_mcp": legal,
    }


def _compact_extraction_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    identity = result.get("identity_verification")
    return {
        "ok": bool(result.get("ok")),
        "status": result.get("status"),
        "full_text_status": result.get("full_text_status"),
        "text_chars": result.get("text_chars"),
        "pages_count": result.get("pages_count"),
        "extraction_method": result.get("extraction_method"),
        "retrieved_via_mcp": bool(result.get("retrieved_via_mcp")),
        "mcp_called": bool(result.get("mcp_called")),
        "legal_provider": result.get("legal_provider"),
        "identity_verified": (
            bool(identity.get("verified"))
            if isinstance(identity, dict)
            else None
        ),
        "same_article": (
            bool(identity.get("same_article"))
            if isinstance(identity, dict)
            else None
        ),
        "output_path": result.get("output_path"),
        "error": result.get("error"),
    }


def prepare_accepted_guided_sources(
    db: Session,
    project: Project,
    *,
    sources: list[dict[str, Any]],
    candidate_ids: Iterable[str],
    rebuild_scientific_payloads: bool = True,
) -> dict[str, Any]:
    """Prépare uniquement les candidats visés par la décision courante."""
    wanted = {_clean(value) for value in candidate_ids if _clean(value)}
    updated_sources = deepcopy(sources)
    scholar_run = get_current_scholar_run(db, project)
    reports: list[dict[str, Any]] = []
    ready_article_ids: list[int] = []

    if scholar_run is None:
        return {
            "ok": False,
            "status": "missing_current_scholar_run",
            "message": (
                "Aucun ScholarRun courant : les sources restent acceptées mais "
                "ne peuvent pas encore être préparées."
            ),
            "sources": updated_sources,
            "reports": [],
            "ready_article_ids": [],
            "mcp_calls_count": 0,
        }

    for source in updated_sources:
        candidate_id = _clean(source.get("candidate_id"))
        if candidate_id not in wanted:
            continue
        if _norm(source.get("consultant_decision")) != "accepted":
            continue

        if not is_scientific_publication_source(source):
            source["fulltext_preparation"] = {
                "ok": True,
                "status": "not_applicable_technical_or_context_source",
                "scientific_evidence_eligible": False,
                "prepared_at": _utc_now(),
            }
            reports.append(
                {
                    "candidate_id": candidate_id,
                    "title": source.get("title"),
                    "status": "not_applicable_technical_or_context_source",
                    "mcp_called": False,
                }
            )
            continue

        try:
            article, created = _upsert_selected_article(
                db,
                project,
                scholar_run,
                source,
            )
            extraction = prepare_article_fulltext_with_mcp_fallback(
                db,
                project,
                article,
            )
        except Exception as exc:
            article = None
            created = False
            extraction = {
                "ok": False,
                "status": "source_preparation_exception",
                "retrieval_stage": "unavailable",
                "mcp_called": False,
                "direct": None,
                "legal_mcp": None,
                "error": str(exc),
            }

        ready = bool(extraction.get("ok"))
        if ready and article is not None:
            ready_article_ids.append(int(article.id))
        source["scientific_evidence_eligible"] = ready
        source["fulltext_verified"] = ready
        source["fulltext_preparation"] = {
            "ok": ready,
            "status": extraction.get("status"),
            "retrieval_stage": extraction.get("retrieval_stage"),
            "mcp_called": bool(extraction.get("mcp_called")),
            "article_id": int(article.id) if article is not None else None,
            "article_created": created,
            "direct": _compact_extraction_result(extraction.get("direct")),
            "legal_mcp": _compact_extraction_result(extraction.get("legal_mcp")),
            "error": extraction.get("error"),
            "usable_as_scientific_evidence": ready,
            "proof_policy": (
                "fulltext_verified_only"
                if ready
                else "forbidden_until_verified_fulltext"
            ),
            "prepared_at": _utc_now(),
        }
        reports.append(
            {
                "candidate_id": candidate_id,
                "title": source.get("title"),
                "article_id": int(article.id) if article is not None else None,
                "status": extraction.get("status"),
                "retrieval_stage": extraction.get("retrieval_stage"),
                "mcp_called": bool(extraction.get("mcp_called")),
                "ready_for_writing": ready,
            }
        )

    selection_payload: dict[str, Any] | None = None
    article_cards_payload: dict[str, Any] | None = None
    rebuild_errors: list[dict[str, str]] = []
    if rebuild_scientific_payloads and reports:
        try:
            selection_payload = build_state_of_art_selection_payload(db, project)
        except Exception as exc:
            rebuild_errors.append(
                {"stage": "selection_payload", "error": str(exc)}
            )
        try:
            article_cards_payload = build_article_cards_for_selected_articles(
                db,
                project,
                mode="auto",
                force=False,
            )
        except Exception as exc:
            rebuild_errors.append(
                {"stage": "article_cards", "error": str(exc)}
            )

    return {
        "ok": all(
            report.get("ready_for_writing", True)
            for report in reports
        ),
        "status": (
            "all_scientific_sources_ready"
            if all(report.get("ready_for_writing", True) for report in reports)
            else "some_scientific_sources_unavailable"
        ),
        "sources": updated_sources,
        "reports": reports,
        "ready_article_ids": ready_article_ids,
        "mcp_calls_count": sum(
            1 for report in reports if report.get("mcp_called") is True
        ),
        "selection_payload_path": (
            selection_payload.get("payload_path")
            if isinstance(selection_payload, dict)
            else None
        ),
        "article_cards_payload_path": (
            article_cards_payload.get("payload_path")
            if isinstance(article_cards_payload, dict)
            else None
        ),
        "writing_ready_cards_count": (
            article_cards_payload.get("writing_ready_cards_count")
            if isinstance(article_cards_payload, dict)
            else None
        ),
        "rebuild_errors": rebuild_errors,
        "prepared_at": _utc_now(),
    }
