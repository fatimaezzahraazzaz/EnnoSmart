from dataclasses import replace
from dataclasses import asdict
from typing import Iterable, Optional
from .audit import build_audit
from .chunking import build_chunks
from .config import PipelineConfig
from .frascati import FrascatiEvaluator
from .fusion import fuse_items
from .consolidation import GlobalLockConsolidator
from .qualification import LockQualifier
from .review import build_review_queue
from .schemas import DocumentInput, PipelineResult
from .semantic_extractor import LLMClientProtocol, SemanticExtractor
from .global_project_reviewer import GlobalProjectReviewer
from .global_fast_path import GlobalFastExtractor, build_passages
from .diagnostic_debug import DiagnosticDebug


def _attach_source_roles(entities, extractions):
    """Ajoute la provenance sans supprimer aucun candidat."""
    extraction_by_id = {item.item_id: item for item in extractions}
    output = []

    for entity in entities:
        roles = []
        evidence_source_roles = {}

        for item_id in entity.source_item_ids:
            item = extraction_by_id.get(item_id)
            if item is None:
                continue

            role = str(item.metadata.get("source_role") or "unknown").strip().lower()
            if role == "contextual":
                role = "context"
            if role not in {"project_evidence", "literature", "admin", "context", "unknown"}:
                role = "unknown"

            roles.append(role)
            evidence_source_roles[item.evidence.evidence_id] = role

        unique_roles = sorted(set(roles)) or ["unknown"]
        metadata = dict(entity.metadata)
        metadata.update({
            "source_roles": unique_roles,
            "evidence_source_roles": evidence_source_roles,
            "has_project_evidence": "project_evidence" in unique_roles,
        })

        output.append(replace(entity, metadata=metadata))

    return output


VERSION = "ennodiagnostic_v2_simple_semantic_pipeline_1"

class EnnoDiagnosticV2:
    def __init__(
        self,
        *,
        semantic_llm: LLMClientProtocol,
        consolidation_llm: Optional[LLMClientProtocol] = None,
        qualification_llm: Optional[LLMClientProtocol] = None,
        frascati_llm: Optional[LLMClientProtocol] = None,
        config: Optional[PipelineConfig] = None,
        global_reviewer: Optional[GlobalProjectReviewer] = None,
        fast_extractor: Optional[GlobalFastExtractor] = None,
        debug_directory=None,
    ):
        self.config = config or PipelineConfig()
        self.global_reviewer = global_reviewer
        self.fast_extractor = fast_extractor
        self.debug_directory = debug_directory
        self.last_debug_reference = None
        self.extractor = SemanticExtractor(semantic_llm)
        self.consolidator = GlobalLockConsolidator(
            consolidation_llm or semantic_llm
        )
        self.qualifier = LockQualifier(qualification_llm)
        self.frascati = FrascatiEvaluator(
            frascati_llm if self.config.run_frascati else None
        )

    def run(self, documents: Iterable[DocumentInput]) -> PipelineResult:
        documents = list(documents)
        debug = None
        if self.debug_directory is not None or (self.fast_extractor and getattr(getattr(self.fast_extractor, "llm", None), "client", None) is not None):
            debug = DiagnosticDebug(self.debug_directory)
            self.last_debug_reference = str(debug.path)
            debug.record("pipeline.documents", [{"document_id": d.document_id,
                "name": d.name, "chars": len(d.text)} for d in documents])
        if self.fast_extractor:
            self.fast_extractor.debug_sink = debug
        self.consolidator.debug_sink = debug
        chunks, reports = [], []

        for doc in documents:
            doc_chunks = build_chunks(doc, self.config.chunk)
            sections = list(dict.fromkeys(
                chunk.section_title for chunk in doc_chunks if chunk.section_title
            ))
            outline = " | ".join(sections)[:1500]
            for chunk in doc_chunks:
                chunk.metadata["document_outline"] = outline
            chunks.extend(doc_chunks)
            reports.append({
                "document_id": doc.document_id,
                "name": doc.name,
                "declared_mode": doc.declared_mode,
                "detected_mode": doc_chunks[0].document_mode if doc_chunks else "unknown",
                "chunks": len(doc_chunks),
            })

        fast_meta = None
        if self.fast_extractor:
            try:
                print("[EnnoDiagnostic][FastPath] revue du dossier global démarrée", flush=True)
                extractions, fast_meta = self.fast_extractor.extract(documents)
                print(
                    f"[EnnoDiagnostic][FastPath] terminé: {len(extractions)} faits, "
                    f"{fast_meta.get('llm_calls', 0)} appel(s) LLM",
                    flush=True,
                )
            except Exception as exc:
                fast_meta = {"status": "fallback", "error": f"{type(exc).__name__}:{exc}"}
                print(f"[EnnoDiagnostic][FastPath] échec: {fast_meta['error']}; chemin historique", flush=True)
                extractions = self.extractor.extract(chunks)
        else:
            extractions = self.extractor.extract(chunks)

        admitted = (
            [item for item in extractions if (item.metadata or {}).get("project_admission")
             or (item.kind == "lock" and (item.metadata or {}).get("lock_candidate_admission"))]
            if fast_meta and fast_meta.get("status") != "fallback" else extractions
        )
        self.consolidator.require_intergroup = bool(fast_meta and fast_meta.get("status") != "fallback")
        lock_entities = self.consolidator.consolidate(admitted)

        non_lock_items = [
            item for item in admitted
            if item.kind != "lock"
        ]

        non_lock_entities = fuse_items(
            non_lock_items,
            self.config.fusion,
        )

        entities = lock_entities + non_lock_entities

        # Les faits bruts restent dans extractions ; les entités de travail du
        # chemin rapide ne contiennent que des faits projet admis.
        entities = _attach_source_roles(entities, extractions)

        # Le premier appel GPT-5 a déjà comparé tout le dossier. Le reviewer
        # relit seulement les faits incertains, avec leurs citations exactes.
        if fast_meta and fast_meta.get("status") != "fallback":
            decisions = [{
                "item_id": item.item_id,
                "original_kind": item.kind, "reviewed_kind": item.kind,
                "original_source_role": (item.metadata or {}).get("reported_source_role") or (item.metadata or {}).get("source_role"),
                "source_role": (item.metadata or {}).get("source_role"),
                "project_admission": bool((item.metadata or {}).get("project_admission")),
                "needs_review": item.needs_review,
                "duplicate_of": None,
                "evidence_ids": [item.evidence.evidence_id],
                "original_item_ids": [item.item_id],
                "reason": (item.metadata or {}).get("validation_error") or "Extraction globale validée sur passage exact",
            } for item in extractions]
            targeted = [
                item for item in extractions
                if item.kind != "lock" and item.needs_review
            ]
            targeted_audit = {
                "status": "not_needed" if not targeted else "unavailable",
                "submitted_count": len(targeted), "reviewed_count": 0,
                "newly_admitted_count": 0, "changed_kind_count": 0,
                "changed_role_count": 0, "new_facts_count": 0,
            }
            if targeted and self.global_reviewer:
                review_result = self.global_reviewer.review_cached(
                    chunks, targeted, entities,
                    cache_dir=getattr(self.fast_extractor, "cache_dir", None),
                    corpus_fingerprint=fast_meta.get("fingerprint", ""),
                )
                targeted_audit = {
                    "status": review_result.get("status"),
                    "mode": review_result.get("mode"),
                    "submitted_count": len(targeted),
                    "reviewed_count": review_result.get("reviewed_count", 0),
                    "batch_count": review_result.get("batch_count", 0),
                    "reconciliation_calls": review_result.get("reconciliation_calls", 0),
                    "error": review_result.get("error", ""),
                    "new_facts_count": 0,
                    "cache_hit": review_result.get("cache_hit", False),
                    "model": review_result.get("model"),
                    "provider": review_result.get("provider"),
                    "reasoning_effort": review_result.get("reasoning_effort"),
                    "audit": review_result.get("audit", {}),
                }
                if review_result.get("status") == "complete" and review_result.get("coverage") == 1.0:
                    revisions = {row["item_id"]: row for row in review_result["decisions"]}
                    if set(revisions) == {item.item_id for item in targeted}:
                        passages = {
                            p.passage_id: p for p in build_passages(
                                documents, self.fast_extractor.config.passage_max_chars,
                            )
                        }
                        for item in targeted:
                            row = revisions[item.item_id]
                            if row["project_admission"]:
                                try:
                                    self.fast_extractor._validate({
                                        "kind": row["reviewed_kind"], "statement": item.statement,
                                        "source_role": row["source_role"],
                                        "temporal_status": item.temporal_status,
                                        "passage_ids": [item.evidence.passage_id],
                                        "document_ids": [item.evidence.document_id],
                                        "needs_review": row["needs_review"],
                                    }, passages)
                                except (ValueError, TypeError, KeyError) as exc:
                                    revisions[item.item_id] = {
                                        **row, "project_admission": False, "needs_review": True,
                                        "reason": f"Revue ciblée non admise par validation Python : {exc}",
                                    }
                        originals = {row["item_id"]: row for row in decisions}
                        targeted_audit.update({
                            "newly_admitted_count": sum(
                                bool(row["project_admission"]) and not originals[item_id]["project_admission"]
                                for item_id, row in revisions.items()
                            ),
                            "changed_kind_count": sum(
                                row["reviewed_kind"] != originals[item_id]["reviewed_kind"]
                                for item_id, row in revisions.items()
                            ),
                            "changed_role_count": sum(
                                row["source_role"] != originals[item_id]["source_role"]
                                for item_id, row in revisions.items()
                            ),
                        })
                        decisions = [revisions.get(row["item_id"], row) for row in decisions]
                    else:
                        targeted_audit["status"] = "coverage_mismatch"
                print(
                    f"[EnnoDiagnostic][GlobalProjectReviewer] {targeted_audit['status']}: "
                    f"{targeted_audit['reviewed_count']}/{len(targeted)} faits incertains, "
                    f"{targeted_audit.get('newly_admitted_count', 0)} admis",
                    flush=True,
                )
            global_review = {
                "status": "complete", "mode": "global_fast_path", "coverage": 1.0,
                "submitted_count": len(extractions), "reviewed_count": len(decisions),
                "decisions": decisions,
                "lock_observations": [row for row in decisions if row["original_kind"] == "lock"],
                "model": self.fast_extractor.config.model,
                "provider": self.fast_extractor.config.provider,
                "reasoning_effort": self.fast_extractor.config.reasoning_effort,
                "fast_path": fast_meta,
                "targeted_reviewer": targeted_audit,
            }
        else:
            global_review = self.global_reviewer.review(chunks, extractions, entities) if self.global_reviewer else {
                "status": "fallback", "error": "global_reviewer_unavailable",
                "submitted_count": len(extractions), "reviewed_count": 0,
                "coverage": 0.0, "decisions": [], "lock_observations": [],
            }
            if fast_meta:
                global_review["fast_path"] = fast_meta

        # Qualification = annotation uniquement.
        # Aucun candidat n'est supprim?.
        if debug:
            debug.record("qualification.input_candidates", [asdict(e) for e in entities if e.kind == "lock"])
        entities = self.qualifier.qualify(
            entities, chunks=chunks, extractions=extractions
        )

        frascati = self.frascati.assess(entities)
        review = build_review_queue(
            entities,
            require_all_locks=self.config.require_human_review_for_locks,
        )
        audit = build_audit(chunks, extractions, entities)
        audit["global_review_status"] = global_review["status"]
        audit["global_review_coverage"] = global_review["coverage"]
        audit["extraction_path"] = (
            "GLOBAL_FAST_PATH" if fast_meta and fast_meta.get("status") != "fallback"
            else "legacy_semantic_path"
        )
        if fast_meta:
            audit["global_fast_path"] = fast_meta
            audit["lock_coverage_complete"] = (fast_meta.get("completeness_audit") or {}).get("lock_coverage_complete", False)
            audit["lock_intergroup_validated"] = all(
                e.metadata.get("intergroup_validated", len(lock_entities) < 2)
                for e in lock_entities)
        if debug:
            debug.record("pipeline.final", {"extractions": [asdict(i) for i in extractions],
                "entities": [asdict(e) for e in entities], "audit": audit})
            # Only a reference is exposed, never raw model responses/source debug.
            audit["debug_reference"] = str(debug.path)

        return PipelineResult(
            version=VERSION,
            documents=reports,
            chunks=chunks,
            extractions=extractions,
            entities=entities,
            frascati=frascati,
            review_queue=review,
            audit=audit,
            global_review=global_review,
        )
