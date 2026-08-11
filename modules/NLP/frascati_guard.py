# -*- coding: utf-8 -*-
"""Frascati V2 après regroupement technique unique.

- Le regroupement canonique reste dans ``evidence_graph``.
- Frascati n'invente ni ne supprime aucun verrou.
- Frascati fournit maintenant une recommandation binaire 1/0 au consultant.
- La décision administrative finale reste humaine.
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
from .semantic_lock_finalizer import finalize_lock_groups
from .frascati_assessment import assess_project_frascati


VERSION = "frascati_guard_v191_traceable_eligibility_evidence"


def _unique_support_ids(groups: Iterable[Mapping[str, Any]]) -> set[str]:
    return {
        passage_identity(item)
        for group in groups
        for item in (group.get("supporting_passages") or [])
        if isinstance(item, Mapping)
    }


def assess_groups(groups: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Ajoute la grille Frascati V2 aux groupes déjà construits."""
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
        group_assessment = dict(by_id.get(group_id) or {})
        recommendation = int(group_assessment.get("eligibility_recommendation") or 0)

        item["frascati_assessment"] = group_assessment
        item["frascati_decision"] = recommendation
        item["frascati_recommendation"] = recommendation
        item["frascati_recommendation_label"] = group_assessment.get("recommendation_label")
        item["frascati_risk_level"] = group_assessment.get("risk_level")

        # Frascati évalue l'éligibilité mais ne supprime jamais le verrou détecté.
        item["rejected_as_verrou"] = False
        item["needs_human_validation"] = True

        if item.get("display_as_main_lock"):
            item["technical_classification"] = "verrou_potentiel"
            item["final_role"] = "verrou_potentiel"
        else:
            item["technical_classification"] = "sous_probleme_technique"
            item["final_role"] = "sous_probleme_technique"

        item["derived_view"] = "technical_group_with_frascati_v2_recommendation"
        item["verrou_source"] = "nlp_single_grouping_before_frascati_v2_assessment"
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
    """API compatible avec l'ancien pipeline, avec recommandation Frascati 1/0."""
    source = pack or evidence_pack or pack_before_frascati or {}
    normalized = normalize_pack(source)
    candidates = normalized.get(LOCK_CANDIDATE_KEY, [])

    # Les candidats FastJudge créent les groupes ; le catalogue complet fournit
    # objectifs, méthodes, paramètres, résultats, limites et contributions.
    grouping_input = normalized.get(EVIDENCE_CATALOG_KEY, []) or candidates
    grouping = build_technical_lock_groups(
        grouping_input,
        encode_texts=encode_texts,
    )

    # V189 : unique consolidation conceptuelle, dans le NLP et AVANT Frascati.
    #
    # Le NLI distingue :
    # - vrai verrou principal,
    # - preuve / r?sultat / sous-probl?me,
    # - m?thode,
    # - bruit,
    # puis regroupe les formulations appartenant au
    # m?me probl?me scientifique parent.
    semantic_finalization = finalize_lock_groups(
        grouping.get("groups") or [],
        encode_texts=encode_texts,
    )

    grouping = dict(grouping)

    grouping[
        "pre_semantic_finalization_groups_count"
    ] = len(
        grouping.get("groups")
        or []
    )

    grouping[
        "pre_semantic_finalization_group_ids"
    ] = [
        str(
            item.get("lock_group_id")
            or item.get("passage_id")
            or ""
        )
        for item in (
            grouping.get("groups")
            or []
        )
    ]

    grouping[
        "groups"
    ] = semantic_finalization.get(
        "groups"
    ) or []

    grouping[
        "groups_count"
    ] = len(
        grouping["groups"]
    )

    grouping[
        "semantic_final_main_groups_count"
    ] = len(
        semantic_finalization.get(
            "main_groups"
        )
        or []
    )

    grouping[
        "semantic_final_secondary_groups_count"
    ] = len(
        semantic_finalization.get(
            "secondary_groups"
        )
        or []
    )

    grouping[
        "semantic_finalization"
    ] = semantic_finalization.get(
        "audit"
    ) or {}

    grouping[
        "semantic_finalizer_version"
    ] = semantic_finalization.get(
        "version"
    )

    grouping[
        "method"
    ] = (
        str(
            grouping.get("method")
            or ""
        )
        + "_then_single_nlp_nli_conceptual_parent_lock_finalization"
    )

    assessed = assess_groups(
        grouping.get("groups")
        or []
    )

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

    # Tous les groupes structurants restent disponibles au RAG ; Frascati ne filtre pas.
    final_pack[QUALIFIED_LOCK_KEY] = main_groups
    final_pack[REJECTED_LOCK_KEY] = []
    final_pack[TECHNICAL_GROUPS_KEY] = all_groups
    final_pack[SECONDARY_TECHNICAL_GROUPS_KEY] = secondary_groups
    final_pack["frascati_assessment"] = assessed["frascati_assessment"]
    final_pack["_contract_version"] = "nlp_evidence_v189_single_grouping_before_frascati"

    support_ids = _unique_support_ids(main_groups)
    assessment = assessed["frascati_assessment"]
    recommendation = int(assessment.get("eligibility_recommendation") or 0)
    recommendation_label = assessment.get("recommendation_label") or (
        "eligible_potentiel" if recommendation else "non_eligible_potentiel"
    )
    documentary_coverage = float(assessment.get("documentary_coverage") or 0.0)

    return {
        "version": VERSION,
        "mode": mode,
        "decision": recommendation,
        "eligibility_recommendation": recommendation,
        "recommendation_label": recommendation_label,
        "decision_semantics": "recommendation_ennosmart_not_administrative_cir_decision",
        "human_validation_required": True,
        "contract": "single_technical_grouping_then_frascati_v2_binary_recommendation",
        "qualification_order": "fastjudge_candidate_then_topic_grouping_then_frascati_assessment",
        "domain": domain or {},
        "documents_count": len(documents or []),
        "candidates_input_count": len(candidates),

        "fastjudge_verrou_signals_count":
            sum(
                bool(
                    item.get(
                        "fastjudge_verrou_signal",
                        True,
                    )
                )
                for item
                in candidates
            ),

        "project_lock_seed_count":
            sum(
                bool(
                    item.get(
                        "project_lock_seed"
                    )
                )
                for item
                in candidates
            ),

        "fastjudge_verrou_demoted_count":
            sum(
                bool(
                    item.get(
                        "fastjudge_verrou_signal",
                        True,
                    )
                )
                and not bool(
                    item.get(
                        "project_lock_seed"
                    )
                )
                for item
                in candidates
            ),

        "fastjudge_verrou_signals_audit":
            [
                dict(item)
                for item
                in candidates
            ],
        "candidate_groups_before_frascati_count": len(all_groups),
        "qualified_lock_passages_count": len(support_ids),
        "qualified_lock_groups_count": len(main_groups),
        "technical_lock_groups": all_groups,
        "verrous_rnd_locaux": main_groups,
        "secondary_technical_groups": secondary_groups,
        "lock_grouping_report": grouping,
        "frascati_assessment": assessment,
        "demarche_legibility": assessment.get("demarche_legibility", {}),
        "risk_report": {
            # Ancien champ conservé pour compatibilité. Il représente désormais
            # la couverture documentaire, pas une probabilité d'éligibilité.
            "global_frascati_score": documentary_coverage,
            "global_frascati_score_semantics": "documentary_coverage_not_probability_not_official_score",
            "documentary_coverage": documentary_coverage,
            "eligibility_assessment_score": assessment.get("eligibility_assessment_score", 0.0),
            "rnd_defensibility_index": assessment.get("rnd_defensibility_index", 0.0),
            "eligibility_assessment_score_semantics": assessment.get("eligibility_assessment_score_semantics"),
            "eligibility_recommendation": recommendation,
            "recommendation_label": recommendation_label,
            "risk_level": assessment.get("risk_level", "eleve"),
            "criteria_summary": assessment.get("criteria_summary", {}),
            "criteria": assessment.get("criteria", {}),
            "questions_to_ask": assessment.get("questions_to_ask", []),
            "demarche_legibility": assessment.get("demarche_legibility", {}),
            "human_validation_required": True,
            "decision": recommendation,
            "decision_semantics": "recommendation_ennosmart_not_administrative_cir_decision",
            "groups_removed_by_frascati": 0,
        },
        "consultant_view": {
            "eligibility_recommendation": recommendation,
            "recommendation_label": recommendation_label,
            "risk_level": assessment.get("risk_level", "eleve"),
            "documentary_coverage": documentary_coverage,
            "eligibility_assessment_score": assessment.get("eligibility_assessment_score", 0.0),
            "rnd_defensibility_index": assessment.get("rnd_defensibility_index", 0.0),
            "criteria_summary": assessment.get("criteria_summary", {}),
            "demarche_legibility": assessment.get("demarche_legibility", {}),
            "potential_verrous_count": len(main_groups),
            "secondary_technical_groups_count": len(secondary_groups),
            "qualified_passages_count": len(support_ids),
            "display_status": (
                "candidat_potentiellement_eligible_a_valider"
                if recommendation == 1
                else "non_eligible_potentiel_a_revoir_humainement"
            ),
        },
        # Champs historiques conservés pour ne pas casser les consommateurs.
        "verrous_probables": [],
        "verrous_a_verifier": [],
        "groupes_rejetes": [],
        "faux_verrous_rejetes": [],
        "groups_removed_by_frascati": 0,
        "qualified_pack_for_ennodiagnostic": final_pack,
    }
