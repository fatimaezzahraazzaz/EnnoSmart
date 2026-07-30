# -*- coding: utf-8 -*-
"""Pipeline RAW NLP : documents -> passages -> modèles -> pack de preuves.

Ce module ne regroupe pas les verrous techniques. Cette étape est réalisée une
seule fois, après la fusion des routes, par ``frascati_guard`` via
``pipeline_route``.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from .candidates import make_candidates
from .document_structure_mapper import map_documents_structure
from .document_type_classifier import enrich_document_type
from .domain_classifier import classify_domain
from .filter import apply_quality_filter, thresholds
from .grouping import build_evidence_pack, group_items
from .models import apply_models


VERSION = "raw_nlp_pipeline_v178_domain_neutral_before_single_lock_grouping"


def _as_documents(documents: Iterable[Mapping[str, Any]] | None) -> List[Dict[str, Any]]:
    """Normalise les documents sans modifier les objets de l'appelant."""
    prepared: List[Dict[str, Any]] = []
    for raw in documents or []:
        if not isinstance(raw, Mapping):
            continue
        document = dict(raw)
        if not str(document.get("text") or "").strip():
            continue
        try:
            document = enrich_document_type(document)
        except Exception:
            # Un type inconnu ne doit jamais faire échouer l'analyse NLP.
            document.setdefault("document_type", "unknown_document")
            document.setdefault("source_policy", "secondary")
            document.setdefault("document_weight", 0.55)
            document.setdefault("source_weight", document["document_weight"])
        prepared.append(document)
    return prepared


def _is_cir_final(document: Mapping[str, Any]) -> bool:
    return str(document.get("content_origin") or "").lower() in {
        "cir_final",
        "cir_final_validated",
    } or str(document.get("document_type") or "").lower() == "cir_final_validated"


def _is_state_of_art(document: Mapping[str, Any]) -> bool:
    return str(document.get("content_origin") or "").lower() == "state_of_art" or str(
        document.get("document_type") or ""
    ).lower() in {"etat_art_bibliographie", "publication_scientifique"}


def _detect_domain(documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    text = "\n".join(
        f"{doc.get('document', '')}\n{str(doc.get('text') or '')[:12000]}"
        for doc in documents
    )
    if not text.strip():
        return {"confidence": 0.0, "top_domains": [], "warning": "aucun texte"}
    try:
        return classify_domain(text[:250000])
    except Exception as exc:
        return {"confidence": 0.0, "top_domains": [], "warning": f"domain detection failed: {exc}"}


def run_nlp_pipeline_fast(
    *,
    documents: Iterable[Mapping[str, Any]],
    max_candidates: int = 700,
    include_state_of_art_in_candidates: bool = True,
    include_cir_final: bool = False,
    top_k: Optional[Dict[str, int]] = None,
    organisme: str = "",
    project: str = "",
    year: str = "",
    qualify: bool = False,
    **_: Any,
) -> Dict[str, Any]:
    """Construit le pack RAW consommé ensuite par ``pipeline_route``.

    ``qualify`` est conservé pour compatibilité. Il reste désactivé dans le
    routage normal afin d'éviter un deuxième regroupement des verrous.
    """
    prepared = _as_documents(documents)
    retained: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for document in prepared:
        if not include_cir_final and _is_cir_final(document):
            skipped.append({"document": str(document.get("document") or ""), "reason": "cir_final_excluded"})
            continue
        retained.append(document)

    structures = map_documents_structure(retained)
    candidate_documents: List[Dict[str, Any]] = []
    candidate_structures: List[Dict[str, Any]] = []
    for document, structure in zip(retained, structures):
        if not include_state_of_art_in_candidates and _is_state_of_art(document):
            continue
        candidate_documents.append(document)
        candidate_structures.append(structure)

    raw_candidates = make_candidates(
        candidate_documents,
        max_candidates=max(0, int(max_candidates)),
        section_info=candidate_structures,
    )
    modeled = apply_models(raw_candidates) if raw_candidates else []
    quality = apply_quality_filter(modeled)
    semantic_groups = group_items(quality["kept"])
    evidence_pack = build_evidence_pack(
        semantic_groups,
        top_k=top_k,
        items=quality["kept"],
        lock_candidates=quality["lock_candidates"],
    )

    domain = _detect_domain(retained)
    result: Dict[str, Any] = {
        "version": VERSION,
        "pipeline_type": "raw_documents_before_single_lock_grouping",
        "logic": (
            "extract candidates across documents, infer semantic roles and lock scores, "
            "filter evidence, build semantic sections, then defer technical lock grouping "
            "to pipeline_route"
        ),
        "stats": {
            "documents_input": len(prepared),
            "documents_retained": len(retained),
            "documents_skipped": len(skipped),
            "candidates": len(raw_candidates),
            "modeled": len(modeled),
            "kept": len(quality["kept"]),
            "rejected": len(quality["rejected"]),
            "raw_sections": sum(len(item.get("sections") or []) for item in structures),
            "semantic_groups": len(semantic_groups),
            "nlp_lock_candidates": len(quality["lock_candidates"]),
        },
        "thresholds": thresholds(),
        "domain_detection": domain,
        "documents": retained,
        "document_structures": structures,
        "skipped_documents": skipped,
        "evidence_pack_before_frascati": evidence_pack,
        "raw_candidates_audit": raw_candidates,
        "quality_filter_stats": quality["stats"],
        "qualification_deferred": not bool(qualify),
        "context": {"organisme": organisme, "project": project, "year": year},
    }

    if qualify:
        # Compatibilité pour les appels directs historiques. Le routeur normal
        # appelle ``frascati_guard`` après fusion de toutes les routes.
        from .frascati_guard import apply_frascati_guard

        guard = apply_frascati_guard(
            pack=evidence_pack,
            documents=retained,
            mode="raw_construction",
            domain=domain,
        )
        result["frascati_guard"] = guard
        result["evidence_pack_for_ennodiagnostic"] = guard["qualified_pack_for_ennodiagnostic"]

    return result


run_nlp_pipeline = run_nlp_pipeline_fast


__all__ = ["run_nlp_pipeline_fast", "run_nlp_pipeline"]
