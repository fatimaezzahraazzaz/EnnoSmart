# -*- coding: utf-8 -*-
# ENNODIAG_FULL_FIX_V5_20260829 — project-evidence-only R&D causal chain
from __future__ import annotations

"""Étude documentaire de la nécessité des démarches techniques.

L'unité principale est l'opération rattachée à un verrou consolidé, jamais le
passage isolé. Les passages ordonnés servent à reconstruire une chaîne causale
incertitude -> hypothèse/raison -> expérience -> résultat/apprentissage. Les
activités internes sont ensuite périmétrées séparément.
"""

from typing import Any, Dict, Iterable, List, Mapping, Set

try:
    from agents.EnnoDiagnostic.project_fact_gate import (
        gate_project_fact,
        has_explicit_executed_method,
        is_external_or_reference as _gate_external_or_reference,
        is_noise_or_interview as _gate_noise_or_interview,
    )
except Exception:
    def has_explicit_executed_method(source):  # type: ignore
        return False
    def _gate_external_or_reference(source):  # type: ignore
        return False
    def _gate_noise_or_interview(source):  # type: ignore
        return False
    def gate_project_fact(source, section_key):  # type: ignore
        class _Decision:
            allowed = True
            reason = "gate_unavailable"
        return _Decision()
import re
import unicodedata


VERSION = "demarche_legibility_v5_8_mixed_role_executed_evidence"
LLM_POLICY = "reuse_existing_ennodiagnostic_call_only_no_dedicated_call_by_default"


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip()


PATTERNS = {
    "method": re.compile(
        r"\b(?:demarche|methode|approche|etape|phase|procedure|protocole|"
        r"essai\w*|test\w*|simulation\w*|prototype\w*|iteration\w*|variante\w*|"
        r"configuration\w*|integration\w*|implementation\w*|deploiement\w*|"
        r"developpement\w*|generation\w*|entrainement\w*|traitement\w*)\b",
        re.I,
    ),
    "uncertainty": re.compile(
        r"\b(?:incert\w*|inconnu\w*|imprevisible\w*|non maitr\w*|verrou\w*|"
        r"limite\w*|echec\w*|impossible (?:a|de) (?:predire|garantir|determiner)|"
        r"reste a comprendre|comportement indetermine|resultat non garanti|"
        r"mal maitrise\w*|question ouverte|knowledge gap)\b",
        re.I,
    ),
    "hypothesis": re.compile(
        r"\b(?:hypothese\w*|nous suppos\w*|postulat\w*|afin de verifier|pour verifier|"
        r"chercher a determiner|question de recherche|scenario teste|"
        r"pourrait influencer|influence-t-il|impact de .* sur)\b",
        re.I,
    ),
    "evaluation": re.compile(
        r"\b(?:essai\w*|test\w*|experiment\w*|mesur\w*|compar\w*|benchmark\w*|"
        r"plan d.experience|prototype\w*|simulation\w*|validation\w*|critere\w*|"
        r"indicateur\w*|metrique\w*|temoin\w*|conditions? controlees?)\b",
        re.I,
    ),
    "learning": re.compile(
        r"\b(?:a montre|ont montre|a revele|ont revele|a permis de|nous avons observe|"
        r"resultat\w*|enseignement\w*|conclusion\w*|rejete\w*|retenu\w*|"
        r"abandonne\w*|en consequence|a conduit a|suite aux essais|ecart observe|"
        r"gain de|difference de|contre \d|impact mesurable|apprentissage\w*)\b",
        re.I,
    ),
    "rationale": re.compile(
        r"\b(?:parce que|afin de|dans le but de|pour evaluer|pour determiner|"
        r"compte tenu|en raison de|suite a|pour lever|pour comprendre|"
        r"pour caracteriser|pour quantifier|pour comparer)\b",
        re.I,
    ),
    "routine": re.compile(
        r"\b(?:simple (?:parametrage|integration|adaptation|configuration)|"
        r"configuration standard|procedure standard|mode operatoire|bonne pratique|"
        r"best practice|conformement a la documentation|installation\w*|migration\w*|"
        r"deploiement\w*|maintenance\w*|mise a jour|correction de bug|"
        r"recette fonctionnelle|developpement (?:crud|standard)|"
        r"solution (?:standard|connue) directement applicable)\b",
        re.I,
    ),
    "routine_validation": re.compile(
        r"\b(?:verifier le bon fonctionnement|validation unitaire|"
        r"conforme aux specifications|conformite aux specifications|valeur attendue|"
        r"retrouver .* theorique|fonctionnement nominal|respect du format|"
        r"correctement genere\w*|correctement localise\w*|a l.endroit attendu|"
        r"controle de conformite|verification de conformite)\b",
        re.I,
    ),
    # Une validation ou une mesure n'est pas, à elle seule, une activité de
    # recherche. Ces marqueurs couvrent les contrôles de performance et de
    # conformité qui doivent être rattachés à un protocole R&D explicite avant
    # de pouvoir dépasser le statut de support.
    "generic_validation": re.compile(
        r"\b(?:valid(?:ation|er|e|ee|es)|verifi(?:cation|er|e|ee|es)|"
        r"evaluation(?: de| des)? performance|mesure(?:r|s)? (?:la |les )?performance|"
        r"precision|accuracy|matrice de confusion|jeu de test|test set|"
        r"controle(?:r)?|conformite|recette|qualification fonctionnelle)\b",
        re.I,
    ),
    # Signaux minimaux d'un protocole qui cherche réellement à produire une
    # connaissance sur une incertitude : comparaison discriminante, variation
    # contrôlée, hypothèse testée, ablation ou exploration de scénarios.
    "research_protocol": re.compile(
        r"\b(?:plan d.experience|conditions? controlees?|groupe temoin|baseline|"
        r"ablation|faire varier|variation de|sensibilite a|influence de|effet de|"
        r"impact de|comparaison de (?:plusieurs|differentes?)|compar(?:er|aison) "
        r"(?:des|les|plusieurs|differentes?) (?:approches|methodes|modeles|variantes|scenarios)|"
        r"hypothese testee|scenario teste|iterations? experimentales?|"
        r"parametres? explores?|protocole experimental)\b",
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

def _is_reference_like_passage(passage: Mapping[str, Any]) -> bool:
    if _gate_external_or_reference(passage) or _gate_noise_or_interview(passage):
        return True
    if bool(passage.get("reference_like") or passage.get("is_state_of_art") or passage.get("is_external_literature")):
        return True
    conflicts = " ".join(str(v) for v in (passage.get("semantic_role_conflicts") or []))
    if "etat_art" in _norm(conflicts) or "state_of_art" in _norm(conflicts):
        return True
    section = _norm(passage.get("section_title"))
    body = _norm(_context_text(passage))
    return bool(_REFERENCE_SECTION_RE.search(section) or _REFERENCE_BODY_RE.search(body))


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


def _primary_text(passage: Mapping[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in (
            passage.get("text"),
            passage.get("analysis_text"),
            passage.get("section_title"),
        )
    ).strip()


def _context_text(passage: Mapping[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in (
            passage.get("context_before"),
            _primary_text(passage),
            passage.get("context_after"),
        )
    ).strip()


def _evidence_id(passage: Mapping[str, Any], index: int) -> str:
    return str(passage.get("passage_id") or passage.get("id") or f"E{index}")


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _record(passage: Mapping[str, Any], index: int) -> Dict[str, Any]:
    primary_raw = _primary_text(passage)
    context_raw = _context_text(passage)
    position_value = passage.get("sentence_start")
    if position_value in (None, ""):
        position_value = passage.get("paragraph_index")
    has_explicit_position = position_value not in (None, "")
    position = _safe_int(position_value, index)
    return {
        "passage": passage,
        "evidence_id": _evidence_id(passage, index),
        "original_index": index,
        "document": str(passage.get("document") or passage.get("source_path") or ""),
        "document_key": _norm(passage.get("document") or passage.get("source_path") or ""),
        "section_title": str(passage.get("section_title") or ""),
        "section_key": _norm(passage.get("section_title") or ""),
        "position": position,
        "has_explicit_position": has_explicit_position,
        "primary_raw": primary_raw,
        "primary": _norm(primary_raw),
        "context": _norm(context_raw),
        "roles": _roles(passage),
    }


def _ordered_records(passages: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    records = [_record(passage, index) for index, passage in enumerate(passages, start=1)]
    records.sort(key=lambda item: (item["document_key"], item["position"], item["original_index"]))
    for sequence_index, item in enumerate(records, start=1):
        item["sequence_index"] = sequence_index
    return records


def _has_uncertainty(record: Mapping[str, Any]) -> bool:
    passage = record.get("passage") if isinstance(record.get("passage"), Mapping) else {}
    features = passage.get("lock_candidate_features") if isinstance(passage, Mapping) else {}
    feature_signal = bool(
        isinstance(features, Mapping)
        and any(
            features.get(name)
            for name in ("uncertainty", "causal_gap", "knowledge_gap", "open_validation")
        )
    )
    return bool(
        PATTERNS["uncertainty"].search(str(record.get("context") or ""))
        or set(record.get("roles") or set()) & UNCERTAINTY_ROLES
        or (isinstance(passage, Mapping) and passage.get("direct_lock_candidate"))
        or feature_signal
    )


def _is_activity(record: Mapping[str, Any]) -> bool:
    roles = set(record.get("roles") or set())
    explicit_method = bool(roles & METHOD_ROLES)
    result_or_uncertainty_only = bool(roles & (RESULT_ROLES | UNCERTAINTY_ROLES)) and not explicit_method
    primary = str(record.get("primary") or "")
    return bool(
        explicit_method
        or has_explicit_executed_method(record.get("passage") or {})
        or (
            not result_or_uncertainty_only
            and (PATTERNS["method"].search(primary) or PATTERNS["final_solution"].search(primary))
        )
    )


def _is_direct_rnd_protocol(record: Mapping[str, Any]) -> bool:
    """Réserve ``direct_rnd`` aux expériences qui interrogent une incertitude.

    Un test, une validation ou une métrique isolée reste du support. Pour être
    R&D directe, l'activité doit être expérimentale *et* expliciter soit la
    question/hypothèse étudiée, soit un dispositif comparatif ou contrôlé.
    """
    context = str(record.get("context") or "")
    primary = str(record.get("primary") or "")
    if not PATTERNS["evaluation"].search(context):
        return False
    if PATTERNS["routine"].search(primary) or PATTERNS["routine_validation"].search(primary):
        return False
    explicit_question_in_activity = bool(
        PATTERNS["uncertainty"].search(primary)
        or PATTERNS["hypothesis"].search(primary)
    )
    discriminating_protocol = bool(PATTERNS["research_protocol"].search(context))
    discriminating_protocol_in_activity = bool(PATTERNS["research_protocol"].search(primary))
    if PATTERNS["generic_validation"].search(primary) and not discriminating_protocol_in_activity:
        return False
    return explicit_question_in_activity or discriminating_protocol


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


def _evidence_ids(records: List[Mapping[str, Any]]) -> List[str]:
    return list(dict.fromkeys(str(record.get("evidence_id")) for record in records if record.get("evidence_id")))


def _linked_to_chain(
    activity: Mapping[str, Any],
    chain_records: List[Mapping[str, Any]],
    operation_status: str,
) -> bool:
    if operation_status not in {"rnd_core_defendable", "rnd_core_partial"}:
        return False
    context = str(activity.get("context") or "")
    if (
        PATTERNS["uncertainty"].search(context)
        or PATTERNS["hypothesis"].search(context)
        or PATTERNS["rationale"].search(context)
    ):
        return True

    same_document = [
        record
        for record in chain_records
        if record.get("document_key") == activity.get("document_key")
    ]
    if same_document:
        if activity.get("section_key") and any(
            record.get("section_key") == activity.get("section_key") for record in same_document
        ):
            return True
        if activity.get("has_explicit_position"):
            for record in same_document:
                if record.get("has_explicit_position") and abs(
                    int(record.get("position") or 0) - int(activity.get("position") or 0)
                ) <= 12:
                    return True
        if not activity.get("has_explicit_position"):
            return True

    # Les groupes NLP sont déjà consolidés autour d'un même verrou. En absence
    # de coordonnées documentaires, cette appartenance est un lien prudent,
    # signalé comme support et non comme R&D autonome.
    return bool(chain_records and not activity.get("document_key"))


def _empty_report() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "analysis_unit": "consolidated_lock_operation_not_passage",
        "direct_rnd_rule": "explicit_uncertainty_or_hypothesis_driven_discriminating_protocol_not_generic_validation",
        "operation_status": "insufficient_evidence",
        "operation_count": 0,
        "label": "insufficient_documentation",
        "readability_score": 0.0,
        "readability_score_semantics": "causal_chain_documentation_completeness_not_eligibility_probability",
        "documentary_confidence": "low",
        "activities_count": 0,
        "direct_rnd_activities_count": 0,
        "necessary_rnd_support_activities_count": 0,
        "classical_engineering_activities_count": 0,
        "insufficient_evidence_activities_count": 0,
        "routine_validation_candidates_count": 0,
        "method_steps_count": 0,
        "research_justified_steps_count": 0,
        "routine_engineering_steps_count": 0,
        "unexplained_steps_count": 0,
        "redundant_steps_count": 0,
        "causal_chain": {
            "uncertainty_evidence_ids": [],
            "hypothesis_or_rationale_evidence_ids": [],
            "experiment_evidence_ids": [],
            "result_or_learning_evidence_ids": [],
            "ordered_evidence_ids": [],
            "complete": False,
        },
        "direct_final_solution_assessment": "not_assessable",
        "direct_final_solution_risk": False,
        "eligibility_impact": "causal_chain_completeness_calibrates_rnd_defensibility_score",
        "risk_adjustment": "raise_to_medium_or_human_review",
        "llm_review_recommended": False,
        "llm_review_reasons": [],
        "llm_policy": LLM_POLICY,
        "activities": [],
        "steps": [],
        "questions_to_ask": [
            "Quelle chaîne relie l'incertitude, l'hypothèse, l'expérience et l'apprentissage de cette opération ?"
        ],
        "human_validation_required": True,
    }


def assess_group_demarche_legibility(group: Mapping[str, Any]) -> Dict[str, Any]:
    passages = [
        passage
        for passage in (group.get("supporting_passages") or [])
        if isinstance(passage, Mapping) and not _is_reference_like_passage(passage)
    ]
    if not passages:
        return _empty_report()

    records = _ordered_records(passages)
    uncertainty_records = [record for record in records if _has_uncertainty(record)]
    hypothesis_records = [
        record
        for record in records
        if PATTERNS["hypothesis"].search(str(record.get("context") or ""))
        or PATTERNS["rationale"].search(str(record.get("context") or ""))
    ]
    experiment_records = [
        record
        for record in records
        if PATTERNS["evaluation"].search(str(record.get("context") or ""))
        and gate_project_fact(
            record.get("passage") if isinstance(record.get("passage"), Mapping) else {},
            "demarche_detectee",
        ).allowed
    ]
    result_records = [
        record
        for record in records
        if (
            PATTERNS["learning"].search(str(record.get("context") or ""))
            or set(record.get("roles") or set()) & RESULT_ROLES
        )
        and gate_project_fact(
            record.get("passage") if isinstance(record.get("passage"), Mapping) else {},
            "resultats_metriques",
        ).allowed
    ]
    activity_records = [
        record
        for record in records
        if _is_activity(record)
        and gate_project_fact(
            record.get("passage") if isinstance(record.get("passage"), Mapping) else {},
            "demarche_detectee",
        ).allowed
    ]
    routine_activity_records = [
        record
        for record in activity_records
        if PATTERNS["routine"].search(str(record.get("primary") or ""))
        or PATTERNS["routine_validation"].search(str(record.get("primary") or ""))
        or (
            PATTERNS["generic_validation"].search(str(record.get("primary") or ""))
            and not _is_direct_rnd_protocol(record)
        )
    ]
    nonroutine_experiment_records = [
        record
        for record in activity_records
        if _is_direct_rnd_protocol(record)
    ]

    has_uncertainty = bool(uncertainty_records)
    has_investigation_reason = bool(hypothesis_records)
    has_experiment = bool(experiment_records)
    has_result = bool(result_records)
    complete_chain = all((has_uncertainty, has_investigation_reason, has_experiment, has_result))

    if complete_chain and (nonroutine_experiment_records or hypothesis_records):
        operation_status = "rnd_core_defendable"
    elif (
        routine_activity_records
        and not nonroutine_experiment_records
        and not has_investigation_reason
    ):
        operation_status = "classical_engineering"
    elif has_uncertainty and has_experiment and (has_investigation_reason or has_result):
        operation_status = "rnd_core_partial"
    elif routine_activity_records and not has_uncertainty:
        operation_status = "classical_engineering"
    else:
        operation_status = "insufficient_evidence"

    chain_by_id = {
        id(record): record
        for record in uncertainty_records + hypothesis_records + experiment_records + result_records
    }
    ordered_chain_records = sorted(
        chain_by_id.values(),
        key=lambda record: int(record.get("sequence_index") or 0),
    )

    activities: List[Dict[str, Any]] = []
    previous_texts: List[str] = []
    for activity_number, record in enumerate(activity_records, start=1):
        primary = str(record.get("primary") or "")
        context = str(record.get("context") or "")
        routine_candidate = bool(
            PATTERNS["routine"].search(primary)
            or PATTERNS["routine_validation"].search(primary)
            or (
                PATTERNS["generic_validation"].search(primary)
                and not _is_direct_rnd_protocol(record)
            )
        )
        evaluation = bool(PATTERNS["evaluation"].search(context))
        direct_rnd_protocol = _is_direct_rnd_protocol(record)
        linked = _linked_to_chain(record, ordered_chain_records, operation_status)
        redundant = any(_near_duplicate(primary, previous) for previous in previous_texts)
        previous_texts.append(primary)

        if operation_status in {"rnd_core_defendable", "rnd_core_partial"}:
            if direct_rnd_protocol and linked:
                activity_status = "direct_rnd"
                necessity = "explicit_uncertainty_driven_research_protocol"
            elif linked:
                activity_status = "necessary_rnd_support"
                necessity = "linked_to_rnd_operation"
            elif routine_candidate:
                activity_status = "classical_engineering"
                necessity = "not_linked_to_rnd_operation"
            else:
                activity_status = "insufficient_evidence"
                necessity = "link_not_demonstrated"
        elif routine_candidate:
            activity_status = "classical_engineering"
            necessity = "standard_or_known_validation"
        else:
            activity_status = "insufficient_evidence"
            necessity = "link_not_demonstrated"

        signals = {
            "evaluation_protocol": evaluation,
            "direct_rnd_protocol": direct_rnd_protocol,
            "research_protocol_signal": bool(PATTERNS["research_protocol"].search(context)),
            "routine_validation_candidate": routine_candidate,
            "linked_to_operation_chain": linked,
            "final_solution": bool(PATTERNS["final_solution"].search(primary)),
            "near_duplicate": redundant,
        }
        activities.append({
            "activity_number": activity_number,
            "step_number": activity_number,
            "evidence_id": record.get("evidence_id"),
            "document": record.get("document"),
            "section_title": record.get("section_title"),
            "sentence_start": record.get("position") if record.get("has_explicit_position") else None,
            "text_excerpt": " ".join(str(record.get("primary_raw") or "").split())[:500],
            "activity_status": activity_status,
            "activity_status_alias": (
                "rnd_support_activity"
                if activity_status == "necessary_rnd_support"
                else "needs_human_review"
                if activity_status == "insufficient_evidence"
                else activity_status
            ),
            "classification": activity_status,
            "necessity_status": necessity,
            "needs_human_review": activity_status == "insufficient_evidence",
            "signals": signals,
        })

    activity_counts = {
        status: sum(activity.get("activity_status") == status for activity in activities)
        for status in (
            "direct_rnd",
            "necessary_rnd_support",
            "classical_engineering",
            "insufficient_evidence",
        )
    }
    redundant_count = sum(bool(activity.get("signals", {}).get("near_duplicate")) for activity in activities)
    final_activities = [activity for activity in activities if activity.get("signals", {}).get("final_solution")]
    direct_final_risk = bool(final_activities and operation_status != "rnd_core_defendable")
    if direct_final_risk:
        final_assessment = "possible_shortcut_not_excluded"
    elif final_activities and complete_chain:
        final_assessment = "final_solution_supported_by_documented_causal_chain"
    elif final_activities:
        final_assessment = "final_solution_present_without_complete_trajectory"
    else:
        final_assessment = "not_assessable"

    chain_components = sum((has_uncertainty, has_investigation_reason, has_experiment, has_result))
    readability_score = round(chain_components / 4.0, 4)
    if operation_status == "rnd_core_defendable":
        label = "clear_research_trajectory"
        impact = "supports_rnd_core"
        risk_adjustment = "none_from_approach"
        confidence = "high"
    elif operation_status == "rnd_core_partial":
        label = "mixed_or_partially_justified_trajectory"
        impact = "causal_chain_completeness_calibrates_rnd_defensibility_score"
        risk_adjustment = "raise_to_medium"
        confidence = "medium"
    elif operation_status == "classical_engineering":
        label = "routine_engineering_dominant"
        impact = "blocks_this_operation_without_defendable_rnd_core"
        risk_adjustment = "high_and_non_eligible_operation"
        confidence = "high" if routine_activity_records else "medium"
    else:
        label = "insufficient_documentation"
        impact = "insufficient_evidence_blocks_rnd_defensibility_score"
        risk_adjustment = "raise_to_medium_or_human_review"
        confidence = "low"

    questions: List[str] = []
    if operation_status in {"rnd_core_partial", "insufficient_evidence"}:
        missing = []
        if not has_uncertainty:
            missing.append("l'incertitude initiale")
        if not has_investigation_reason:
            missing.append("l'hypothèse ou la raison de l'investigation")
        if not has_experiment:
            missing.append("l'expérience ou la comparaison")
        if not has_result:
            missing.append("le résultat ou l'apprentissage")
        questions.append("Documenter explicitement " + ", ".join(missing) + " pour cette opération.")
    if activity_counts["classical_engineering"]:
        questions.append(
            "Les validations ou procédures connues sont-elles indispensables au protocole R&D, ou extérieures à son périmètre ?"
        )
    if activity_counts["insufficient_evidence"]:
        questions.append(
            "À quelle opération R&D chaque activité non reliée contribue-t-elle, et pourquoi est-elle nécessaire ?"
        )
    if direct_final_risk:
        questions.append(
            "Pourquoi la solution finalement retenue ne pouvait-elle pas être choisie dès le départ à partir des connaissances accessibles ?"
        )

    llm_reasons: List[str] = []
    if operation_status in {"rnd_core_partial", "insufficient_evidence"}:
        llm_reasons.append("operation_causal_chain_incomplete_or_ambiguous")
    if direct_final_risk:
        llm_reasons.append("possible_direct_final_solution_shortcut")
    if activity_counts["insufficient_evidence"]:
        llm_reasons.append("activities_not_linked_to_an_rnd_operation")

    causal_chain = {
        "uncertainty_evidence_ids": _evidence_ids(uncertainty_records),
        "hypothesis_or_rationale_evidence_ids": _evidence_ids(hypothesis_records),
        "experiment_evidence_ids": _evidence_ids(experiment_records),
        "result_or_learning_evidence_ids": _evidence_ids(result_records),
        "ordered_evidence_ids": _evidence_ids(ordered_chain_records),
        "complete": complete_chain,
    }
    operation_count = 1
    research_operation = 1 if operation_status == "rnd_core_defendable" else 0
    classical_operation = 1 if operation_status == "classical_engineering" else 0
    unexplained_operation = 1 if operation_status == "insufficient_evidence" else 0

    return {
        "version": VERSION,
        "analysis_unit": "consolidated_lock_operation_not_passage",
        "direct_rnd_rule": "explicit_uncertainty_or_hypothesis_driven_discriminating_protocol_not_generic_validation",
        "operation_id": group.get("lock_group_id") or group.get("passage_id"),
        "operation_status": operation_status,
        "operation_count": operation_count,
        "label": label,
        "readability_score": readability_score,
        "readability_score_semantics": "causal_chain_documentation_completeness_not_eligibility_probability",
        "documentary_confidence": confidence,
        "activities_count": len(activities),
        "direct_rnd_activities_count": activity_counts["direct_rnd"],
        "necessary_rnd_support_activities_count": activity_counts["necessary_rnd_support"],
        "rnd_support_activities_count": activity_counts["necessary_rnd_support"],
        "classical_engineering_activities_count": activity_counts["classical_engineering"],
        "insufficient_evidence_activities_count": activity_counts["insufficient_evidence"],
        "routine_validation_candidates_count": len(routine_activity_records),
        # Compatibilité : ces champs comptent désormais des opérations, pas des passages.
        "method_steps_count": operation_count,
        "research_justified_steps_count": research_operation,
        "routine_engineering_steps_count": classical_operation,
        "unexplained_steps_count": unexplained_operation,
        "redundant_steps_count": redundant_count,
        "causal_chain": causal_chain,
        "direct_final_solution_assessment": final_assessment,
        "direct_final_solution_risk": direct_final_risk,
        "eligibility_impact": impact,
        "risk_adjustment": risk_adjustment,
        "llm_review_recommended": bool(llm_reasons),
        "llm_review_reasons": llm_reasons,
        "llm_policy": LLM_POLICY,
        "activities": activities,
        "steps": activities,
        "questions_to_ask": questions,
        "human_validation_required": True,
    }


def assess_project_demarche_legibility(groups: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    reports = [assess_group_demarche_legibility(group) for group in groups]
    if not reports:
        report = _empty_report()
        report.update({
            "project_status": "insufficient_evidence",
            "operations_count": 0,
            "operation_reports": [],
            "group_reports": [],
            "all_operations_classical_engineering": False,
        })
        return report

    operations = [report for report in reports if int(report.get("operation_count") or 0) > 0]
    status_counts = {
        status: sum(report.get("operation_status") == status for report in operations)
        for status in (
            "rnd_core_defendable",
            "rnd_core_partial",
            "classical_engineering",
            "insufficient_evidence",
        )
    }
    operations_count = len(operations)
    all_classical = bool(
        operations_count
        and status_counts["classical_engineering"] == operations_count
    )
    if status_counts["rnd_core_defendable"]:
        project_status = "rnd_core_defendable"
    elif status_counts["rnd_core_partial"]:
        project_status = "rnd_core_partial"
    elif all_classical:
        project_status = "classical_engineering"
    else:
        project_status = "insufficient_evidence"

    activities_count = sum(int(report.get("activities_count") or 0) for report in reports)
    direct_rnd = sum(int(report.get("direct_rnd_activities_count") or 0) for report in reports)
    support = sum(int(report.get("necessary_rnd_support_activities_count") or 0) for report in reports)
    classical_activities = sum(int(report.get("classical_engineering_activities_count") or 0) for report in reports)
    insufficient_activities = sum(int(report.get("insufficient_evidence_activities_count") or 0) for report in reports)
    redundant = sum(int(report.get("redundant_steps_count") or 0) for report in reports)
    shortcut_groups = sum(bool(report.get("direct_final_solution_risk")) for report in reports)

    if operations:
        readability_score = round(
            sum(float(report.get("readability_score") or 0.0) for report in operations)
            / len(operations),
            4,
        )
    else:
        readability_score = 0.0

    mixed_perimeter = bool(
        status_counts["rnd_core_defendable"]
        and (
            status_counts["rnd_core_partial"]
            or status_counts["classical_engineering"]
            or status_counts["insufficient_evidence"]
            or classical_activities
            or insufficient_activities
        )
    )
    if project_status == "rnd_core_defendable":
        label = "mixed_or_partially_justified_trajectory" if mixed_perimeter else "clear_research_trajectory"
        impact = "supports_rnd_core_with_perimeter_review" if mixed_perimeter else "supports_rnd_core"
        risk_adjustment = "raise_to_medium_for_perimeter" if mixed_perimeter else "none_from_approach"
    elif project_status == "rnd_core_partial":
        label = "mixed_or_partially_justified_trajectory"
        impact = "causal_chain_completeness_calibrates_rnd_defensibility_score"
        risk_adjustment = "raise_to_medium"
    elif project_status == "classical_engineering":
        label = "routine_engineering_dominant"
        impact = "blocks_project_when_no_defendable_rnd_operation_exists"
        risk_adjustment = "high_and_non_eligible"
    else:
        label = "insufficient_documentation"
        impact = "insufficient_evidence_blocks_rnd_defensibility_score"
        risk_adjustment = "raise_to_medium_or_human_review"

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
        "analysis_unit": "consolidated_lock_operation_not_passage",
        "direct_rnd_rule": "explicit_uncertainty_or_hypothesis_driven_discriminating_protocol_not_generic_validation",
        "project_status": project_status,
        "operation_status": project_status,
        "operations_count": operations_count,
        "operation_count": operations_count,
        "rnd_core_defendable_operations_count": status_counts["rnd_core_defendable"],
        "rnd_core_partial_operations_count": status_counts["rnd_core_partial"],
        "classical_engineering_operations_count": status_counts["classical_engineering"],
        "insufficient_evidence_operations_count": status_counts["insufficient_evidence"],
        "all_operations_classical_engineering": all_classical,
        "label": label,
        "readability_score": readability_score,
        "readability_score_semantics": "average_causal_chain_documentation_completeness_not_eligibility_probability",
        "documentary_confidence": (
            "high" if project_status in {"rnd_core_defendable", "classical_engineering"} and not mixed_perimeter
            else "medium" if project_status in {"rnd_core_defendable", "rnd_core_partial"}
            else "low"
        ),
        "activities_count": activities_count,
        "direct_rnd_activities_count": direct_rnd,
        "necessary_rnd_support_activities_count": support,
        "rnd_support_activities_count": support,
        "classical_engineering_activities_count": classical_activities,
        "insufficient_evidence_activities_count": insufficient_activities,
        # Compatibilité : ces champs comptent des opérations, pas les passages internes.
        "method_steps_count": operations_count,
        "research_justified_steps_count": status_counts["rnd_core_defendable"],
        "routine_engineering_steps_count": status_counts["classical_engineering"],
        "unexplained_steps_count": status_counts["insufficient_evidence"],
        "redundant_steps_count": redundant,
        "groups_with_possible_direct_final_solution_shortcut": shortcut_groups,
        "direct_final_solution_risk": shortcut_groups > 0,
        "eligibility_impact": impact,
        "risk_adjustment": risk_adjustment,
        "llm_review_recommended": bool(llm_reasons),
        "llm_review_reasons": llm_reasons,
        "llm_policy": LLM_POLICY,
        "questions_to_ask": questions,
        "operation_reports": reports,
        "group_reports": reports,
        "human_validation_required": True,
    }
