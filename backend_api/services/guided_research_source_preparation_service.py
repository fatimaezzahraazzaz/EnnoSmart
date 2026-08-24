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
    covered_verrou_ids = [
        _clean(value, 100)
        for value in (source.get("target_verrous") or [])
        if _clean(value)
    ]
    payload["covered_verrou_ids"] = covered_verrou_ids
    # Une recherche guidée peut viser un verrou précis ou compléter le projet
    # entier (demande libre du chat, nom d'outil, paragraphe, etc.).  Une source
    # sans verrou explicite ne doit pas être perdue par le filtre de la génération
    # diagnostique : elle devient une source globale du corpus projet.
    payload["project_corpus_eligible"] = True
    payload["project_corpus_scope"] = (
        "verrou" if covered_verrou_ids else "project"
    )
    payload["project_corpus_global"] = not bool(covered_verrou_ids)
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


def _get_or_create_improvement_scholar_run(
    db: Session,
    project: Project,
    corpus_scope_id: str,
    guided_session_id: str | None,
) -> ScholarRun:
    """Crée un stockage scientifique privé à une conversation d'amélioration."""

    scope_id = _clean(corpus_scope_id, 120)
    rows = (
        db.query(ScholarRun)
        .filter(ScholarRun.project_id == project.id)
        .filter(ScholarRun.status == "improvement_corpus")
        .order_by(ScholarRun.created_at.desc(), ScholarRun.id.desc())
        .all()
    )
    for row in rows:
        raw = dict(row.raw_result_json or {})
        if _clean(raw.get("corpus_scope_id"), 120) == scope_id:
            guided_id = _clean(guided_session_id, 120)
            guided_ids = [
                _clean(value, 120)
                for value in (raw.get("guided_session_ids") or [])
                if _clean(value, 120)
            ]
            if guided_id and guided_id not in guided_ids:
                raw["guided_session_ids"] = [*guided_ids, guided_id]
                raw["updated_at"] = _utc_now()
                row.raw_result_json = raw
                db.add(row)
                db.commit()
                db.refresh(row)
            return row

    row = ScholarRun(
        project_id=project.id,
        status="improvement_corpus",
        raw_result_json={
            "mode": "ennoamelioration_conversation",
            "corpus_scope_id": scope_id,
            "guided_session_ids": [
                _clean(guided_session_id, 120)
            ] if guided_session_id else [],
            "created_at": _utc_now(),
            "isolation_policy": "one_improvement_conversation_one_corpus",
        },
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_or_create_guided_conversation_scholar_run(
    db: Session,
    project: Project,
    corpus_scope_id: str,
    guided_session_id: str | None,
    *,
    create: bool = True,
    context: dict[str, Any] | None = None,
) -> ScholarRun | None:
    """Retourne le ScholarRun privé d'une conversation EnnoScholar.

    Les anciens runs ``guided_research_standalone`` sont adoptés lorsqu'ils
    appartiennent à la même session. Cela conserve les articles déjà préparés
    tout en empêchant désormais leur mélange avec le corpus historique.
    """
    scope_id = _clean(corpus_scope_id, 120)
    session_id = _clean(guided_session_id, 120)
    if not scope_id:
        return None
    rows = (
        db.query(ScholarRun)
        .filter(ScholarRun.project_id == project.id)
        .filter(
            ScholarRun.status.in_([
                "guided_conversation_corpus",
                "guided_research_standalone",
            ])
        )
        .order_by(ScholarRun.created_at.desc(), ScholarRun.id.desc())
        .all()
    )
    row: ScholarRun | None = None
    for candidate in rows:
        raw = dict(candidate.raw_result_json or {})
        candidate_scope = _clean(raw.get("corpus_scope_id"), 120)
        candidate_session = _clean(
            raw.get("guided_session_id"), 120
        )
        candidate_sessions = {
            _clean(value, 120)
            for value in (raw.get("guided_session_ids") or [])
            if _clean(value, 120)
        }
        if (
            candidate_scope == scope_id
            or (session_id and candidate_session == session_id)
            or (session_id and session_id in candidate_sessions)
        ):
            row = candidate
            break

    if row is None and not create:
        return None
    if row is None:
        row = ScholarRun(
            project_id=project.id,
            status="guided_conversation_corpus",
            raw_result_json={},
        )

    raw = dict(row.raw_result_json or {})
    guided_ids = [
        _clean(value, 120)
        for value in (raw.get("guided_session_ids") or [])
        if _clean(value, 120)
    ]
    if session_id and session_id not in guided_ids:
        guided_ids.append(session_id)
    standalone_context = dict(context or {})
    raw.update({
        "mode": "guided_conversation",
        "corpus_scope_id": scope_id,
        "guided_session_id": session_id or None,
        "guided_session_ids": guided_ids,
        "operating_mode": _clean(
            standalone_context.get("operating_mode"), 80
        ),
        "project_brief": dict(
            standalone_context.get("standalone_project_brief") or {}
        ),
        "consultant_verrous": list(
            standalone_context.get("consultant_verrous") or []
        ),
        "project_verrous": list(
            standalone_context.get("project_verrous") or []
        ),
        "active_verrou_ids": list(
            standalone_context.get("active_verrou_ids") or []
        ),
        "review_scope": _clean(
            standalone_context.get("review_scope"), 40
        ),
        "isolation_policy": "one_guided_conversation_one_corpus",
        "updated_at": _utc_now(),
    })
    raw.setdefault("created_at", _utc_now())
    row.status = "guided_conversation_corpus"
    row.raw_result_json = raw
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _upsert_selected_article(
    db: Session,
    project: Project,
    scholar_run: ScholarRun,
    source: dict[str, Any],
    *,
    attach_to_db_verrou: bool = True,
    guided_session_id: str | None = None,
    corpus_scope_id: str | None = None,
) -> tuple[Article, bool]:
    article = _find_existing_article(db, scholar_run, source)
    created = article is None
    valid_verrous = (
        _valid_target_verrou_ids(
            db,
            project,
            source.get("target_verrous") or [],
        )
        if attach_to_db_verrou
        else []
    )
    source_json = _known_source_urls(source)
    if not attach_to_db_verrou:
        source_json.update({
            "guided_session_id": _clean(guided_session_id, 120) or None,
            "corpus_scope_id": _clean(corpus_scope_id, 120) or None,
            "conversation_owned": True,
            "origin": "guided_research_conversation",
        })
    requested_tag = _clean(source.get("full_scholar_tag"), 80)
    article_tag = (
        requested_tag
        if _norm(requested_tag) in {"direct", "connexe", "fondamental"}
        else "Connexe"
    )

    if article is None:
        article = Article(
            scholar_run_id=scholar_run.id,
            verrou_id=valid_verrous[0] if valid_verrous else None,
            title=_clean(source.get("title"), 4000),
            year=_year(source.get("year")),
            source=_provider(source),
            tag_article=article_tag,
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
        if not attach_to_db_verrou:
            article.verrou_id = None
        elif article.verrou_id is None and valid_verrous:
            article.verrou_id = valid_verrous[0]
        if not article.url:
            article.url = _clean(source.get("url") or source.get("pdf_url"), 4000) or None
        if not article.doi:
            article.doi = _normalize_doi(source.get("doi")) or None
        if not article.year:
            article.year = _year(source.get("year"))
        if not article.source:
            article.source = _provider(source)
        if not article.tag_article or requested_tag:
            article.tag_article = article_tag
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
    candidate_urls: list[str] = []
    for row in [
        *(result.get("pdf_candidates") or []),
        *(result.get("candidates") or []),
        *(result.get("candidate_attempts") or []),
    ]:
        if not isinstance(row, dict):
            continue
        for key in (
            "browser_download_url",
            "pdf_url",
            "url",
            "landing_page_url",
        ):
            value = _clean(row.get(key), 4000)
            if value and value not in candidate_urls:
                candidate_urls.append(value)
    consultation_url = next(
        (
            _clean(result.get(key), 4000)
            for key in (
                "browser_download_url",
                "pdf_url",
                "url",
                "landing_page_url",
            )
            if _clean(result.get(key), 4000)
        ),
        "",
    ) or (candidate_urls[0] if candidate_urls else "")
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
        "message": result.get("message"),
        "final_failure_code": (
            result.get("final_failure_code")
            or result.get("failure_code")
            or result.get("mcp_failure_code")
        ),
        "needs_consultant_upload": bool(
            result.get("needs_consultant_upload")
        ),
        "consultation_url": consultation_url or None,
        "candidate_urls": candidate_urls[:5],
    }


# BEGIN ENNOSCHOLAR_GUIDED_TERMINAL_PREFLIGHT_V4
def _guided_terminal_evidence_payload(
    report: dict[str, Any],
) -> dict[str, Any]:
    # Traduit la préparation guidée en statut terminal lu par l'UI compacte.
    #
    # Le workflow guided écrit ses résultats dans les artefacts fulltext et
    # selected_sources, alors que GET /articles?compact=true lit
    # source_json.evidence_preflight. Sans ce pont, l'UI peut rester à
    # NOT_CHECKED (0/1) après une préparation réellement terminée.
    status = _norm(report.get("status"))
    fulltext_ready = bool(report.get("fulltext_ready"))
    card_ready = bool(report.get("article_card_ready"))
    ready_for_writing = bool(report.get("ready_for_writing"))
    mcp_called = bool(report.get("mcp_called"))
    retrieval_stage = _clean(
        report.get("retrieval_stage"),
        80,
    )

    if fulltext_ready:
        evidence_status = "FULLTEXT_READY"
        label = (
            "Texte intégral et Article Card prêts"
            if card_ready
            else "Texte intégral prêt — Article Card à reconstruire"
        )
        reason_code = (
            "guided_fulltext_and_card_ready"
            if card_ready
            else "guided_fulltext_ready_card_missing"
        )
        reason_detail = (
            "Le texte intégral a été vérifié et la fiche scientifique "
            "est prête pour la rédaction."
            if card_ready
            else (
                "Le texte intégral est vérifié, mais l'Article Card "
                "n'a pas encore passé son contrôle de qualité."
            )
        )
        recommended_action = (
            "ready_for_writing"
            if card_ready
            else "rebuild_article_card"
        )
        candidate_only = not ready_for_writing

    elif "exception" in status or "error" in status:
        evidence_status = "EXTRACTION_FAILED"
        label = "Extraction terminée avec erreur"
        reason_code = "guided_source_preparation_failed"
        reason_detail = (
            "La tentative de préparation s'est terminée avec une erreur. "
            "Le statut n'est plus en attente."
        )
        recommended_action = "retry_or_import_pdf"
        candidate_only = True

    else:
        evidence_status = "ACCESS_UNAVAILABLE"
        label = "Texte intégral non récupéré automatiquement"
        reason_code = "guided_fulltext_unavailable_after_recovery"
        reason_detail = (
            "Les accès connus ont été testés"
            + (
                " ainsi que la récupération légale MCP"
                if mcp_called
                else ""
            )
            + ", sans obtenir de texte intégral vérifié."
        )
        recommended_action = "import_authorized_pdf"
        candidate_only = True

    return {
        "evidence_status": evidence_status,
        "evidence_label": label,
        "evidence_usable": ready_for_writing,
        "fulltext_ready": fulltext_ready,
        "article_card_ready": card_ready,
        "candidate_only": candidate_only,
        "access_check_status": "completed",
        "reason_code": reason_code,
        "reason_detail": reason_detail,
        "recommended_action": recommended_action,
        "access_kind": retrieval_stage or "guided_preparation",
        "guided_preparation_terminal": True,
        "guided_preparation_status": report.get("status"),
        "mcp_called": mcp_called,
        "updated_at": _utc_now(),
    }


def _sync_guided_article_terminal_preflight(
    db: Session,
    project: Project,
    report: dict[str, Any],
) -> None:
    try:
        article_id = int(report.get("article_id"))
    except (TypeError, ValueError):
        return

    article = (
        db.query(Article)
        .join(
            ScholarRun,
            Article.scholar_run_id == ScholarRun.id,
        )
        .filter(
            Article.id == article_id,
            ScholarRun.project_id == project.id,
        )
        .first()
    )
    if article is None:
        return

    source_json = (
        dict(article.source_json)
        if isinstance(article.source_json, dict)
        else {}
    )
    previous = (
        dict(source_json.get("evidence_preflight"))
        if isinstance(
            source_json.get("evidence_preflight"),
            dict,
        )
        else {}
    )
    previous.update(
        _guided_terminal_evidence_payload(report)
    )
    source_json["evidence_preflight"] = previous
    source_json["guided_source_preparation"] = {
        "candidate_id": report.get("candidate_id"),
        "status": report.get("status"),
        "retrieval_stage": report.get("retrieval_stage"),
        "fulltext_ready": bool(
            report.get("fulltext_ready")
        ),
        "article_card_ready": bool(
            report.get("article_card_ready")
        ),
        "ready_for_writing": bool(
            report.get("ready_for_writing")
        ),
        "mcp_called": bool(report.get("mcp_called")),
        "synced_at": _utc_now(),
    }
    fulltext_ready = bool(report.get("fulltext_ready"))
    source_json["project_corpus_eligible"] = True
    source_json["project_corpus_status"] = (
        "fulltext_ready" if fulltext_ready else "needs_manual_upload"
    )
    source_json["project_corpus_updated_at"] = _utc_now()
    article.source_json = source_json
    db.add(article)


# END ENNOSCHOLAR_GUIDED_TERMINAL_PREFLIGHT_V4


def prepare_accepted_guided_sources(
    db: Session,
    project: Project,
    *,
    sources: list[dict[str, Any]],
    candidate_ids: Iterable[str],
    rebuild_scientific_payloads: bool = True,
    standalone_context: dict[str, Any] | None = None,
    guided_session_id: str | None = None,
    entry_module: str | None = None,
    corpus_scope_id: str | None = None,
) -> dict[str, Any]:
    """Prépare uniquement les candidats visés par la décision courante."""
    wanted = {_clean(value) for value in candidate_ids if _clean(value)}
    updated_sources = deepcopy(sources)
    conversation_scope = _clean(corpus_scope_id, 120)
    if _norm(entry_module) == "ennoamel" and conversation_scope:
        scholar_run = _get_or_create_improvement_scholar_run(
            db,
            project,
            conversation_scope,
            guided_session_id,
        )
    elif conversation_scope:
        scholar_run = get_or_create_guided_conversation_scholar_run(
            db,
            project,
            conversation_scope,
            guided_session_id,
            context=standalone_context,
        )
    else:
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

        article = None
        created = False
        try:
            article, created = _upsert_selected_article(
                db,
                project,
                scholar_run,
                source,
                attach_to_db_verrou=not bool(conversation_scope),
                guided_session_id=guided_session_id,
                corpus_scope_id=conversation_scope or None,
            )
            extraction = prepare_article_fulltext_with_mcp_fallback(
                db,
                project,
                article,
            )
        except Exception as exc:
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
        if article is not None:
            # « Accepté dans le chat » ne signifie pas encore « gardé dans le
            # corpus ». Un article impossible à extraire reste rattaché à sa
            # carte pour permettre l'import PDF, avec un statut d'attente qui
            # l'exclut de toutes les sélections scientifiques.
            article.consultant_status = (
                "garde" if ready else "en_attente_pdf"
            )
            db.add(article)
            db.commit()
            db.refresh(article)
            if ready:
                ready_article_ids.append(int(article.id))
        source["scientific_evidence_eligible"] = ready
        source["fulltext_verified"] = ready
        compact_direct = _compact_extraction_result(extraction.get("direct"))
        compact_legal = _compact_extraction_result(extraction.get("legal_mcp"))
        failure_detail = (
            compact_legal
            if isinstance(compact_legal, dict) and not compact_legal.get("ok")
            else compact_direct
            if isinstance(compact_direct, dict) and not compact_direct.get("ok")
            else {}
        )
        source["fulltext_preparation"] = {
            "ok": ready,
            "status": extraction.get("status"),
            "retrieval_stage": extraction.get("retrieval_stage"),
            "mcp_called": bool(extraction.get("mcp_called")),
            "article_id": int(article.id) if article is not None else None,
            "article_created": created,
            "direct": compact_direct,
            "legal_mcp": compact_legal,
            "error": extraction.get("error"),
            "failure_code": failure_detail.get("final_failure_code"),
            "failure_message": (
                failure_detail.get("message")
                or failure_detail.get("error")
            ),
            "needs_consultant_upload": bool(
                not ready
                and (
                    failure_detail.get("needs_consultant_upload")
                    or article is not None
                )
            ),
            "consultation_url": (
                failure_detail.get("consultation_url")
                or _clean(source.get("pdf_url") or source.get("url"), 4000)
                or None
            ),
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
        if conversation_scope:
            selection_payload = {
                "ok": True,
                "scope_id": conversation_scope,
                "policy": "conversation_scoped_no_global_selection_mutation",
            }
        else:
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
                scholar_run_id=(int(scholar_run.id) if conversation_scope else None),
                scope_id=conversation_scope or None,
            )
        except Exception as exc:
            rebuild_errors.append(
                {"stage": "article_cards", "error": str(exc)}
            )

    cards_by_article_id: dict[int, dict[str, Any]] = {}
    if isinstance(article_cards_payload, dict):
        for card in article_cards_payload.get("cards") or []:
            if not isinstance(card, dict):
                continue
            try:
                card_article_id = int(card.get("article_id"))
            except (TypeError, ValueError):
                continue
            cards_by_article_id[card_article_id] = card

    for report in reports:
        if "ready_for_writing" not in report:
            continue
        fulltext_ready = bool(report.get("ready_for_writing"))
        try:
            report_article_id = int(report.get("article_id"))
        except (TypeError, ValueError):
            report_article_id = 0
        card = cards_by_article_id.get(report_article_id)
        quality = (
            dict(card.get("quality_guard") or {})
            if isinstance(card, dict)
            else {}
        )
        quality_status = _norm(quality.get("status"))
        card_ready = bool(
            card
            and quality_status in {"valid", "valid with warnings"}
        )
        report["fulltext_ready"] = fulltext_ready
        report["selection_payload_ready"] = bool(
            isinstance(selection_payload, dict)
            and selection_payload.get("ok", True)
        )
        report["article_card_ready"] = card_ready
        report["article_card"] = {
            "citation_label": card.get("citation_label") if card else None,
            "quality_status": quality.get("status"),
            "guided_candidate_id": (
                card.get("guided_candidate_id") if card else None
            ),
        }
        report["ready_for_writing"] = bool(fulltext_ready and card_ready)
        if fulltext_ready and not card_ready:
            report["status"] = "article_card_unavailable"

    reports_by_candidate_id = {
        _clean(report.get("candidate_id")): report
        for report in reports
        if _clean(report.get("candidate_id"))
    }
    for source in updated_sources:
        report = reports_by_candidate_id.get(_clean(source.get("candidate_id")))
        if report is None:
            continue
        preparation_state = dict(source.get("fulltext_preparation") or {})
        preparation_state.update(
            {
                "article_card_ready": bool(report.get("article_card_ready")),
                "ready_for_writing": bool(report.get("ready_for_writing")),
            }
        )
        source["fulltext_preparation"] = preparation_state
        source["scientific_evidence_eligible"] = bool(
            report.get("ready_for_writing")
        )

    # BEGIN ENNOSCHOLAR_GUIDED_TERMINAL_PREFLIGHT_V4
    # Synchronise le statut réellement terminé avec la structure lue par
    # GET /articles?compact=true. Cela arrête le faux 0/1 et le polling.
    for report in reports:
        _sync_guided_article_terminal_preflight(
            db,
            project,
            report,
        )
    if reports:
        db.commit()
    # END ENNOSCHOLAR_GUIDED_TERMINAL_PREFLIGHT_V4

    writing_ready_article_ids = [
        int(report["article_id"])
        for report in reports
        if report.get("ready_for_writing") is True
        and report.get("article_id") is not None
    ]
    all_ready = all(
        report.get("ready_for_writing", True)
        for report in reports
    )

    return {
        "ok": all_ready,
        "status": (
            "all_scientific_sources_ready"
            if all_ready
            else "some_scientific_sources_unavailable"
        ),
        "sources": updated_sources,
        "reports": reports,
        "ready_article_ids": ready_article_ids,
        "fulltext_ready_article_ids": ready_article_ids,
        "writing_ready_article_ids": writing_ready_article_ids,
        "mcp_calls_count": sum(
            1 for report in reports if report.get("mcp_called") is True
        ),
        "mcp_success_count": sum(
            1
            for report in reports
            if report.get("mcp_called") is True
            and report.get("fulltext_ready") is True
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
            len(writing_ready_article_ids)
        ),
        "rebuild_errors": rebuild_errors,
        "prepared_at": _utc_now(),
    }
