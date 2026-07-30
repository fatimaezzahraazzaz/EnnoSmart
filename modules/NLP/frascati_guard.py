# -*- coding: utf-8 -*-
"""Évaluation Frascati sans second moteur de regroupement.

Le regroupement canonique est réalisé une seule fois par ``evidence_graph``.
Frascati ajoute ensuite un diagnostic et des questions, sans inventer ni
supprimer de preuve. Cette version remplace les deux définitions concurrentes
de ``apply_frascati_guard`` présentes dans l'ancien fichier.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from .evidence_contract import (
    EVIDENCE_CATALOG_KEY,
    LOCK_CANDIDATE_KEY,
    LOCK_SUPPORT_KEY,
    QUALIFIED_LOCK_KEY,
    REJECTED_LOCK_KEY,
    SECONDARY_TECHNICAL_GROUPS_KEY,
    TECHNICAL_GROUPS_KEY,
    normalize_pack,
    passage_identity,
)
from .evidence_graph import build_technical_lock_groups
from .frascati_assessment import assess_project_frascati


VERSION = "frascati_guard_v177_single_grouping_assessment_only"


def _unique_support_ids(groups: Iterable[Mapping[str, Any]]) -> set[str]:
    return {
        passage_identity(item)
        for group in groups
        for item in (group.get("supporting_passages") or [])
        if isinstance(item, Mapping)
    }


def assess_groups(groups: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Ajoute l'évaluation Frascati aux groupes déjà construits."""
    groups_list = [dict(group) for group in groups if isinstance(group, Mapping)]
    report = assess_project_frascati(groups_list)
    by_id = {
        str(item.get("group_id") or ""): item
        for item in (report.get("group_assessments") or [])
        if isinstance(item, Mapping)
    }

    enriched: List[Dict[str, Any]] = []
    for group in groups_list:
        group_id = str(group.get("lock_group_id") or group.get("passage_id") or "")
        item = dict(group)
        item["frascati_assessment"] = dict(by_id.get(group_id) or {})
        item["frascati_decision"] = None
        item["rejected_as_verrou"] = False
        item["needs_human_validation"] = True
        if item.get("display_as_main_lock"):
            item["technical_classification"] = "verrou_potentiel"
            item["final_role"] = "verrou_potentiel"
        else:
            item["technical_classification"] = "sous_probleme_technique"
            item["final_role"] = "sous_probleme_technique"
        item["derived_view"] = "technical_group_with_frascati_assessment"
        item["verrou_source"] = "nlp_single_grouping_before_frascati_assessment"
        enriched.append(item)

    main_groups = [item for item in enriched if item.get("display_as_main_lock")]
    secondary_groups = [item for item in enriched if not item.get("display_as_main_lock")]
    return {
        "version": VERSION,
        "technical_lock_groups": enriched,
        "verrous_rnd_locaux": main_groups,
        "secondary_technical_groups": secondary_groups,
        "frascati_assessment": report,
        "groups_removed_by_frascati": 0,
    }


def apply_frascati_guard(
    pack: Optional[Mapping[str, Any]] = None,
    *,
    evidence_pack: Optional[Mapping[str, Any]] = None,
    pack_before_frascati: Optional[Mapping[str, Any]] = None,
    documents: Optional[List[Dict[str, Any]]] = None,
    mode: str = "raw_construction",
    domain: Optional[Dict[str, Any]] = None,
    encode_texts: Optional[Callable[[List[str]], Sequence[Sequence[float]]]] = None,
    **_: Any,
) -> Dict[str, Any]:
    """API compatible avec l'ancien pipeline, avec un seul regroupement."""
    source = pack or evidence_pack or pack_before_frascati or {}
    normalized = normalize_pack(source)
    candidates = normalized.get(LOCK_CANDIDATE_KEY, [])
    # Les candidats créent les groupes ; le catalogue complet fournit les
    # objectifs, méthodes, paramètres et résultats qui les documentent.
    grouping_input = normalized.get(EVIDENCE_CATALOG_KEY, []) or candidates

    grouping = build_technical_lock_groups(grouping_input, encode_texts=encode_texts)
    assessed = assess_groups(grouping.get("groups") or [])
    all_groups = assessed["technical_lock_groups"]
    main_groups = assessed["verrous_rnd_locaux"]
    secondary_groups = assessed["secondary_technical_groups"]

    final_pack: Dict[str, Any] = dict(normalized)
    classified_passages = list(grouping.get("candidate_passages") or [])
    final_pack[LOCK_CANDIDATE_KEY] = [
        item for item in classified_passages if item.get("direct_lock_candidate")
    ] or list(candidates)
    final_pack[LOCK_SUPPORT_KEY] = [
        item
        for item in classified_passages
        if item.get("supporting_lock_evidence") and not item.get("direct_lock_candidate")
    ]
    # Seuls les groupes structurants partent au RAG avec le rôle verrou.
    final_pack[QUALIFIED_LOCK_KEY] = main_groups
    final_pack[REJECTED_LOCK_KEY] = []
    final_pack[TECHNICAL_GROUPS_KEY] = all_groups
    final_pack[SECONDARY_TECHNICAL_GROUPS_KEY] = secondary_groups
    final_pack["frascati_assessment"] = assessed["frascati_assessment"]
    final_pack["_contract_version"] = "nlp_evidence_v177_single_grouping"

    support_ids = _unique_support_ids(main_groups)
    assessment = assessed["frascati_assessment"]
    return {
        "version": VERSION,
        "mode": mode,
        "decision": None,
        "human_validation_required": True,
        "contract": "single_technical_grouping_then_frascati_assessment",
        "qualification_order": "candidate_filter_then_topic_grouping_then_assessment",
        "domain": domain or {},
        "documents_count": len(documents or []),
        "candidates_input_count": len(candidates),
        "candidate_groups_before_frascati_count": len(all_groups),
        "qualified_lock_passages_count": len(support_ids),
        "qualified_lock_groups_count": len(main_groups),
        "technical_lock_groups": all_groups,
        "verrous_rnd_locaux": main_groups,
        "secondary_technical_groups": secondary_groups,
        "lock_grouping_report": grouping,
        "frascati_assessment": assessment,
        "risk_report": {
            "global_frascati_score": assessment.get("eligibility_score", 0.0),
            "risk_level": assessment.get("risk_level", "eleve"),
            "questions_to_ask": assessment.get("questions_to_ask", []),
            "human_validation_required": True,
            "decision": None,
            "groups_removed_by_frascati": 0,
        },
        "consultant_view": {
            "potential_verrous_count": len(main_groups),
            "secondary_technical_groups_count": len(secondary_groups),
            "qualified_passages_count": len(support_ids),
            "display_status": "groupes_techniques_a_valider_humainement",
        },
        "verrous_probables": [],
        "verrous_a_verifier": [],
        "groupes_rejetes": [],
        "faux_verrous_rejetes": [],
        "groups_removed_by_frascati": 0,
        "qualified_pack_for_ennodiagnostic": final_pack,
    }
