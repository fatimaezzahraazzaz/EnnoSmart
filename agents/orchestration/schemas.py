"""
modules/orchestration/schemas.py — EnnoSmart / Orchestrateur POC
──────────────────────────────────────────────────────────────────────────────
Schémas communs pour l'orchestrateur Orchestrateur.

Rôle :
  - Centraliser les dataclasses utilisées par l'orchestration.
  - Éviter de dupliquer les structures dans ennoamel.py, workflow.py,
    agent_registry.py et response_builder.py.
  - Fournir des objets JSON-serializables pour Streamlit/API.

Architecture :
  Extraction → NLP → RAG → Orchestrateur
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ══════════════════════════════════════════════════════════════════════════════

class WorkflowMode(str, Enum):
    RAW_DOCUMENT = "raw_document"
    NLP_JSON_FILE = "nlp_json_file"
    NLP_JSON_MEMORY = "nlp_json_memory"
    UNKNOWN = "unknown"


class PipelineStep(str, Enum):
    EXTRACTION = "extraction"
    NLP = "nlp"
    RAG = "rag"
    ASK = "ask"


class StepStatus(str, Enum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    SKIPPED = "skipped"


class AgentStatus(str, Enum):
    AVAILABLE = "available"
    IN_PROGRESS = "in_progress"
    PLANNED = "planned"
    DISABLED = "disabled"


class AgentKind(str, Enum):
    ORCHESTRATOR = "orchestrator"
    DIAGNOSTIC = "diagnostic"
    SCHOLAR = "scholar"
    VALOR = "valor"


# ══════════════════════════════════════════════════════════════════════════════
# BASE MIXIN
# ══════════════════════════════════════════════════════════════════════════════

class SerializableMixin:
    """
    Petit helper pour uniformiser les sorties dict.
    """

    def to_dict(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, list):
                return [convert(v) for v in value]
            if isinstance(value, dict):
                return {str(k): convert(v) for k, v in value.items()}
            if hasattr(value, "to_dict"):
                return value.to_dict()
            return value

        return {
            key: convert(value)
            for key, value in self.__dict__.items()
        }


# ══════════════════════════════════════════════════════════════════════════════
# AGENT SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class AgentCapability(SerializableMixin):
    """
    Décrit une capacité métier d'un agent.
    """
    name: str
    description: str
    examples: list[str] = field(default_factory=list)


@dataclass
class AgentInfo(SerializableMixin):
    """
    Description d'un agent EnnoSmart.
    """
    name: str
    kind: AgentKind
    status: AgentStatus
    role: str
    description: str
    supported_intents: list[str] = field(default_factory=list)
    capabilities: list[AgentCapability] = field(default_factory=list)
    poc_message: str = ""
    available_in_poc: bool = False


@dataclass
class AgentRoute(SerializableMixin):
    """
    Décision de routage vers un agent.
    """
    agent_name: str
    intent: str
    action: str
    confidence: float
    requires_specialized_agent: bool = False
    reason: str = ""
    agent_status: str = ""
    available_in_poc: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class StepReport(SerializableMixin):
    """
    Résultat d'une étape du pipeline.
    """
    step: PipelineStep
    status: StepStatus = StepStatus.NOT_STARTED
    ok: bool = False
    message: str = ""
    duration: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowReport(SerializableMixin):
    """
    Rapport complet de préparation documentaire.
    """
    ok: bool = False
    mode: WorkflowMode = WorkflowMode.UNKNOWN

    file_path: Optional[str] = None
    file_name: Optional[str] = None

    extraction: StepReport = field(
        default_factory=lambda: StepReport(step=PipelineStep.EXTRACTION)
    )
    nlp: StepReport = field(
        default_factory=lambda: StepReport(step=PipelineStep.NLP)
    )
    rag: StepReport = field(
        default_factory=lambda: StepReport(step=PipelineStep.RAG)
    )

    indexed_chunks: int = 0
    total_chunks: int = 0
    processing_time: float = 0.0

    document_metadata: dict[str, Any] = field(default_factory=dict)
    output_json_path: Optional[str] = None

    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class OrchestratorState(SerializableMixin):
    """
    État courant de l'orchestrateur Orchestrateur.
    """
    has_document: bool = False
    has_extraction: bool = False
    has_nlp: bool = False
    has_rag_index: bool = False

    current_file_path: Optional[str] = None
    current_file_name: Optional[str] = None

    indexed_chunks: int = 0
    total_rag_chunks: int = 0

    last_workflow: Optional[WorkflowReport] = None

    # Objets Python non sérialisés directement dans l'API.
    extraction_result: Optional[Any] = field(default=None, repr=False)
    nlp_result: Optional[Any] = field(default=None, repr=False)
    nlp_json: Optional[dict[str, Any]] = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_document": self.has_document,
            "has_extraction": self.has_extraction,
            "has_nlp": self.has_nlp,
            "has_rag_index": self.has_rag_index,
            "current_file_path": self.current_file_path,
            "current_file_name": self.current_file_name,
            "indexed_chunks": self.indexed_chunks,
            "total_rag_chunks": self.total_rag_chunks,
            "last_workflow": self.last_workflow.to_dict() if self.last_workflow else None,
        }


# ══════════════════════════════════════════════════════════════════════════════
# RESPONSE SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SourceRef(SerializableMixin):
    """
    Source utilisée dans une réponse RAG.
    """
    ref: str
    chunk_id: Optional[str] = None
    file_name: Optional[str] = None
    domaine_principal: Optional[str] = None
    score: Optional[float] = None
    vector_score: Optional[float] = None
    metadata_bonus: Optional[float] = None
    source: Optional[str] = None
    excerpt: Optional[str] = None


@dataclass
class BuiltResponse(SerializableMixin):
    """
    Réponse finale formatée par response_builder.py.
    """
    answer: str
    intent: str
    recommended_agent: str
    action: str
    confidence: float

    sources: list[SourceRef] = field(default_factory=list)

    rag_used: bool = False
    chunks_used: int = 0
    needs_specialized_agent: bool = False

    route_explanation: str = ""
    agent_note: str = ""
    poc_warning: str = ""

    processing_time: float = 0.0
    error: Optional[str] = None

    debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "intent": self.intent,
            "recommended_agent": self.recommended_agent,
            "action": self.action,
            "confidence": round(float(self.confidence), 3),
            "sources": [s.to_dict() for s in self.sources],
            "rag_used": self.rag_used,
            "chunks_used": self.chunks_used,
            "needs_specialized_agent": self.needs_specialized_agent,
            "route_explanation": self.route_explanation,
            "agent_note": self.agent_note,
            "poc_warning": self.poc_warning,
            "processing_time": round(float(self.processing_time), 2),
            "error": self.error,
            "debug": self.debug,
        }


@dataclass
class AskContext(SerializableMixin):
    """
    Contexte d'une question utilisateur.
    """
    question: str
    intent: str = "qa"
    recommended_agent: str = "Orchestrateur"
    action: str = "answer_question_with_rag"
    confidence: float = 0.0
    rag_query: str = ""
    filter_meta: Optional[dict[str, Any]] = None
    top_k: int = 5


# ══════════════════════════════════════════════════════════════════════════════
# STREAMLIT / UI SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ChatMessage(SerializableMixin):
    """
    Message pour interface chat Streamlit.
    """
    role: str  # "user" | "assistant" | "system"
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SidebarStatus(SerializableMixin):
    """
    État affichable dans la sidebar Streamlit.
    """
    document_loaded: bool = False
    rag_ready: bool = False
    file_name: Optional[str] = None
    indexed_chunks: int = 0
    embedding_model: str = ""
    llm_model: str = ""
    active_agent: str = "Orchestrateur"