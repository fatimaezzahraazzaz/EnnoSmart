from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Literal, Optional

Kind = Literal["objective", "lock", "method", "parameter", "result"]
Explicitness = Literal["explicit", "implicit", "unknown"]

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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
