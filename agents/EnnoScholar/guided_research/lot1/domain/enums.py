# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """Enum sérialisable comme une chaîne dans Pydantic, JSON et PostgreSQL."""

    def __str__(self) -> str:
        return self.value


class GuidedResearchEntryModule(StrEnum):
    ENNOSCHOLAR = "ennoscholar"
    ENNOAMEL = "ennoamel"


class GuidedResearchTargetMode(StrEnum):
    GLOBAL = "global"
    PER_VERROU = "per_verrou"
    SECTION_IMPROVEMENT = "section_improvement"
    FULL_CIR_IMPROVEMENT = "full_cir_improvement"


class GuidedResearchState(StrEnum):
    BRIEF_IN_PROGRESS = "brief_in_progress"
    BRIEF_PARSED = "brief_parsed"
    COVERAGE_ANALYZED = "coverage_analyzed"
    RESEARCH_PLAN_READY = "research_plan_ready"
    RESEARCH_IN_PROGRESS = "research_in_progress"
    SOURCES_PROPOSED = "sources_proposed"
    WAITING_CONSULTANT_FEEDBACK = "waiting_consultant_feedback"
    RESEARCH_REFINEMENT = "research_refinement"
    READY_TO_WRITE = "ready_to_write"
    WRITING_IN_PROGRESS = "writing_in_progress"
    DRAFT_READY = "draft_ready"
    FINAL_VALIDATION = "final_validation"
    ACCEPTED = "accepted"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ConsultantIntent(StrEnum):
    CONVERSE = "converse"
    DESCRIBE_REQUIREMENTS = "describe_requirements"
    PROPOSE_PLAN = "propose_plan"
    ADD_TOPIC = "add_topic"
    REMOVE_TOPIC = "remove_topic"
    CHANGE_PLAN = "change_plan"

    SEARCH_MORE = "search_more"
    SEARCH_ALTERNATIVE = "search_alternative"
    REPLACE_SOURCE = "replace_source"
    EXPLAIN_SOURCE = "explain_source"

    ACCEPT_PLAN = "accept_plan"
    ACCEPT_SOURCES = "accept_sources"
    START_WRITING = "start_writing"

    REVISE_DRAFT = "revise_draft"
    ACCEPT_DRAFT = "accept_draft"
    CANCEL = "cancel"

    UNKNOWN = "unknown"


class ConversationRole(StrEnum):
    CONSULTANT = "consultant"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class RequestedEntityType(StrEnum):
    SCIENTIFIC_METHOD = "scientific_method"
    SCIENTIFIC_SOFTWARE = "scientific_software"
    DATASET = "dataset"
    AI_MODEL = "ai_model"
    SOFTWARE_LIBRARY = "software_library"
    PROTOCOL = "protocol"
    STANDARD = "standard"
    HISTORICAL_TOPIC = "historical_topic"
    SCIENTIFIC_RESULT = "scientific_result"
    SCIENTIFIC_CONCEPT = "scientific_concept"
    TOOL = "tool"
    CONCEPT = "concept"
    OTHER = "other"


class RequestedDepth(StrEnum):
    SHORT = "short"
    STANDARD = "standard"
    DETAILED = "detailed"
    VERY_DETAILED = "very_detailed"


class CoverageLevel(StrEnum):
    ABSENT = "absent"
    PARTIAL = "partial"
    COVERED = "covered"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class NextAction(StrEnum):
    CONTINUE_BRIEF = "continue_brief"
    ANALYZE_COVERAGE = "analyze_coverage"
    RUN_RESEARCH = "run_research"
    REVIEW_SOURCES = "review_sources"
    WAIT_FOR_WRITE_COMMAND = "wait_for_write_command"
    START_WRITING = "start_writing"
    REVIEW_DRAFT = "review_draft"
    NONE = "none"
