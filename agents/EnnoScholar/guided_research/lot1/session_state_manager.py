# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from .json_safety import sanitize_json_text
from .domain.enums import (
    ConsultantIntent,
    ConversationRole,
    GuidedResearchEntryModule,
    GuidedResearchState,
    GuidedResearchTargetMode,
)
from .domain.models import (
    Base,
    ConsultantBrief,
    ConversationTurn,
    GuidedResearchMessageORM,
    GuidedResearchSessionData,
    GuidedResearchSessionORM,
    new_uuid,
    utcnow,
)


class GuidedResearchSessionNotFoundError(LookupError):
    pass


class InvalidStateTransitionError(ValueError):
    pass


class ConcurrentSessionUpdateError(RuntimeError):
    pass


ALLOWED_TRANSITIONS: dict[GuidedResearchState, set[GuidedResearchState]] = {
    GuidedResearchState.BRIEF_IN_PROGRESS: {
        GuidedResearchState.BRIEF_PARSED,
        GuidedResearchState.CANCELLED,
        GuidedResearchState.BLOCKED,
    },
    GuidedResearchState.BRIEF_PARSED: {
        GuidedResearchState.BRIEF_IN_PROGRESS,
        GuidedResearchState.COVERAGE_ANALYZED,
        GuidedResearchState.RESEARCH_PLAN_READY,
        GuidedResearchState.CANCELLED,
        GuidedResearchState.BLOCKED,
    },
    GuidedResearchState.COVERAGE_ANALYZED: {
        GuidedResearchState.RESEARCH_PLAN_READY,
        GuidedResearchState.READY_TO_WRITE,
        GuidedResearchState.BRIEF_IN_PROGRESS,
        GuidedResearchState.CANCELLED,
        GuidedResearchState.BLOCKED,
    },
    GuidedResearchState.RESEARCH_PLAN_READY: {
        GuidedResearchState.RESEARCH_IN_PROGRESS,
        GuidedResearchState.BRIEF_IN_PROGRESS,
        GuidedResearchState.CANCELLED,
        GuidedResearchState.BLOCKED,
    },
    GuidedResearchState.RESEARCH_IN_PROGRESS: {
        GuidedResearchState.SOURCES_PROPOSED,
        GuidedResearchState.RESEARCH_REFINEMENT,
        GuidedResearchState.BLOCKED,
        GuidedResearchState.CANCELLED,
    },
    GuidedResearchState.SOURCES_PROPOSED: {
        GuidedResearchState.WAITING_CONSULTANT_FEEDBACK,
        GuidedResearchState.RESEARCH_REFINEMENT,
        GuidedResearchState.READY_TO_WRITE,
        GuidedResearchState.CANCELLED,
    },
    GuidedResearchState.WAITING_CONSULTANT_FEEDBACK: {
        GuidedResearchState.RESEARCH_REFINEMENT,
        GuidedResearchState.READY_TO_WRITE,
        GuidedResearchState.BRIEF_IN_PROGRESS,
        GuidedResearchState.CANCELLED,
    },
    GuidedResearchState.RESEARCH_REFINEMENT: {
        GuidedResearchState.RESEARCH_IN_PROGRESS,
        GuidedResearchState.SOURCES_PROPOSED,
        GuidedResearchState.BLOCKED,
        GuidedResearchState.CANCELLED,
    },
    GuidedResearchState.READY_TO_WRITE: {
        GuidedResearchState.WRITING_IN_PROGRESS,
        GuidedResearchState.RESEARCH_REFINEMENT,
        GuidedResearchState.BRIEF_IN_PROGRESS,
        GuidedResearchState.CANCELLED,
    },
    GuidedResearchState.WRITING_IN_PROGRESS: {
        GuidedResearchState.DRAFT_READY,
        GuidedResearchState.BLOCKED,
    },
    GuidedResearchState.DRAFT_READY: {
        GuidedResearchState.FINAL_VALIDATION,
        GuidedResearchState.WRITING_IN_PROGRESS,
        GuidedResearchState.ACCEPTED,
        GuidedResearchState.CANCELLED,
    },
    GuidedResearchState.FINAL_VALIDATION: {
        GuidedResearchState.WRITING_IN_PROGRESS,
        GuidedResearchState.ACCEPTED,
        GuidedResearchState.CANCELLED,
    },
    GuidedResearchState.BLOCKED: {
        GuidedResearchState.BRIEF_IN_PROGRESS,
        GuidedResearchState.RESEARCH_REFINEMENT,
        GuidedResearchState.CANCELLED,
    },
    GuidedResearchState.ACCEPTED: set(),
    GuidedResearchState.CANCELLED: set(),
}


class GuidedResearchSessionStateManager:
    """Persistance transactionnelle des sessions et messages dans PostgreSQL."""

    def ensure_schema(self, engine: Engine) -> None:
        Base.metadata.create_all(
            bind=engine,
            tables=[GuidedResearchSessionORM.__table__, GuidedResearchMessageORM.__table__],
        )

    def create_session(
        self,
        db: Session,
        *,
        project_id: int,
        created_by_user_id: int | None,
        entry_module: GuidedResearchEntryModule,
        target_mode: GuidedResearchTargetMode,
        initial_context: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> GuidedResearchSessionData:
        orm = GuidedResearchSessionORM(
            id=session_id or new_uuid(),
            project_id=project_id,
            created_by_user_id=created_by_user_id,
            entry_module=entry_module.value,
            target_mode=target_mode.value,
            state=GuidedResearchState.BRIEF_IN_PROGRESS.value,
            context_json=sanitize_json_text(dict(initial_context or {})),
            brief_json={},
        )
        db.add(orm)
        try:
            db.commit()
            db.refresh(orm)
        except SQLAlchemyError:
            db.rollback()
            raise
        return self._to_domain(orm, include_messages=True)

    def get_session(
        self,
        db: Session,
        session_id: str,
        *,
        include_messages: bool = True,
    ) -> GuidedResearchSessionData:
        orm = db.get(GuidedResearchSessionORM, session_id)
        if orm is None:
            raise GuidedResearchSessionNotFoundError(
                f"Session guided research introuvable : {session_id}"
            )
        return self._to_domain(orm, include_messages=include_messages)

    def list_project_sessions(
        self,
        db: Session,
        project_id: int,
        *,
        limit: int = 50,
        include_messages: bool = False,
        entry_module: GuidedResearchEntryModule | str | None = None,
    ) -> list[GuidedResearchSessionData]:
        statement = select(GuidedResearchSessionORM).where(
            GuidedResearchSessionORM.project_id == project_id,
        )
        if entry_module is not None:
            module_value = (
                entry_module.value
                if isinstance(entry_module, GuidedResearchEntryModule)
                else str(entry_module).strip()
            )
            statement = statement.where(
                GuidedResearchSessionORM.entry_module == module_value,
            )
        statement = statement.order_by(
            GuidedResearchSessionORM.updated_at.desc(),
        ).limit(max(1, min(limit, 500)))
        if include_messages:
            statement = statement.options(
                selectinload(GuidedResearchSessionORM.messages),
            )
        rows = db.scalars(statement).all()
        return [
            self._to_domain(row, include_messages=include_messages)
            for row in rows
        ]

    def delete_session(self, db: Session, session_id: str) -> None:
        orm = self._get_orm(db, session_id)
        try:
            db.delete(orm)
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise

    def update_brief(
        self,
        db: Session,
        session_id: str,
        brief: ConsultantBrief,
        *,
        expected_version: int | None = None,
    ) -> GuidedResearchSessionData:
        orm = self._get_orm(db, session_id)
        self._check_version(orm, expected_version)
        orm.brief_json = sanitize_json_text(brief.model_dump(mode="json"))
        orm.updated_at = utcnow()
        orm.version += 1
        if GuidedResearchState(orm.state) == GuidedResearchState.BRIEF_IN_PROGRESS:
            orm.state = GuidedResearchState.BRIEF_PARSED.value
        self._commit(db, orm)
        return self._to_domain(orm, include_messages=True)

    def update_context(
        self,
        db: Session,
        session_id: str,
        updates: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> GuidedResearchSessionData:
        orm = self._get_orm(db, session_id)
        self._check_version(orm, expected_version)
        merged = dict(orm.context_json or {})
        merged.update(updates or {})
        orm.context_json = sanitize_json_text(merged)
        orm.updated_at = utcnow()
        orm.version += 1
        self._commit(db, orm)
        return self._to_domain(orm, include_messages=True)

    def append_message(
        self,
        db: Session,
        session_id: str,
        *,
        role: ConversationRole,
        content: str,
        intent: ConsultantIntent | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationTurn:
        orm = self._get_orm(db, session_id)
        text = sanitize_json_text(str(content or "")).strip()
        if not text:
            raise ValueError("Le contenu du message ne peut pas être vide.")

        message = GuidedResearchMessageORM(
            id=new_uuid(),
            session_id=session_id,
            role=role.value,
            content=text,
            intent=intent.value if intent else None,
            metadata_json=sanitize_json_text(dict(metadata or {})),
            created_at=utcnow(),
        )
        db.add(message)
        orm.updated_at = utcnow()
        orm.version += 1
        try:
            db.commit()
            db.refresh(message)
        except SQLAlchemyError:
            db.rollback()
            raise
        return self._message_to_domain(message)

    def transition(
        self,
        db: Session,
        session_id: str,
        new_state: GuidedResearchState,
        *,
        force: bool = False,
        ready_to_write: bool | None = None,
        expected_version: int | None = None,
    ) -> GuidedResearchSessionData:
        orm = self._get_orm(db, session_id)
        self._check_version(orm, expected_version)
        old_state = GuidedResearchState(orm.state)
        if not force and new_state != old_state and new_state not in ALLOWED_TRANSITIONS[old_state]:
            raise InvalidStateTransitionError(
                f"Transition interdite : {old_state.value} -> {new_state.value}"
            )
        orm.state = new_state.value
        if ready_to_write is not None:
            orm.ready_to_write = bool(ready_to_write)
        elif new_state == GuidedResearchState.READY_TO_WRITE:
            orm.ready_to_write = True
        elif new_state in {
            GuidedResearchState.BRIEF_IN_PROGRESS,
            GuidedResearchState.BRIEF_PARSED,
            GuidedResearchState.RESEARCH_REFINEMENT,
        }:
            orm.ready_to_write = False
        orm.updated_at = utcnow()
        orm.version += 1
        self._commit(db, orm)
        return self._to_domain(orm, include_messages=True)

    def _get_orm(self, db: Session, session_id: str) -> GuidedResearchSessionORM:
        orm = db.get(GuidedResearchSessionORM, session_id)
        if orm is None:
            raise GuidedResearchSessionNotFoundError(
                f"Session guided research introuvable : {session_id}"
            )
        return orm

    @staticmethod
    def _check_version(orm: GuidedResearchSessionORM, expected_version: int | None) -> None:
        if expected_version is not None and orm.version != expected_version:
            raise ConcurrentSessionUpdateError(
                f"Version attendue={expected_version}, version en base={orm.version}."
            )

    @staticmethod
    def _commit(db: Session, orm: GuidedResearchSessionORM) -> None:
        try:
            db.add(orm)
            db.commit()
            db.refresh(orm)
        except SQLAlchemyError:
            db.rollback()
            raise

    def _to_domain(
        self,
        orm: GuidedResearchSessionORM,
        *,
        include_messages: bool,
    ) -> GuidedResearchSessionData:
        brief = None
        if orm.brief_json:
            try:
                brief = ConsultantBrief.model_validate(orm.brief_json)
            except ValidationError:
                # Les conversations persistées peuvent provenir d'une version
                # antérieure du schéma. On conserve tous les champs encore
                # reconnus et on ignore uniquement les métadonnées obsolètes.
                brief = ConsultantBrief.model_validate(
                    orm.brief_json,
                    extra="ignore",
                )
        messages = (
            [self._message_to_domain(message) for message in list(orm.messages or [])]
            if include_messages
            else []
        )
        return GuidedResearchSessionData(
            session_id=orm.id,
            project_id=orm.project_id,
            created_by_user_id=orm.created_by_user_id,
            entry_module=GuidedResearchEntryModule(orm.entry_module),
            target_mode=GuidedResearchTargetMode(orm.target_mode),
            state=GuidedResearchState(orm.state),
            brief=brief,
            context=dict(orm.context_json or {}),
            messages=messages,
            ready_to_write=bool(orm.ready_to_write),
            version=orm.version,
            created_at=orm.created_at,
            updated_at=orm.updated_at,
        )

    @staticmethod
    def _message_to_domain(message: GuidedResearchMessageORM) -> ConversationTurn:
        return ConversationTurn(
            message_id=message.id,
            role=ConversationRole(message.role),
            content=message.content,
            intent=ConsultantIntent(message.intent) if message.intent else None,
            metadata=dict(message.metadata_json or {}),
            created_at=message.created_at,
        )
