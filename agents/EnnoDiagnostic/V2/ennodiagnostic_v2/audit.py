from collections import Counter

def build_audit(chunks, extractions, entities):
    chunks, extractions, entities = list(chunks), list(extractions), list(entities)
    return {
        "chunks_total": len(chunks),
        "extractions_total": len(extractions),
        "entities_total": len(entities),
        "extractions_by_kind": dict(Counter(x.kind for x in extractions)),
        "entities_by_kind": dict(Counter(x.kind for x in entities)),
        "unverified_quotes": [x.item_id for x in extractions if not x.evidence.quote_verified],
        "fastjudge_disagreements": [x.item_id for x in extractions if x.fastjudge_disagreement],
        "documents_covered": sorted({x.document_id for x in chunks}),
        "principles": [
            "same_semantic_pipeline_for_all_document_modes",
            "llm_is_primary_semantic_extractor",
            "fastjudge_is_secondary_signal_not_gate",
            "frascati_does_not_delete_extractions",
            "all_source_evidence_is_preserved",
        ],
    }
