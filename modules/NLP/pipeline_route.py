# -*- coding: utf-8 -*-
"""
pipeline_route.py — V30 multi-document + pré-CIR client + FrascatiGuard

Routage : raw / pre_cir / cir / auto / skip.
Pré-CIR client = brut structuré important, Frascati OUI, validation consultant obligatoire.
"""
from __future__ import annotations

import traceback
from typing import Any, Dict, List, Optional

from .pipeline import run_nlp_pipeline_fast
from .domain_classifier import classify_domain
from .evidence_merger import merge_evidence_packs
from .nlp_document_aggregator import build_ennodiagnostic_nlp_context
from .document_type_classifier import enrich_document_type, classify_document_type
from .frascati_guard import apply_frascati_guard

try:
    from .CIR.cir_pipeline import run_cir_pipeline
    from .CIR.cir_structure_detector import detect_cir_structure
except Exception as e:
    run_cir_pipeline = None
    detect_cir_structure = None
    _CIR_IMPORT_ERROR = e
else:
    _CIR_IMPORT_ERROR = None

PACK_KEYS = [
    "objectifs_locaux", "verrous_rnd_locaux", "methodes_locales", "resultats_locaux",
    "limites_locales", "contributions_locales", "etat_art_local", "parametres_locaux"
]


def _empty_pack() -> Dict[str, List[Dict[str, Any]]]:
    return {k: [] for k in PACK_KEYS}


def _safe_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def _get_doc_name(doc: Dict[str, Any]) -> str:
    return str(doc.get("document") or doc.get("file_name") or "")


def _get_pack(result: Optional[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    if not isinstance(result, dict):
        return _empty_pack()
    pack = (
        result.get("evidence_pack_before_frascati")
        or result.get("merged_evidence_pack_before_frascati")
        or result.get("evidence_pack_for_ennodiagnostic")
        or result.get("multi_document_evidence_pack_for_ennodiagnostic")
        or result.get("merged_evidence_pack_for_ennodiagnostic")
        or {}
    )
    final = _empty_pack()
    if isinstance(pack, dict):
        for k in PACK_KEYS:
            final[k] = _safe_list(pack.get(k))
    return final


def _auto_route(doc: Dict[str, Any]) -> str:
    try:
        info = classify_document_type(doc)
        dt = info.get("document_type")
        if dt == "pre_cir_client":
            return "pre_cir"
        if dt == "cir_final_validated":
            return "cir"
    except Exception:
        pass

    if detect_cir_structure is None:
        return "raw"
    try:
        rep = detect_cir_structure(doc.get("text") or "", document=_get_doc_name(doc))
        if rep.get("is_cir_structured") and rep.get("keyword_hits", 0) >= 3 and rep.get("important_heading_hits", 0) >= 3:
            return "cir"
    except Exception:
        pass
    return "raw"


def _force_doc_mode_fields(doc: Dict[str, Any], mode: str) -> Dict[str, Any]:
    d = dict(doc or {})
    if mode == "pre_cir":
        d["document_type"] = "pre_cir_client"
        d["source_policy"] = "core_or_useful"
        d["document_weight"] = float(d.get("document_weight") or 1.30)
        d["source_weight"] = float(d.get("source_weight") or 1.30)
        d["content_origin"] = d.get("content_origin") or "client_pre_cir"
        d["pre_cir_client"] = True
        d["needs_human_validation"] = True
        d["validation_status"] = d.get("validation_status") or "consultant_required"
    elif mode == "cir":
        d["content_origin"] = d.get("content_origin") or "cir_structured"
    elif mode == "raw":
        d["content_origin"] = d.get("content_origin") or "raw_client_document"
    return d


def _route_by_user(documents: List[Dict[str, Any]], document_modes: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    document_modes = document_modes or {}
    raw_docs, cir_docs, pre_cir_docs, skipped, routing = [], [], [], [], []

    for original in documents or []:
        try:
            d = enrich_document_type(dict(original))
        except Exception:
            d = dict(original or {})

        name = _get_doc_name(d)
        mode = str(document_modes.get(name, document_modes.get(d.get("source_path", ""), "auto"))).lower().strip()
        if mode in {"pre-cir", "pre_cir_client", "precir", "client_pre_cir"}:
            mode = "pre_cir"
        if mode in {"cir_final", "cir_final_validated"}:
            mode = "cir"
        if mode not in {"raw", "cir", "pre_cir", "auto", "skip"}:
            mode = "auto"

        selected = mode
        if mode == "auto":
            mode = _auto_route(d)
        d = _force_doc_mode_fields(d, mode)

        if mode == "skip":
            skipped.append(d)
            route = "skip"
        elif mode == "pre_cir":
            pre_cir_docs.append(d)
            route = "pre_cir_client"
        elif mode == "cir":
            cir_docs.append(d)
            route = "cir_structured"
        else:
            raw_docs.append(d)
            route = "raw"

        routing.append({
            "document": name,
            "selected_mode": selected,
            "route": route,
            "reason": "user_selected" if selected != "auto" else "auto_route",
            "confidence": 1.0 if selected != "auto" else None,
            "content_origin": d.get("content_origin"),
            "document_type": d.get("document_type"),
            "source_policy": d.get("source_policy"),
            "validation_status": d.get("validation_status"),
        })

    return {
        "raw_documents": raw_docs,
        "cir_structured_documents": cir_docs,
        "pre_cir_documents": pre_cir_docs,
        "skipped_documents": skipped,
        "routing": routing,
    }


def _run_raw(raw_docs: List[Dict[str, Any]], max_candidates: int = 700, include_state_of_art_in_candidates: bool = True, top_k=None) -> Optional[Dict[str, Any]]:
    if not raw_docs:
        return None
    try:
        return run_nlp_pipeline_fast(
            documents=raw_docs,
            max_candidates=max_candidates,
            include_state_of_art_in_candidates=include_state_of_art_in_candidates,
            include_cir_final=True,
            top_k=top_k,
        )
    except Exception as e:
        return {"error": f"RAW pipeline error: {e}", "traceback": traceback.format_exc(), "evidence_pack_for_ennodiagnostic": _empty_pack()}


def _run_cir(cir_docs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not cir_docs:
        return None
    if run_cir_pipeline is None:
        return {"error": f"CIR pipeline import error: {_CIR_IMPORT_ERROR}", "evidence_pack_for_ennodiagnostic": _empty_pack()}
    try:
        return run_cir_pipeline(cir_docs)
    except Exception as e:
        return {"error": f"CIR pipeline error: {e}", "traceback": traceback.format_exc(), "evidence_pack_for_ennodiagnostic": _empty_pack()}


def _mark_pack_items(pack: Dict[str, List[Dict[str, Any]]], *, source_type: str, content_origin: str, document_type: str, validation_status: str, source_policy: str = "core_or_useful", document_weight: float = 1.30, needs_human_validation: bool = True, pre_cir_client: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    final = _empty_pack()
    for k in PACK_KEYS:
        arr = []
        for item in _safe_list((pack or {}).get(k)):
            if not isinstance(item, dict):
                continue
            x = dict(item)
            x["source_type"] = source_type
            x["content_origin"] = content_origin
            x["document_type"] = document_type
            x["validation_status"] = validation_status
            x["needs_human_validation"] = needs_human_validation
            x["source_policy"] = source_policy
            x["document_weight"] = float(x.get("document_weight") or document_weight)
            x["source_weight"] = float(x.get("source_weight") or document_weight)
            if pre_cir_client:
                x["pre_cir_client"] = True
                x["client_pre_cir"] = True
                x["not_final_cir"] = True
            arr.append(x)
        final[k] = arr
    return final


def _merge_many_packs(*packs: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    merged = _empty_pack()
    for pack in packs:
        if isinstance(pack, dict):
            merged = merge_evidence_packs(raw_pack=merged, cir_pack=pack)
    return merged


def _domain_valid(d: Any) -> bool:
    return isinstance(d, dict) and bool(d.get("domain_code_niv1") or d.get("domain_code_niv2") or d.get("domain_code_niv3") or d.get("display_label"))


def _detect_domain_from_results(documents: List[Dict[str, Any]], raw_result: Optional[Dict[str, Any]], pre_cir_result: Optional[Dict[str, Any]], cir_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    for result_name, result in [("raw_result", raw_result), ("pre_cir_result", pre_cir_result), ("cir_result", cir_result)]:
        if isinstance(result, dict) and _domain_valid(result.get("domain_detection")):
            d = dict(result["domain_detection"])
            d["domain_source"] = result_name
            return d
    text = "\n".join(str(d.get("document", "")) + "\n" + str(d.get("text") or "")[:12000] for d in documents or [] if d.get("content_origin") in {"project_core", "unknown", "raw_client_document", "client_pre_cir"})
    if not text.strip():
        text = "\n".join(str(d.get("document", "")) + "\n" + str(d.get("text") or "")[:8000] for d in documents or [])
    try:
        d = classify_domain(text[:200000])
        if isinstance(d, dict):
            d["domain_source"] = "pipeline_route_fallback"
        return d
    except Exception as e:
        return {"warning": f"domain detection failed: {e}", "top_domains": [], "confidence": 0.0, "domain_source": "pipeline_route_error"}


def _prepare_docs_for_context(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for d in documents or []:
        try:
            out.append(enrich_document_type(dict(d)))
        except Exception:
            out.append(dict(d))
    return out


def run_nlp_pipeline_routed(documents: List[Dict[str, Any]], document_modes: Optional[Dict[str, str]] = None, max_candidates: int = 700, include_state_of_art_in_candidates: bool = True, top_k=None, **kwargs) -> Dict[str, Any]:
    routed = _route_by_user(documents, document_modes=document_modes)
    raw_docs = routed["raw_documents"]
    cir_docs = routed["cir_structured_documents"]
    pre_cir_docs = routed["pre_cir_documents"]
    skipped_docs = routed["skipped_documents"]

    raw_result = _run_raw(raw_docs, max_candidates=max_candidates, include_state_of_art_in_candidates=include_state_of_art_in_candidates, top_k=top_k)
    pre_cir_result = _run_cir(pre_cir_docs)
    cir_result = _run_cir(cir_docs)

    raw_pack = _get_pack(raw_result)
    pre_cir_pack = _mark_pack_items(_get_pack(pre_cir_result), source_type="pre_cir_client", content_origin="client_pre_cir", document_type="pre_cir_client", validation_status="consultant_required", source_policy="core_or_useful", document_weight=1.30, needs_human_validation=True, pre_cir_client=True)
    cir_pack = _get_pack(cir_result)
    merged_pack_simple = _merge_many_packs(raw_pack, pre_cir_pack, cir_pack)

    docs_for_context = _prepare_docs_for_context(raw_docs + pre_cir_docs + cir_docs)
    domain_detection = _detect_domain_from_results(documents or [], raw_result, pre_cir_result, cir_result)

    if pre_cir_docs:
        frascati_mode = "mixed"
    elif raw_docs and cir_docs:
        frascati_mode = "mixed"
    elif cir_docs and not raw_docs:
        frascati_mode = "cir_audit"
    else:
        frascati_mode = "raw_construction"

    frascati_guard = apply_frascati_guard(pack=merged_pack_simple, documents=docs_for_context, mode=frascati_mode, domain=domain_detection)
    qualified_pack = frascati_guard.get("qualified_pack_for_ennodiagnostic") or merged_pack_simple
    context = build_ennodiagnostic_nlp_context(documents=docs_for_context, pack=qualified_pack)
    merged_pack_multidoc = context["multi_document_evidence_pack_for_ennodiagnostic"]

    raw_stats = raw_result.get("stats", {}) if isinstance(raw_result, dict) else {}
    pre_cir_stats = pre_cir_result.get("stats", {}) if isinstance(pre_cir_result, dict) else {}
    cir_stats = cir_result.get("stats", {}) if isinstance(cir_result, dict) else {}

    return {
        "version": "v30_route_multidoc_pre_cir_client_frascati_guard",
        "logic": "RAW/CIR/PRE-CIR route + FrascatiGuard + pré-CIR client comme document brut structuré à valider",
        "stats": {
            "documents_input": len(documents or []),
            "raw_documents": len(raw_docs),
            "pre_cir_documents": len(pre_cir_docs),
            "cir_structured_documents": len(cir_docs),
            "documents_skipped": len(skipped_docs),
            "raw_candidates": raw_stats.get("candidates", 0),
            "raw_kept": raw_stats.get("kept", 0),
            "raw_sections": raw_stats.get("raw_sections", raw_stats.get("sections_detected", 0)),
            "pre_cir_sections": pre_cir_stats.get("sections_detected", 0),
            "cir_sections": cir_stats.get("sections_detected", 0),
            "merged_verrous": len(merged_pack_multidoc.get("verrous_rnd_locaux", [])),
            "frascati_mode": frascati_mode,
            "frascati_verrous_probables": len(frascati_guard.get("verrous_probables", [])),
            "frascati_verrous_a_verifier": len(frascati_guard.get("verrous_a_verifier", [])),
            "frascati_faux_verrous_rejetes": len(frascati_guard.get("faux_verrous_rejetes", [])),
            "frascati_verrous_potentiels_consultant": (frascati_guard.get("consultant_view") or {}).get("potential_verrous_count", 0),
            "frascati_display_status": (frascati_guard.get("consultant_view") or {}).get("display_status"),
            "global_frascati_score": (frascati_guard.get("risk_report") or {}).get("global_frascati_score"),
            "frascati_risk_level": (frascati_guard.get("risk_report") or {}).get("risk_level"),
            "documents_with_evidence": context.get("coverage_report", {}).get("documents_with_evidence"),
        },
        "document_modes": document_modes or {},
        "domain_detection": domain_detection,
        "routing": routed["routing"],
        "skipped_documents": [{"document": _get_doc_name(d), "reason": "user_skip"} for d in skipped_docs],
        "raw_result": raw_result,
        "pre_cir_structured_result": pre_cir_result,
        "cir_structured_result": cir_result,
        "raw_evidence_pack_before_frascati": raw_pack,
        "pre_cir_evidence_pack_before_frascati": pre_cir_pack,
        "cir_evidence_pack_before_frascati": cir_pack,
        "merged_evidence_pack_before_frascati": merged_pack_simple,
        "frascati_guard": frascati_guard,
        "merged_evidence_pack_for_ennodiagnostic": merged_pack_multidoc,
        "document_evidence_summaries": context["document_evidence_summaries"],
        "multi_document_evidence_pack_for_ennodiagnostic": merged_pack_multidoc,
        "coverage_report": context["coverage_report"],
        "gap_analysis": {
            "raw_verrous_count": len(raw_pack.get("verrous_rnd_locaux", [])),
            "pre_cir_verrous_count": len(pre_cir_pack.get("verrous_rnd_locaux", [])),
            "cir_verrous_count": len(cir_pack.get("verrous_rnd_locaux", [])),
            "frascati_probable_count": len(frascati_guard.get("verrous_probables", [])),
            "frascati_to_validate_count": len(frascati_guard.get("verrous_a_verifier", [])),
            "frascati_rejected_false_verrous_count": len(frascati_guard.get("faux_verrous_rejetes", [])),
            "documents_without_evidence": context.get("coverage_report", {}).get("documents_without_evidence", []),
            "notes": [
                "Le pré-CIR client est exploité comme document courant structuré, pas comme CIR final.",
                "Les items pré-CIR sont marqués validation_status=consultant_required.",
                "FrascatiGuard reste actif sur le pré-CIR afin de distinguer vrais verrous R&D et rédaction client non éligible.",
            ],
        },
    }


def run_nlp_pipeline_route(*args, **kwargs):
    return run_nlp_pipeline_routed(*args, **kwargs)


def run_pipeline_route(*args, **kwargs):
    return run_nlp_pipeline_routed(*args, **kwargs)


def run_pipeline(*args, **kwargs):
    return run_nlp_pipeline_routed(*args, **kwargs)
