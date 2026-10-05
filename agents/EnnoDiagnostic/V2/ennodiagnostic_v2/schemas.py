from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional


Kind = Literal[
    "objective",
    "lock",
    "method",
    "demarche",
    "parameter",
    "metric",
    "result",
]
Explicitness = Literal["explicit", "implicit", "unknown"]
TemporalStatus = Literal["planned", "in_progress", "performed", "unclear"]


@dataclass
class DocumentInput:
    document_id: str
    name: str
    text: str
    declared_mode: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    document_name: str
    text: str
    context_before: str = ""
    context_after: str = ""
    section_title: str = ""
    document_mode: str = "raw"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceRef:
    evidence_id: str
    document_id: str
    document_name: str
    chunk_id: str
    quote: str
    section_title: str = ""
    page: Optional[int] = None
    quote_verified: bool = False
    passage_id: str = ""
    char_start: Optional[int] = None
    char_end: Optional[int] = None


@dataclass
class TraceablePoint:
    """
    Point court destiné à l'affichage frontend, toujours rattaché
    à une ou plusieurs preuves documentaires réelles.
    """
    text: str
    evidence_ids: List[str] = field(default_factory=list)
    temporal_status: str = "unclear"
    source_role: str = "unknown"
    sources: List[Dict[str, str]] = field(default_factory=list)
    original_item_ids: List[str] = field(default_factory=list)


@dataclass
class RnDStep:
    """
    Compatibilité avec les anciennes sorties frontend.

    La démarche projet n'est plus extraite par rnd_approach.py.
    Les nouvelles démarches sont des ExtractionItem(kind="demarche")
    puis sont exposées par ProjectSynthesis.demarches.
    """
    category: str
    text: str
    evidence_ids: List[str] = field(default_factory=list)


@dataclass
class ExtractionItem:
    item_id: str
    kind: Kind
    statement: str
    evidence: EvidenceRef
    explicitness: Explicitness = "unknown"
    rationale: str = ""
    technical_object: str = ""
    unresolved_question: str = ""
    llm_confidence: Optional[float] = None
    needs_review: bool = False

    # Principalement utile pour les démarches, mais conservé générique
    # afin de préserver la temporalité de toute information extraite.
    temporal_status: TemporalStatus = "unclear"

    # Position uniquement lorsqu'un ordre est explicitement fourni
    # dans le passage source. Ne jamais l'inventer.
    sequence_position: Optional[int] = None

    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CanonicalEntity:
    entity_id: str
    kind: Kind
    statement: str
    source_item_ids: List[str] = field(default_factory=list)
    evidences: List[EvidenceRef] = field(default_factory=list)
    document_ids: List[str] = field(default_factory=list)
    needs_review: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FrascatiCriterion:
    criterion: str
    status: str
    explanation: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    questions: List[str] = field(default_factory=list)


@dataclass
class FrascatiAssessment:
    lock_entity_id: str
    status: str
    criteria: List[FrascatiCriterion] = field(default_factory=list)
    questions: List[str] = field(default_factory=list)


@dataclass
class ReviewItem:
    review_id: str
    entity_id: str
    kind: Kind
    statement: str
    status: str = "pending"
    reason: str = ""
    evidence_ids: List[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    version: str
    documents: List[Dict[str, Any]]
    chunks: List[Chunk]
    extractions: List[ExtractionItem]
    entities: List[CanonicalEntity]
    frascati: List[FrascatiAssessment]
    review_queue: List[ReviewItem]
    audit: Dict[str, Any]
    global_review: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
