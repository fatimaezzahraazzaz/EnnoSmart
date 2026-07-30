# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .enums import (
    ConsultantIntent,
    ConversationRole,
    GuidedResearchEntryModule,
    GuidedResearchState,
    GuidedResearchTargetMode,
    NextAction,
    RequestedDepth,
    RequestedEntityType,
)

try:  # EnnoSmart backend normal path
    from db.database import Base  # type: ignore
except Exception:
    try:  # Import possible depuis la racine si backend_api est un package
        from backend_api.db.database import Base  # type: ignore
    except Exception:  # Tests autonomes / outils hors backend
        from sqlalchemy.orm import declarative_base

        Base = declarative_base()


JSONBType = JSONB().with_variant(JSON(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid4())


def _dedupe_strings(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = " ".join(str(value or "").split()).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


class RequestedTopic(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    topic_id: str = Field(default_factory=new_uuid)
    name: str = Field(min_length=2, max_length=300)
    entity_type: RequestedEntityType = RequestedEntityType.OTHER
    requested_dimensions: list[str] = Field(default_factory=list)
    target_sections: list[str] = Field(default_factory=list)
    target_verrous: list[str] = Field(default_factory=list)
    source_preferences: list[str] = Field(default_factory=list)
    notes: str = ""
    required: bool = True

    @field_validator(
        "requested_dimensions", "target_sections", "target_verrous",
        "source_preferences", mode="after"
    )
    @classmethod
    def dedupe_lists(cls, value: list[str]) -> list[str]:
        return _dedupe_strings(value)


class RequestedSection(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    section_id: str = Field(default_factory=new_uuid)
    title: str = Field(min_length=2, max_length=400)
    order: int = Field(ge=1, le=1000)
    parent_id: str | None = None
    level: int = Field(default=1, ge=1, le=6)
    objective: str = ""
    instructions: list[str] = Field(default_factory=list)
    required_dimensions: list[str] = Field(default_factory=list)
    visual_requirements: list[str] = Field(default_factory=list)
    source_preferences: list[str] = Field(default_factory=list)
    topic_ids: list[str] = Field(default_factory=list)
    target_verrous: list[str] = Field(default_factory=list)
    depth: RequestedDepth = RequestedDepth.STANDARD
    target_words: int | None = Field(default=None, ge=100, le=20000)

    @field_validator(
        "topic_ids",
        "target_verrous",
        "instructions",
        "required_dimensions",
        "visual_requirements",
        "source_preferences",
        mode="after",
    )
    @classmethod
    def dedupe_lists(cls, value: list[str]) -> list[str]:
        return _dedupe_strings(value)


class ConsultantBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    raw_request: str = ""
    requested_sections: list[RequestedSection] = Field(default_factory=list)
    requested_topics: list[RequestedTopic] = Field(default_factory=list)
    verrou_instructions: dict[str, list[str]] = Field(default_factory=dict)

    use_selected_articles: bool = True
    use_previous_cir: bool = False
    previous_years: list[int] = Field(default_factory=list)
    research_new_sources: bool = True

    output_mode: GuidedResearchTargetMode = GuidedResearchTargetMode.GLOBAL
    language: str = "fr"
    general_constraints: list[str] = Field(default_factory=list)
    desired_depth: RequestedDepth = RequestedDepth.DETAILED

    updated_at: datetime = Field(default_factory=utcnow)

    @field_validator("previous_years", mode="after")
    @classmethod
    def normalize_years(cls, value: list[int]) -> list[int]:
        return sorted({int(year) for year in value if 1900 <= int(year) <= 2200}, reverse=True)

    @field_validator("general_constraints", mode="after")
    @classmethod
    def normalize_constraints(cls, value: list[str]) -> list[str]:
        return _dedupe_strings(value)

    @field_validator("verrou_instructions", mode="after")
    @classmethod
    def normalize_verrou_instructions(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        return {
            str(key): _dedupe_strings(instructions)
            for key, instructions in (value or {}).items()
            if str(key).strip() and _dedupe_strings(instructions)
        }

    @model_validator(mode="after")
    def normalize_sections_and_topic_links(self) -> "ConsultantBrief":
        self.requested_sections.sort(key=lambda item: (item.order, item.title.casefold()))
        for index, section in enumerate(self.requested_sections, start=1):
            section.order = index

        valid_topic_ids = {topic.topic_id for topic in self.requested_topics}
        for section in self.requested_sections:
            section.topic_ids = [topic_id for topic_id in section.topic_ids if topic_id in valid_topic_ids]
        self.updated_at = utcnow()
        return self


class IntentClassification(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    intent: ConsultantIntent
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""
    target_topic: str | None = None
    target_source_id: str | None = None
    requested_actions: list[ConsultantIntent] = Field(default_factory=list)
    forbidden_actions: list[ConsultantIntent] = Field(default_factory=list)
    explicit_write_command: bool = False
    explicit_plan_approval: bool = False
    explicit_research_command: bool = False
    replace_current_plan: bool = False
    use_current_sources_only: bool = False
    writing_source_scope: Literal[
        "unspecified",
        "all_validated",
        "baseline_verrou_corpus",
        "guided_research_additions",
        "explicit_selection",
    ] = "unspecified"
    writing_source_identifiers: list[str] = Field(default_factory=list)
    requested_source_count: int | None = Field(default=None, ge=1, le=500)
    needs_clarification: bool = False
    corrected_message: str = ""
    context_references: list[str] = Field(default_factory=list)
    extracted_text: str = ""
    classifier: str = "llm_contextual"

    @field_validator(
        "rationale",
        "corrected_message",
        "extracted_text",
        "classifier",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str:
        return "" if value is None else str(value)

    @field_validator(
        "requested_actions",
        "forbidden_actions",
        "context_references",
        "writing_source_identifiers",
        mode="before",
    )
    @classmethod
    def normalize_optional_lists(cls, value: Any) -> Any:
        return [] if value is None else value


class ConversationMemory(BaseModel):
    """Mémoire projet durable, limitée aux faits établis dans la conversation."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    project_facts: list[str] = Field(default_factory=list)
    consultant_preferences: list[str] = Field(default_factory=list)
    validated_decisions: list[str] = Field(default_factory=list)
    rejected_options: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    current_focus: str = ""
    last_consultant_goal: str = ""

    @field_validator(
        "project_facts",
        "consultant_preferences",
        "validated_decisions",
        "rejected_options",
        "open_questions",
        mode="after",
    )
    @classmethod
    def dedupe_memory_lists(cls, value: list[str]) -> list[str]:
        return _dedupe_strings(value)[:100]


class ConversationUnderstanding(BaseModel):
    """Compréhension structurée d'un tour avant toute action applicative."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    classification: IntentClassification
    assistant_message: str = ""
    plan: list[dict[str, Any]] = Field(default_factory=list)
    topics: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    search_requests: list[dict[str, Any]] = Field(default_factory=list)
    memory: ConversationMemory = Field(default_factory=ConversationMemory)
    interpreter: dict[str, Any] = Field(default_factory=dict)


class BriefParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    brief: ConsultantBrief
    summary: str
    changes: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    parser: str = "deterministic"


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False, from_attributes=True)

    message_id: str = Field(default_factory=new_uuid)
    role: ConversationRole
    content: str = Field(min_length=1)
    intent: ConsultantIntent | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class GuidedResearchSessionData(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False, from_attributes=True)

    session_id: str = Field(default_factory=new_uuid)
    project_id: int
    created_by_user_id: int | None = None

    entry_module: GuidedResearchEntryModule
    target_mode: GuidedResearchTargetMode
    state: GuidedResearchState = GuidedResearchState.BRIEF_IN_PROGRESS

    brief: ConsultantBrief | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    messages: list[ConversationTurn] = Field(default_factory=list)

    ready_to_write: bool = False
    version: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ConversationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    session_id: str
    intent: ConsultantIntent
    state: GuidedResearchState
    assistant_message: str
    next_action: NextAction
    brief: ConsultantBrief | None = None
    ready_to_write: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class GuidedResearchSessionORM(Base):
    __tablename__ = "guided_research_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    entry_module: Mapped[str] = mapped_column(String(32), nullable=False)
    target_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(
        String(50), nullable=False, default=GuidedResearchState.BRIEF_IN_PROGRESS.value, index=True
    )

    brief_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)

    # Colonnes préparées pour les lots suivants afin d'éviter une migration à chaque étape.
    coverage_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    research_plan_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    selected_sources_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONBType, nullable=False, default=list)
    evidence_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONBType, nullable=False, default=list)
    writing_contract_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    draft_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)

    ready_to_write: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    messages: Mapped[list["GuidedResearchMessageORM"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="GuidedResearchMessageORM.created_at",
    )

    __table_args__ = (
        Index("ix_guided_research_project_state", "project_id", "state"),
        Index("ix_guided_research_project_updated", "project_id", "updated_at"),
    )


class GuidedResearchMessageORM(Base):
    __tablename__ = "guided_research_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("guided_research_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    session: Mapped[GuidedResearchSessionORM] = relationship(back_populates="messages")
