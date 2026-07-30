# -*- coding: utf-8 -*-
"""Filtrage qualité V178 : rôle sémantique et signal de verrou inter-domaines.

La promotion en candidat verrou reste prudente, mais elle ne dépend plus
uniquement du mot « incertitude ». Les limites de mesure, les dépendances à des
conditions, les causalités non établies et les compromis techniques sont des
signaux génériques qui doivent survivre jusqu'au regroupement inter-document.
Le filtre ne contient aucun secteur industriel, nom de projet, équipement ou
unité métier pour décider qu'un passage est un verrou.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Tuple

from .cleaner import is_noise_line
from .evidence_contract import NON_LOCK_SEMANTIC_ROLES


STRICT = {
    "objectif": 0.72,
    "methode": 0.70,
    "parametre": 0.70,
    "resultat": 0.70,
    "limite": 0.70,
    "contribution": 0.70,
    "etat_art": 0.70,
}
RECALL = {role: 0.56 for role in STRICT}

LOCK_CANDIDATE_RECALL = 0.46
LOCK_CANDIDATE_STRONG = 0.72
LOCK_ROLE_RECALL = 0.54

STRICT_VERROU_DETECTOR = LOCK_CANDIDATE_STRONG
RECALL_VERROU_DETECTOR = LOCK_CANDIDATE_RECALL
VERROU_SCORE_BOOST_THRESHOLD = LOCK_CANDIDATE_STRONG

BAD_SYNTH = (
    "tapez ici",
    "nom de la présentation",
    "document security",
    "charte graphique",
    "quelles sont les questions",
    "quels sont les enjeux",
    "quels environnements",
    "quelles démarches",
)
CONTEXT_ONLY_TYPES = {
    "norme_reglementation",
    "plan_schema",
    "administratif",
    "template_formulaire",
    "publication_scientifique",
    "etat_art_bibliographie",
}
CONTEXT_ONLY_ORIGINS = {"metadata", "state_of_art", "external_context"}

UNCERTAINTY_PATTERNS = (
    r"\bincertitud",
    r"\bverrou",
    r"\bdifficult[ée].{0,45}(?:pr[ée]dire|ma[iî]triser|d[ée]terminer|garantir|anticiper|transposer)",
    r"\bimpossib",
    r"\binconnu|\bunknown\b",
    r"\bvariabilit[ée]|\bvariability\b",
    r"\bnon[ -](?:ma[iî]tris|r[ée]solu|d[ée]termin|valid|garanti)",
    r"\b(?:non reproductible|non conforme|incompatible|instable|divergent)\b",
    r"\breste(?:nt)? [àa] (?:d[ée]terminer|d[ée]montrer|valider|comprendre|r[ée]soudre|confirmer)",
    r"\bne (?:permet|permettent|peut|peuvent|garantit|garantissent)(?: pas)?.{0,45}(?:predire|determiner|garantir|maitriser|anticiper|expliquer)\b",
    r"\b(?:cannot|can not|unable to|fails? to)\b",
    r"\binsuffisan.{0,45}(?:connaissance|mod[èe]le|m[ée]thode|pr[ée]diction|compr[ée]hension)",
    r"\bsujet(?:te)? [àa] caution",
    r"\bsans garantie|\bnot guaranteed\b",
    r"\bknowledge gap(?:s)?\b",
    r"\brepr[ée]sentativit[ée]\b|\brepresentativeness\b",
)

MEASUREMENT_LIMIT_PATTERNS = (
    r"\bmesur(?:e|es|er).{0,45}(?:fauss|biais|limit|liss|imprecis|incertain|pertinen|fiabl)",
    r"\bcapteur.{0,45}(?:pollu|fauss|satur|limit|biais)",
    r"\bmoyen(?:s)? de mesure.{0,50}(?:limit|insuffisant|moyennement|peu)",
    r"\b(?:precision|resolution|sensibilite).{0,40}(?:insuffisant|limit|incertain|non garanti)",
)

CAUSAL_GAP_PATTERNS = (
    r"\b(?:cause|origine|mecanisme|facteur).{0,50}(?:inconnu|non determine|a determiner|incertain|semble|pourrait)",
    r"\bne semble pas.{0,45}(?:expliquer|etre en relation|suffire)",
    r"\b(?:a rechercher|reste a rechercher|doit etre recherche).{0,60}(?:cote|niveau|facteur|condition)",
    r"\bplusieurs facteurs|\bfacteurs combines|\binteraction(?:s)? entre",
)

DEPENDENCY_PATTERNS = (
    r"\bdepend(?:re|ant|ance)? de\b",
    r"\binfluenc[ée].{0,35}(?:par|selon|en fonction)",
    r"\ben fonction de\b",
    r"\bselon (?:la|le|les|l['’])?(?:configuration|condition|contexte|version|scenario|environnement|parametrage|impl[ée]mentation)\b",
    r"\bconditions? (?:d essai|de fonctionnement|d utilisation).{0,50}(?:different|variable|non identique)",
    r"\bnon (?:comparable|transposable|generalisable)|\bdifficilement transposable",
)

TRADEOFF_PATTERNS = (
    r"\bcompromis\b",
    r"\bsans degrader\b",
    r"\btout en (?:respectant|conservant|maintenant|evitant)",
    r"\b(?:optimiser|ameliorer).{0,45}(?:sans|sous contrainte|tout en)",
    r"\bcontraintes? (?:contradictoires|concurrentes|simultanees)",
)

OPEN_VALIDATION_PATTERNS = (
    r"\b(?:a valider|a confirmer|a verifier|a caracteriser|a quantifier|a evaluer)\b",
    r"\b(?:essais?|simulation|mesures?) complementaires?\b",
    r"\bnecessite une etude\b|\bdoit faire l objet d",
    r"\bnon realisable\b|\bpas realisable\b",
)

KNOWLEDGE_GAP_PATTERNS = (
    r"\b(?:litterature|etat de l art|travaux existants|solutions existantes).{0,90}(?:ne couvre|ne permettent|insuffisant|non transposable|non transferable)",
    r"\b(?:non transposable|non transferable|difficilement transposable|pas directement applicable)\b",
    r"\b(?:aucun|pas de) modele.{0,60}(?:predire|decrire|representer|anticiper)\b",
    r"\b(?:ne peut|ne peuvent) etre (?:predit|predits|predite|predites|determine|determines)\b",
    r"\b(?:comportement|phenomene|mecanisme).{0,60}(?:mal compris|non maitrise|non determine|inconnu)\b",
)

ROUTINE_RESOLUTION_PATTERNS = (
    r"\b(?:cause|origine) (?:est|a ete) (?:identifiee|connue|determinee)\b",
    r"\b(?:probleme|defaut) (?:vient|provient) (?:de|du|de la)\b",
    r"\b(?:est|sont) (?:du|dus|due|dues) a\b",
    r"\bil (?:faut|suffit de) (?:changer|remplacer|resserrer|nettoyer|regler|reparer)\b",
    r"\b(?:maintenance|remplacement|reglage) (?:standard|courant|habituel)\b",
)

# Ces motifs décrivent une forme de contenu technique, pas un métier. Ils ne
# contiennent ni vocabulaire compresseur, ni vocabulaire thermique, ni unités
# choisies pour un projet. Le score du modèle et un problème non résolu restent
# obligatoires : un mot isolé ne peut pas créer un verrou.
TECHNICAL_PATTERNS = (
    r"\b(modele|algorithme|logiciel|simulateur|systeme|procede|processus|architecture|prototype|dispositif|plateforme|implementation)\b",
    r"\b(mesure|donnee|parametre|calcul|signal|materiau|composant|interface|environnement|structure|protocole|jeu de donnees|dataset)\b",
    r"\b(precision|robustesse|fiabilite|convergence|representativite|variabilite|performances?|comportement|phenomene|propriete|compatibilite|stabilite|reproductibilite|integrite)\b",
    r"\b(condition|conditions|configuration|configurations|variable|variables|critere|criteres|dimension|dimensions|contrainte|parametrage|scenario)\b",
    r"(?<![a-z0-9])[-+]?\d+(?:[.,]\d+)?\s*(?:%|°\s*[cfk]|[a-zµ]{1,6}(?:/[a-z0-9µ]{1,6})?)\b",
    r"\b(?:non realisable|instable|non conforme|incompatible|degrad(?:e|ee|é|ée|ation)|defaillance|saturation|divergence|non reproductible)\b",
)


def _sf(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _role_scores(item: Dict[str, Any]) -> Dict[str, float]:
    raw = item.get("role_scores") or item.get("scores") or {}
    return {str(role): _sf(score) for role, score in raw.items()} if isinstance(raw, dict) else {}


def _best_non_lock_role(item: Dict[str, Any]) -> Tuple[str, float]:
    scores = _role_scores(item)
    available = [(role, scores.get(role, 0.0)) for role in NON_LOCK_SEMANTIC_ROLES]
    return max(available, key=lambda pair: pair[1], default=("limite", 0.0))


def _choose_semantic_role(item: Dict[str, Any]) -> Tuple[str, float, str]:
    model_role = str(item.get("original_model_role") or item.get("role") or "bruit").lower()
    hint = str(item.get("section_role_hint") or "unknown").lower()
    scores = _role_scores(item)

    if hint == "etat_art":
        return "etat_art", max(scores.get("etat_art", 0.0), 0.70), "state_of_art_section_hint"
    if hint in NON_LOCK_SEMANTIC_ROLES and model_role in {"verrou", "bruit", "unknown", ""}:
        return hint, max(scores.get(hint, 0.0), 0.56), "section_hint"
    if model_role in NON_LOCK_SEMANTIC_ROLES:
        return model_role, _sf(item.get("model_confidence") or scores.get(model_role)), "fastjudge"

    role, score = _best_non_lock_role(item)
    if score >= 0.20:
        return role, score, "best_non_lock_probability"
    return "limite", max(score, 0.45), "lock_fallback_to_limit"


def _is_context_only(item: Dict[str, Any]) -> bool:
    source_policy = str(item.get("source_policy") or "").lower()
    role = str(item.get("semantic_role") or item.get("role") or "").lower()
    hint = str(item.get("section_role_hint") or "").lower()
    return (
        item.get("document_type") in CONTEXT_ONLY_TYPES
        or item.get("content_origin") in CONTEXT_ONLY_ORIGINS
        or source_policy in {"context_only", "style_only", "secondary_context"}
        or role == "etat_art"
        or hint == "etat_art"
    )


def _analysis_text(item: Dict[str, Any]) -> str:
    # Le nom du fichier reste une métadonnée. L'utiliser ici créait des faux
    # signaux (par exemple un plan nommé « séparateur » devenait une preuve).
    text = " ".join(
        str(value or "")
        for value in (
            item.get("section_title"),
            item.get("context_before"),
            item.get("analysis_text"),
            item.get("text"),
            item.get("context_after"),
        )
        if value
    ).lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(char for char in text if not unicodedata.combining(char))


def _has_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def _signal_features(item: Dict[str, Any]) -> Dict[str, bool]:
    text = _analysis_text(item)
    return {
        "uncertainty": _has_any(text, UNCERTAINTY_PATTERNS),
        "measurement_limit": _has_any(text, MEASUREMENT_LIMIT_PATTERNS),
        "causal_gap": _has_any(text, CAUSAL_GAP_PATTERNS),
        "dependency": _has_any(text, DEPENDENCY_PATTERNS),
        "tradeoff": _has_any(text, TRADEOFF_PATTERNS),
        "open_validation": _has_any(text, OPEN_VALIDATION_PATTERNS),
        "knowledge_gap": _has_any(text, KNOWLEDGE_GAP_PATTERNS),
        "routine_resolution": _has_any(text, ROUTINE_RESOLUTION_PATTERNS),
        "technical": _has_any(text, TECHNICAL_PATTERNS),
    }


def safe_for_synthesis(item: Dict[str, Any]) -> bool:
    text = str(item.get("text") or "").strip()
    low = text.lower()
    if len(text) < 45 or any(marker in low for marker in BAD_SYNTH):
        return False
    if is_noise_line(text):
        return False
    if item.get("content_origin") == "metadata":
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ]{3,}", text)
    return len(words) >= 6


def rank_score(item: Dict[str, Any]) -> float:
    semantic_conf = _sf(item.get("semantic_role_confidence"))
    lock_score = _sf(item.get("lock_candidate_score") or item.get("verrou_score"))
    source_weight = _sf(item.get("source_weight") or item.get("document_weight") or 0.75)
    score = semantic_conf * max(0.25, source_weight) + 0.15 * lock_score
    if item.get("section_role_hint") == item.get("semantic_role"):
        score *= 1.06
    if _is_context_only(item):
        score *= 0.55
    return round(score, 4)


def _mark_lock_candidate(item: Dict[str, Any]) -> None:
    model_role = str(item.get("original_model_role") or item.get("role") or "").lower()
    hint = str(item.get("section_role_hint") or "").lower()
    model_confidence = _sf(item.get("model_confidence"))
    lock_score = _sf(item.get("lock_candidate_score") or item.get("verrou_score"))
    features = _signal_features(item)
    strong_problem_signal = any(
        features[key]
        for key in ("uncertainty", "measurement_limit", "causal_gap", "tradeoff", "open_validation", "knowledge_gap")
    )
    explicit = bool(
        features["technical"]
        and strong_problem_signal
        and (
            hint == "verrou"
            or (
                model_role == "verrou"
                and model_confidence >= max(LOCK_ROLE_RECALL, 0.68)
            )
        )
    )
    contextual_problem_signal = strong_problem_signal or features["dependency"]
    technical_problem = bool(features["technical"] and strong_problem_signal)
    routine_only = bool(
        features["routine_resolution"]
        and not features["tradeoff"]
        and not features["knowledge_gap"]
        and not features["open_validation"]
    )

    candidate = bool(
        explicit
        or (
            model_role != "bruit"
            and technical_problem
            and not routine_only
            and (
                (features["knowledge_gap"] and lock_score >= 0.40)
                or (features["tradeoff"] and lock_score >= 0.40)
                or (features["causal_gap"] and lock_score >= 0.46)
                or (features["uncertainty"] and lock_score >= LOCK_CANDIDATE_RECALL)
                or (features["open_validation"] and lock_score >= LOCK_CANDIDATE_RECALL)
                or (
                    features["measurement_limit"]
                    and features["open_validation"]
                    and lock_score >= 0.50
                )
            )
        )
        or (
            model_role == "bruit"
            and technical_problem
            and lock_score >= LOCK_CANDIDATE_STRONG
            and (features["knowledge_gap"] or features["tradeoff"])
        )
    )

    signal_count = sum(bool(value) for value in features.values())
    item["lock_candidate"] = bool(candidate)
    item["lock_candidate_score"] = lock_score
    item["lock_candidate_explicit"] = bool(explicit)
    item["lock_candidate_uncertainty_signal"] = bool(features["uncertainty"])
    item["lock_candidate_features"] = features
    item["lock_candidate_signal_count"] = signal_count
    item["lock_eligible"] = bool(candidate and not _is_context_only(item))

    if not candidate:
        status = "not_candidate"
    elif not item["lock_eligible"]:
        status = "context_only_candidate"
        item["non_verrou_reason"] = "source contextuelle : preuve de soutien uniquement"
    elif explicit or features["knowledge_gap"] or features["tradeoff"]:
        status = "strong_candidate_to_group"
    else:
        status = "candidate_to_group"
    item["lock_candidate_status"] = status


def apply_quality_filter(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    kept: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    lock_candidates: List[Dict[str, Any]] = []

    for raw in items or []:
        item = dict(raw)
        semantic_role, semantic_conf, semantic_source = _choose_semantic_role(item)
        item["semantic_role"] = semantic_role
        item["semantic_role_confidence"] = semantic_conf
        item["semantic_role_source"] = semantic_source
        item["role"] = semantic_role
        _mark_lock_candidate(item)

        if item["lock_candidate"]:
            lock_candidates.append(item)

        safe = safe_for_synthesis(item)
        strict = semantic_conf >= STRICT.get(semantic_role, 1.0)
        recall = semantic_conf >= RECALL.get(semantic_role, 1.0)

        if safe and (strict or recall or item["lock_candidate"]):
            status = "strict" if strict else "recall" if recall else "lock_candidate_preserved"
            item["quality_status"] = status
            item["accepted_for_semantic_section"] = True
            item["accepted_for_synthesis"] = True
            item["rank_score"] = rank_score(item)
            kept.append(item)
        else:
            item["quality_status"] = "rejected_noise" if not safe else "rejected_low_semantic_confidence"
            item["accepted_for_semantic_section"] = False
            item["accepted_for_synthesis"] = False
            item["rank_score"] = rank_score(item)
            rejected.append(item)

    return {
        "kept": kept,
        "rejected": rejected,
        "lock_candidates": lock_candidates,
        "all_items": [*kept, *rejected],
        "stats": {
            "input": len(items or []),
            "kept": len(kept),
            "rejected": len(rejected),
            "strict": sum(item.get("quality_status") == "strict" for item in kept),
            "recall_only": sum(item.get("quality_status") == "recall" for item in kept),
            "lock_candidates_total": len(lock_candidates),
            "lock_candidates_eligible": sum(bool(item.get("lock_eligible")) for item in lock_candidates),
            "lock_candidates_context_only": sum(not bool(item.get("lock_eligible")) for item in lock_candidates),
        },
    }


def thresholds() -> Dict[str, Any]:
    return {
        **{f"strict_{key}": value for key, value in STRICT.items()},
        **{f"recall_{key}": value for key, value in RECALL.items()},
        "lock_candidate_recall": LOCK_CANDIDATE_RECALL,
        "lock_candidate_strong": LOCK_CANDIDATE_STRONG,
        "lock_role_recall": LOCK_ROLE_RECALL,
        "rule": (
            "explicit OR uncertainty/measurement/causal/tradeoff/open-validation signal with calibrated "
            "lock score; high detector score alone remains insufficient"
        ),
    }
