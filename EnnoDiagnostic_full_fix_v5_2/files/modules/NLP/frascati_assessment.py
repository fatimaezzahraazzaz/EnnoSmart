# -*- coding: utf-8 -*-
# ENNODIAG_FULL_FIX_V5_20260829 — state-art cannot prove project R&D chain
from __future__ import annotations

# ENNODIAG_FINAL_FIX_V4_20260829 — frascati_assessment

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

VERSION = "frascati_assessment_v186_external_chain_evidence_excluded"

DIMENSIONS = (
    "novelty",
    "creativity",
    "uncertainty",
    "systematicity",
    "transferability",
)

DIMENSION_LABELS = {
    "novelty": "Nouveauté",
    "creativity": "Créativité",
    "uncertainty": "Incertitude scientifique ou technique",
    "systematicity": "Démarche systématique",
    "transferability": "Transférabilité ou reproductibilité",
}

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


_REFERENCE_SECTION_RE = re.compile(
    r"\b(?:etat de l art|state of the art|related work|revue de la litterature|bibliograph|references?)\b",
    re.I,
)
_REFERENCE_BODY_RE = re.compile(
    r"\b(?:dans le papier|the paper|les auteurs|the authors|selon (?:les auteurs|l etude|l article)|"
    r"l etude a (?:inclus|porte|teste|evalue)|une etude empirique|"
    r"analyse de \d+ (?:papers?|articles?|publications?)|survey|systematic review|"
    r"certaines etudes (?:montrent|suggerent|indiquent)|des etudes (?:montrent|suggerent|indiquent)|"
    r"ils ont (?:utilise|cree|entraine|propose|compare|evalue|configure)|"
    r"une approche .{0,120}(?:a ete|est) proposee|"
    r"les resultats montrent que .{0,220}(?:couramment|frequemment)|et al\.)\b",
    re.I,
)

def _passages(group: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [p for p in (group.get("supporting_passages") or []) if isinstance(p, Mapping)]


def _is_reference_like_passage(passage: Mapping[str, Any]) -> bool:
    if bool(passage.get("reference_like") or passage.get("is_state_of_art") or passage.get("is_external_literature")):
        return True
    conflicts = " ".join(str(v) for v in (passage.get("semantic_role_conflicts") or []))
    if "etat_art" in _norm(conflicts) or "state_of_art" in _norm(conflicts):
        return True
    section = _norm(passage.get("section_title"))
    body = _norm(" ".join(str(passage.get(k) or "") for k in ("context_before", "text", "analysis_text", "context_after")))
    return bool(_REFERENCE_SECTION_RE.search(section) or _REFERENCE_BODY_RE.search(body))


def _project_passages(group: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [p for p in _passages(group) if not _is_reference_like_passage(p)]


def _group_with_project_passages(group: Mapping[str, Any]) -> Dict[str, Any]:
    value = dict(group)
    value["supporting_passages"] = list(_project_passages(group))
    return value


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
) -> Dict[str, Any]:
    pattern_ids = _ids_matching_pattern(group, PATTERNS["systematicity_positive"])
    role_ids = _ids_for_roles(group, ROLE_SUPPORT["systematicity"])
    strong_roles = roles & {"methode", "parametre", "resultat"}

    # La systematicite mesure seulement l'organisation et la tracabilite de la
    # demarche. Une procedure peut etre tres systematique tout en restant de
    # l'ingenierie classique ; cette nature est decidee par le garde separe
    # demarche_legibility, jamais par ce critere Frascati.
    if pattern_ids and len(strong_roles) >= 2:
        return _criterion(
            name="systematicity",
            status="documented",
            evidence_ids=pattern_ids + role_ids,
            reason="Le dossier documente une démarche structurée avec essais, mesures, paramètres, comparaisons ou résultats.",
            question_needed=False,
        )
    if pattern_ids or role_ids:
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


def _criteria_score_breakdown(
    criteria: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Explique exactement la part acquise et la part manquante du score.

    Chaque critère pèse 20 points. ``documented`` apporte 20 points,
    ``partial`` 10 points et ``missing``/``contradictory`` 0 point. Ce détail
    évite toute justification LLM inventée du type « +4 dû à ... ».
    """
    weight = round(1.0 / len(DIMENSIONS), 4)
    output: List[Dict[str, Any]] = []
    for name in DIMENSIONS:
        item = criteria.get(name) if isinstance(criteria.get(name), Mapping) else {}
        status = str(item.get("status") or "missing")
        status_value = STATUS_VALUE.get(status, 0.0)
        contribution = round(weight * status_value, 4)
        gap = round(weight - contribution, 4)
        output.append({
            "criterion": name,
            "label": DIMENSION_LABELS[name],
            "status": status,
            "criterion_weight": weight,
            "contribution_to_index": contribution,
            "remaining_gap_to_full_coverage": gap,
            "reason": item.get("reason"),
            "evidence_ids": list(dict.fromkeys(
                str(value) for value in (item.get("evidence_ids") or []) if value
            )),
            "question": item.get("question"),
        })
    return output


def _eligibility_assessment_score(
    recommendation: int,
    documentary_coverage: float,
    demarche: Mapping[str, Any],
) -> float:
    """Sépare couverture documentaire et défendabilité R&D de la démarche.

    Un dossier peut citer les cinq critères tout en ne décrivant qu'une mise en
    œuvre d'ingénierie. Le score affiché ne doit donc plus être la simple copie
    de la couverture documentaire. La complétude de la chaîne causale, déjà
    calculée par ``demarche_legibility``, calibre le noyau R&D partiel.
    """
    coverage = max(0.0, min(1.0, float(documentary_coverage or 0.0)))
    operation_status = str(
        demarche.get("project_status")
        or demarche.get("operation_status")
        or ""
    )
    if operation_status == "classical_engineering":
        # Une opération purement classique peut être très bien documentée sans
        # devenir de la R&D. Le plancher non nul signale que l'analyse a bien eu
        # lieu, sans suggérer une éligibilité potentielle.
        return 0.01 if coverage > 0 else 0.0
    if not recommendation or operation_status == "insufficient_evidence":
        return 0.0
    if operation_status == "rnd_core_partial":
        causal_readability = max(
            0.0,
            min(1.0, float(demarche.get("readability_score") or 0.0)),
        )
        return round(coverage * causal_readability, 4)
    return round(coverage, 4)


def assess_group_frascati(group: Mapping[str, Any]) -> Dict[str, Any]:
    # L'état de l'art peut étayer la nouveauté, mais il ne peut jamais prouver
    # créativité, incertitude propre au projet, démarche, résultat ou transfert.
    project_group = _group_with_project_passages(group)
    blob_all = _text_blob(group)
    roles_all = _all_roles(group)
    blob_project = _text_blob(project_group)
    roles_project = _all_roles(project_group)
    demarche = assess_group_demarche_legibility(project_group)

    criteria: Dict[str, Dict[str, Any]] = {
        "novelty": _assess_novelty(group, blob_all, roles_all),
        "creativity": _assess_creativity(project_group, blob_project, roles_project),
        "uncertainty": _assess_uncertainty(project_group, blob_project),
        "systematicity": _assess_systematicity(project_group, blob_project, roles_project),
        "transferability": _assess_transferability(project_group, blob_project, roles_project),
    }

    recommendation, recommendation_label, blocking = _recommendation(criteria)
    if demarche.get("operation_status") == "classical_engineering":
        recommendation = 0
        recommendation_label = "non_eligible_potentiel"
        blocking = list(dict.fromkeys([*blocking, "classical_engineering_operation"]))
    risk = _risk_level(recommendation, criteria)
    if (
        recommendation == 1
        and demarche.get("operation_status") in {"rnd_core_partial", "insufficient_evidence"}
        and risk == "faible"
    ):
        risk = "moyen"
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
            "reason": "La chaîne causale de l'opération ou le rattachement de ses activités doit être démontré.",
        })

    counts = {
        "documented": sum(1 for name in DIMENSIONS if criteria[name]["status"] == "documented"),
        "partial": sum(1 for name in DIMENSIONS if criteria[name]["status"] == "partial"),
        "missing": sum(1 for name in DIMENSIONS if criteria[name]["status"] == "missing"),
        "contradictory": sum(1 for name in DIMENSIONS if criteria[name]["status"] == "contradictory"),
    }
    score_breakdown = _criteria_score_breakdown(criteria)

    return {
        "version": VERSION,
        "group_id": group.get("lock_group_id") or group.get("passage_id"),
        "criteria": criteria,
        # Alias conservé pour compatibilité avec l'ancien frontend/backend.
        "dimensions": criteria,
        "criteria_summary": counts,
        "documentary_coverage": coverage,
        "documented_share": coverage,
        "remaining_documentary_gap": round(max(0.0, 1.0 - coverage), 4),
        "criteria_score_breakdown": score_breakdown,
        "score_formula": "equal_weight_20_percent_per_frascati_criterion_documented_20_partial_10_missing_or_contradictory_0",
        "documentary_coverage_label": f"{counts['documented']} documentes, {counts['partial']} partiels sur 5",
        "eligibility_score": coverage,
        "eligibility_score_semantics": "legacy_alias_of_documentary_coverage_not_probability_not_official_frascati_score",
        "eligibility_assessment_score": eligibility_assessment_score,
        "eligibility_assessment_score_semantics": "rnd_defensibility_index_separate_from_documentary_coverage_calibrated_by_operation_nature_and_causal_chain_not_official_cir_probability",
        "rnd_defensibility_index": eligibility_assessment_score,
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

    # Cette fusion est une vue portefeuille uniquement. Elle ne sert jamais à
    # rendre le projet éligible : la décision reste calculée opération par
    # opération afin de ne pas assembler cinq critères provenant de groupes
    # différents en une opération R&D fictive.
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
            "documented_share": 0.0,
            "remaining_documentary_gap": 1.0,
            "score_basis_group_id": None,
            "score_basis_operation_status": "insufficient_evidence",
            "score_basis_criteria_breakdown": _criteria_score_breakdown(empty_criteria),
            "score_basis_guard_blocked": True,
            "score_formula": "equal_weight_20_percent_per_frascati_criterion_documented_20_partial_10_missing_or_contradictory_0_then_separate_classical_engineering_guard",
            "eligibility_score": 0.0,
            "eligibility_score_semantics": "legacy_alias_of_documentary_coverage_not_probability_not_official_frascati_score",
            "eligibility_assessment_score": 0.0,
            "eligibility_assessment_score_semantics": "rnd_defensibility_index_equal_to_frascati_documentary_coverage_when_operation_not_classical_not_official_cir_probability",
            "rnd_defensibility_index": 0.0,
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
    eligible_groups = [
        item
        for item in assessments
        if int(item.get("eligibility_recommendation") or 0) == 1
        and (item.get("demarche_legibility") or {}).get("operation_status")
        in {"rnd_core_defendable", "rnd_core_partial"}
    ]
    recommendation = 1 if eligible_groups else 0
    recommendation_label = "eligible_potentiel" if recommendation else "non_eligible_potentiel"

    questions: List[Dict[str, str]] = []
    seen = set()
    for item in assessments:
        for question in item.get("questions_to_ask") or []:
            signature = (question.get("dimension"), question.get("question"))
            if signature not in seen:
                seen.add(signature)
                questions.append(dict(question))

    portfolio_coverage = _coverage(project_criteria)
    coverage_pool = eligible_groups or assessments

    # L'indice projet doit être porté en priorité par l'opération R&D la plus
    # défendable, puis par la mieux documentée. À couverture égale (ou même
    # lorsqu'un noyau partiel est davantage documenté), on ne laisse pas une
    # opération partielle devenir l'opération de référence si un noyau R&D
    # défendable existe. Cette règle reste générique et ne dépend d'aucun projet.
    status_priority = {
        "rnd_core_defendable": 4,
        "rnd_core_partial": 3,
        "insufficient_evidence": 2,
        "classical_engineering": 1,
    }

    def _score_basis_key(item: Mapping[str, Any]) -> tuple:
        demarche = item.get("demarche_legibility")
        demarche = demarche if isinstance(demarche, Mapping) else {}
        operation_status = str(demarche.get("operation_status") or "insufficient_evidence")
        return (
            status_priority.get(operation_status, 0),
            float(item.get("documentary_coverage") or 0.0),
            1 if int(item.get("eligibility_recommendation") or 0) == 1 else 0,
        )

    score_basis = max(coverage_pool, key=_score_basis_key, default={})
    coverage = float(score_basis.get("documentary_coverage") or 0.0)
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
    if recommendation == 0:
        risk = "eleve"
    else:
        eligible_risks = [str(item.get("risk_level") or "moyen") for item in eligible_groups]
        mixed_perimeter = len(eligible_groups) != len(assessments) or bool(
            project_demarche.get("risk_adjustment")
            in {"raise_to_medium", "raise_to_medium_for_perimeter", "raise_to_medium_or_human_review"}
        )
        if "eleve" in eligible_risks:
            risk = "eleve"
        elif mixed_perimeter or "moyen" in eligible_risks:
            risk = "moyen"
        else:
            risk = "faible"
    eligibility_assessment_score = _eligibility_assessment_score(
        recommendation,
        coverage,
        project_demarche,
    )
    basis_breakdown = score_basis.get("criteria_score_breakdown")
    if not isinstance(basis_breakdown, list):
        basis_criteria = score_basis.get("criteria") or score_basis.get("dimensions") or {}
        basis_breakdown = _criteria_score_breakdown(basis_criteria) if isinstance(basis_criteria, Mapping) else []

    return {
        "version": VERSION,
        "criteria": project_criteria,
        "dimensions": project_criteria,
        "criteria_summary": summary,
        "documentary_coverage": coverage,
        "documented_share": coverage,
        "remaining_documentary_gap": round(max(0.0, 1.0 - coverage), 4),
        "score_basis_group_id": score_basis.get("group_id"),
        "score_basis_operation_status": (
            (score_basis.get("demarche_legibility") or {}).get("operation_status")
            if isinstance(score_basis.get("demarche_legibility"), Mapping)
            else None
        ),
        "score_basis_criteria_breakdown": basis_breakdown,
        "score_basis_guard_blocked": recommendation == 0,
        "score_formula": "five_equal_weight_frascati_criteria_for_documentary_coverage_then_rnd_defensibility_calibrated_by_operation_nature_and_causal_chain",
        "documentary_coverage_semantics": "best_complete_operation_coverage_not_cross_operation_merge_not_probability",
        "portfolio_criteria_coverage": portfolio_coverage,
        "portfolio_criteria_coverage_semantics": "descriptive_cross_operation_summary_not_used_for_project_recommendation",
        "eligibility_score": coverage,
        "eligibility_score_semantics": "legacy_alias_of_documentary_coverage_not_probability_not_official_frascati_score",
        "eligibility_assessment_score": eligibility_assessment_score,
        "eligibility_assessment_score_semantics": "rnd_defensibility_index_separate_from_documentary_coverage_calibrated_by_operation_nature_and_causal_chain_not_official_cir_probability",
        "rnd_defensibility_index": eligibility_assessment_score,
        "eligibility_recommendation": recommendation,
        "recommendation_label": recommendation_label,
        "eligibility_blocking_reason": (
            "no_potentially_eligible_operation"
            if not eligible_groups else None
        ),
        "risk_level": risk,
        "demarche_legibility": project_demarche,
        "eligible_groups_count": len(eligible_groups),
        "eligible_operations_count": len(eligible_groups),
        "groups_count": len(assessments),
        "project_criteria_semantics": "portfolio_summary_only_project_recommendation_is_based_on_complete_per_operation_assessments",
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
