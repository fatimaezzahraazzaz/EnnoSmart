# -*- coding: utf-8 -*-
from __future__ import annotations

"""Questionnaire Frascati explicable.

Ce module ne valide, ne rejette et ne supprime aucun verrou. Il évalue les cinq
critères R&D, indique les preuves disponibles, les manques et les questions à
poser au consultant. La décision finale reste humaine.
"""

from typing import Any, Dict, Iterable, List, Mapping
import math

VERSION = "frascati_assessment_v177_questions_scores_no_lock_filtering"
DIMENSIONS = ("novelty", "creativity", "uncertainty", "systematicity", "transferability")
WEIGHTS = {"novelty": 0.20, "creativity": 0.18, "uncertainty": 0.27, "systematicity": 0.20, "transferability": 0.15}
QUESTIONS = {
    "novelty": "Quelles connaissances ou solutions existantes ont été étudiées et pourquoi étaient-elles insuffisantes ?",
    "creativity": "Quelles conceptions, hypothèses ou combinaisons originales ont été élaborées ?",
    "uncertainty": "Quels résultats ou comportements étaient impossibles à prévoir au début des travaux ?",
    "systematicity": "Quelles hypothèses, campagnes d’essais, itérations et décisions sont tracées ?",
    "transferability": "Quelles connaissances acquises peuvent être documentées, reproduites ou réutilisées ?",
}


def _safe_float(value: Any) -> float:
    try:
        number = float(value or 0.0)
        return 0.0 if math.isnan(number) or math.isinf(number) else number
    except Exception:
        return 0.0


def _evidence_ids(group: Mapping[str, Any]) -> List[str]:
    output: List[str] = []
    for index, passage in enumerate(group.get("supporting_passages") or [], start=1):
        if not isinstance(passage, Mapping):
            continue
        output.append(str(passage.get("passage_id") or passage.get("id") or f"E{index}"))
    return output


def _feature_counts(group: Mapping[str, Any]) -> Dict[str, int]:
    counts = {name: 0 for name in (
        "uncertainty", "causal_gap", "tradeoff", "open_validation",
        "knowledge_gap", "measurement_limit", "dependency", "technical",
    )}
    roles = set()
    for passage in group.get("supporting_passages") or []:
        if not isinstance(passage, Mapping):
            continue
        features = passage.get("lock_candidate_features") or {}
        if isinstance(features, Mapping):
            for name in counts:
                counts[name] += int(bool(features.get(name)))
        role = str(passage.get("semantic_role") or passage.get("original_model_role") or "").strip().lower()
        if role:
            roles.add(role)
    counts["roles_count"] = len(roles)
    counts["documents_count"] = len(group.get("supporting_documents") or [])
    counts["passages_count"] = len(group.get("supporting_passages") or [])
    return counts


def _dimension_score(name: str, counts: Mapping[str, int]) -> float:
    passages = max(1, counts.get("passages_count", 0))
    documents = counts.get("documents_count", 0)
    roles = counts.get("roles_count", 0)
    if name == "uncertainty":
        value = 0.18 + 0.18 * counts.get("uncertainty", 0) + 0.22 * counts.get("causal_gap", 0) + 0.18 * counts.get("tradeoff", 0) + 0.12 * counts.get("open_validation", 0) + 0.10 * counts.get("knowledge_gap", 0)
    elif name == "systematicity":
        value = 0.12 + min(0.38, passages * 0.06) + min(0.20, roles * 0.05) + min(0.20, documents * 0.05)
    elif name == "creativity":
        value = 0.18 + min(0.26, roles * 0.06) + 0.14 * counts.get("tradeoff", 0) + 0.08 * counts.get("dependency", 0)
    elif name == "novelty":
        value = 0.16 + 0.15 * counts.get("causal_gap", 0) + 0.12 * counts.get("open_validation", 0) + 0.22 * counts.get("knowledge_gap", 0) + min(0.22, documents * 0.05)
    else:
        value = 0.12 + min(0.32, passages * 0.05) + min(0.18, documents * 0.05) + min(0.18, roles * 0.04)
    return round(min(max(value, 0.0), 1.0), 4)


def _answer(score: float) -> str:
    if score >= 0.75:
        return "fortement_documente"
    if score >= 0.55:
        return "partiellement_documente"
    if score >= 0.35:
        return "a_completer"
    return "insuffisamment_documente"


def _risk(score: float, missing_count: int) -> str:
    if score >= 0.72 and missing_count <= 1:
        return "faible"
    if score >= 0.50:
        return "moyen"
    return "eleve"


def assess_group_frascati(group: Mapping[str, Any]) -> Dict[str, Any]:
    counts = _feature_counts(group)
    evidence = _evidence_ids(group)
    dimensions: Dict[str, Any] = {}
    questions: List[Dict[str, str]] = []
    missing_total = 0
    for name in DIMENSIONS:
        score = _dimension_score(name, counts)
        answer = _answer(score)
        missing = [] if score >= 0.65 else [QUESTIONS[name]]
        if missing:
            missing_total += 1
            questions.append({"dimension": name, "question": QUESTIONS[name], "reason": "preuves documentaires insuffisantes ou indirectes"})
        dimensions[name] = {
            "score": score,
            "answer": answer,
            "confidence": round(min(0.95, 0.40 + 0.05 * counts.get("passages_count", 0) + 0.04 * counts.get("documents_count", 0)), 4),
            "evidence_ids": evidence,
            "missing_evidence": missing,
        }

    eligibility = round(sum(dimensions[name]["score"] * WEIGHTS[name] for name in DIMENSIONS), 4)
    if eligibility >= 0.75:
        interpretation = "signaux_rnd_forts"
    elif eligibility >= 0.55:
        interpretation = "signaux_rnd_presents_preuves_a_completer"
    elif eligibility >= 0.35:
        interpretation = "eligibilite_incertaine"
    else:
        interpretation = "preuves_rnd_insuffisantes"

    return {
        "version": VERSION,
        "group_id": group.get("lock_group_id") or group.get("passage_id"),
        "dimensions": dimensions,
        "eligibility_score": eligibility,
        "interpretation": interpretation,
        "risk_level": _risk(eligibility, missing_total),
        "questions_to_ask": questions,
        "human_validation_required": True,
        "principle": "Frascati évalue l’éligibilité R&D ; il ne valide, ne rejette et ne supprime aucun verrou technique.",
    }


def assess_project_frascati(groups: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    assessments = [assess_group_frascati(group) for group in groups]
    if not assessments:
        return {"version": VERSION, "eligibility_score": 0.0, "risk_level": "eleve", "group_assessments": [], "questions_to_ask": [], "human_validation_required": True}
    score = round(sum(item["eligibility_score"] for item in assessments) / len(assessments), 4)
    questions = []
    seen = set()
    for item in assessments:
        for question in item.get("questions_to_ask") or []:
            signature = (question.get("dimension"), question.get("question"))
            if signature not in seen:
                seen.add(signature)
                questions.append(question)
    return {
        "version": VERSION,
        "eligibility_score": score,
        "risk_level": _risk(score, len(questions)),
        "group_assessments": assessments,
        "questions_to_ask": questions,
        "human_validation_required": True,
        "decision": None,
    }
