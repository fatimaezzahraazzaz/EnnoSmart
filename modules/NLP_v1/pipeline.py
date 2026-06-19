# -*- coding: utf-8 -*-
"""
pipeline.py — V23 structure-aware RAW pipeline

Objectif : garder tes 2 modèles rapides, mais ne plus juger une phrase isolée.
Nouvelle logique :
Extraction déjà faite
↓
clean_text
↓
map_documents_structure : titres / sections / rôle section / type document
↓
make_candidates contextualisés
↓
FastJudge + VerrouDetector sur `model_input`
↓
quality_filter contextualisé
↓
grouping par rôle + section
↓
evidence graph / verrous locaux contextualisés
↓
domaine détecté sur texte projet focalisé, pas sur tous les articles
"""
from __future__ import annotations

from typing import Dict, Any, List

from .cleaner import clean_text
from .document_structure_mapper import map_documents_structure
from .candidates import make_candidates
from .models import apply_models
from .filter import apply_quality_filter, thresholds
from .grouping import group_items, build_evidence_pack
from .verrou_builder import enrich_evidence_pack_with_verrous
from .domain_classifier import classify_domain

VERSION = "v23_structure_context_2models_fast"


def _prepare_documents(
    documents: List[Dict[str, Any]],
    include_state_of_art_in_candidates: bool = True,
    include_cir_final: bool = False,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    skipped: List[Dict[str, Any]] = []
    docs: List[Dict[str, Any]] = []

    for d in documents or []:
        d = dict(d)
        d["text"] = clean_text(d.get("text", ""))
        if not d["text"].strip():
            skipped.append({"document": d.get("document"), "reason": "empty_text"})
            continue
        if d.get("content_origin") == "cir_final" and not include_cir_final:
            skipped.append({"document": d.get("document"), "reason": "cir_final_excluded"})
            continue
        if d.get("content_origin") == "state_of_art" and not include_state_of_art_in_candidates:
            skipped.append({"document": d.get("document"), "reason": "state_of_art_excluded"})
            continue
        docs.append(d)

    docs = map_documents_structure(docs)
    return docs, skipped


def _domain_focus_text(docs: List[Dict[str, Any]], accepted_passages: List[Dict[str, Any]] | None = None) -> str:
    """
    Domaine : ne pas classer sur tous les PDF scientifiques.
    On utilise d'abord : noms, titres de sections, documents projet, objectifs/verrous/méthodes forts.
    """
    parts: List[str] = []

    for d in docs or []:
        if d.get("content_origin") in {"project_core", "unknown"}:
            parts.append(str(d.get("document", "")))
            parts.append(str(d.get("text", ""))[:12000])
            for sec in (d.get("sections") or [])[:80]:
                parts.append(str(sec.get("section_title", "")))

    if accepted_passages:
        useful_roles = {"objectif", "verrou", "limite", "methode", "resultat", "contribution"}
        selected = [x for x in accepted_passages if x.get("role") in useful_roles]
        selected = sorted(selected, key=lambda x: x.get("rank_score", 0), reverse=True)[:80]
        for x in selected:
            if x.get("content_origin") in {"project_core", "unknown"}:
                parts.append(str(x.get("section_title", "")))
                parts.append(str(x.get("text", "")))

    if not parts:
        for d in docs or []:
            parts.append(str(d.get("document", "")))
            parts.append(str(d.get("text", ""))[:8000])

    return "\n".join(parts)[:250000]


def run_nlp_pipeline_fast(
    documents: List[Dict[str, Any]],
    max_candidates: int = 700,
    include_state_of_art_in_candidates: bool = True,
    include_cir_final: bool = False,
    top_k=None,
) -> Dict[str, Any]:
    docs, skipped = _prepare_documents(
        documents,
        include_state_of_art_in_candidates=include_state_of_art_in_candidates,
        include_cir_final=include_cir_final,
    )

    candidates = make_candidates(docs, max_candidates=max_candidates)
    judged = apply_models(candidates) if candidates else []
    filtered = apply_quality_filter(judged)
    groups = group_items(filtered["kept"])
    pack = build_evidence_pack(groups, top_k=top_k)
    pack = enrich_evidence_pack_with_verrous(pack)

    domain_text = _domain_focus_text(docs, filtered["kept"])
    domain = classify_domain(domain_text)

    stats = {
        "documents_input": len(documents or []),
        "documents_used": len(docs),
        "documents_skipped": len(skipped),
        "candidates": len(candidates),
        **filtered["stats"],
        "groups": len(groups),
        "verrous_final": len(pack.get("verrous_rnd_locaux", [])),
        "sections_detected": sum(len(d.get("sections") or []) for d in docs),
    }

    return {
        "version": VERSION,
        "logic": "structure_mapper + contextual_candidates + 2models + evidence_graph; no_llm_no_embedding",
        "stats": stats,
        "domain_detection": domain,
        "skipped_documents": skipped,
        "thresholds": thresholds(),
        "documents_summary": [
            {
                k: d.get(k)
                for k in [
                    "document", "extension", "loader", "chars", "content_origin", "source_weight",
                    "reason", "document_type", "document_type_confidence"
                ]
            }
            | {"sections": len(d.get("sections") or [])}
            for d in docs
        ],
        "evidence_pack_for_ennodiagnostic": pack,
        "accepted_passages": filtered["kept"][:300],
        "rejected_count": len(filtered["rejected"]),
    }
