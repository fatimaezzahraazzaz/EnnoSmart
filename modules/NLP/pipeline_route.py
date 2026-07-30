# -*- coding: utf-8 -*-
"""Point d'entrée public : routage RAW / pré-CIR / CIR puis qualification unique."""
from __future__ import annotations

import re
import traceback
from typing import Any, Dict, List, Optional

from .document_type_classifier import classify_document_type, enrich_document_type
from .domain_classifier import classify_domain
from .evidence_contract import (
    ALL_PACK_KEYS,
    LOCK_CANDIDATE_KEY,
    QUALIFIED_LOCK_KEY,
    empty_pack,
    normalize_pack,
)
from .evidence_merger import merge_evidence_packs
from .frascati_guard import apply_frascati_guard
from .technical_system_graph import build_technical_system_graph
from .nlp_document_aggregator import build_ennodiagnostic_nlp_context
from .pipeline import run_nlp_pipeline_fast


try:
    from .CIR.cir_pipeline import run_cir_pipeline
    from .CIR.cir_structure_detector import detect_cir_structure
except Exception as exc:  # module optionnel dans certaines installations
    run_cir_pipeline = None
    detect_cir_structure = None
    _CIR_IMPORT_ERROR = exc
else:
    _CIR_IMPORT_ERROR = None


VERSION = "v178_domain_neutral_single_lock_grouping_with_technical_system_graph"


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _get_doc_name(doc: Dict[str, Any]) -> str:
    return str(doc.get("document") or doc.get("file_name") or "")


def _get_pack(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return empty_pack()
    source = (
        result.get("evidence_pack_before_frascati")
        or result.get("merged_evidence_pack_before_frascati")
        or result.get("evidence_pack_for_ennodiagnostic")
        or result.get("multi_document_evidence_pack_for_ennodiagnostic")
        or result.get("merged_evidence_pack_for_ennodiagnostic")
        or {}
    )
    return normalize_pack(source)



def _load_embedding_encoder():
    """Charge paresseusement le modèle d'embeddings déjà utilisé par le RAG.

    Le NLP reste fonctionnel sans le RAG : en cas d'échec d'import ou de
    chargement, ``evidence_graph`` utilise son repli lexical.
    """
    try:
        from modules.RAG.vector_store import encode_texts
        return encode_texts
    except Exception:
        return None


def _build_v177_frascati_report(
    *,
    merged_before: Dict[str, Any],
    mode: str,
    domain: Dict[str, Any],
    max_candidates: int = 180,
) -> Dict[str, Any]:
    """Exécute une seule fois le regroupement, puis construit le graphe système."""
    report = apply_frascati_guard(
        pack=merged_before,
        mode=mode,
        domain=domain,
        encode_texts=_load_embedding_encoder(),
    )
    # Le graphe du système technique reste une vue indépendante. Il ne crée et
    # ne reclasse aucun verrou.
    report["technical_system_graph"] = build_technical_system_graph(merged_before)
    report["max_candidates_compatibility_value"] = int(max_candidates)
    return report

def _auto_route(doc: Dict[str, Any]) -> str:
    document_type = "unknown_document"
    try:
        document_type = classify_document_type(doc).get("document_type") or "unknown_document"
        if document_type == "pre_cir_client":
            return "pre_cir"
        if document_type == "cir_final_validated":
            return "cir"
    except Exception:
        pass

    # Une structure avec « objectifs / méthodes / résultats » n'est pas un CIR :
    # c'est aussi la structure normale d'un rapport d'étude. Le dossier de test
    # envoyait ainsi deux ``rapport_test`` dans le pipeline CIR et perdait leur
    # analyse candidate. Le détecteur structurel n'est utilisé qu'en renfort
    # d'une identité CIR explicite ; sinon la route sûre est RAW.
    identity_text = " ".join(
        [
            _get_doc_name(doc),
            str(doc.get("text") or "")[:5000],
        ]
    ).lower()
    explicit_cir_identity = bool(
        re.search(
            r"\b(?:dossier\s+cir|fiche\s+cir|pr[ée][ -]?cir|cr[ée]dit\s+d[' ]?imp[oô]t\s+recherche|cir\s+final|d[ée]claration\s+cir)\b",
            identity_text,
            flags=re.I,
        )
    )

    if explicit_cir_identity and document_type == "unknown_document" and detect_cir_structure is not None:
        try:
            report = detect_cir_structure(doc.get("text") or "", document=_get_doc_name(doc))
            if report.get("is_cir_structured") and report.get("keyword_hits", 0) >= 3 and report.get("important_heading_hits", 0) >= 3:
                return "cir"
        except Exception:
            pass
    return "raw"


def _force_doc_mode_fields(doc: Dict[str, Any], mode: str) -> Dict[str, Any]:
    out = dict(doc or {})
    if mode == "pre_cir":
        out.update(
            {
                "document_type": "pre_cir_client",
                "source_policy": "core_or_useful",
                "content_origin": "client_pre_cir",
                "pre_cir_client": True,
                "needs_human_validation": True,
                "validation_status": "consultant_required",
            }
        )
        out["document_weight"] = float(out.get("document_weight") or 1.30)
        out["source_weight"] = float(out.get("source_weight") or 1.30)
    elif mode == "cir":
        out.setdefault("content_origin", "cir_structured")
    else:
        out.setdefault("content_origin", "raw_client_document")
    return out


def _route_by_user(documents: List[Dict[str, Any]], document_modes: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    modes = document_modes or {}
    buckets = {"raw": [], "cir": [], "pre_cir": [], "skip": []}
    routing = []

    for original in documents or []:
        try:
            doc = enrich_document_type(dict(original or {}))
        except Exception:
            doc = dict(original or {})
        name = _get_doc_name(doc)
        selected = str(modes.get(name, modes.get(doc.get("source_path", ""), "auto"))).lower().strip()
        aliases = {
            "pre-cir": "pre_cir",
            "pre_cir_client": "pre_cir",
            "precir": "pre_cir",
            "client_pre_cir": "pre_cir",
            "cir_final": "cir",
            "cir_final_validated": "cir",
        }
        selected = aliases.get(selected, selected)
        if selected not in {"raw", "cir", "pre_cir", "auto", "skip"}:
            selected = "auto"
        mode = _auto_route(doc) if selected == "auto" else selected
        doc = _force_doc_mode_fields(doc, mode)
        buckets[mode].append(doc)
        routing.append(
            {
                "document": name,
                "selected_mode": selected,
                "route": mode,
                "reason": "auto_route" if selected == "auto" else "user_selected",
                "document_type": doc.get("document_type"),
                "content_origin": doc.get("content_origin"),
                "source_policy": doc.get("source_policy"),
            }
        )

    return {
        "raw_documents": buckets["raw"],
        "cir_structured_documents": buckets["cir"],
        "pre_cir_documents": buckets["pre_cir"],
        "skipped_documents": buckets["skip"],
        "routing": routing,
    }


def _run_raw(
    documents: List[Dict[str, Any]],
    *,
    max_candidates: int,
    include_state_of_art_in_candidates: bool,
    top_k: Any,
    organisme: str,
    project: str,
    year: str,
) -> Optional[Dict[str, Any]]:
    if not documents:
        return None
    try:
        return run_nlp_pipeline_fast(
            documents=documents,
            max_candidates=max_candidates,
            include_state_of_art_in_candidates=include_state_of_art_in_candidates,
            include_cir_final=True,
            top_k=top_k,
            organisme=organisme,
            project=project,
            year=year,
            qualify=False,
        )
    except Exception as exc:
        return {
            "error": f"RAW pipeline error: {exc}",
            "traceback": traceback.format_exc(),
            "evidence_pack_before_frascati": empty_pack(),
        }


def _run_cir(documents: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not documents:
        return None
    if run_cir_pipeline is None:
        return {
            "error": f"CIR pipeline import error: {_CIR_IMPORT_ERROR}",
            "evidence_pack_for_ennodiagnostic": empty_pack(),
        }
    try:
        return run_cir_pipeline(documents)
    except Exception as exc:
        return {
            "error": f"CIR pipeline error: {exc}",
            "traceback": traceback.format_exc(),
            "evidence_pack_for_ennodiagnostic": empty_pack(),
        }


def _mark_pack_items(
    pack: Dict[str, Any],
    *,
    source_type: str,
    content_origin: str,
    document_type: str,
    validation_status: str,
    source_policy: str = "core_or_useful",
    document_weight: float = 1.30,
    needs_human_validation: bool = True,
    pre_cir_client: bool = False,
) -> Dict[str, Any]:
    normalized = normalize_pack(pack)
    out = empty_pack()
    for key in ALL_PACK_KEYS:
        values = []
        for raw in normalized.get(key, []):
            item = dict(raw)
            item.update(
                {
                    "source_type": source_type,
                    "content_origin": content_origin,
                    "document_type": document_type,
                    "validation_status": validation_status,
                    "needs_human_validation": needs_human_validation,
                    "source_policy": source_policy,
                }
            )
            item["document_weight"] = float(item.get("document_weight") or document_weight)
            item["source_weight"] = float(item.get("source_weight") or document_weight)
            if pre_cir_client:
                item.update({"pre_cir_client": True, "client_pre_cir": True, "not_final_cir": True})
            values.append(item)
        out[key] = values
    out["_contract_version"] = "nlp_evidence_v177_routed_before_lock_grouping"
    return out


def _merge_many_packs(*packs: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = empty_pack()
    for pack in packs:
        merged = merge_evidence_packs(merged, pack)
    return merged


def _domain_valid(value: Any) -> bool:
    return isinstance(value, dict) and bool(
        value.get("domain_code_niv1")
        or value.get("domain_code_niv2")
        or value.get("domain_code_niv3")
        or value.get("display_label")
    )


def _detect_domain(documents: List[Dict[str, Any]], *results: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    for result in results:
        if isinstance(result, dict) and _domain_valid(result.get("domain_detection")):
            return dict(result["domain_detection"])
    text = "\n".join(
        f"{doc.get('document', '')}\n{str(doc.get('text') or '')[:12000]}"
        for doc in documents or []
    )
    try:
        return classify_domain(text[:250000]) if text.strip() else {}
    except Exception as exc:
        return {"warning": f"domain detection failed: {exc}", "top_domains": [], "confidence": 0.0}


def _prepare_docs(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for doc in documents or []:
        try:
            out.append(enrich_document_type(dict(doc)))
        except Exception:
            out.append(dict(doc))
    return out


def _compact_subresult(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Résumé sérialisable sans recopier les evidence packs."""
    if not isinstance(result, dict):
        return None
    keys = (
        "version",
        "pipeline_type",
        "logic",
        "stats",
        "domain_detection",
        "skipped_documents",
        "thresholds",
        "error",
    )
    return {key: result.get(key) for key in keys if key in result}


def run_nlp_pipeline_routed(
    documents: List[Dict[str, Any]],
    document_modes: Optional[Dict[str, str]] = None,
    max_candidates: int = 700,
    include_state_of_art_in_candidates: bool = True,
    top_k=None,
    organisme: str = "",
    project: str = "",
    year: str = "",
    memory_v2_report: Optional[Dict[str, Any]] = None,
    debug: bool = False,
    legacy_aliases: bool = False,
    **_: Any,
) -> Dict[str, Any]:
    routed = _route_by_user(documents, document_modes=document_modes)
    raw_docs = routed["raw_documents"]
    pre_cir_docs = routed["pre_cir_documents"]
    cir_docs = routed["cir_structured_documents"]
    skipped_docs = routed["skipped_documents"]

    raw_result = _run_raw(
        raw_docs,
        max_candidates=max_candidates,
        include_state_of_art_in_candidates=include_state_of_art_in_candidates,
        top_k=top_k,
        organisme=organisme,
        project=project,
        year=year,
    )
    pre_cir_result = _run_cir(pre_cir_docs)
    cir_result = _run_cir(cir_docs)

    raw_pack = _get_pack(raw_result)
    pre_cir_pack = _mark_pack_items(
        _get_pack(pre_cir_result),
        source_type="pre_cir_client",
        content_origin="client_pre_cir",
        document_type="pre_cir_client",
        validation_status="consultant_required",
        needs_human_validation=True,
        pre_cir_client=True,
    )
    cir_pack = _mark_pack_items(
        _get_pack(cir_result),
        source_type="cir_structured",
        content_origin="cir_structured",
        document_type="cir_final_validated",
        validation_status="source_declared_to_audit",
        needs_human_validation=True,
    )
    merged_before = _merge_many_packs(raw_pack, pre_cir_pack, cir_pack)

    docs_for_context = _prepare_docs([*raw_docs, *pre_cir_docs, *cir_docs])
    domain = _detect_domain(documents or [], raw_result, pre_cir_result, cir_result)
    if pre_cir_docs or raw_docs and cir_docs:
        frascati_mode = "mixed"
    elif cir_docs and not raw_docs:
        frascati_mode = "cir_audit"
    else:
        frascati_mode = "raw_construction"

    frascati = _build_v177_frascati_report(
        merged_before=merged_before,
        mode=frascati_mode,
        domain=domain,
        max_candidates=min(max(80, int(max_candidates)), 220),
    )
    qualified_pack = frascati.get("qualified_pack_for_ennodiagnostic") or merged_before
    context = build_ennodiagnostic_nlp_context(documents=docs_for_context, pack=qualified_pack)
    multi_pack = context["multi_document_evidence_pack_for_ennodiagnostic"]

    lock_grouping = frascati.get("lock_grouping_report") or {}
    qualified_passages_count = int(
        frascati.get("qualified_lock_passages_count")
        or lock_grouping.get("qualified_passages_total")
        or 0
    )
    qualified_groups_count = int(
        frascati.get("qualified_lock_groups_count")
        or lock_grouping.get("lock_evidence_groups_total")
        or len(multi_pack.get(QUALIFIED_LOCK_KEY, []))
    )

    memory_report = {
        "ok": True,
        "available": False,
        "mode": "agent_context_only",
        "message": "Memory V2 n'intervient plus dans la classification NLP ; EnnoDiagnostic l'injecte comme contexte/style.",
    }
    raw_stats = raw_result.get("stats", {}) if isinstance(raw_result, dict) else {}
    pre_stats = pre_cir_result.get("stats", {}) if isinstance(pre_cir_result, dict) else {}
    cir_stats = cir_result.get("stats", {}) if isinstance(cir_result, dict) else {}

    frascati_summary = {
        key: value
        for key, value in frascati.items()
        if key not in {"qualified_pack_for_ennodiagnostic", "evidence_pack_for_ennodiagnostic", "technical_system_graph"}
    }

    result = {
        "version": VERSION,
        "logic": "route documents, group lock evidence once, separate main locks from local subproblems, build the technical system graph independently, then assess Frascati",
        "stats": {
            "documents_input": len(documents or []),
            "raw_documents": len(raw_docs),
            "pre_cir_documents": len(pre_cir_docs),
            "cir_structured_documents": len(cir_docs),
            "documents_skipped": len(skipped_docs),
            "raw_candidates": raw_stats.get("candidates", 0),
            "raw_kept": raw_stats.get("kept", 0),
            "raw_sections": raw_stats.get("raw_sections", 0),
            "pre_cir_sections": pre_stats.get("sections_detected", 0),
            "cir_sections": cir_stats.get("sections_detected", 0),
            "nlp_lock_candidates": len(merged_before.get(LOCK_CANDIDATE_KEY, [])),
            "qualified_lock_passages": qualified_passages_count,
            "qualified_lock_groups": qualified_groups_count,
            "technical_groups": len(frascati.get("technical_lock_groups", [])),
            "secondary_technical_groups": len(frascati.get("secondary_technical_groups", [])),
            "candidate_groups_before_frascati": frascati.get("candidate_groups_before_frascati_count", 0),
            "frascati_verrous_probables": len(frascati.get("verrous_probables", [])),
            "frascati_verrous_a_verifier": len(frascati.get("verrous_a_verifier", [])),
            "frascati_faux_verrous_rejetes": len(frascati.get("faux_verrous_rejetes", [])),
            "global_frascati_score": (frascati.get("risk_report") or {}).get("global_frascati_score"),
            "lock_seed_candidates": lock_grouping.get("seed_count", 0),
            "lock_supporting_evidence": lock_grouping.get("support_count", 0),
            "lock_duplicates_removed": len(lock_grouping.get("duplicates_removed") or []),
            "lock_unassigned_supports": len(lock_grouping.get("unassigned_support_passage_ids") or []),
            "documents_with_evidence": context.get("coverage_report", {}).get("documents_with_evidence"),
            "technical_objects": ((frascati.get("technical_system_graph") or {}).get("stats") or {}).get("technical_objects_count", 0),
            "provisional_subsystems": ((frascati.get("technical_system_graph") or {}).get("stats") or {}).get("provisional_subsystems_count", 0),
        },
        "document_modes": document_modes or {},
        "domain_detection": domain,
        "routing": routed["routing"],
        "skipped_documents": [{"document": _get_doc_name(doc), "reason": "user_skip"} for doc in skipped_docs],
        "memory_v2_verrou_report": memory_report,
        "raw_result": _compact_subresult(raw_result),
        "pre_cir_structured_result": _compact_subresult(pre_cir_result),
        "cir_structured_result": _compact_subresult(cir_result),
        "frascati_guard": frascati_summary,
        "technical_system_graph": frascati.get("technical_system_graph") or {},
        "document_evidence_summaries": context["document_evidence_summaries"],
        # Pack canonique unique utilisé par json_to_chunks / RAG.
        "multi_document_evidence_pack_for_ennodiagnostic": multi_pack,
        "coverage_report": context["coverage_report"],
        "gap_analysis": {
            "nlp_lock_candidates": len(merged_before.get(LOCK_CANDIDATE_KEY, [])),
            "qualified_lock_passages": qualified_passages_count,
            "qualified_lock_groups": qualified_groups_count,
            "secondary_technical_groups": len(frascati.get("secondary_technical_groups", [])),
            "candidate_groups_before_frascati": frascati.get("candidate_groups_before_frascati_count", 0),
            "frascati_probable_count": len(frascati.get("verrous_probables", [])),
            "frascati_to_validate_count": len(frascati.get("verrous_a_verifier", [])),
            "rejected_lock_candidates_kept_in_semantic_sections": len(frascati.get("faux_verrous_rejetes", [])),
            "documents_without_evidence": context.get("coverage_report", {}).get("documents_without_evidence", []),
            "notes": [
                "Aucun minimum ni maximum de verrous n'est imposé.",
                "Les candidats et preuves complémentaires sont regroupés entre documents avant l’évaluation Frascati.",
                "Le regroupement V177 utilise les embeddings existants et le complete-linkage, sans perdre les passages sources.",
                "Le Technical System Graph décrit objets, fonctions, phénomènes et paramètres sans créer de verrou.",
                "Frascati ne rejette et ne supprime aucun groupe technique ; il calcule uniquement un score et des questions.",
                "Memory V2 est un contexte de l'agent, jamais une preuve ni un classifieur NLP.",
            ],
        },
    }

    if legacy_aliases:
        # À activer temporairement uniquement si un ancien consommateur exige
        # ce nom. En JSON, cet alias duplique tout le pack.
        result["merged_evidence_pack_for_ennodiagnostic"] = multi_pack

    if debug:
        result["debug"] = {
            "raw_result": raw_result,
            "pre_cir_structured_result": pre_cir_result,
            "cir_structured_result": cir_result,
            "raw_evidence_pack_before_frascati": raw_pack,
            "pre_cir_evidence_pack_before_frascati": pre_cir_pack,
            "cir_evidence_pack_before_frascati": cir_pack,
            "merged_evidence_pack_before_frascati": merged_before,
        }

    return result


def run_nlp_pipeline_route(*args, **kwargs):
    return run_nlp_pipeline_routed(*args, **kwargs)


def run_pipeline_route(*args, **kwargs):
    return run_nlp_pipeline_routed(*args, **kwargs)


def run_pipeline(*args, **kwargs):
    return run_nlp_pipeline_routed(*args, **kwargs)
