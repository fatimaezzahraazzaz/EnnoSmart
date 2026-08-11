# -*- coding: utf-8 -*-
from __future__ import annotations

"""Audit documentaire de la pertinence des demarches techniques.

Ce module ne decide pas seul de l'eligibilite CIR. Il verifie si le dossier
explique pourquoi chaque etape etait necessaire face a une incertitude, ce
qu'elle cherchait a verifier et ce qui a ete appris. Il distingue ainsi une
trajectoire de recherche d'une simple succession de procedures d'ingenierie.
"""

from typing import Any, Dict, Iterable, List, Mapping, Set
import re
import unicodedata


VERSION = "demarche_legibility_v1_necessity_routine_shortcut_audit"


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip()


PATTERNS = {
    "method": re.compile(
        r"\b(?:demarche|methode|approche|etape|phase|procedure|protocole|"
        r"essai\w*|test\w*|simulation\w*|prototype\w*|iteration\w*|variante\w*|configuration\w*|"
        r"integration|implementation|deploiement|developpement)\b",
        re.I,
    ),
    "uncertainty": re.compile(
        r"\b(?:incert|inconnu|imprevisible|non maitr|verrou|limite|echec|"
        r"impossible (?:a|de) (?:predire|garantir|determiner)|reste a comprendre|"
        r"comportement indetermine|resultat non garanti)\b",
        re.I,
    ),
    "hypothesis": re.compile(
        r"\b(?:hypothese|nous suppos|postulat|afin de verifier|pour verifier|"
        r"chercher a determiner|question de recherche|scenario teste)\b",
        re.I,
    ),
    "evaluation": re.compile(
        r"\b(?:essai\w*|test\w*|experiment\w*|mesur\w*|compar\w*|benchmark\w*|plan d.experience|"
        r"prototype\w*|simulation\w*|validation\w*|critere\w*|indicateur\w*|metrique\w*|temoin\w*)\b",
        re.I,
    ),
    "learning": re.compile(
        r"\b(?:a montre|a revele|a permis de|nous avons observe|resultat|"
        r"enseignement|conclusion|rejete|retenu|abandonne|en consequence|"
        r"a conduit a|suite aux essais|ecart observe)\b",
        re.I,
    ),
    "rationale": re.compile(
        r"\b(?:parce que|afin de|dans le but de|pour evaluer|pour determiner|"
        r"compte tenu|en raison de|suite a|pour lever|pour comprendre)\b",
        re.I,
    ),
    "routine": re.compile(
        r"\b(?:simple (?:parametrage|integration|adaptation|configuration)|"
        r"configuration standard|procedure standard|mode operatoire|"
        r"bonne pratique|best practice|conformement a la documentation|"
        r"installation|migration|deploiement|maintenance|mise a jour|"
        r"correction de bug|recette fonctionnelle|developpement (?:crud|standard)|"
        r"solution (?:standard|connue) directement applicable)\b",
        re.I,
    ),
    "final_solution": re.compile(
        r"\b(?:solution finale|solution retenue|choix retenu|meilleure solution|"
        r"approche retenue|derniere version|finalement|au final)\b",
        re.I,
    ),
}


METHOD_ROLES = {"methode", "parametre"}
RESULT_ROLES = {"resultat", "contribution"}
UNCERTAINTY_ROLES = {"verrou", "limite"}


def _roles(passage: Mapping[str, Any]) -> Set[str]:
    return {
        role
        for role in (
            _norm(passage.get("original_model_role")),
            _norm(passage.get("semantic_role")),
            _norm(passage.get("role")),
        )
        if role
    }


def _passage_text(passage: Mapping[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in (
            passage.get("text"),
            passage.get("analysis_text"),
            passage.get("section_title"),
        )
    ).strip()


def _evidence_id(passage: Mapping[str, Any], index: int) -> str:
    return str(passage.get("passage_id") or passage.get("id") or f"E{index}")


def _tokens(text: str) -> Set[str]:
    stop = {
        "avec", "dans", "pour", "cette", "nous", "une", "des", "les", "est",
        "sont", "par", "sur", "qui", "que", "afin", "plus", "ete", "etape",
        "phase", "methode", "approche", "procedure",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]{3,}", _norm(text))
        if token not in stop
    }


def _near_duplicate(left: str, right: str) -> bool:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if len(left_tokens) < 5 or len(right_tokens) < 5:
        return False
    union = left_tokens | right_tokens
    return bool(union) and len(left_tokens & right_tokens) / len(union) >= 0.78


def _empty_report() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "label": "insufficient_documentation",
        "readability_score": 0.0,
        "readability_score_semantics": "documentary_clarity_of_approach_not_cir_probability",
        "method_steps_count": 0,
        "research_justified_steps_count": 0,
        "routine_engineering_steps_count": 0,
        "unexplained_steps_count": 0,
        "redundant_steps_count": 0,
        "direct_final_solution_assessment": "not_assessable",
        "direct_final_solution_risk": False,
        "eligibility_impact": "documentation_gap_requires_human_review",
        "llm_review_recommended": False,
        "llm_review_reasons": [],
        "llm_policy": "reuse_existing_ennodiagnostic_call_only_no_dedicated_call_by_default",
        "steps": [],
        "questions_to_ask": [
            "Quelles etapes ont ete necessaires pour lever chaque incertitude, et quel apprentissage a motive le passage a l'etape suivante ?"
        ],
        "human_validation_required": True,
    }


def assess_group_demarche_legibility(group: Mapping[str, Any]) -> Dict[str, Any]:
    passages = [
        passage
        for passage in (group.get("supporting_passages") or [])
        if isinstance(passage, Mapping)
    ]
    if not passages:
        return _empty_report()

    group_blob = _norm("\n".join(_passage_text(passage) for passage in passages))
    group_has_uncertainty = bool(PATTERNS["uncertainty"].search(group_blob)) or any(
        _roles(passage) & UNCERTAINTY_ROLES for passage in passages
    )
    group_has_hypothesis = bool(PATTERNS["hypothesis"].search(group_blob))
    group_has_result = bool(PATTERNS["learning"].search(group_blob)) or any(
        _roles(passage) & RESULT_ROLES for passage in passages
    )

    method_passages: List[tuple[int, Mapping[str, Any], str, str]] = []
    for index, passage in enumerate(passages, start=1):
        raw_text = _passage_text(passage)
        text = _norm(raw_text)
        passage_roles = _roles(passage)
        explicit_method_role = bool(passage_roles & METHOD_ROLES)
        result_or_uncertainty_only = bool(
            passage_roles & (RESULT_ROLES | UNCERTAINTY_ROLES)
        ) and not explicit_method_role
        if (
            explicit_method_role
            or (
                not result_or_uncertainty_only
                and (
                    PATTERNS["method"].search(text)
                    or PATTERNS["final_solution"].search(text)
                )
            )
        ):
            method_passages.append((index, passage, raw_text, text))

    if not method_passages:
        return _empty_report()

    steps: List[Dict[str, Any]] = []
    previous_texts: List[str] = []
    for step_number, (index, passage, raw_text, text) in enumerate(method_passages, start=1):
        local_uncertainty = bool(PATTERNS["uncertainty"].search(text))
        local_hypothesis = bool(PATTERNS["hypothesis"].search(text))
        local_learning = bool(PATTERNS["learning"].search(text))
        local_rationale = bool(PATTERNS["rationale"].search(text))
        signals = {
            # Les quatre premiers signaux sont locaux a l'etape. Le contexte du
            # groupe reste visible separement et ne suffit jamais, a lui seul,
            # a transformer une procedure courante en demarche de recherche.
            "uncertainty_link": local_uncertainty,
            "hypothesis_link": local_hypothesis,
            "evaluation_protocol": bool(PATTERNS["evaluation"].search(text)),
            "learning_or_decision": local_learning,
            "necessity_rationale": local_rationale,
            "routine_engineering": bool(PATTERNS["routine"].search(text)),
            "final_solution": bool(PATTERNS["final_solution"].search(text)),
            "context_has_uncertainty": group_has_uncertainty,
            "context_has_hypothesis": group_has_hypothesis,
            "context_has_result": group_has_result,
        }
        redundant = any(_near_duplicate(text, previous) for previous in previous_texts)
        previous_texts.append(text)

        # Une campagne globale contenant un verrou et un resultat ne justifie
        # pas automatiquement toutes ses etapes. Chaque etape doit exprimer au
        # moins un lien causal local, puis etre rattachee a une evaluation et a
        # un apprentissage local ou documente dans le meme groupe.
        causal_link = bool(
            local_uncertainty
            or local_hypothesis
            or (local_rationale and group_has_uncertainty)
        )
        research_core = bool(
            signals["evaluation_protocol"]
            and causal_link
            and (local_learning or group_has_result)
        )
        if redundant and not research_core:
            classification = "redundant_or_undifferentiated_step"
            necessity = "not_demonstrated"
        elif signals["routine_engineering"] and not research_core:
            classification = "routine_engineering"
            necessity = "not_demonstrated"
        elif research_core:
            classification = "research_step_justified"
            necessity = "demonstrated"
        else:
            classification = "needs_explanation"
            necessity = "partial"

        steps.append(
            {
                "step_number": step_number,
                "evidence_id": _evidence_id(passage, index),
                "text_excerpt": " ".join(raw_text.split())[:500],
                "classification": classification,
                "necessity_status": necessity,
                "signals": signals,
            }
        )

    justified = sum(step["classification"] == "research_step_justified" for step in steps)
    routine = sum(step["classification"] == "routine_engineering" for step in steps)
    redundant = sum(step["classification"] == "redundant_or_undifferentiated_step" for step in steps)
    unexplained = sum(step["classification"] == "needs_explanation" for step in steps)
    final_positions = [i for i, step in enumerate(steps) if step["signals"]["final_solution"]]
    prior_to_final = steps[: final_positions[-1]] if final_positions else []
    unjustified_before_final = sum(
        step["classification"] != "research_step_justified" for step in prior_to_final
    )
    direct_final_risk = bool(
        final_positions
        and prior_to_final
        and unjustified_before_final * 2 >= len(prior_to_final)
    )
    if direct_final_risk:
        final_assessment = "possible_shortcut_not_excluded"
    elif final_positions and prior_to_final:
        final_assessment = "iterations_justified_by_documented_learning"
    elif final_positions:
        final_assessment = "final_solution_present_without_prior_trajectory"
    else:
        final_assessment = "not_assessable"

    score = round((justified + 0.5 * unexplained) / len(steps), 4)
    if justified == len(steps):
        label = "clear_research_trajectory"
        impact = "supports_systematicity"
    elif justified == 0 and (routine + redundant) * 2 >= len(steps):
        label = "routine_engineering_dominant"
        impact = "weakens_systematicity_and_rnd_character"
    else:
        label = "mixed_or_partially_justified_trajectory"
        impact = "requires_human_review"

    questions: List[str] = []
    if routine:
        questions.append(
            "Quelles incertitudes non resolues rendaient les etapes d'ingenierie standard necessaires a une investigation de R&D ?"
        )
    if unexplained or redundant:
        questions.append(
            "Pour chaque variante ou procedure, preciser l'hypothese testee, le critere de comparaison et l'apprentissage obtenu."
        )
    if direct_final_risk:
        questions.append(
            "Pourquoi la solution finalement retenue ne pouvait-elle pas etre choisie des le depart a partir des connaissances accessibles ?"
        )
    llm_reasons: List[str] = []
    if label == "mixed_or_partially_justified_trajectory":
        llm_reasons.append("mixed_research_and_engineering_signals")
    if direct_final_risk:
        llm_reasons.append("possible_direct_final_solution_shortcut")
    if len(steps) > 1 and (unexplained or redundant):
        llm_reasons.append("multiple_steps_without_explicit_causal_links")

    return {
        "version": VERSION,
        "label": label,
        "readability_score": score,
        "readability_score_semantics": "documentary_clarity_of_approach_not_cir_probability",
        "method_steps_count": len(steps),
        "research_justified_steps_count": justified,
        "routine_engineering_steps_count": routine,
        "unexplained_steps_count": unexplained,
        "redundant_steps_count": redundant,
        "direct_final_solution_assessment": final_assessment,
        "direct_final_solution_risk": direct_final_risk,
        "eligibility_impact": impact,
        "llm_review_recommended": bool(llm_reasons),
        "llm_review_reasons": llm_reasons,
        "llm_policy": "reuse_existing_ennodiagnostic_call_only_no_dedicated_call_by_default",
        "steps": steps,
        "questions_to_ask": questions,
        "human_validation_required": True,
    }


def assess_project_demarche_legibility(groups: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    reports = [assess_group_demarche_legibility(group) for group in groups]
    if not reports:
        report = _empty_report()
        report["group_reports"] = []
        return report

    method_steps = sum(int(report.get("method_steps_count") or 0) for report in reports)
    justified = sum(int(report.get("research_justified_steps_count") or 0) for report in reports)
    routine = sum(int(report.get("routine_engineering_steps_count") or 0) for report in reports)
    unexplained = sum(int(report.get("unexplained_steps_count") or 0) for report in reports)
    redundant = sum(int(report.get("redundant_steps_count") or 0) for report in reports)
    shortcut_groups = sum(bool(report.get("direct_final_solution_risk")) for report in reports)

    if method_steps == 0:
        label = "insufficient_documentation"
        impact = "documentation_gap_requires_human_review"
        score = 0.0
    else:
        score = round((justified + 0.5 * unexplained) / method_steps, 4)
        if justified == method_steps:
            label = "clear_research_trajectory"
            impact = "supports_systematicity"
        elif justified == 0 and (routine + redundant) * 2 >= method_steps:
            label = "routine_engineering_dominant"
            impact = "weakens_systematicity_and_rnd_character"
        else:
            label = "mixed_or_partially_justified_trajectory"
            impact = "requires_human_review"

    questions: List[str] = []
    llm_reasons: List[str] = []
    for report in reports:
        for question in report.get("questions_to_ask") or []:
            if question not in questions:
                questions.append(str(question))
        for reason in report.get("llm_review_reasons") or []:
            if reason not in llm_reasons:
                llm_reasons.append(str(reason))

    return {
        "version": VERSION,
        "label": label,
        "readability_score": score,
        "readability_score_semantics": "documentary_clarity_of_approach_not_cir_probability",
        "method_steps_count": method_steps,
        "research_justified_steps_count": justified,
        "routine_engineering_steps_count": routine,
        "unexplained_steps_count": unexplained,
        "redundant_steps_count": redundant,
        "groups_with_possible_direct_final_solution_shortcut": shortcut_groups,
        "direct_final_solution_risk": shortcut_groups > 0,
        "eligibility_impact": impact,
        "llm_review_recommended": bool(llm_reasons),
        "llm_review_reasons": llm_reasons,
        "llm_policy": "reuse_existing_ennodiagnostic_call_only_no_dedicated_call_by_default",
        "questions_to_ask": questions,
        "group_reports": reports,
        "human_validation_required": True,
    }
