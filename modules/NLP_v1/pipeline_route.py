# -*- coding: utf-8 -*-
"""
pipeline_route.py — V23
Route RAW/CIR/AUTO/SKIP sans modifier le principe :
- RAW : modules.NLP.pipeline.run_nlp_pipeline_fast V23
- CIR : modules.NLP.CIR.cir_pipeline.run_cir_pipeline si présent

Important : plus d'override domaine codé en dur dans le routeur.
Le domaine vient du pipeline RAW/CIR ou de domain_classifier sur le texte utile.
"""
from __future__ import annotations

import re
import traceback
from typing import Any, Dict, List, Optional

from .pipeline import run_nlp_pipeline_fast
from .domain_classifier import classify_domain
from .evidence_merger import merge_evidence_packs

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
    pack = result.get("evidence_pack_for_ennodiagnostic") or result.get("merged_evidence_pack_for_ennodiagnostic") or {}
    final = _empty_pack()
    if isinstance(pack, dict):
        for k in PACK_KEYS:
            final[k] = _safe_list(pack.get(k))
    return final


def _auto_route(doc: Dict[str, Any]) -> str:
    if detect_cir_structure is None:
        return "raw"
    try:
        rep = detect_cir_structure(doc.get("text") or "", document=_get_doc_name(doc))
        if rep.get("is_cir_structured") and rep.get("keyword_hits", 0) >= 3 and rep.get("important_heading_hits", 0) >= 3:
            return "cir"
    except Exception:
        pass
    return "raw"


def _route_by_user(documents: List[Dict[str, Any]], document_modes: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    document_modes = document_modes or {}
    raw_docs, cir_docs, skipped, routing = [], [], [], []

    for d in documents or []:
        name = _get_doc_name(d)
        mode = str(document_modes.get(name, document_modes.get(d.get("source_path", ""), "auto"))).lower()
        if mode not in {"raw", "cir", "auto", "skip"}:
            mode = "auto"
        selected = mode
        if mode == "auto":
            mode = _auto_route(d)
        if mode == "skip":
            skipped.append(d)
            route = "skip"
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
        })

    return {"raw_documents": raw_docs, "cir_structured_documents": cir_docs, "skipped_documents": skipped, "routing": routing}


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


def _detect_domain_from_results(documents: List[Dict[str, Any]], raw_result: Optional[Dict[str, Any]], cir_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    # 1) Si RAW a déjà calculé un domaine fiable, le garder.
    if isinstance(raw_result, dict) and isinstance(raw_result.get("domain_detection"), dict):
        d = raw_result["domain_detection"]
        if d.get("domain_code_niv1") or d.get("domain_code_niv2") or d.get("domain_code_niv3"):
            return d

    # 2) Sinon CIR.
    if isinstance(cir_result, dict) and isinstance(cir_result.get("domain_detection"), dict):
        d = cir_result["domain_detection"]
        if d.get("domain_code_niv1") or d.get("domain_code_niv2") or d.get("domain_code_niv3"):
            return d

    # 3) Fallback non codé en dur : classifier sur les documents projet uniquement.
    text = "\n".join(str(d.get("document", "")) + "\n" + str(d.get("text") or "")[:12000] for d in documents or [] if d.get("content_origin") in {"project_core", "unknown"})
    if not text.strip():
        text = "\n".join(str(d.get("document", "")) + "\n" + str(d.get("text") or "")[:8000] for d in documents or [])
    try:
        return classify_domain(text[:200000])
    except Exception as e:
        return {"warning": f"domain detection failed: {e}", "top_domains": [], "confidence": 0.0}


def run_nlp_pipeline_routed(
    documents: List[Dict[str, Any]],
    document_modes: Optional[Dict[str, str]] = None,
    max_candidates: int = 700,
    include_state_of_art_in_candidates: bool = True,
    top_k=None,
    **kwargs,
) -> Dict[str, Any]:
    routed = _route_by_user(documents, document_modes=document_modes)
    raw_docs = routed["raw_documents"]
    cir_docs = routed["cir_structured_documents"]
    skipped_docs = routed["skipped_documents"]

    raw_result = _run_raw(raw_docs, max_candidates=max_candidates, include_state_of_art_in_candidates=include_state_of_art_in_candidates, top_k=top_k)
    cir_result = _run_cir(cir_docs)

    raw_pack = _get_pack(raw_result)
    cir_pack = _get_pack(cir_result)
    merged_pack = merge_evidence_packs(raw_pack=raw_pack, cir_pack=cir_pack)

    raw_stats = raw_result.get("stats", {}) if isinstance(raw_result, dict) else {}
    cir_stats = cir_result.get("stats", {}) if isinstance(cir_result, dict) else {}

    return {
        "version": "v23_manual_route_raw_structure_plus_cir_addon",
        "logic": "RAW V23 structure-aware + CIR add-on + manual RAW/CIR/AUTO/SKIP route",
        "stats": {
            "documents_input": len(documents or []),
            "raw_documents": len(raw_docs),
            "cir_structured_documents": len(cir_docs),
            "documents_skipped": len(skipped_docs),
            "raw_candidates": raw_stats.get("candidates", 0),
            "raw_kept": raw_stats.get("kept", 0),
            "raw_sections": raw_stats.get("sections_detected", 0),
            "cir_sections": cir_stats.get("sections_detected", 0),
            "merged_verrous": len(merged_pack.get("verrous_rnd_locaux", [])),
        },
        "document_modes": document_modes or {},
        "domain_detection": _detect_domain_from_results(documents or [], raw_result, cir_result),
        "routing": routed["routing"],
        "skipped_documents": [{"document": _get_doc_name(d), "reason": "user_skip"} for d in skipped_docs],
        "raw_result": raw_result,
        "cir_structured_result": cir_result,
        "merged_evidence_pack_for_ennodiagnostic": merged_pack,
        "gap_analysis": {
            "raw_verrous_count": len(raw_pack.get("verrous_rnd_locaux", [])),
            "cir_verrous_count": len(cir_pack.get("verrous_rnd_locaux", [])),
            "notes": [],
        },
    }


def run_nlp_pipeline_route(*args, **kwargs):
    return run_nlp_pipeline_routed(*args, **kwargs)


def run_pipeline_route(*args, **kwargs):
    return run_nlp_pipeline_routed(*args, **kwargs)


def run_pipeline(*args, **kwargs):
    return run_nlp_pipeline_routed(*args, **kwargs)
