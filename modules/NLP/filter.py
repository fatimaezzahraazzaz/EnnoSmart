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



def _normalize_signal_text(*values: Any) -> str:
    text = " ".join(
        str(value or "")
        for value in values
        if value
    ).lower()

    text = unicodedata.normalize(
        "NFKD",
        text,
    )

    text = "".join(
        char
        for char in text
        if not unicodedata.combining(char)
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def _source_analysis_text(
    item: Dict[str, Any],
) -> str:
    """
    Texte r?ellement pr?dit par FastJudge.

    Le contexte voisin ne doit pas transformer
    une m?tadonn?e en verrou.
    """

    return _normalize_signal_text(
        item.get("text")
    )


def _context_analysis_text(
    item: Dict[str, Any],
) -> str:
    """
    Contexte local utilis? uniquement comme preuve
    compl?mentaire.
    """

    return _normalize_signal_text(
        item.get("section_title"),
        item.get("context_before"),
        item.get("context_after"),
    )


def _analysis_text(
    item: Dict[str, Any],
) -> str:
    """
    Vue compl?te conserv?e pour les autres fonctions.
    """

    return _normalize_signal_text(
        item.get("section_title"),
        item.get("context_before"),
        item.get("text"),
        item.get("context_after"),
    )

def _has_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)



def _features_from_text(
    text: str,
) -> Dict[str, bool]:

    return {
        "uncertainty":
            _has_any(
                text,
                UNCERTAINTY_PATTERNS,
            ),

        "measurement_limit":
            _has_any(
                text,
                MEASUREMENT_LIMIT_PATTERNS,
            ),

        "causal_gap":
            _has_any(
                text,
                CAUSAL_GAP_PATTERNS,
            ),

        "dependency":
            _has_any(
                text,
                DEPENDENCY_PATTERNS,
            ),

        "tradeoff":
            _has_any(
                text,
                TRADEOFF_PATTERNS,
            ),

        "open_validation":
            _has_any(
                text,
                OPEN_VALIDATION_PATTERNS,
            ),

        "knowledge_gap":
            _has_any(
                text,
                KNOWLEDGE_GAP_PATTERNS,
            ),

        "routine_resolution":
            _has_any(
                text,
                ROUTINE_RESOLUTION_PATTERNS,
            ),

        "technical":
            _has_any(
                text,
                TECHNICAL_PATTERNS,
            ),
    }


def _generic_technical_expression(
    text: str,
) -> bool:
    """
    Compl?ment bilingue g?n?rique.

    Aucun vocabulaire projet ou m?tier sp?cifique.
    """

    return bool(
        re.search(
            r"\b(?:"
            r"model|models|modeling|modelling|"
            r"method|methods|approach|approaches|"
            r"algorithm|algorithms|"
            r"simulation|simulations|simulator|simulators|"
            r"system|systems|"
            r"process|processes|"
            r"measurement|measurements|"
            r"data|dataset|datasets|"
            r"parameter|parameters|"
            r"material|materials|"
            r"signal|signals|"
            r"performance|performances|accuracy|"
            r"robustness|validation|"
            r"phenomenon|phenomena|physical|"
            r"computational|training|generalization|"
            r"modele|modeles|methode|methodes|"
            r"algorithme|algorithmes|simulation|"
            r"simulateur|simulateurs|systeme|systemes|"
            r"mesure|mesures|donnee|donnees|"
            r"parametre|parametres|materiau|materiaux|"
            r"signal|signaux|performance|performances|"
            r"precision|robustesse|validation|"
            r"phenomene|phenomenes|calcul"
            r")\b",
            text,
            flags=re.I,
        )
    )


def _generic_problem_expression(
    text: str,
) -> bool:
    """
    Expression g?n?rique d'un probl?me scientifique/
    technique encore ouvert.

    Aucun secteur, outil ou projet n'est cod? ici.
    """

    patterns = (
        r"\buncertain(?:ty)?\b",
        r"\bunknown\b",
        r"\bunresolved\b",
        r"\bnot understood\b",
        r"\bnot known\b",
        r"\bnot validated\b",
        r"\bnot guaranteed\b",
        r"\bnot representative\b",
        r"\bnot applicable\b",

        r"\bcannot\b",
        r"\bcan not\b",
        r"\bunable to\b",
        r"\bfails? to\b",

        r"\blimitation(?:s)?\b",
        r"\bdrawback(?:s)?\b",

        r"\btrade[- ]?off\b",
        r"\bcompromise\b",

        r"\bgap between\b",
        r"\bperformance gap\b",
        r"\bgeneralization gap\b",
        r"\bgeneralisation gap\b",

        r"\bgeneralization (?:issue|issues|problem|problems)\b",
        r"\bgeneralisation (?:issue|issues|problem|problems)\b",

        r"\bsimplifying assumption(?:s)?\b",

        r"\bremains? (?:unknown|unclear|unresolved|limited)\b",

        r"\bremains? to (?:be )?"
        r"(?:determined|validated|verified|understood|confirmed)\b",

        r"\bopen question(?:s)?\b",

        r"\bdifficult(?:y)? to\b",

        r"\binsufficient\b",
        r"\black of\b",

        r"\bincertitud",
        r"\binconnu",
        r"\bnon resolu",
        r"\bnon maitrise",
        r"\bnon valide",
        r"\bnon garanti",
        r"\breste a (?:determiner|valider|verifier|comprendre|confirmer)\b",
        r"\bdifficulte a\b",
        r"\blimite(?:s)?\b",
        r"\bcompromis\b",
    )

    return _has_any(
        text,
        patterns,
    )


def _looks_like_editorial_noise(
    text: str,
) -> bool:
    """
    Bruit ?ditorial g?n?rique.

    On ne l'utilise jamais seul pour supprimer
    un vrai probl?me technique.
    """

    patterns = (
        r"\bto cite this version\b",
        r"\barchive for the deposit\b",
        r"\bthis work has been funded\b",
        r"\bfunded by\b",
        r"\backnowledg(?:e)?ments?\b",
        r"\bcopyright\b",
        r"\ball rights reserved\b",
        r"\blicense\b",
        r"\bauthor affiliations?\b",
        r"\bcorresponding author\b",
        r"\bdoi\s*:",
        r"\breferences\s*$",
        r"\bbibliography\s*$",
        r"\bfinanc[?e] par\b",
        r"\bremerciements?\b",
    )

    return _has_any(
        text,
        patterns,
    )


def _problem_bridge_expression(
    text: str,
) -> bool:
    """
    Un passage peut d?pendre de la phrase pr?c?dente
    ou suivante, mais il doit lui-m?me contenir une
    articulation de probl?me.
    """

    patterns = (
        r"\bhowever\b",
        r"\bnevertheless\b",
        r"\bbut\b",
        r"\balthough\b",
        r"\bdespite\b",
        r"\bwhereas\b",
        r"\bwhile\b",

        r"\bimpact\b",
        r"\bdepends?\b",
        r"\bdifference(?:s)?\b",
        r"\bgap\b",
        r"\blimit(?:ed|ation|ations)?\b",

        r"\bcependant\b",
        r"\btoutefois\b",
        r"\bmais\b",
        r"\bmalgre\b",
        r"\bimpact\b",
        r"\bdepend\b",
        r"\bdifference",
        r"\becart\b",
        r"\blimit",
    )

    return _has_any(
        text,
        patterns,
    )


def _signal_feature_views(
    item: Dict[str, Any],
):
    source_text = _source_analysis_text(
        item
    )

    context_text = _context_analysis_text(
        item
    )

    source_features = _features_from_text(
        source_text
    )

    context_features = _features_from_text(
        context_text
    )

    source_features["technical"] = bool(
        source_features["technical"]
        or _generic_technical_expression(
            source_text
        )
    )

    context_features["technical"] = bool(
        context_features["technical"]
        or _generic_technical_expression(
            context_text
        )
    )

    return (
        source_features,
        context_features,
    )


def _signal_features(
    item: Dict[str, Any],
) -> Dict[str, bool]:
    """
    Vue fusionn?e conserv?e pour compatibilit?.

    La d?cision project_lock_seed utilise, elle,
    source et contexte s?par?ment.
    """

    source_features, context_features = (
        _signal_feature_views(
            item
        )
    )

    return {
        key: bool(
            source_features.get(
                key
            )
            or context_features.get(
                key
            )
        )
        for key in source_features
    }


def safe_for_synthesis(
    item: Dict[str, Any],
) -> bool:

    text = str(
        item.get("text")
        or ""
    ).strip()

    low = text.lower()

    if (
        len(text) < 45
        or any(
            marker in low
            for marker in BAD_SYNTH
        )
    ):
        return False

    if is_noise_line(
        text
    ):
        return False

    if (
        item.get("content_origin")
        == "metadata"
    ):
        return False

    source_text = _source_analysis_text(
        item
    )

    # M?tadonn?e ?ditoriale pure.
    # Si elle contient r?ellement une formulation
    # technique ouverte, elle n'est pas rejet?e ici.
    if (
        _looks_like_editorial_noise(
            source_text
        )
        and not _generic_problem_expression(
            source_text
        )
    ):
        return False

    words = re.findall(
        r"[A-Za-z?-?]{3,}",
        text,
    )

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



def _mark_lock_candidate(
    item: Dict[str, Any],
) -> None:

    model_role = str(
        item.get(
            "original_model_role"
        )
        or item.get("role")
        or ""
    ).lower()

    model_confidence = _sf(
        item.get(
            "model_confidence"
        )
    )

    lock_score = _sf(
        item.get(
            "lock_candidate_score"
        )
        or item.get(
            "verrou_score"
        )
    )

    source_text = (
        _source_analysis_text(
            item
        )
    )

    source_features, context_features = (
        _signal_feature_views(
            item
        )
    )


    # ========================================================
    # 1. RESULTAT FASTJUDGE
    #
    # Jamais supprim?.
    # ========================================================

    fastjudge_signal = bool(
        model_role == "verrou"
    )


    # ========================================================
    # 2. PROBLEME DANS LE PASSAGE SOURCE
    # ========================================================

    strong_keys = (
        "uncertainty",
        "measurement_limit",
        "causal_gap",
        "tradeoff",
        "open_validation",
        "knowledge_gap",
    )

    source_structured_problem = any(
        bool(
            source_features.get(
                key
            )
        )
        for key in strong_keys
    )

    context_structured_problem = any(
        bool(
            context_features.get(
                key
            )
        )
        for key in strong_keys
    )

    source_problem = bool(
        source_structured_problem
        or _generic_problem_expression(
            source_text
        )
    )

    context_text = (
        _context_analysis_text(
            item
        )
    )

    context_problem = bool(
        context_structured_problem
        or _generic_problem_expression(
            context_text
        )
    )


    # ========================================================
    # 3. TECHNICITE
    # ========================================================

    source_technical = bool(
        source_features.get(
            "technical"
        )
    )

    context_technical = bool(
        context_features.get(
            "technical"
        )
    )


    # ========================================================
    # 4. BRUIT / RESOLUTION ROUTINIERE
    # ========================================================

    editorial_noise = bool(
        _looks_like_editorial_noise(
            source_text
        )
        and not source_problem
    )

    routine_only = bool(
        source_features.get(
            "routine_resolution"
        )
        and not source_features.get(
            "tradeoff"
        )
        and not source_features.get(
            "knowledge_gap"
        )
        and not source_features.get(
            "open_validation"
        )
        and not source_problem
    )


    # ========================================================
    # 5. DEUX CHEMINS LEGITIMES
    # ========================================================

    # Le passage lui-m?me formule le verrou.
    direct_source_problem = bool(
        source_technical
        and source_problem
    )


    # Le probl?me est formul? juste avant/apr?s,
    # MAIS le passage courant doit lui-m?me ?tre
    # technique et contenir un lien logique.
    contextual_problem = bool(
        source_technical
        and context_technical
        and context_problem
        and _problem_bridge_expression(
            source_text
        )
    )


    # ========================================================
    # 6. PROJECT LOCK SEED
    #
    # Seul FastJudge peut cr?er le signal verrou.
    # Les r?gles ci-dessous ne cr?ent donc jamais
    # un verrou ? partir d'objectif/methode/resultat.
    # ========================================================

    project_lock_seed = bool(
        fastjudge_signal
        and not _is_context_only(
            item
        )
        and not editorial_noise
        and not routine_only
        and (
            direct_source_problem
            or contextual_problem
        )
    )


    # ========================================================
    # 7. TRACE COMPLETE
    # ========================================================

    item[
        "fastjudge_verrou_signal"
    ] = fastjudge_signal

    # Compatibilit? :
    # lock_candidate = signal FastJudge brut.
    item[
        "lock_candidate"
    ] = fastjudge_signal

    item[
        "project_lock_seed"
    ] = project_lock_seed

    item[
        "lock_eligible"
    ] = project_lock_seed

    item[
        "lock_candidate_score"
    ] = lock_score

    item[
        "lock_candidate_explicit"
    ] = bool(
        project_lock_seed
        and direct_source_problem
    )

    item[
        "lock_candidate_features"
    ] = {
        key: bool(
            source_features.get(
                key
            )
            or context_features.get(
                key
            )
        )
        for key in source_features
    }

    item[
        "lock_source_features"
    ] = source_features

    item[
        "lock_context_features"
    ] = context_features

    item[
        "lock_source_problem"
    ] = source_problem

    item[
        "lock_context_problem"
    ] = context_problem

    item[
        "lock_source_technical"
    ] = source_technical

    item[
        "lock_editorial_noise"
    ] = editorial_noise

    item[
        "lock_contextual_bridge"
    ] = contextual_problem


    # ========================================================
    # 8. STATUT EXPLICABLE
    # ========================================================

    if not fastjudge_signal:

        status = (
            "not_fastjudge_verrou"
        )

        reason = (
            "FastJudge n'a pas pr?dit verrou."
        )

    elif _is_context_only(
        item
    ):

        status = (
            "fastjudge_signal_context_only"
        )

        reason = (
            "Signal FastJudge conserv?, "
            "mais source contextuelle uniquement."
        )

    elif editorial_noise:

        status = (
            "fastjudge_signal_demoted_editorial"
        )

        reason = (
            "Signal FastJudge conserv?, "
            "mais passage ?ditorial/m?tadonn?e "
            "sans probl?me technique exprim?."
        )

    elif routine_only:

        status = (
            "fastjudge_signal_demoted_routine"
        )

        reason = (
            "Signal FastJudge conserv?, "
            "mais r?solution technique routini?re."
        )

    elif project_lock_seed:

        status = (
            "project_lock_seed"
        )

        reason = (
            "FastJudge=verrou + probl?me "
            "technique non r?solu exprim? "
            "dans le passage ou son contexte "
            "local coh?rent."
        )

    else:

        status = (
            "fastjudge_signal_support_only"
        )

        reason = (
            "Signal FastJudge conserv?, "
            "mais le passage d?crit surtout "
            "une m?thode, un r?sultat, un param?tre "
            "ou un contexte sans verrou suffisamment "
            "explicite."
        )


    item[
        "lock_candidate_status"
    ] = status

    item[
        "project_lock_seed_reason"
    ] = reason

    if not project_lock_seed:
        item[
            "non_verrou_reason"
        ] = reason

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

        # LinearSVC n'expose pas de probabilités calibrées. Ses marges transformées
        # en scores 0..1 sont utiles pour le ranking, pas pour appliquer les anciens
        # seuils de probabilité. Dans ce cas, on fait confiance à la classe prédite
        # pour les rôles non-bruit, tout en conservant le filtre de qualité textuelle.
        score_source = str(item.get("model_score_source") or "")
        linear_svc_role_accepted = bool(
            score_source == "decision_function_sigmoid_uncalibrated"
            and str(item.get("original_model_role") or "").lower() in NON_LOCK_SEMANTIC_ROLES
        )

        if safe and (strict or recall or linear_svc_role_accepted or item["lock_candidate"]):
            if item["lock_candidate"]:
                status = "lock_candidate_preserved"
            elif strict:
                status = "strict"
            elif recall:
                status = "recall"
            else:
                status = "linear_svc_prediction"
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
            "fastjudge_verrou_signals":
                sum(
                    bool(
                        item.get(
                            "fastjudge_verrou_signal"
                        )
                    )
                    for item
                    in lock_candidates
                ),

            "project_lock_seeds":
                sum(
                    bool(
                        item.get(
                            "project_lock_seed"
                        )
                    )
                    for item
                    in lock_candidates
                ),

            "fastjudge_verrou_demoted":
                sum(
                    bool(
                        item.get(
                            "fastjudge_verrou_signal"
                        )
                    )
                    and not bool(
                        item.get(
                            "project_lock_seed"
                        )
                    )
                    for item
                    in lock_candidates
                ),

        },
    }


def thresholds() -> Dict[str, Any]:
    return {
        **{f"strict_{key}": value for key, value in STRICT.items()},
        **{f"recall_{key}": value for key, value in RECALL.items()},
        "lock_candidate_recall": LOCK_CANDIDATE_RECALL,
        "lock_candidate_strong": LOCK_CANDIDATE_STRONG,
        "lock_role_recall": LOCK_ROLE_RECALL,
        "lock_detection": "FastJudge predicted role == verrou",
        "legacy_lock_thresholds_used_for_detection": False,
        "rule": (
            "FastJudge is the only lock detector. Generic uncertainty/technical patterns are retained "
            "for evidence support, grouping and Frascati explainability, not to create or reject a lock."
        ),
    }
