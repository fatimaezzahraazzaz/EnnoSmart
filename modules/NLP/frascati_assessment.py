# -*- coding: utf-8 -*-
from __future__ import annotations

"""Frascati V2 - grille métier binaire, explicable et réaliste.

Objectif EnnoSmart :
- FastJudge détecte / classe les passages et fournit les candidats verrous.
- Frascati n'invente et ne supprime aucun verrou.
- Frascati évalue les cinq critères R&D à partir des preuves disponibles.
- La sortie exploitable pour le consultant est une RECOMMANDATION binaire 1/0.
- Un manque documentaire n'est PAS assimilé à un échec du critère.
- La validation finale reste humaine.

Important : ``eligibility_score`` est conservé uniquement comme alias de
compatibilité et correspond désormais à la COUVERTURE DOCUMENTAIRE, pas à une
probabilité d'éligibilité CIR et pas à un score officiel du Manuel de Frascati.
"""

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set
import re
import unicodedata

from .demarche_legibility import (
    assess_group_demarche_legibility,
    assess_project_demarche_legibility,
)

VERSION = "frascati_assessment_v182_demarche_necessity_and_routine_control"

DIMENSIONS = (
    "novelty",
    "creativity",
    "uncertainty",
    "systematicity",
    "transferability",
)

QUESTIONS = {
    "novelty": "Quelles connaissances ou solutions existantes ont été étudiées et pourquoi étaient-elles insuffisantes ?",
    "creativity": "Quelles conceptions, hypothèses ou combinaisons originales ont été élaborées ?",
    "uncertainty": "Quels résultats ou comportements étaient impossibles à prévoir au début des travaux ?",
    "systematicity": "Quelles hypothèses, campagnes d’essais, itérations et décisions sont tracées ?",
    "transferability": "Quelles connaissances acquises peuvent être documentées, reproduites ou réutilisées ?",
}

# Marqueurs prudents : ils servent à qualifier les preuves, jamais à créer un verrou.
PATTERNS = {
    "novelty_positive": re.compile(
        r"\b(?:etat de l.?art|solutions? existantes? (?:insuffisantes?|inadaptees?)|"
        r"aucune solution existante|non couvert par l.?existant|knowledge gap|"
        r"limites? de l.?existant|au-dela de l.?etat de l.?art)\b",
        re.I,
    ),
    "creativity_positive": re.compile(
        r"\b(?:hypoth[eè]se|conception|approche originale|nouvelle approche|architecture nouvelle|"
        r"combinaison originale|prototype|exploration de plusieurs solutions|strategie experimentale)\b",
        re.I,
    ),
    "systematicity_positive": re.compile(
        r"\b(?:campagne d.?essais?|protocole|exp[eé]riment|it[eé]ration|mesur|compar|benchmark|"
        r"validation|essais?|tests?|param[eè]tr|hypoth[eè]se|plan d.?exp[eé]rience)\b",
        re.I,
    ),
    "transferability_positive": re.compile(
        r"\b(?:reproduct|r[eé]utilis|g[eé]n[eé]ralis|document[eé]|capitalis|connaissances? acquises?|"
        r"m[eé]thode g[eé]n[eé]rique|transposable|r[eé]plicable)\b",
        re.I,
    ),
    "routine_contradiction": re.compile(
        r"\b(?:simple param[eé]trage|param[eé]trage standard|configuration standard|"
        r"solution standard directement applicable|solution connue directement applicable|"
        r"proc[eé]dure standard sans modification|simple int[eé]gration|adaptation courante|"
        r"aucune incertitude(?: technique| technologique| scientifique)?|aucun verrou(?: technique| technologique| scientifique)?|"
        r"aucun d[eé]veloppement exp[eé]rimental|aucun essai n.?a [eé]t[eé] n[eé]cessaire)\b",
        re.I,
    ),
}

ROLE_SUPPORT = {
    "novelty": {"objectif", "contribution", "limite"},
    "creativity": {"objectif", "methode", "contribution"},
    "uncertainty": {"verrou", "limite"},
    "systematicity": {"methode", "parametre", "resultat"},
    "transferability": {"methode", "resultat", "contribution"},
}

STATUS_VALUE = {
    "documented": 1.0,
    "partial": 0.5,
    "missing": 0.0,
    "contradictory": 0.0,
}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip()


def _passages(group: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [p for p in (group.get("supporting_passages") or []) if isinstance(p, Mapping)]


def _evidence_id(passage: Mapping[str, Any], index: int) -> str:
    return str(passage.get("passage_id") or passage.get("id") or f"E{index}")


def _all_evidence_ids(group: Mapping[str, Any]) -> List[str]:
    return [_evidence_id(p, i) for i, p in enumerate(_passages(group), start=1)]


def _text_blob(group: Mapping[str, Any]) -> str:
    parts = [str(group.get("text") or ""), str(group.get("analysis_text") or "")]
    for passage in _passages(group):
        parts.extend([
            str(passage.get("text") or ""),
            str(passage.get("analysis_text") or ""),
            str(passage.get("section_title") or ""),
        ])
    return _norm("\n".join(parts))


def _roles_for_passage(passage: Mapping[str, Any]) -> Set[str]:
    roles: Set[str] = set()
    for key in ("original_model_role", "semantic_role", "role"):
        role = _norm(passage.get(key))
        if role:
            roles.add(role)
    return roles


def _all_roles(group: Mapping[str, Any]) -> Set[str]:
    roles: Set[str] = set()
    for passage in _passages(group):
        roles.update(_roles_for_passage(passage))
    for role in group.get("source_semantic_roles") or []:
        normalized = _norm(role)
        if normalized:
            roles.add(normalized)
    return roles


def _ids_for_roles(group: Mapping[str, Any], wanted_roles: Set[str]) -> List[str]:
    output: List[str] = []
    for i, passage in enumerate(_passages(group), start=1):
        if _roles_for_passage(passage) & wanted_roles:
            output.append(_evidence_id(passage, i))
    return list(dict.fromkeys(output))


def _ids_matching_pattern(group: Mapping[str, Any], pattern: re.Pattern[str]) -> List[str]:
    output: List[str] = []
    for i, passage in enumerate(_passages(group), start=1):
        text = _norm(" ".join([
            str(passage.get("text") or ""),
            str(passage.get("analysis_text") or ""),
            str(passage.get("section_title") or ""),
        ]))
        if pattern.search(text):
            output.append(_evidence_id(passage, i))
    return list(dict.fromkeys(output))


def _uncertainty_signal_ids(group: Mapping[str, Any]) -> List[str]:
    output: List[str] = []
    for i, passage in enumerate(_passages(group), start=1):
        features = passage.get("lock_candidate_features") or {}
        has_feature = False
        if isinstance(features, Mapping):
            has_feature = any(bool(features.get(name)) for name in (
                "uncertainty",
                "causal_gap",
                "tradeoff",
                "open_validation",
                "knowledge_gap",
                "measurement_limit",
            ))
        roles = _roles_for_passage(passage)
        if (
            "verrou" in roles
            or bool(passage.get("direct_lock_candidate"))
            or bool(passage.get("lock_candidate"))
            or has_feature
        ):
            output.append(_evidence_id(passage, i))
    return list(dict.fromkeys(output))


def _criterion(
    *,
    name: str,
    status: str,
    evidence_ids: Sequence[str],
    reason: str,
    question_needed: bool,
) -> Dict[str, Any]:
    return {
        "status": status,
        "evidence_ids": list(dict.fromkeys(str(x) for x in evidence_ids if x)),
        "reason": reason,
        "question": QUESTIONS[name] if question_needed else None,
    }


def _assess_uncertainty(group: Mapping[str, Any], blob: str) -> Dict[str, Any]:
    if PATTERNS["routine_contradiction"].search(blob) and "aucune incertitude" in blob:
        return _criterion(
            name="uncertainty",
            status="contradictory",
            evidence_ids=_all_evidence_ids(group),
            reason="Les documents indiquent explicitement l'absence d'incertitude R&D.",
            question_needed=False,
        )

    signal_ids = _uncertainty_signal_ids(group)
    if signal_ids:
        return _criterion(
            name="uncertainty",
            status="documented",
            evidence_ids=signal_ids,
            reason="Au moins une preuve directe formule un verrou, une incertitude ou un résultat non maîtrisé.",
            question_needed=False,
        )

    role_ids = _ids_for_roles(group, ROLE_SUPPORT["uncertainty"])
    if role_ids:
        return _criterion(
            name="uncertainty",
            status="partial",
            evidence_ids=role_ids,
            reason="Des limites ou difficultés sont présentes mais l'incertitude R&D reste à expliciter.",
            question_needed=True,
        )

    return _criterion(
        name="uncertainty",
        status="missing",
        evidence_ids=[],
        reason="Aucune preuve suffisamment explicite d'incertitude scientifique ou technologique n'est disponible.",
        question_needed=True,
    )


def _assess_systematicity(
    group: Mapping[str, Any],
    blob: str,
    roles: Set[str],
    demarche: Mapping[str, Any],
) -> Dict[str, Any]:
    pattern_ids = _ids_matching_pattern(group, PATTERNS["systematicity_positive"])
    role_ids = _ids_for_roles(group, ROLE_SUPPORT["systematicity"])
    strong_roles = roles & {"methode", "parametre", "resultat"}
    justified_steps = int(demarche.get("research_justified_steps_count") or 0)
    method_steps = int(demarche.get("method_steps_count") or 0)

    if (
        demarche.get("label") == "routine_engineering_dominant"
        and justified_steps == 0
    ):
        return _criterion(
            name="systematicity",
            status="contradictory",
            evidence_ids=role_ids + pattern_ids,
            reason=(
                "Les etapes detectees relevent surtout de procedures d'ingenierie "
                "routiniere ou non differenciees, sans hypothese, necessite ni "
                "apprentissage experimental suffisamment traces."
            ),
            question_needed=False,
        )

    if justified_steps > 0 and pattern_ids and len(strong_roles) >= 2:
        return _criterion(
            name="systematicity",
            status="documented",
            evidence_ids=pattern_ids + role_ids,
            reason="Le dossier documente une démarche structurée avec essais, mesures, paramètres, comparaisons ou résultats.",
            question_needed=False,
        )
    if method_steps or pattern_ids or role_ids:
        return _criterion(
            name="systematicity",
            status="partial",
            evidence_ids=pattern_ids + role_ids,
            reason="Des éléments de démarche expérimentale ou méthodologique existent, mais leur traçabilité reste incomplète.",
            question_needed=True,
        )
    return _criterion(
        name="systematicity",
        status="missing",
        evidence_ids=[],
        reason="Aucune démarche expérimentale ou méthodologique suffisamment documentée n'est disponible.",
        question_needed=True,
    )


def _assess_novelty(group: Mapping[str, Any], blob: str, roles: Set[str]) -> Dict[str, Any]:
    # Hooks prévus pour EnnoScholar / une validation externe future.
    external = group.get("novelty_external_validation") or group.get("ennoscholar_novelty") or {}
    if isinstance(external, Mapping):
        ext_status = _norm(external.get("status"))
        if ext_status in {"documented", "validated", "supported", "confirmed"}:
            return _criterion(
                name="novelty",
                status="documented",
                evidence_ids=list(external.get("evidence_ids") or []),
                reason="La nouveauté / insuffisance de l'existant est étayée par une validation externe ou l'état de l'art.",
                question_needed=False,
            )
        if ext_status in {"contradictory", "rejected", "not_supported"}:
            return _criterion(
                name="novelty",
                status="contradictory",
                evidence_ids=list(external.get("evidence_ids") or []),
                reason="L'état de l'art indique que la solution était déjà connue ou directement disponible.",
                question_needed=False,
            )

    if PATTERNS["routine_contradiction"].search(blob):
        return _criterion(
            name="novelty",
            status="contradictory",
            evidence_ids=_all_evidence_ids(group),
            reason="Le dossier décrit explicitement une solution standard, connue ou une adaptation courante.",
            question_needed=False,
        )

    pattern_ids = _ids_matching_pattern(group, PATTERNS["novelty_positive"])
    if pattern_ids:
        return _criterion(
            name="novelty",
            status="documented",
            evidence_ids=pattern_ids,
            reason="Le dossier explicite les limites de l'existant ou un écart par rapport à l'état des connaissances.",
            question_needed=False,
        )

    role_ids = _ids_for_roles(group, ROLE_SUPPORT["novelty"])
    if role_ids or (roles & ROLE_SUPPORT["novelty"]):
        return _criterion(
            name="novelty",
            status="partial",
            evidence_ids=role_ids,
            reason="Le projet vise une contribution ou un objectif nouveau, mais l'état de l'art doit encore confirmer l'insuffisance de l'existant.",
            question_needed=True,
        )

    return _criterion(
        name="novelty",
        status="missing",
        evidence_ids=[],
        reason="La nouveauté par rapport aux connaissances ou solutions existantes n'est pas encore documentée.",
        question_needed=True,
    )


def _assess_creativity(group: Mapping[str, Any], blob: str, roles: Set[str]) -> Dict[str, Any]:
    if PATTERNS["routine_contradiction"].search(blob) and not (roles & {"methode", "contribution"}):
        return _criterion(
            name="creativity",
            status="contradictory",
            evidence_ids=_all_evidence_ids(group),
            reason="Le dossier décrit principalement une application standard sans conception ou hypothèse propre identifiable.",
            question_needed=False,
        )

    pattern_ids = _ids_matching_pattern(group, PATTERNS["creativity_positive"])
    role_ids = _ids_for_roles(group, ROLE_SUPPORT["creativity"])
    if pattern_ids and (roles & {"methode", "contribution"}):
        return _criterion(
            name="creativity",
            status="documented",
            evidence_ids=pattern_ids + role_ids,
            reason="Des hypothèses, conceptions ou choix originaux sont explicitement documentés.",
            question_needed=False,
        )
    if pattern_ids or role_ids:
        return _criterion(
            name="creativity",
            status="partial",
            evidence_ids=pattern_ids + role_ids,
            reason="Des choix de conception ou contributions sont présents mais leur caractère créatif doit être davantage justifié.",
            question_needed=True,
        )
    return _criterion(
        name="creativity",
        status="missing",
        evidence_ids=[],
        reason="Aucune conception, hypothèse ou combinaison originale n'est suffisamment documentée.",
        question_needed=True,
    )


def _assess_transferability(group: Mapping[str, Any], blob: str, roles: Set[str]) -> Dict[str, Any]:
    pattern_ids = _ids_matching_pattern(group, PATTERNS["transferability_positive"])
    role_ids = _ids_for_roles(group, ROLE_SUPPORT["transferability"])
    if pattern_ids and (roles & {"resultat", "contribution", "methode"}):
        return _criterion(
            name="transferability",
            status="documented",
            evidence_ids=pattern_ids + role_ids,
            reason="Le dossier décrit des connaissances, méthodes ou résultats pouvant être documentés, reproduits ou réutilisés.",
            question_needed=False,
        )
    if role_ids:
        return _criterion(
            name="transferability",
            status="partial",
            evidence_ids=role_ids,
            reason="Des résultats, méthodes ou contributions existent, mais leur réutilisabilité / reproductibilité reste à expliciter.",
            question_needed=True,
        )
    return _criterion(
        name="transferability",
        status="missing",
        evidence_ids=[],
        reason="La capitalisation, reproductibilité ou transférabilité des connaissances n'est pas documentée.",
        question_needed=True,
    )


def _recommendation(criteria: Mapping[str, Mapping[str, Any]]) -> tuple[int, str, List[str]]:
    """Règle métier réaliste : le manque documentaire ne vaut pas rejet.

    Le 0 est réservé aux cas où le noyau R&D n'est pas défendable :
    - incertitude absente / explicitement contredite ; ou
    - aucune démarche de résolution documentée ; ou
    - nouveauté explicitement contredite par une solution standard/connue.

    Sinon la recommandation est 1 (candidat potentiellement éligible), avec un
    risque qui dépend des preuves manquantes. Le consultant confirme toujours.
    """
    uncertainty = str(criteria["uncertainty"]["status"])
    systematicity = str(criteria["systematicity"]["status"])
    novelty = str(criteria["novelty"]["status"])

    blocking: List[str] = []
    if uncertainty in {"missing", "contradictory"}:
        blocking.append("uncertainty")
    if systematicity in {"missing", "contradictory"}:
        blocking.append("systematicity")
    if novelty == "contradictory":
        blocking.append("novelty")

    if blocking:
        return 0, "non_eligible_potentiel", blocking
    return 1, "eligible_potentiel", []


def _risk_level(recommendation: int, criteria: Mapping[str, Mapping[str, Any]]) -> str:
    """Niveau de risque opérationnel pour le consultant.

    Principe V181 :
    - recommandation 0 => risque élevé ;
    - contradiction explicite => risque élevé ;
    - recommandation 1 mais dossier incomplet/partiel => risque moyen ;
    - recommandation 1 et cinq critères documentés => risque faible.

    Un critère simplement ``missing`` dans un dossier par ailleurs candidat ne
    suffit donc plus à faire passer automatiquement le risque à ``eleve``.
    """
    statuses = [str(criteria[name]["status"]) for name in DIMENSIONS]

    if recommendation == 0:
        return "eleve"

    if "contradictory" in statuses:
        return "eleve"

    if all(status == "documented" for status in statuses):
        return "faible"

    return "moyen"


def _coverage(criteria: Mapping[str, Mapping[str, Any]]) -> float:
    # Indicateur descriptif à poids égaux, PAS un score d'éligibilité.
    value = sum(STATUS_VALUE.get(str(criteria[name].get("status")), 0.0) for name in DIMENSIONS)
    return round(value / len(DIMENSIONS), 4)


def _eligibility_assessment_score(
    recommendation: int,
    documentary_coverage: float,
    demarche: Mapping[str, Any],
) -> float:
    """Score interne combinant Frascati et pertinence de la demarche.

    Ce score n'est pas un score officiel CIR. Une demarche d'ingenierie
    classique dominante sans etape R&D justifiee vaut directement 0. Dans les
    cas mixtes, la couverture Frascati est ponderee par la lisibilite causale
    des etapes et par le risque de raccourci vers la solution finale.
    """
    if not recommendation:
        return 0.0
    if (
        demarche.get("label") == "routine_engineering_dominant"
        and int(demarche.get("research_justified_steps_count") or 0) == 0
    ):
        return 0.0
    if int(demarche.get("method_steps_count") or 0) <= 0:
        return 0.0
    try:
        readability = min(1.0, max(0.0, float(demarche.get("readability_score") or 0.0)))
    except Exception:
        readability = 0.0
    shortcut_factor = 0.8 if demarche.get("direct_final_solution_risk") else 1.0
    return round(float(documentary_coverage) * readability * shortcut_factor, 4)


def assess_group_frascati(group: Mapping[str, Any]) -> Dict[str, Any]:
    blob = _text_blob(group)
    roles = _all_roles(group)
    demarche = assess_group_demarche_legibility(group)

    criteria: Dict[str, Dict[str, Any]] = {
        "novelty": _assess_novelty(group, blob, roles),
        "creativity": _assess_creativity(group, blob, roles),
        "uncertainty": _assess_uncertainty(group, blob),
        "systematicity": _assess_systematicity(group, blob, roles, demarche),
        "transferability": _assess_transferability(group, blob, roles),
    }

    recommendation, recommendation_label, blocking = _recommendation(criteria)
    risk = _risk_level(recommendation, criteria)
    coverage = _coverage(criteria)
    eligibility_assessment_score = _eligibility_assessment_score(
        recommendation,
        coverage,
        demarche,
    )

    questions: List[Dict[str, str]] = []
    for name in DIMENSIONS:
        status = str(criteria[name].get("status"))
        question = criteria[name].get("question")
        if question and status in {"partial", "missing"}:
            questions.append({
                "dimension": name,
                "question": str(question),
                "reason": str(criteria[name].get("reason") or "preuve à compléter"),
            })
    for question in demarche.get("questions_to_ask") or []:
        questions.append({
            "dimension": "demarche_legibility",
            "question": str(question),
            "reason": "La necessite scientifique de certaines etapes doit etre demontree.",
        })

    counts = {
        "documented": sum(1 for name in DIMENSIONS if criteria[name]["status"] == "documented"),
        "partial": sum(1 for name in DIMENSIONS if criteria[name]["status"] == "partial"),
        "missing": sum(1 for name in DIMENSIONS if criteria[name]["status"] == "missing"),
        "contradictory": sum(1 for name in DIMENSIONS if criteria[name]["status"] == "contradictory"),
    }

    return {
        "version": VERSION,
        "group_id": group.get("lock_group_id") or group.get("passage_id"),
        "criteria": criteria,
        # Alias conservé pour compatibilité avec l'ancien frontend/backend.
        "dimensions": criteria,
        "criteria_summary": counts,
        "documentary_coverage": coverage,
        "documentary_coverage_label": f"{counts['documented']} documentes, {counts['partial']} partiels sur 5",
        "eligibility_score": coverage,
        "eligibility_score_semantics": "legacy_alias_of_documentary_coverage_not_probability_not_official_frascati_score",
        "eligibility_assessment_score": eligibility_assessment_score,
        "eligibility_assessment_score_semantics": "internal_decision_aid_combining_frascati_criteria_and_approach_legibility_not_official_cir_score",
        "eligibility_recommendation": recommendation,
        "recommendation_label": recommendation_label,
        "blocking_criteria": blocking,
        "risk_level": risk,
        "demarche_legibility": demarche,
        "interpretation": (
            "candidat_rnd_potentiellement_eligible_a_valider"
            if recommendation == 1
            else "noyau_rnd_insuffisamment_defendable_a_ce_stade"
        ),
        "questions_to_ask": questions,
        "human_validation_required": True,
        "decision": recommendation,
        "decision_semantics": "recommendation_ennosmart_not_administrative_cir_decision",
        "principle": (
            "Frascati fournit une recommandation binaire réaliste à partir des preuves disponibles. "
            "Un manque documentaire n'est pas un échec. Aucun verrou n'est supprimé et la validation finale reste humaine."
        ),
    }


def _merge_project_criterion_status(assessments: Sequence[Mapping[str, Any]], name: str) -> Dict[str, Any]:
    items = [
        (item.get("criteria") or item.get("dimensions") or {}).get(name) or {}
        for item in assessments
    ]
    statuses = [str(item.get("status") or "missing") for item in items]

    # Au niveau projet, une preuve documentée dans au moins une opération suffit
    # à documenter le critère pour la recommandation globale. Une contradiction
    # n'écrase pas automatiquement une autre opération R&D valide.
    if "documented" in statuses:
        status = "documented"
    elif "partial" in statuses:
        status = "partial"
    elif statuses and all(value == "contradictory" for value in statuses):
        status = "contradictory"
    elif "contradictory" in statuses and "missing" not in statuses:
        status = "contradictory"
    else:
        status = "missing"

    evidence_ids: List[str] = []
    reasons: List[str] = []
    for item in items:
        evidence_ids.extend(str(v) for v in (item.get("evidence_ids") or []) if v)
        if item.get("reason"):
            reasons.append(str(item.get("reason")))

    return {
        "status": status,
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
        "reason": " | ".join(dict.fromkeys(reasons))[:1200],
        "question": QUESTIONS[name] if status in {"partial", "missing"} else None,
    }


def assess_project_frascati(groups: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    groups_list = [group for group in groups if isinstance(group, Mapping)]
    assessments = [assess_group_frascati(group) for group in groups_list]
    project_demarche = assess_project_demarche_legibility(groups_list)

    if not assessments:
        empty_criteria = {
            name: _criterion(
                name=name,
                status="missing",
                evidence_ids=[],
                reason="Aucun verrou technique principal n'a été identifié pour documenter ce critère.",
                question_needed=True,
            )
            for name in DIMENSIONS
        }
        return {
            "version": VERSION,
            "criteria": empty_criteria,
            "dimensions": empty_criteria,
            "criteria_summary": {"documented": 0, "partial": 0, "missing": 5, "contradictory": 0},
            "documentary_coverage": 0.0,
            "eligibility_score": 0.0,
            "eligibility_score_semantics": "legacy_alias_of_documentary_coverage_not_probability_not_official_frascati_score",
            "eligibility_assessment_score": 0.0,
            "eligibility_assessment_score_semantics": "internal_decision_aid_combining_frascati_criteria_and_approach_legibility_not_official_cir_score",
            "eligibility_recommendation": 0,
            "recommendation_label": "non_eligible_potentiel",
            "risk_level": "eleve",
            "demarche_legibility": project_demarche,
            "group_assessments": [],
            "questions_to_ask": [
                {"dimension": name, "question": QUESTIONS[name], "reason": "aucune preuve disponible"}
                for name in DIMENSIONS
            ],
            "human_validation_required": True,
            "decision": 0,
            "decision_semantics": "recommendation_ennosmart_not_administrative_cir_decision",
        }

    project_criteria = {
        name: _merge_project_criterion_status(assessments, name)
        for name in DIMENSIONS
    }

    # Réalisme opérationnel : s'il existe au moins une opération / un verrou
    # principal recommandé 1, le projet est présenté comme candidat potentiel.
    # Le consultant voit ensuite les groupes à risque et les critères manquants.
    eligible_groups = [item for item in assessments if int(item.get("eligibility_recommendation") or 0) == 1]
    recommendation = 1 if eligible_groups else 0
    strict_routine_block = bool(
        project_demarche.get("label") == "routine_engineering_dominant"
        and int(project_demarche.get("research_justified_steps_count") or 0) == 0
    )
    if strict_routine_block:
        recommendation = 0
    recommendation_label = "eligible_potentiel" if recommendation else "non_eligible_potentiel"

    questions: List[Dict[str, str]] = []
    seen = set()
    for item in assessments:
        for question in item.get("questions_to_ask") or []:
            signature = (question.get("dimension"), question.get("question"))
            if signature not in seen:
                seen.add(signature)
                questions.append(dict(question))

    coverage = _coverage(project_criteria)
    summary = {
        "documented": sum(1 for name in DIMENSIONS if project_criteria[name]["status"] == "documented"),
        "partial": sum(1 for name in DIMENSIONS if project_criteria[name]["status"] == "partial"),
        "missing": sum(1 for name in DIMENSIONS if project_criteria[name]["status"] == "missing"),
        "contradictory": sum(1 for name in DIMENSIONS if project_criteria[name]["status"] == "contradictory"),
    }

    # V181 : le risque projet est calculé sur la grille consolidée, et non
    # comme le maximum mécanique des risques de chaque groupe. Cela évite
    # qu'un verrou secondaire incomplet fasse apparaître tout le projet comme
    # risque élevé alors que le noyau R&D reste défendable.
    risk = _risk_level(recommendation, project_criteria)
    if (
        recommendation == 1
        and project_demarche.get("label")
        in {"mixed_or_partially_justified_trajectory", "routine_engineering_dominant"}
        and risk == "faible"
    ):
        risk = "moyen"
    eligibility_assessment_score = _eligibility_assessment_score(
        recommendation,
        coverage,
        project_demarche,
    )

    return {
        "version": VERSION,
        "criteria": project_criteria,
        "dimensions": project_criteria,
        "criteria_summary": summary,
        "documentary_coverage": coverage,
        "eligibility_score": coverage,
        "eligibility_score_semantics": "legacy_alias_of_documentary_coverage_not_probability_not_official_frascati_score",
        "eligibility_assessment_score": eligibility_assessment_score,
        "eligibility_assessment_score_semantics": "internal_decision_aid_combining_frascati_criteria_and_approach_legibility_not_official_cir_score",
        "eligibility_recommendation": recommendation,
        "recommendation_label": recommendation_label,
        "eligibility_blocking_reason": (
            "routine_engineering_without_justified_rnd_step"
            if strict_routine_block else None
        ),
        "risk_level": risk,
        "demarche_legibility": project_demarche,
        "eligible_groups_count": len(eligible_groups),
        "groups_count": len(assessments),
        "group_assessments": assessments,
        "questions_to_ask": questions,
        "human_validation_required": True,
        "decision": recommendation,
        "decision_semantics": "recommendation_ennosmart_not_administrative_cir_decision",
        "principle": (
            "1 = candidat potentiellement éligible selon les preuves disponibles ; "
            "0 = noyau R&D insuffisamment défendable à ce stade. Validation consultant obligatoire."
        ),
    }
