# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session


def _ensure_root() -> None:
    root = Path("C:/EnnoSmart")
    if root.exists() and str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_root()

from agents.EnnoScholar.guided_research.application.guided_research_agent import (
    EnnoScholarGuidedResearchAgent,
)


_AGENT: EnnoScholarGuidedResearchAgent | None = None


def get_guided_research_agent() -> EnnoScholarGuidedResearchAgent:
    global _AGENT
    if _AGENT is None:
        _AGENT = EnnoScholarGuidedResearchAgent()
    return _AGENT


def attach_uploaded_article_to_session(
    db: Session,
    project: Any,
    *,
    session_id: str,
    article: Any,
    extraction: dict[str, Any],
) -> dict[str, Any]:
    """Ajoute un PDF consultant au corpus acceptÃ© de la conversation active."""

    agent = get_guided_research_agent()
    session = agent.state_manager.get_session(
        db,
        session_id,
        include_messages=False,
    )
    if int(session.project_id) != int(project.id):
        raise PermissionError("Cette conversation appartient Ã  un autre projet.")

    snapshot = agent.repository.snapshot(db, session_id)
    context = dict(snapshot.get("context") or {})
    _, corpus_run, _ = _guided_corpus_run(
        db,
        project,
        session_id=session_id,
        create=True,
    )
    if corpus_run is None or int(article.scholar_run_id) != int(corpus_run.id):
        raise PermissionError(
            "Cet article n'appartient pas au corpus de cette conversation."
        )
    all_verrous = [
        dict(row)
        for row in (context.get("consultant_verrous") or [])
        if isinstance(row, dict)
    ]
    active_ids = [
        str(value).strip()
        for value in (context.get("active_verrou_ids") or [])
        if str(value).strip()
    ]
    target_verrous = active_ids or [
        str(row.get("id") or "").strip()
        for row in all_verrous
        if str(row.get("id") or "").strip()
    ]
    source_json = dict(article.source_json) if isinstance(article.source_json, dict) else {}
    corpus_scope_id = str(
        context.get("corpus_scope_id") or session_id
    ).strip()
    source_json.update({
        "guided_session_id": session_id,
        "corpus_scope_id": corpus_scope_id,
        "conversation_owned": True,
        "origin": "guided_research_conversation",
        "guided_research_source": True,
    })
    article.source_json = source_json
    article.verrou_id = None
    db.add(article)
    db.commit()
    db.refresh(article)
    candidate_id = str(
        source_json.get("guided_candidate_id") or f"UPLOAD-{int(article.id)}"
    )
    source = {
        "candidate_id": candidate_id,
        "candidate_kind": "scientific_article",
        "title": article.title,
        "authors": list(source_json.get("authors") or []),
        "year": article.year,
        "doi": article.doi,
        "url": article.url,
        "provider": "consultant_upload",
        "source": "consultant_upload",
        "open_access": True,
        "relevance_role": source_json.get("consultant_evidence_role") or "connected_evidence",
        "direct_evidence": False,
        "scientific_evidence_eligible": True,
        "consultant_decision": "accepted",
        "consultant_reason": "Publication PDF ajoutÃ©e explicitement par le consultant.",
        "target_verrous": target_verrous,
        "section_ids": list(source_json.get("section_ids") or []),
        "guided_session_id": session_id,
        "corpus_scope_id": corpus_scope_id,
        "fulltext_verified": True,
        "fulltext_preparation": {
            "ok": True,
            "status": extraction.get("status") or "text_extracted_from_uploaded_pdf",
            "retrieval_stage": "consultant_upload",
            "mcp_called": False,
            "article_id": int(article.id),
            "article_created": True,
            "usable_as_scientific_evidence": True,
            "proof_policy": "fulltext_verified_only",
            "output_path": extraction.get("output_path"),
            "prepared_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    selected_sources = [
        dict(row)
        for row in (snapshot.get("selected_sources") or [])
        if isinstance(row, dict)
        and str(row.get("candidate_id") or "") != candidate_id
        and int((row.get("fulltext_preparation") or {}).get("article_id") or 0)
        != int(article.id)
    ]
    selected_sources.append(source)
    agent.repository.update(
        db,
        session_id,
        selected_sources=selected_sources,
        context_updates={
            "last_uploaded_article_id": int(article.id),
            "last_uploaded_candidate_id": candidate_id,
        },
    )

    from agents.EnnoScholar.consultant_plan_service import write_json

    accepted_sources = [
        row
        for row in selected_sources
        if row.get("consultant_decision") == "accepted"
    ]
    write_json(
        agent._session_sources_path(project, session_id),
        {
            "ok": True,
            "payload_type": "guided_accepted_sources_v2_fulltext_gated",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "sources": accepted_sources,
        },
    )
    return source


def _guided_corpus_run(
    db: Session,
    project: Any,
    *,
    session_id: str,
    create: bool = False,
) -> tuple[Any, Any, dict[str, Any]]:
    """Résout la session et son ScholarRun privé, sans corpus global implicite."""
    from db.models import ScholarRun

    agent = get_guided_research_agent()
    session = agent.state_manager.get_session(
        db, session_id, include_messages=False
    )
    if int(session.project_id) != int(project.id):
        raise PermissionError("Cette conversation appartient à un autre projet.")
    snapshot = agent.repository.snapshot(db, session_id)
    context = dict(snapshot.get("context") or {})
    scope_id = str(context.get("corpus_scope_id") or session_id).strip()
    entry_module = str(snapshot.get("entry_module") or "").casefold()

    rows = (
        db.query(ScholarRun)
        .filter(ScholarRun.project_id == project.id)
        .filter(
            ScholarRun.status.in_([
                "guided_conversation_corpus",
                "guided_research_standalone",
                "improvement_corpus",
            ])
        )
        .order_by(ScholarRun.created_at.desc(), ScholarRun.id.desc())
        .all()
    )
    run = None
    for candidate in rows:
        raw = dict(candidate.raw_result_json or {})
        session_ids = {
            str(value).strip()
            for value in (raw.get("guided_session_ids") or [])
            if str(value).strip()
        }
        if (
            str(raw.get("corpus_scope_id") or "").strip() == scope_id
            or str(raw.get("guided_session_id") or "").strip() == session_id
            or session_id in session_ids
        ):
            run = candidate
            break

    if run is None and create:
        from services.guided_research_source_preparation_service import (
            _get_or_create_improvement_scholar_run,
            get_or_create_guided_conversation_scholar_run,
        )

        if entry_module == "ennoamel":
            run = _get_or_create_improvement_scholar_run(
                db, project, scope_id, session_id
            )
        else:
            run = get_or_create_guided_conversation_scholar_run(
                db,
                project,
                scope_id,
                session_id,
                context=context,
            )
    return session, run, snapshot


def _serialize_guided_corpus_article(article: Any) -> dict[str, Any]:
    from schemas.scholar import ArticleRead

    payload = ArticleRead.model_validate(article).model_dump(mode="json")
    source_json = (
        dict(article.source_json)
        if isinstance(article.source_json, dict)
        else {}
    )
    evidence = (
        dict(source_json.get("evidence_preflight") or {})
        if isinstance(source_json.get("evidence_preflight"), dict)
        else {}
    )
    status = str(evidence.get("evidence_status") or "NOT_CHECKED").upper()
    manual_upload_required = status in {
        "ACCESS_UNAVAILABLE",
        "BROWSER_DOWNLOAD_REQUIRED",
        "ABSTRACT_READY",
        "METADATA_ONLY",
        "EXTRACTION_FAILED",
        "NOT_CHECKED",
    } and not bool(evidence.get("fulltext_ready"))
    payload.update({
        "source_json": source_json,
        "evidence_status": status,
        "evidence_label": (
            "PDF de l’article non récupéré"
            if manual_upload_required
            else evidence.get("evidence_label")
        ),
        "evidence_usable": bool(evidence.get("evidence_usable")),
        "fulltext_ready": bool(evidence.get("fulltext_ready")),
        "candidate_only": bool(evidence.get("candidate_only", True)),
        "access_check_status": evidence.get("access_check_status"),
        "evidence_reason_code": evidence.get("reason_code"),
        "evidence_reason_detail": evidence.get("reason_detail"),
        "evidence_recommended_action": evidence.get("recommended_action"),
        "evidence_access_kind": evidence.get("access_kind"),
        "manual_upload_required": manual_upload_required,
    })
    return payload


def read_guided_research_corpus(
    db: Session,
    project: Any,
    *,
    session_id: str,
) -> dict[str, Any]:
    from db.models import Article

    _, run, snapshot = _guided_corpus_run(
        db, project, session_id=session_id, create=False
    )
    context = dict(snapshot.get("context") or {})
    if run is None:
        return {
            "ok": True,
            "session_id": session_id,
            "corpus_scope_id": context.get("corpus_scope_id") or session_id,
            "scholar_run_id": None,
            "articles": [],
        }
    articles = (
        db.query(Article)
        .filter(Article.scholar_run_id == run.id)
        .filter(Article.consultant_status == "garde")
        .order_by(Article.score.desc().nullslast(), Article.id.asc())
        .all()
    )
    return {
        "ok": True,
        "session_id": session_id,
        "corpus_scope_id": context.get("corpus_scope_id") or session_id,
        "scholar_run_id": int(run.id),
        "articles": [
            _serialize_guided_corpus_article(article) for article in articles
        ],
    }


def rebuild_guided_research_corpus_cards(
    db: Session,
    project: Any,
    *,
    session_id: str,
    force: bool = True,
) -> dict[str, Any]:
    from services.article_card_builder import (
        build_article_cards_for_selected_articles,
    )

    _, run, snapshot = _guided_corpus_run(
        db, project, session_id=session_id, create=True
    )
    if run is None:
        raise LookupError("Corpus de conversation introuvable.")
    context = dict(snapshot.get("context") or {})
    scope_id = str(context.get("corpus_scope_id") or session_id).strip()
    return build_article_cards_for_selected_articles(
        db,
        project,
        mode="auto",
        force=force,
        scholar_run_id=int(run.id),
        scope_id=scope_id,
    )


def remove_guided_research_corpus_article(
    db: Session,
    project: Any,
    *,
    session_id: str,
    article_id: int,
) -> dict[str, Any]:
    from db.models import Article

    agent = get_guided_research_agent()
    _, run, snapshot = _guided_corpus_run(
        db, project, session_id=session_id, create=False
    )
    if run is None:
        raise LookupError("Corpus de conversation introuvable.")
    article = (
        db.query(Article)
        .filter(Article.id == article_id)
        .filter(Article.scholar_run_id == run.id)
        .first()
    )
    if article is None:
        raise LookupError("Article absent de cette conversation.")
    article.consultant_status = "rejete"
    db.add(article)
    db.commit()

    source_json = dict(article.source_json or {})
    candidate_id = str(source_json.get("guided_candidate_id") or "").strip()
    selected_sources = []
    for raw in (snapshot.get("selected_sources") or []):
        if not isinstance(raw, dict):
            continue
        source = dict(raw)
        source_article_id = int(
            (source.get("fulltext_preparation") or {}).get("article_id") or 0
        )
        if (
            source_article_id == int(article_id)
            or (
                candidate_id
                and str(source.get("candidate_id") or "") == candidate_id
            )
        ):
            source["consultant_decision"] = "rejected"
            source["consultant_reason"] = "Retiré du corpus de cette conversation."
        selected_sources.append(source)
    agent.repository.update(
        db, session_id, selected_sources=selected_sources
    )
    cards = rebuild_guided_research_corpus_cards(
        db, project, session_id=session_id, force=True
    )
    return {
        "ok": True,
        "article_id": int(article_id),
        "phase_2": cards,
        "corpus": read_guided_research_corpus(
            db, project, session_id=session_id
        ),
    }


def record_guided_pipeline_result(
    db: Session,
    project: Any,
    *,
    session_id: str,
    result: dict[str, Any],
) -> None:
    """Rattache le rÃ©sultat du pipeline commun Ã  la bonne conversation."""

    agent = get_guided_research_agent()
    session = agent.state_manager.get_session(
        db,
        session_id,
        include_messages=False,
    )
    if int(session.project_id) != int(project.id):
        raise PermissionError("Cette conversation appartient Ã  un autre projet.")
    try:
        from agents.EnnoScholar.guided_research.lot1.domain.enums import (
            GuidedResearchState,
        )
    except Exception:
        from modules.EnnoScholar.guided_research.lot1.domain.enums import (  # type: ignore
            GuidedResearchState,
        )

    if result.get("ok"):
        # BEGIN ENNOSCHOLAR_SESSION_VERSION_HISTORY_V4
        current_snapshot = agent.repository.snapshot(db, session_id)
        current_context = dict(current_snapshot.get("context") or {})
        version_history = [
            dict(row)
            for row in (current_context.get("state_of_art_versions") or [])
            if isinstance(row, dict)
        ]
        new_version = result.get("state_of_art_version")
        if isinstance(new_version, dict) and new_version.get("version_id"):
            if not any(
                row.get("version_id") == new_version.get("version_id")
                for row in version_history
            ):
                version_history.append(dict(new_version))
        # END ENNOSCHOLAR_SESSION_VERSION_HISTORY_V4
        agent.repository.update(
            db,
            session_id,
            draft={
                "ok": True,
                "pipeline": "phase_1_to_phase_5",
                "markdown": result.get("markdown") or "",
                "state_of_art_view": result.get("state_of_art_view") or {},
                "paths": result.get("paths") or {},
            },
            state=GuidedResearchState.DRAFT_READY,
            ready_to_write=False,
            context_updates={
                "pipeline_execution_requested": False,
                "standalone_full_pipeline_completed": True,
                "state_of_art_versions": version_history,
                "latest_state_of_art_version": (
                    dict(new_version)
                    if isinstance(new_version, dict)
                    else current_context.get("latest_state_of_art_version")
                ),
            },
        )
    else:
        agent.repository.update(
            db,
            session_id,
            state=GuidedResearchState.READY_TO_WRITE,
            ready_to_write=True,
            context_updates={
                "pipeline_execution_requested": False,
                "last_pipeline_status": result.get("status"),
            },
        )


def ensure_guided_research_tables(engine: Any) -> None:
    get_guided_research_agent().state_manager.ensure_schema(engine)


def create_guided_research_session(
    db: Session,
    project: Any,
    *,
    user_id: int | None,
    target_mode: str = "global",
    entry_module: str = "ennoscholar",
    context_updates: dict[str, Any] | None = None,
    handoff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from agents.EnnoScholar.guided_research.lot1.domain.enums import (
            GuidedResearchEntryModule,
            GuidedResearchTargetMode,
        )
    except Exception:
        from modules.EnnoScholar.guided_research.lot1.domain.enums import (  # type: ignore
            GuidedResearchEntryModule,
            GuidedResearchTargetMode,
        )
    # BEGIN ENNOSCHOLAR_HANDOFF_V1
    # Le snapshot est résolu AVANT le premier message consultant puis figé dans
    # context_json. Sans payload explicite, on photographie automatiquement le
    # dernier état du projet afin de rester compatible avec le frontend V5.
    from services.ennoscholar_handoff_service import (
        build_guided_research_handoff_context,
    )

    resolved_context_updates = build_guided_research_handoff_context(
        db,
        project,
        requested_handoff=handoff,
    )
    if context_updates:
        resolved_context_updates.update(dict(context_updates))

    session = get_guided_research_agent().create_session(
        db,
        project,
        created_by_user_id=user_id,
        target_mode=GuidedResearchTargetMode(target_mode),
        entry_module=GuidedResearchEntryModule(entry_module),
    )
    if resolved_context_updates:
        get_guided_research_agent().repository.update(
            db,
            session.session_id,
            context_updates=resolved_context_updates,
        )
        session = get_guided_research_agent().state_manager.get_session(
            db,
            session.session_id,
        )
    # END ENNOSCHOLAR_HANDOFF_V1
    return session.model_dump(mode="json")


def list_guided_research_sessions(
    db: Session,
    project: Any,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Retourne les conversations d'un projet avec un libellé exploitable par l'UI."""
    manager = get_guided_research_agent().state_manager
    sessions = manager.list_project_sessions(
        db,
        int(project.id),
        limit=limit,
        include_messages=True,
    )
    output: list[dict[str, Any]] = []
    for session in sessions:
        messages = list(session.messages or [])
        consultant_messages = [
            message
            for message in messages
            if str(getattr(message.role, "value", message.role)).casefold()
            == "consultant"
        ]
        first_consultant = consultant_messages[0] if consultant_messages else None
        last_message = messages[-1] if messages else None
        title = (
            str(first_consultant.content).strip()
            if first_consultant is not None
            else "Nouvelle conversation"
        )
        preview = (
            str(last_message.content).strip()
            if last_message is not None
            else "Aucun message pour le moment."
        )
        row = session.model_dump(mode="json")
        row.update(
            {
                "title": title[:90],
                "preview": preview[:180],
                "message_count": len(messages),
            }
        )
        output.append(row)
    return output


def delete_guided_research_session(
    db: Session,
    project: Any,
    *,
    session_id: str,
) -> None:
    manager = get_guided_research_agent().state_manager
    session = manager.get_session(
        db,
        session_id,
        include_messages=False,
    )
    if int(session.project_id) != int(project.id):
        raise PermissionError("Cette conversation appartient à un autre projet.")
    manager.delete_session(db, session_id)


def send_guided_research_message(
    db: Session,
    project: Any,
    *,
    session_id: str,
    message: str,
) -> dict[str, Any]:
    result = get_guided_research_agent().handle_message(
        db,
        project,
        session_id=session_id,
        consultant_message=message,
    )
    return result.model_dump(mode="json")


def run_guided_research_requests(
    db: Session,
    project: Any,
    *,
    session_id: str,
    requests_payload: list[dict[str, Any]],
) -> dict[str, Any]:
    """Exécute un plan de recherche explicite dans une session guidée existante.

    Ce point d'entrée sert aux modules orchestrateurs qui connaissent déjà le
    passage et ses sous-sections. Il évite qu'une demande éditoriale très large
    dépende uniquement de l'interprétation conversationnelle d'une requête.
    """

    agent = get_guided_research_agent()
    session = agent.state_manager.get_session(db, session_id)
    if int(session.project_id) != int(project.id):
        raise PermissionError("Cette session appartient à un autre projet.")
    return agent._run_research(db, project, session, requests_payload)


def decide_guided_research_sources(
    db: Session,
    project: Any,
    *,
    session_id: str,
    candidate_ids: list[str],
    decision: str,
    reason: str = "",
    prepare_after_acceptance: bool = True,
) -> dict[str, Any]:
    agent = get_guided_research_agent()
    result = agent.decide_sources(
        db,
        project,
        session_id=session_id,
        candidate_ids=candidate_ids,
        decision=decision,
        reason=reason,
        prepare_after_acceptance=prepare_after_acceptance,
    )
    response = result.model_dump(mode="json")
    normalized_decision = str(decision or "").strip().casefold()
    accepted = normalized_decision in {"accept", "accepted", "garde"}
    if not (accepted and prepare_after_acceptance):
        return response

    from services.guided_research_source_preparation_service import (
        prepare_accepted_guided_sources,
    )

    snapshot = agent.repository.snapshot(db, session_id)
    preparation = prepare_accepted_guided_sources(
        db,
        project,
        sources=list(snapshot.get("selected_sources") or []),
        candidate_ids=candidate_ids,
        rebuild_scientific_payloads=True,
        standalone_context=dict(snapshot.get("context") or {}),
        guided_session_id=session_id,
        entry_module=str(snapshot.get("entry_module") or ""),
        corpus_scope_id=str(
            (snapshot.get("context") or {}).get("corpus_scope_id")
            or session_id
        ),
    )
    updated_sources = list(preparation.pop("sources", []) or [])
    refreshed_coverage = (
        agent._coverage(project, result.brief)
        if result.brief is not None
        else dict(snapshot.get("coverage") or {})
    )
    agent.repository.update(
        db,
        session_id,
        selected_sources=updated_sources,
        coverage=refreshed_coverage,
        context_updates={
            "last_source_preparation": preparation,
        },
    )

    accepted_sources = [
        dict(row)
        for row in updated_sources
        if row.get("consultant_decision") == "accepted"
    ]
    from agents.EnnoScholar.consultant_plan_service import write_json

    write_json(
        agent._session_sources_path(project, session_id),
        {
            "ok": True,
            "payload_type": "guided_accepted_sources_v2_fulltext_gated",
            "updated_at": preparation.get("prepared_at"),
            "sources": accepted_sources,
            "preparation": preparation,
            "policy": {
                "consultant_validated": True,
                "direct_fulltext_attempt_first": True,
                "mcp_fallback_after_direct_failure": True,
                "scientific_evidence_requires_verified_fulltext": True,
                "official_documentation_scope_limited": True,
            },
        },
    )
    response.setdefault("metadata", {})["source_preparation"] = preparation
    response.setdefault("metadata", {})["coverage"] = refreshed_coverage
    reports = list(preparation.get("reports") or [])
    targeted_count = len(reports)
    accepted_count = len(accepted_sources)
    fulltext_count = sum(
        1
        for source in accepted_sources
        if isinstance(source.get("fulltext_preparation"), dict)
        and bool(
            (source.get("fulltext_preparation") or {}).get(
                "usable_as_scientific_evidence"
            )
        )
    )
    ready_count = int(
        preparation.get("writing_ready_cards_count")
        or fulltext_count
    )
    mcp_count = int(preparation.get("mcp_calls_count") or 0)
    mcp_success_count = int(preparation.get("mcp_success_count") or 0)
    failed_titles = [
        str(report.get("title") or "Source sans titre").strip()
        for report in reports
        if report.get("ready_for_writing") is False
    ]
    response["assistant_message"] = (
        f"{response.get('assistant_message', '').strip()} "
        f"Le corpus total contient {accepted_count} source(s) validée(s), dont "
        f"{fulltext_count} texte(s) intégral(aux) vérifié(s) et {ready_count} "
        f"Article Card(s) prête(s) pour la rédaction. "
        f"Cette mise à jour a traité {targeted_count} source(s) ; le MCP a été "
        f"lancé {mcp_count} fois après échec direct et a permis de préparer "
        f"{mcp_success_count} source(s)."
        + (
            " Sources encore indisponibles : "
            + "; ".join(failed_titles[:5])
            + "."
            if failed_titles
            else ""
        )
    ).strip()
    return response


def read_guided_research_session(db: Session, session_id: str) -> dict[str, Any]:
    return get_guided_research_agent().get_session(db, session_id)


def submit_guided_research_structured_plan(
    db: Session,
    project: Any,
    *,
    session_id: str,
    raw_request: str,
    sections: list[dict[str, Any]],
    general_constraints: list[str] | None = None,
) -> dict[str, Any]:
    """Soumet un plan déjà structuré sans perdre l'ordre ni la hiérarchie."""
    result = get_guided_research_agent().submit_structured_prompt(
        db,
        project,
        session_id=session_id,
        raw_request=raw_request,
        sections=sections,
        general_constraints=general_constraints or [],
    )
    return result.model_dump(mode="json")


def accept_guided_research_plan(
    db: Session,
    project: Any,
    *,
    session_id: str,
) -> dict[str, Any]:
    """Valide le plan interprété puis lance couverture/recherche/préparation."""
    result = get_guided_research_agent().handle_message(
        db,
        project,
        session_id=session_id,
        consultant_message="Je valide le plan proposé.",
    )
    return result.model_dump(mode="json")
