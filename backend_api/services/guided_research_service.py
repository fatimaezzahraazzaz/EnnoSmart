# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session


def _ensure_root() -> None:
    root = Path("C:/EnnoSmart")
    if root.exists() and str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_root()

try:
    from agents.EnnoScholar.guided_research.application.guided_research_agent import (
        EnnoScholarGuidedResearchAgent,
    )
except Exception:
    from modules.EnnoScholar.guided_research.application.guided_research_agent import (  # type: ignore
        EnnoScholarGuidedResearchAgent,
    )


_AGENT: EnnoScholarGuidedResearchAgent | None = None


def get_guided_research_agent() -> EnnoScholarGuidedResearchAgent:
    global _AGENT
    if _AGENT is None:
        _AGENT = EnnoScholarGuidedResearchAgent()
    return _AGENT


def ensure_guided_research_tables(engine: Any) -> None:
    get_guided_research_agent().state_manager.ensure_schema(engine)


def create_guided_research_session(
    db: Session,
    project: Any,
    *,
    user_id: int | None,
    target_mode: str = "global",
    entry_module: str = "ennoscholar",
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
    session = get_guided_research_agent().create_session(
        db,
        project,
        created_by_user_id=user_id,
        target_mode=GuidedResearchTargetMode(target_mode),
        entry_module=GuidedResearchEntryModule(entry_module),
    )
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
        agent._sources_path(project),
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
    ready_count = len(preparation.get("ready_article_ids") or [])
    mcp_count = int(preparation.get("mcp_calls_count") or 0)
    response["assistant_message"] = (
        f"{response.get('assistant_message', '').strip()} "
        f"Préparation scientifique terminée : {ready_count} texte(s) intégral(aux) "
        f"prêt(s), avec {mcp_count} recours au MCP après échec direct."
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
