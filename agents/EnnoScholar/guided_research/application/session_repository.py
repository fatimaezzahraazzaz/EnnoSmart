# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..lot1.domain.enums import GuidedResearchState
from ..lot1.domain.models import GuidedResearchSessionORM
from ..lot1.json_safety import sanitize_json_text


class GuidedResearchSessionRepository:
    """Persistance des artefacts volumineux de la session.

    Le StateManager du Lot 1 gère le brief, les messages et les transitions.
    Ce repository complète ce contrat avec les colonnes JSON déjà prévues :
    couverture, plan de recherche, sources, preuves, contrat rédactionnel et draft.
    """

    ARTIFACT_FIELDS = {
        "coverage": "coverage_json",
        "research_plan": "research_plan_json",
        "selected_sources": "selected_sources_json",
        "evidence": "evidence_json",
        "writing_contract": "writing_contract_json",
        "draft": "draft_json",
    }

    def get_orm(self, db: Session, session_id: str) -> GuidedResearchSessionORM:
        row = db.get(GuidedResearchSessionORM, session_id)
        if row is None:
            raise LookupError(f"Session guided research introuvable : {session_id}")
        return row

    def snapshot(self, db: Session, session_id: str) -> dict[str, Any]:
        row = self.get_orm(db, session_id)
        return {
            "session_id": row.id,
            "project_id": row.project_id,
            "created_by_user_id": row.created_by_user_id,
            "entry_module": row.entry_module,
            "target_mode": row.target_mode,
            "state": row.state,
            "brief": dict(row.brief_json or {}),
            "context": dict(row.context_json or {}),
            "coverage": dict(row.coverage_json or {}),
            "research_plan": dict(row.research_plan_json or {}),
            "selected_sources": list(row.selected_sources_json or []),
            "evidence": list(row.evidence_json or []),
            "writing_contract": dict(row.writing_contract_json or {}),
            "draft": dict(row.draft_json or {}),
            "ready_to_write": bool(row.ready_to_write),
            "version": int(row.version or 1),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def update(
        self,
        db: Session,
        session_id: str,
        *,
        coverage: dict[str, Any] | None = None,
        research_plan: dict[str, Any] | None = None,
        selected_sources: list[dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        writing_contract: dict[str, Any] | None = None,
        draft: dict[str, Any] | None = None,
        context_updates: dict[str, Any] | None = None,
        state: GuidedResearchState | str | None = None,
        ready_to_write: bool | None = None,
    ) -> dict[str, Any]:
        row = self.get_orm(db, session_id)
        values = {
            "coverage_json": coverage,
            "research_plan_json": research_plan,
            "selected_sources_json": selected_sources,
            "evidence_json": evidence,
            "writing_contract_json": writing_contract,
            "draft_json": draft,
        }
        for attr, value in values.items():
            if value is not None:
                setattr(row, attr, sanitize_json_text(value))
        if context_updates:
            context = dict(row.context_json or {})
            context.update(context_updates)
            row.context_json = sanitize_json_text(context)
        if state is not None:
            row.state = state.value if isinstance(state, GuidedResearchState) else str(state)
        if ready_to_write is not None:
            row.ready_to_write = bool(ready_to_write)
        row.version = int(row.version or 0) + 1
        row.updated_at = datetime.now(timezone.utc)
        try:
            db.add(row)
            db.commit()
            db.refresh(row)
        except SQLAlchemyError:
            db.rollback()
            raise
        return self.snapshot(db, session_id)

    def upsert_source_decision(
        self,
        db: Session,
        session_id: str,
        *,
        candidate_ids: list[str],
        decision: str,
        reason: str = "",
    ) -> list[dict[str, Any]]:
        row = self.get_orm(db, session_id)
        wanted = {str(x).strip() for x in candidate_ids if str(x).strip()}
        sources = list(row.selected_sources_json or [])
        for source in sources:
            if str(source.get("candidate_id") or "") in wanted:
                source["consultant_decision"] = decision
                source["consultant_reason"] = reason
                source["decision_at"] = datetime.now(timezone.utc).isoformat()
        self.update(db, session_id, selected_sources=sources)
        return sources
