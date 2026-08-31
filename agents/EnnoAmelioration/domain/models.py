from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ImprovementState(str, Enum):
    TARGET_IDENTIFICATION = "target_identification"
    AUDIT = "audit"
    PLAN = "plan"
    AWAITING_EVIDENCE = "awaiting_evidence"
    EVIDENCE_READY = "evidence_ready"
    CANDIDATE_READY = "candidate_ready"
    REVIEW = "review"
    PUBLISHED = "published"


class TargetScope(str, Enum):
    SELECTION = "selection"
    PARAGRAPH = "paragraph"
    SECTION = "section"
    MULTI_SECTION = "multi_section"
    FULL_DOCUMENT = "full_document"


class ImprovementIntent(str, Enum):
    SMALL_TALK = "small_talk"
    CLARITY = "clarity"
    STYLE = "style"
    STRUCTURE = "structure"
    CONCISION = "concision"
    ARGUMENTATION = "argumentation"
    SCIENTIFIC_ENRICHMENT = "scientific_enrichment"
    CIR_ELIGIBILITY = "cir_eligibility"
    RESEARCH = "research"
    TARGET_SELECTION = "target_selection"
    GENERAL_REVISION = "general_revision"
    CANDIDATE_REVISION = "candidate_revision"


class SpecialistRoute(str, Enum):
    WRITER = "writer"
    DIAGNOSTIC = "diagnostic"
    SCHOLAR = "scholar"
    DIAGNOSTIC_SCHOLAR = "diagnostic_scholar"


class SectionFunction(str, Enum):
    """Fonction sémantique d'une section, indépendante de son intitulé."""

    CONTEXT = "context"
    SCIENTIFIC_LANDSCAPE = "scientific_landscape"
    UNCERTAINTY = "uncertainty"
    METHOD = "method"
    PARAMETER = "parameter"
    RESULT = "result"
    LIMITATION = "limitation"
    CONTRIBUTION = "contribution"
    SYNTHESIS = "synthesis"
    OTHER = "other"


class ParsedSection(BaseModel):
    section_id: str
    title: str
    level: int = 1
    start: int
    end: int
    content: str


class AuditFinding(BaseModel):
    code: str
    label: str
    severity: str
    explanation: str
    recommendation: str
    metrics: dict[str, Any] = Field(default_factory=dict)


class SectionRoutingPlan(BaseModel):
    section_id: str
    title: str = ""
    function: SectionFunction = SectionFunction.OTHER
    confidence: float = 0.0
    classifier: str = "fallback"
    route: SpecialistRoute = SpecialistRoute.WRITER
    needs_diagnostic: bool = False
    needs_scholar: bool = False
    rationale: list[str] = Field(default_factory=list)


class RoutingDecision(BaseModel):
    intents: list[ImprovementIntent]
    target_scope: TargetScope
    needs_diagnostic: bool = False
    needs_scholar: bool = False
    needs_new_research: bool = False
    forbids_new_research: bool = False
    forbids_scholar: bool = False
    needs_project_evidence: bool = False
    specialist_route: SpecialistRoute = SpecialistRoute.WRITER
    section_function: SectionFunction = SectionFunction.OTHER
    section_confidence: float = 0.0
    semantic_classifier: str = "instruction"
    section_plan: list[SectionRoutingPlan] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    candidate_revision: bool = False
    revision_allows_evidence_enrichment: bool = False
    # V2.7 - garde-fou éditorial : une demande explicitement limitée à la
    # rédaction/structure, sans nouvel argument/fait/source, doit rester
    # Writer-only même si des mots comme « argument scientifique » apparaissent
    # dans une négation.
    editorial_only: bool = False
    strict_fact_preservation: bool = False


class ImprovementRequest(BaseModel):
    instruction: str
    full_text: str
    target_text: str
    target_scope: TargetScope
    target_section_id: str | None = None
    target_section_title: str | None = None
    project_name: str = ""
    project_domain: str = ""
    evidence_article_ids: list[int] | None = None
    evidence_scope_id: str | None = None
    evidence_cards: list[dict[str, Any]] | None = None
    research_choice: str | None = None
    guided_research_session_id: str | None = None
    # Fonction scientifique transmise à EnnoScholar. Ce champ décrit une cible
    # de recherche (méthode, contexte, résultat, verrou...), pas un verrou
    # EnnoDiagnostic implicite.
    research_target_type: str | None = None
    # Pour un CIR complet, Agent 3 transmet le plan semantique deja calcule au
    # moteur de recherche. Chaque cible bibliographique reste ainsi liee a une
    # vraie section, sans reclasser le document ni construire une requete globale.
    research_section_plan: list[SectionRoutingPlan] = Field(default_factory=list)
    # Le parcours CIR complet calcule un diagnostic structuré une seule fois
    # sur la version active, puis le réinjecte dans chaque tour de section. Le
    # flag scoped reste vrai par défaut pour préserver le comportement des
    # demandes manuelles hors workflow progressif.
    diagnostic_context_override: dict[str, Any] | None = None
    diagnostic_orchestration_override: dict[str, Any] | None = None
    allow_scoped_diagnostic: bool = True
    sections: list[ParsedSection] = Field(default_factory=list)


class ImprovementResult(BaseModel):
    ok: bool
    state: ImprovementState
    assistant_message: str
    routing: RoutingDecision
    audit: list[AuditFinding] = Field(default_factory=list)
    improved_target: str = ""
    improved_full_text: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    generation: dict[str, Any] = Field(default_factory=dict)
    changes: list[dict[str, Any]] = Field(default_factory=list)
    sources_used: list[dict[str, Any]] = Field(default_factory=list)
    agents_used: list[str] = Field(default_factory=list)
    unsupported_claims: list[dict[str, Any]] = Field(default_factory=list)
    questions_for_consultant: list[str] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    research: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False
