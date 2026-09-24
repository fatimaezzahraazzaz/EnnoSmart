from typing import Iterable, Optional
from .audit import build_audit
from .chunking import build_chunks
from .config import PipelineConfig
from .frascati import FrascatiEvaluator
from .fusion import fuse_items
from .consolidation import GlobalLockConsolidator
from .review import build_review_queue
from .schemas import DocumentInput, PipelineResult
from .semantic_extractor import LLMClientProtocol, SemanticExtractor

VERSION = "ennodiagnostic_v2_simple_semantic_pipeline_1"

class EnnoDiagnosticV2:
    def __init__(
        self,
        *,
        semantic_llm: LLMClientProtocol,
        consolidation_llm: Optional[LLMClientProtocol] = None,
        frascati_llm: Optional[LLMClientProtocol] = None,
        config: Optional[PipelineConfig] = None,
    ):
        self.config = config or PipelineConfig()
        self.extractor = SemanticExtractor(semantic_llm)
        self.consolidator = GlobalLockConsolidator(
            consolidation_llm or semantic_llm
        )
        self.frascati = FrascatiEvaluator(
            frascati_llm if self.config.run_frascati else None
        )

    def run(self, documents: Iterable[DocumentInput]) -> PipelineResult:
        documents = list(documents)
        chunks, reports = [], []

        for doc in documents:
            doc_chunks = build_chunks(doc, self.config.chunk)
            chunks.extend(doc_chunks)
            reports.append({
                "document_id": doc.document_id,
                "name": doc.name,
                "declared_mode": doc.declared_mode,
                "detected_mode": doc_chunks[0].document_mode if doc_chunks else "unknown",
                "chunks": len(doc_chunks),
            })

        extractions = self.extractor.extract(chunks)

        lock_entities = self.consolidator.consolidate(extractions)

        non_lock_items = [
            item for item in extractions
            if item.kind != "lock"
        ]

        non_lock_entities = fuse_items(
            non_lock_items,
            self.config.fusion,
        )

        entities = lock_entities + non_lock_entities
        frascati = self.frascati.assess(entities)
        review = build_review_queue(
            entities,
            require_all_locks=self.config.require_human_review_for_locks,
        )
        audit = build_audit(chunks, extractions, entities)

        return PipelineResult(
            version=VERSION,
            documents=reports,
            chunks=chunks,
            extractions=extractions,
            entities=entities,
            frascati=frascati,
            review_queue=review,
            audit=audit,
        )
