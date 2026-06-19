# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Dict, Any, List, Optional


# ---------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------

def norm(text: str) -> str:
    text = str(text or "").lower()
    text = text.replace("é", "e").replace("è", "e").replace("ê", "e")
    text = text.replace("à", "a").replace("ç", "c")
    text = text.replace("ù", "u").replace("ô", "o")
    text = text.replace("î", "i").replace("ï", "i")
    text = text.replace("’", "'")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x or 0.0)
    except Exception:
        return default


def _is_too_short(text: str, min_len: int = 45) -> bool:
    text = str(text or "").strip()
    return len(text) < min_len


def _looks_like_noise(text: str) -> bool:
    low = norm(text)
    if not low:
        return True

    if _is_too_short(low):
        return True

    chars = len(low)
    digits = sum(c.isdigit() for c in low)
    pipes = low.count("|")
    slashes = low.count("/")

    if chars > 0 and digits / chars > 0.55:
        return True

    if pipes >= 6:
        return True

    if slashes >= 10 and len(low) < 180:
        return True

    admin_patterns = [
        "siret", "page ", "revision", "indice", "date", "auteur",
        "signature", "confidentiel", "copyright", "tous droits",
        "web :", "www.", "table des matieres", "sommaire"
    ]

    hits = sum(1 for p in admin_patterns if p in low)
    if hits >= 3 and len(low) < 220:
        return True

    return False


# ---------------------------------------------------------------------
# Types documentaires
# ---------------------------------------------------------------------

CONTEXT_ONLY_TYPES = {
    "norme_reglementation",
    "plan_schema",
    "administratif",
    "template_formulaire",
}

SECONDARY_TYPES = {
    "notice_memoire_technique",
    "etat_art_bibliographie",
}

CORE_TYPES = {
    "concept_projet",
    "brevet",
    "brevet_invention",
    "preuve_depot",
    "preuve_depot_brevet",
    "rapport_test",
    "resultats_mesures",
    "note_projet",
    "presentation_projet",
    "methodologie_protocole",
}


# ---------------------------------------------------------------------
# Signaux verrou
# ---------------------------------------------------------------------

VERROU_PATTERNS = [
    r"\bverrou\b",
    r"\bverrou technologique\b",
    r"\bverrou scientifique\b",
    r"\bverrou technique\b",
    r"\bincertitude\b",
    r"\bincertitude technique\b",
    r"\bincertitude scientifique\b",
    r"\bdifficulte\b",
    r"\bdifficulte majeure\b",
    r"\bprobleme non resolu\b",
    r"\bnon resolu\b",
    r"\bnon maitrise\b",
    r"\bnon controle\b",
    r"\bimpossible de\b",
    r"\bne permet pas de\b",
    r"\bn'a pas permis\b",
    r"\bne satisfait pas\b",
    r"\binsuffisance\b",
    r"\binsuffisant\b",
    r"\babsence de solution\b",
    r"\bmanque de solution\b",
    r"\bfrein majeur\b",
    r"\bobstacle majeur\b",
    r"\blimite majeure\b",
    r"\blimitation majeure\b",
    r"\bhors de portee\b",
    r"\ba verifier\b",
    r"\ba demontrer\b",
    r"\bhypothese\b",
    r"\bvariabilite\b",
    r"\breproductibilite\b",
    r"\binstabilite\b",
    r"\bcompromis technique\b",
    r"\bcontraintes contradictoires\b",
]


# Signaux utiles mais pas suffisants seuls
WEAK_VERROU_PATTERNS = [
    r"\bchallenge\b",
    r"\bblocage\b",
    r"\bbloquant\b",
    r"\bobstacle\b",
    r"\blimite\b",
    r"\brisque\b",
    r"\bcontrainte\b",
    r"\bdefaut\b",
    r"\busure\b",
    r"\bfuite\b",
    r"\bvibration\b",
    r"\bsoufflage\b",
    r"\bperte de charge\b",
]


# Faux verrous typiques : méthode, stats, protocole, description scientifique
METHOD_CONTEXT_PATTERNS = [
    r"\bprotocole\b",
    r"\bmethode\b",
    r"\bmethodologie\b",
    r"\bmode operatoire\b",
    r"\bprocedure\b",
    r"\bdosage\b",
    r"\btest de wilcoxon\b",
    r"\bp[- ]?value\b",
    r"\bseuil de signification\b",
    r"\bmoyenne\b",
    r"\becart type\b",
    r"\bvalidation loss\b",
    r"\blearning rate\b",
    r"\bbatch size\b",
    r"\boptimizer\b",
    r"\bcross[- ]entropy\b",
    r"\bsoft dice loss\b",
    r"\bbackpropagation\b",
    r"\bselon la technique\b",
    r"\bconditions experimentales\b",
    r"\best exprime en\b",
    r"\bla reaction est suivie\b",
]


NOISE_PATTERNS = [
    r"\bgithub\b",
    r"\brepository\b",
    r"\bdoi\b",
    r"\barxiv\b",
    r"\bieee\b",
    r"\bsoft dice loss\b",
    r"\bcross[-\s]entropy\b",
    r"\bbackpropagation\b",
    r"\blearning rate\b",
    r"\bbatch size\b",
    r"\bvalidation loss\b",
    r"\boptimizer\b",
]


def _matches(patterns: List[str], text: str) -> int:
    low = norm(text)
    return sum(1 for p in patterns if re.search(p, low, flags=re.I))


def has_strong_verrou_signal(text: str) -> bool:
    return _matches(VERROU_PATTERNS, text) > 0


def has_weak_verrou_signal(text: str) -> bool:
    return _matches(WEAK_VERROU_PATTERNS, text) > 0


def has_verrou_patterns(text: str) -> bool:
    return has_strong_verrou_signal(text) or has_weak_verrou_signal(text)


def is_method_context(text: str) -> bool:
    return _matches(METHOD_CONTEXT_PATTERNS, text) > 0


def is_noise_verrou(text: str) -> bool:
    low = norm(text)
    for pat in NOISE_PATTERNS:
        if re.search(pat, low, flags=re.I):
            return True
    return False


def _role_scores(item: Dict[str, Any]) -> Dict[str, float]:
    scores = item.get("scores", {}) or {}
    return {str(k): _safe_float(v) for k, v in scores.items()}


def _max_other_role(item: Dict[str, Any]) -> float:
    scores = _role_scores(item)
    return max(
        [
            v for r, v in scores.items()
            if r != "verrou" and r in {"objectif", "methode", "resultat", "contribution", "parametre", "limite"}
        ] or [0.0]
    )


def _dominant_role(item: Dict[str, Any]) -> Optional[str]:
    scores = _role_scores(item)
    if not scores:
        return None
    return max(scores.items(), key=lambda x: x[1])[0]


def _has_bad_role_conflict(item: Dict[str, Any]) -> bool:
    scores = _role_scores(item)

    objectif = _safe_float(scores.get("objectif"))
    methode = _safe_float(scores.get("methode"))
    resultat = _safe_float(scores.get("resultat"))
    parametre = _safe_float(scores.get("parametre"))
    contribution = _safe_float(scores.get("contribution"))

    if objectif >= 0.70:
        return True
    if methode >= 0.68:
        return True
    if resultat >= 0.72:
        return True
    if parametre >= 0.72:
        return True
    if contribution >= 0.72:
        return True

    return False


# ---------------------------------------------------------------------
# Scoring local
# ---------------------------------------------------------------------

def local_verrou_score(item: Dict[str, Any], domain_context: Optional[str] = None) -> float:
    role = item.get("role")
    confidence = _safe_float(item.get("confidence") or item.get("model_confidence"))
    verrou_score = _safe_float(item.get("verrou_score"))
    rank_score_val = _safe_float(item.get("rank_score"))
    dtype = item.get("document_type")
    text = item.get("text", "")
    hint = item.get("section_role_hint")

    score = 0.0

    if role == "verrou":
        score += 0.48
    elif role == "limite":
        score += 0.24
    elif role == "resultat":
        score += 0.08
    elif role == "methode":
        score += 0.04

    score += min(confidence, 1.0) * 0.14
    score += min(verrou_score, 1.0) * 0.26
    score += min(rank_score_val, 1.5) * 0.08

    if item.get("content_origin") == "project_core":
        score += 0.05

    if dtype in CORE_TYPES:
        score += 0.15
    elif dtype in SECONDARY_TYPES:
        score -= 0.22
    elif dtype in CONTEXT_ONLY_TYPES:
        score -= 0.65

    if item.get("quality_status") == "strict":
        score += 0.03

    if has_strong_verrou_signal(text):
        score += 0.14
    elif has_weak_verrou_signal(text):
        score += 0.05

    if hint == "verrou":
        score += 0.10
    elif hint == "limite":
        score += 0.04

    if is_method_context(text) and not has_strong_verrou_signal(text):
        score -= 0.25

    if _has_bad_role_conflict(item):
        score *= 0.45

    if domain_context and domain_context in norm(text):
        score += 0.03

    return round(max(0.0, min(score, 1.2)), 4)


# ---------------------------------------------------------------------
# Décision verrou direct
# ---------------------------------------------------------------------

def should_keep_direct_verrou(item: Dict[str, Any]) -> bool:
    """
    Correction principale :
    Avant, un passage pouvait passer si verrou_score >= 0.50.
    Maintenant, verrou_score seul ne suffit plus.
    Il faut aussi un signal explicite d'incertitude/verrou OU un contexte section solide.
    """

    dtype = item.get("document_type")
    text = item.get("text", "")
    role = item.get("role")
    hint = item.get("section_role_hint")
    quality = item.get("quality_status")

    confidence = _safe_float(item.get("confidence") or item.get("model_confidence"))
    verrou_score = _safe_float(item.get("verrou_score"))
    max_other = _max_other_role(item)

    if dtype in CONTEXT_ONLY_TYPES:
        item["non_verrou_reason"] = "document contextuel/normatif : contrainte ou méthode, pas verrou R&D"
        return False

    if _looks_like_noise(text) or is_noise_verrou(text):
        return False

    if role != "verrou":
        return False

    strong_signal = has_strong_verrou_signal(text)
    weak_signal = has_weak_verrou_signal(text)
    section_verrou = hint == "verrou"

    # Si méthode/contexte domine et aucune incertitude explicite : faux verrou probable
    if is_method_context(text) and not strong_signal:
        item["non_verrou_reason"] = "passage surtout méthodologique/protocolaire, sans incertitude explicite"
        return False

    # Si un autre rôle domine fortement, ne pas garder comme verrou direct
    if _has_bad_role_conflict(item) and not strong_signal:
        item["non_verrou_reason"] = f"autre rôle dominant : {_dominant_role(item)}"
        return False

    # Cas 1 : vrai verrou explicite
    if strong_signal and confidence >= 0.45 and verrou_score >= 0.35:
        return True

    # Cas 2 : modèle très confiant + section verrou
    if section_verrou and confidence >= 0.55 and verrou_score >= 0.55 and max_other < 0.62:
        return True

    # Cas 3 : verrou_score très fort, mais seulement si signal faible ou section verrou
    if verrou_score >= 0.68 and (weak_signal or section_verrou) and max_other < 0.60:
        return True

    # Cas 4 : qualité stricte + signal faible + score correct
    if quality in {"strict", "verrou_boosted", "verrou_score_high"}:
        if weak_signal and confidence >= 0.55 and verrou_score >= 0.50 and max_other < 0.65:
            return True

    return False


# ---------------------------------------------------------------------
# Promotion de limite/résultat/méthode vers verrou
# ---------------------------------------------------------------------

def should_promote_as_local_verrou(item: Dict[str, Any]) -> bool:
    """
    Promotion plus stricte :
    - objectif ne devient jamais verrou ici ;
    - méthode ne devient verrou que si incertitude explicite ;
    - résultat ne devient verrou que s'il exprime une limite/incertitude, pas une simple mesure.
    """

    dtype = item.get("document_type")
    text = item.get("text", "")
    role = item.get("role")
    hint = item.get("section_role_hint")

    verrou_score = _safe_float(item.get("verrou_score"))
    confidence = _safe_float(item.get("confidence") or item.get("model_confidence"))
    max_other = _max_other_role(item)

    if dtype in CONTEXT_ONLY_TYPES:
        return False

    if _looks_like_noise(text) or is_noise_verrou(text):
        return False

    if role in {"objectif", "parametre", "contribution", "bruit"}:
        return False

    strong_signal = has_strong_verrou_signal(text)
    weak_signal = has_weak_verrou_signal(text)
    section_verrou = hint == "verrou"
    section_limite = hint == "limite"

    # Ne pas promouvoir une méthode pure
    if is_method_context(text) and not strong_signal:
        return False

    # Si un autre rôle domine fortement et qu'il n'y a pas d'incertitude claire
    if _has_bad_role_conflict(item) and not strong_signal:
        return False

    # Limite -> verrou : acceptable si vraie limite technique
    if role == "limite":
        if strong_signal and verrou_score >= 0.45:
            return True
        if weak_signal and verrou_score >= 0.62 and confidence >= 0.50:
            return True
        if section_limite and verrou_score >= 0.65 and max_other < 0.60:
            return True

    # Résultat -> verrou : seulement si le résultat montre une limite ou échec
    if role == "resultat":
        if strong_signal and verrou_score >= 0.58 and max_other < 0.68:
            return True
        if section_verrou and verrou_score >= 0.70 and weak_signal:
            return True

    # Méthode -> verrou : seulement si la méthode est décrite comme réponse à une incertitude
    if role == "methode":
        if strong_signal and verrou_score >= 0.62 and max_other < 0.65:
            return True

    # Section verrou + score fort, mais pas si texte méthode pure
    if section_verrou and verrou_score >= 0.68 and (strong_signal or weak_signal) and max_other < 0.62:
        return True

    return False


def make_promoted_verrou(item: Dict[str, Any]) -> Dict[str, Any]:
    x = dict(item)
    x["role"] = "verrou"
    x["quality_status"] = "promoted_local_strict"
    x["verrou_source"] = "promoted_from_local_evidence"
    x["accepted_for_synthesis"] = True
    x["needs_human_validation"] = True
    x["local_verrou_score"] = local_verrou_score(item)
    x["promoted_from_role"] = item.get("role")
    x["promoted_from_category"] = item.get("_source_category")
    return x


# ---------------------------------------------------------------------
# Construction des verrous locaux
# ---------------------------------------------------------------------

def collect_candidate_sources(pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources = []

    categories = [
        "verrous_rnd_locaux",
        "limites_locales",
        "methodes_locales",
        "resultats_locaux",
        "parametres_locaux",
        "objectifs_locaux",
    ]

    for key in categories:
        for item in pack.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            x = dict(item)
            x["_source_category"] = key
            sources.append(x)

            # On récupère aussi les supporting_passages, car parfois le groupe masque le bon passage
            for sp in item.get("supporting_passages") or []:
                if isinstance(sp, dict):
                    y = dict(sp)
                    y.setdefault("document", item.get("document"))
                    y.setdefault("source_path", item.get("source_path"))
                    y.setdefault("document_type", item.get("document_type"))
                    y.setdefault("content_origin", item.get("content_origin"))
                    y.setdefault("source_weight", item.get("source_weight"))
                    y.setdefault("document_weight", item.get("document_weight"))
                    y.setdefault("section_title", item.get("section_title"))
                    y.setdefault("section_role_hint", item.get("section_role_hint"))
                    y.setdefault("role", item.get("role"))
                    y["_source_category"] = key
                    y["_supporting_from"] = item.get("cluster_id") or item.get("passage_id")
                    sources.append(y)

    return sources


def build_local_verrous(
    pack: Dict[str, Any],
    max_verrous: int = 20,
    domain_context: Optional[str] = None,
) -> List[Dict[str, Any]]:

    sources = collect_candidate_sources(pack)

    direct: List[Dict[str, Any]] = []
    promoted: List[Dict[str, Any]] = []
    rejected_debug: List[Dict[str, Any]] = []

    for item in sources:
        item["local_verrou_score"] = local_verrou_score(item, domain_context=domain_context)

        if should_keep_direct_verrou(item):
            x = dict(item)
            x["verrou_source"] = x.get("verrou_source") or "direct_model_evidence_strict"
            x["accepted_for_synthesis"] = True
            x["needs_human_validation"] = True
            direct.append(x)

        elif should_promote_as_local_verrou(item):
            promoted.append(make_promoted_verrou(item))

        else:
            # Debug léger conservé dans l'item si besoin, mais non retourné dans le pack final
            rejected_debug.append({
                "text": item.get("text"),
                "document": item.get("document"),
                "role": item.get("role"),
                "confidence": item.get("confidence"),
                "verrou_score": item.get("verrou_score"),
                "section_role_hint": item.get("section_role_hint"),
                "document_type": item.get("document_type"),
                "non_verrou_reason": item.get("non_verrou_reason"),
                "local_verrou_score": item.get("local_verrou_score"),
            })

    direct = sorted(direct, key=lambda x: _safe_float(x.get("local_verrou_score")), reverse=True)
    promoted = sorted(promoted, key=lambda x: _safe_float(x.get("local_verrou_score")), reverse=True)

    final: List[Dict[str, Any]] = []
    seen = set()

    for item in direct + promoted:
        text_key = norm(item.get("text", ""))[:220]
        key = f"{norm(item.get('document', ''))}|{text_key}"

        if key in seen:
            continue

        seen.add(key)
        final.append(item)

        if len(final) >= max_verrous:
            break

    for i, item in enumerate(final, start=1):
        item.setdefault("cluster_id", f"verrou_local_{i:03d}")
        item["local_verrou_rank"] = i

    return final


def enrich_evidence_pack_with_verrous(
    pack: Dict[str, Any],
    domain_context: Optional[str] = None,
) -> Dict[str, Any]:

    pack = dict(pack or {})

    final_verrous = build_local_verrous(
        pack=pack,
        max_verrous=20,
        domain_context=domain_context,
    )

    pack["verrous_rnd_locaux"] = final_verrous

    return pack