# -*- coding: utf-8 -*-
from __future__ import annotations

"""
cir_style_profile_builder.py

Phase 3 — Style Profile Builder SAFE

Rôle :
- lire style_extraction_payload.json ;
- analyser les signaux de style extraits depuis Memory V2 ;
- produire un style_profile propre, générique et exploitable par le LLM ;
- éviter de transmettre des phrases historiques brutes au few-shot builder.

Principe important :
- Memory V2 = inspiration stylistique uniquement ;
- les anciens CIR ne doivent jamais devenir des preuves ;
- les phrases issues des anciens CIR ne doivent pas être copiées directement ;
- le profil final doit contenir des templates génériques propres avec placeholders.

Sortie principale :
- style_profile_payload.json

Utilisé ensuite par :
- cir_fewshot_builder.py
- EnnoScholar
- EnnoAmel
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


from .cir_style_retriever import (
    clean_text,
    _read_json,
    _write_json,
    style_memory_output_path,
)

from .cir_style_extractor import (
    style_extraction_output_path,
)


# ============================================================
# Ordre rédactionnel cible
# ============================================================

CANONICAL_PARAGRAPH_ORDER = [
    "contexte scientifique du verrou",
    "travaux existants dans la littérature",
    "limites de l’état de l’art",
    "gap scientifique / non-transposabilité au projet courant",
    "justification des travaux R&D nécessaires",
    "transition vers la démarche expérimentale",
]


# ============================================================
# Templates propres — jamais issus directement d’un ancien CIR
# ============================================================

SAFE_INTRO_TEMPLATES = [
    "De nombreux travaux de l’état de l’art ont exploré [famille de méthodes] pour répondre à [problématique scientifique ou technologique].",
    "Dans ce contexte, la littérature fournit plusieurs approches permettant de [apport connu], mais leur transposition au cas du projet reste limitée par [contrainte spécifique].",
    "L’objectif du présent projet R&D est de [objectif technique] dans un contexte marqué par [incertitude scientifique ou verrou technologique].",
    "Un verrou scientifique majeur réside dans la capacité à [action technique] tout en garantissant [critère de performance, robustesse ou généralisation].",
    "La problématique étudiée s’inscrit dans un champ de recherche où les méthodes existantes restent dépendantes de [hypothèse, donnée, protocole ou condition expérimentale].",
]

SAFE_TRANSITION_TEMPLATES = [
    "En effet, ces approches permettent de [apport scientifique identifié], mais elles restent dépendantes de [condition limitante].",
    "En outre, plusieurs travaux ont proposé [famille d’approches], sans toutefois résoudre pleinement [limite scientifique identifiée].",
    "Par ailleurs, les résultats disponibles dans la littérature doivent être interprétés au regard de [contexte expérimental ou hypothèse de validation].",
    "En premier lieu, il est nécessaire de caractériser [paramètre, phénomène ou contrainte] avant de pouvoir valider [objectif du projet].",
    "En second lieu, se pose la question de [incertitude scientifique], en particulier lorsque [condition spécifique du projet].",
    "Enfin, l’évaluation objective de ces approches nécessite [protocole, métrique ou jeu de données adapté].",
    "Ainsi, l’état de l’art fournit un socle méthodologique utile, mais insuffisant pour lever directement le verrou rencontré dans le projet.",
]

SAFE_GAP_TEMPLATES = [
    "Malgré des résultats encourageants, ces approches restent limitées par [limite scientifique ou méthodologique].",
    "Il n’existe pas de consensus clair sur [critère, protocole ou méthode], ce qui maintient une incertitude scientifique pour [cas projet].",
    "La transposition directe de ces travaux au contexte du projet n’est pas immédiate, car [contrainte opérationnelle, expérimentale ou technique].",
    "Cette problématique demeure complexe en raison de [facteur de variabilité, coût, bruit, rareté des données ou contrainte physique].",
    "Le verrou porte donc sur la capacité à [action R&D] tout en maîtrisant [risque, incertitude ou limite de généralisation].",
    "Le gap scientifique réside dans l’écart entre les conditions étudiées dans la littérature et les contraintes spécifiques du dossier courant.",
    "Ces limites justifient la mise en place d’une démarche expérimentale propre au projet afin de vérifier [hypothèse ou performance attendue].",
]

SAFE_CONCLUSION_TEMPLATES = [
    "Ainsi, l’état de l’art met en évidence des pistes pertinentes, mais insuffisantes pour lever complètement le verrou identifié.",
    "Ces limites justifient la mise en œuvre de travaux R&D spécifiques afin de [objectif de validation, adaptation ou généralisation].",
    "Les travaux du projet doivent donc permettre de réduire les incertitudes relatives à [phénomène, méthode ou performance].",
    "Cette démarche vise à établir une contribution scientifique, technique ou technologique propre au contexte du projet.",
    "La contribution attendue ne réside pas uniquement dans l’application d’une méthode existante, mais dans sa validation, son adaptation et son évaluation dans un contexte contraint.",
]

SAFE_FEWSHOT_TEMPLATES = {
    "etat_art": (
        "De nombreux travaux de l’état de l’art ont exploré [famille de méthodes] pour répondre à "
        "[problématique scientifique]. Ces approches montrent l’intérêt de [principe technique], "
        "mais leur efficacité dépend fortement de [conditions expérimentales]. Ainsi, la littérature "
        "fournit des bases méthodologiques utiles, sans lever complètement les incertitudes liées à "
        "[contrainte spécifique du projet]."
    ),
    "verrou": (
        "Un verrou scientifique majeur réside dans la capacité à [objectif technique] tout en garantissant "
        "[critère de robustesse ou de performance]. Cette problématique demeure complexe, car les méthodes "
        "disponibles reposent sur des hypothèses qui ne couvrent pas totalement [conditions du projet courant]. "
        "Il est donc nécessaire de définir une approche spécifique permettant de réduire cette incertitude."
    ),
    "limite": (
        "Malgré des résultats encourageants, les approches existantes restent limitées par "
        "[limite scientifique ou méthodologique]. Leur transposition directe au cas du projet courant "
        "n’est pas immédiate, car [contrainte opérationnelle ou expérimentale]. Le gap scientifique porte "
        "donc sur la capacité à adapter, valider et objectiver ces méthodes dans un contexte plus contraint."
    ),
    "contribution": (
        "Les travaux conduits dans le cadre du projet ont apporté une contribution scientifique, technique "
        "et technologique à la problématique étudiée. Sur le plan scientifique, ils ont permis d’approfondir "
        "la compréhension de [phénomène ou mécanisme]. Sur le plan technique, ils ont structuré une démarche "
        "expérimentale permettant de comparer, valider et fiabiliser les solutions proposées dans le contexte du projet."
    ),
    "objectif": (
        "L’objectif du présent projet R&D est de développer et valider [approche technique] afin de répondre "
        "à [problématique scientifique ou technologique]. Il s’agit de dépasser les limites des approches "
        "existantes en caractérisant [incertitude principale], puis en évaluant la capacité de la solution "
        "proposée à satisfaire [critère de performance, robustesse ou généralisation]."
    ),
}




SAFE_REASONING_PATTERNS = [
    {
        "pattern_id": "family_principle_limit_project",
        "label": "Famille → principe → limite → lien projet",
        "steps": [
            "introduire la famille scientifique",
            "décrire le principe technique",
            "montrer l'apport méthodologique",
            "formuler la limite commune",
            "relier la limite au verrou du projet",
        ],
        "usage": "reasoning_structure_only",
    },
    {
        "pattern_id": "evidence_convergence_gap",
        "label": "Convergence des travaux → incertitude résiduelle → gap",
        "steps": [
            "regrouper les sources convergentes",
            "identifier ce que l'état de l'art établit déjà",
            "identifier ce qui n'est pas démontré pour le projet",
            "formuler l'incertitude technique restante",
            "justifier une validation expérimentale propre au dossier",
        ],
        "usage": "reasoning_structure_only",
    },
    {
        "pattern_id": "related_method_transposition",
        "label": "Travaux connexes → éclairage méthodologique → prudence de transposition",
        "steps": [
            "présenter les travaux connexes comme éclairage",
            "extraire le principe méthodologique utile",
            "limiter la portée de la transposition",
            "ramener la discussion au verrou courant",
        ],
        "usage": "reasoning_structure_only",
    },
]

SAFE_COMPARISON_PATTERNS = [
    {
        "pattern_id": "regroupement_par_familles",
        "label": "Regroupement par familles d'approches",
        "writer_instruction": "Regrouper les sources selon leur mécanisme scientifique, pas selon l'ordre des articles.",
    },
    {
        "pattern_id": "complementarite_methodologique",
        "label": "Complémentarité méthodologique",
        "writer_instruction": "Montrer comment plusieurs travaux éclairent des dimensions différentes du même verrou.",
    },
    {
        "pattern_id": "opposition_apport_limite",
        "label": "Apport connu mais limite persistante",
        "writer_instruction": "Équilibrer l'apport des travaux et la limite qui empêche une transposition directe.",
    },
]

SAFE_SCIENTIFIC_MOVES = [
    {"move_id": "introduire_contexte", "label": "Introduire le contexte scientifique"},
    {"move_id": "decrire_principe", "label": "Décrire le principe technique"},
    {"move_id": "comparer_ou_regrouper", "label": "Comparer ou regrouper plusieurs travaux"},
    {"move_id": "formuler_limite", "label": "Formuler une limite"},
    {"move_id": "relier_au_projet", "label": "Relier au contexte projet"},
    {"move_id": "justifier_rd", "label": "Justifier les travaux R&D"},
]

SAFE_PARAGRAPH_BLUEPRINTS = {
    "travaux_existants_directement_lies": {
        "logic": "famille scientifique → principe → apport → limite commune",
        "moves": ["decrire_principe", "comparer_ou_regrouper", "formuler_limite"],
    },
    "limites_de_l_etat_de_l_art": {
        "logic": "limites communes → conséquences projet → incertitude restante",
        "moves": ["formuler_limite", "comparer_ou_regrouper", "relier_au_projet"],
    },
    "gap_scientifique_technique_justifiant_les_travaux_rd": {
        "logic": "écart littérature/projet → validation manquante → travaux R&D",
        "moves": ["formuler_limite", "relier_au_projet", "justifier_rd"],
    },
    "synthese_cir_exploitable": {
        "logic": "connaissances disponibles → incertitude résiduelle → protocole propre au dossier",
        "moves": ["relier_au_projet", "justifier_rd"],
    },
}

# ============================================================
# Bruit / termes interdits
# ============================================================

BAD_SUBSTRINGS = [
    "CONFIDENTIEL",
    "Classification du document",
    "Ce document est la propriété",
    "Il ne peut être reproduit",
    "Tous droits réservés",
    "FICHE DESCRIPTIVE",
    "PAGE ",
    "[PAGE",
    "CIR 2024",
    "CIR 2023",
    "DU PROJET R&D",
    "MISE EN ŒUVRE DES TECHNIQUES",
    "MISE EN OEUVRE DES TECHNIQUES",
    "PROFOND SPECIFIQUES",
    "SPÉCIFIQUES AU PROBLÈME",
    "SPECIFIQUES AU PROBLEME",
]

BAD_REGEX = [
    r"\.{6,}",
    r"\bpage\s+\d+\b",
    r"\[\s*page\s+\d+\s*\]",
    r"\d+/\d+",
    r"^\s*\d+\s*$",
    r"\b20(1[5-9]|2[0-9]|3[0-5])\b",
    r"\b[A-Z][a-z]+ing\s+and\s+[A-Z]\.?\b",
    r"\b[A-Z]\.\s+[A-Z][a-zÀ-ÿ]+",
    r"\bIEEE\b",
    r"\bSPIE\b",
    r"\bProceedings\b",
    r"\bRemote Sensing\b",
    r"\bvol\.",
    r"\bpp\.",
]

FORBIDDEN_FACTUAL_TERMS = [
    "2023",
    "2024",
    "2025",
]


# ============================================================
# Helpers texte
# ============================================================

def normalize_for_match(text: Any) -> str:
    s = clean_text(text).lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’", "aaaeeeeiioouuuc'")
    s = s.translate(tr)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _project_variants(project: str) -> List[str]:
    raw = clean_text(project)
    if not raw:
        return []

    variants = {
        raw,
        raw.replace("-", "_"),
        raw.replace("_", "-"),
        raw.replace("-", " "),
        raw.replace("_", " "),
        raw.upper(),
        raw.lower(),
    }

    return sorted(variants, key=len, reverse=True)


def anonymize_factual_entities(
    text: Any,
    organisme: str = "",
    project: str = "",
) -> str:
    """
    Fonction conservée pour compatibilité avec les autres fichiers.
    Elle anonymise les noms de projet, années, bases, outils, auteurs.
    """
    s = clean_text(text)
    if not s:
        return ""

    if organisme:
        s = re.sub(re.escape(organisme), "[organisme]", s, flags=re.IGNORECASE)

    for variant in _project_variants(project):
        if variant:
            s = re.sub(re.escape(variant), "[projet R&D]", s, flags=re.IGNORECASE)

    s = re.sub(r"\b20(1[5-9]|2[0-9]|3[0-5])\b", "[année]", s)
    s = re.sub(
        r"\b(?=[A-Z0-9_-]{3,}\b)(?=[A-Z0-9_-]*[A-Z])[A-Z][A-Z0-9_-]*\b",
        "[identifiant]",
        s,
    )

    s = re.sub(
        r"\(([A-ZÉÈÀÂÎÔÛÇ][A-Za-zÀ-ÿ'\-]+)\s+et\s+al\.?\)\s*\d*",
        "([auteurs])",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\(([A-ZÉÈÀÂÎÔÛÇ][A-Za-zÀ-ÿ'\-]+)\s+et\s+[A-ZÉÈÀÂÎÔÛÇ][A-Za-zÀ-ÿ'\-]+\)\s*\d*",
        "([auteurs])",
        s,
        flags=re.IGNORECASE,
    )

    s = re.sub(r"([A-Za-zÀ-ÿ])\d{1,2}\b", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_bibliographic_noise(text: Any) -> bool:
    s = clean_text(text)
    low = normalize_for_match(s)

    biblio_hits = 0
    for token in [
        "ieee",
        "spie",
        "vol.",
        "pp.",
        "proceedings",
        "international geoscience",
        "remote sensing",
        "technical report",
        "signal processing letters",
        "doi",
        "journal",
        "conference",
        "imagery xxvi",
    ]:
        if token in low:
            biblio_hits += 1

    citation_like = len(re.findall(r"\b\d{4}\b", s))
    author_like = len(re.findall(r"\bet al\.?", s, flags=re.IGNORECASE))

    if biblio_hits >= 1 and (citation_like >= 1 or author_like >= 1):
        return True

    return biblio_hits >= 2 or citation_like >= 4 or author_like >= 3


def is_dirty_text(text: Any) -> bool:
    s = clean_text(text)
    if not s:
        return True

    low = normalize_for_match(s)

    for bad in BAD_SUBSTRINGS:
        if normalize_for_match(bad) in low:
            return True

    for pattern in BAD_REGEX:
        if re.search(pattern, s, flags=re.IGNORECASE):
            return True

    if is_bibliographic_noise(s):
        return True

    if re.search(r"\best\s+la\s+(profond|profonde|technique|sp[eé]cifique)", low):
        return True

    if re.search(r"\bdes\s+l[’']entrainement\b", low):
        return True

    if re.search(r"\bles\s+la\s+\w+", low):
        return True

    if re.search(r"\bde\s+le\s+\w+", low):
        return True

    if "à partir de ," in s or "a partir de ," in low:
        return True

    if ".." in s:
        return True

    return False


def is_safe_template(text: Any) -> bool:
    s = clean_text(text)
    if not s:
        return False

    if is_dirty_text(s):
        return False

    low = normalize_for_match(s)

    for term in FORBIDDEN_FACTUAL_TERMS:
        if term in low:
            return False

    # Un template propre doit contenir au moins un placeholder.
    if "[" not in s or "]" not in s:
        return False

    return True


def dedupe_keep_order(items: List[str], max_items: int = 20) -> List[str]:
    out = []
    seen = set()

    for item in items:
        item = clean_text(item)
        if not item:
            continue

        key = normalize_for_match(item)[:260]
        if key in seen:
            continue

        seen.add(key)
        out.append(item)

        if len(out) >= max_items:
            break

    return out


# ============================================================
# Vocabulaire safe
# ============================================================

STOP_VOCAB = {
    "ai",
    "ia",
    "cir",
    "projet",
    "annee",
    "année",
    "page",
    "document",
    "classification",
    "confidentiel",
    "mossing",
    "imagery",
    "xxvi",
}

SAFE_GENERIC_TERMS = [
    "état de l’art",
    "littérature scientifique",
    "verrou scientifique",
    "incertitude technologique",
    "limites méthodologiques",
    "non-transposabilité",
    "généralisation",
    "robustesse",
    "validation expérimentale",
    "protocole d’évaluation",
    "démarche R&D",
    "contribution scientifique",
    "contribution technique",
    "contribution technologique",
]


def clean_vocabulary_term(term: Any) -> str:
    s = clean_text(term)
    if not s:
        return ""

    s = anonymize_factual_entities(s)
    s = s.replace("[base de référence]", "").replace("[outil interne / simulateur]", "")
    s = s.replace("[tâche cible]", "").replace("[tâches de détection/reconnaissance]", "")
    s = re.sub(r"\s+", " ", s).strip(" -_/")

    if not s:
        return ""

    low = normalize_for_match(s)
    if low in STOP_VOCAB:
        return ""

    if any(t in low for t in STOP_VOCAB):
        return ""

    if is_dirty_text(s):
        return ""

    return s


def extract_safe_vocabulary(vocabulary: Dict[str, Any], max_items: int = 20) -> List[str]:
    out = []

    if isinstance(vocabulary, dict):
        for item in vocabulary.get("top_terms", []) or []:
            if not isinstance(item, dict):
                continue
            term = clean_vocabulary_term(item.get("term"))
            if not term:
                continue
            if " " in term:
                continue
            if len(term) < 4:
                continue
            out.append(term)

    out.extend(SAFE_GENERIC_TERMS)
    return dedupe_keep_order(out, max_items=max_items)


def extract_safe_key_phrases(vocabulary: Dict[str, Any], max_items: int = 15) -> List[str]:
    out = []

    if isinstance(vocabulary, dict):
        for key in ["top_bigrams", "top_trigrams"]:
            for item in vocabulary.get(key, []) or []:
                if not isinstance(item, dict):
                    continue

                phrase = clean_vocabulary_term(item.get("term"))
                if not phrase:
                    continue

                if len(phrase) < 6 or len(phrase) > 90:
                    continue

                if " " not in phrase:
                    continue

                out.append(phrase)

    out.extend(SAFE_GENERIC_TERMS)
    return dedupe_keep_order(out, max_items=max_items)


# ============================================================
# Construction templates
# ============================================================

def build_safe_template_list(defaults: List[str], max_items: int) -> List[str]:
    safe = [x for x in defaults if is_safe_template(x)]
    return dedupe_keep_order(safe, max_items=max_items)


def count_specific_factual_patterns(profile: Dict[str, Any]) -> int:
    joined = " ".join(
        (profile.get("intro_patterns") or [])
        + (profile.get("transition_patterns") or [])
        + (profile.get("gap_patterns") or [])
        + (profile.get("conclusion_patterns") or [])
        + list((profile.get("fewshot_templates") or {}).values())
    )

    low = normalize_for_match(joined)

    return sum(1 for term in FORBIDDEN_FACTUAL_TERMS if term in low)


def count_dirty_patterns(profile: Dict[str, Any]) -> int:
    all_items = (
        (profile.get("intro_patterns") or [])
        + (profile.get("transition_patterns") or [])
        + (profile.get("gap_patterns") or [])
        + (profile.get("conclusion_patterns") or [])
        + list((profile.get("fewshot_templates") or {}).values())
    )

    return sum(1 for item in all_items if is_dirty_text(item))


def score_profile_quality(profile: Dict[str, Any], examples_count: int) -> Dict[str, Any]:
    score = 0
    warnings = []

    if examples_count >= 5:
        score += 15
    else:
        warnings.append("Peu d'exemples Memory V2 disponibles.")

    if len(profile.get("intro_patterns") or []) >= 4:
        score += 15
    else:
        warnings.append("Peu de templates d’introduction.")

    if len(profile.get("transition_patterns") or []) >= 5:
        score += 15
    else:
        warnings.append("Peu de templates de transition.")

    if len(profile.get("gap_patterns") or []) >= 5:
        score += 20
    else:
        warnings.append("Peu de templates de gap scientifique.")

    if len(profile.get("conclusion_patterns") or []) >= 4:
        score += 15
    else:
        warnings.append("Peu de templates de conclusion.")

    fewshot_templates = profile.get("fewshot_templates") or {}
    required_roles = {"etat_art", "verrou", "limite", "contribution", "objectif"}

    if required_roles.issubset(set(fewshot_templates.keys())):
        score += 15
    else:
        missing = sorted(required_roles - set(fewshot_templates.keys()))
        warnings.append(f"Templates few-shot manquants : {missing}")

    if profile.get("domain_vocabulary") and profile.get("key_phrases"):
        score += 5
    else:
        warnings.append("Vocabulaire ou phrases clés faibles.")

    if profile.get("reasoning_patterns") and profile.get("scientific_moves") and profile.get("paragraph_blueprints"):
        score += 10
    else:
        warnings.append("Mémoire rhétorique scientifique incomplète.")

    factual_count = count_specific_factual_patterns(profile)
    dirty_count = count_dirty_patterns(profile)

    if factual_count:
        score -= min(30, factual_count * 8)
        warnings.append(f"{factual_count} marqueur(s) factuel(s) détecté(s).")

    if dirty_count:
        score -= min(40, dirty_count * 10)
        warnings.append(f"{dirty_count} pattern(s) sale(s) détecté(s).")

    score = max(0, min(100, score))

    if score >= 85:
        level = "good"
    elif score >= 60:
        level = "usable"
    else:
        level = "weak"

    return {
        "score": score,
        "level": level,
        "warnings": warnings,
        "specific_factual_patterns_count": factual_count,
        "dirty_patterns_count": dirty_count,
    }




def _safe_structured_list_from_extraction(items: Any, fallback: List[Dict[str, Any]], required_key: str = "pattern_id") -> List[Dict[str, Any]]:
    """Garde les structures rhétoriques, jamais des faits bruts."""
    out: List[Dict[str, Any]] = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            cleaned = dict(item)
            # Les exemples observés sont conservés comme signaux anonymisés, pas comme texte à copier.
            if "example_style_signals" in cleaned:
                cleaned["example_style_signals"] = [
                    clean_text(x) for x in (cleaned.get("example_style_signals") or [])
                    if clean_text(x) and not is_dirty_text(x)
                ][:3]
            if required_key and not cleaned.get(required_key):
                continue
            cleaned["memory_as_proof"] = False
            cleaned["can_be_cited"] = False
            out.append(cleaned)
    return out or fallback


def _safe_blueprints_from_extraction(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return SAFE_PARAGRAPH_BLUEPRINTS.copy()
    out: Dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(item, dict):
            continue
        out[clean_text(key)] = {
            "paragraph_role": clean_text(item.get("paragraph_role")),
            "logic": clean_text(item.get("logic")),
            "moves": [clean_text(x) for x in (item.get("moves") or []) if clean_text(x)],
            "avoid": [clean_text(x) for x in (item.get("avoid") or []) if clean_text(x)],
            "usage": "paragraph_structure_only",
            "memory_as_proof": False,
            "can_be_cited": False,
        }
    return out or SAFE_PARAGRAPH_BLUEPRINTS.copy()

# ============================================================
# Construction du style_profile
# ============================================================

def build_style_profile_from_extraction(
    extraction_payload: Dict[str, Any],
    organisme: str = "",
    project: str = "",
) -> Dict[str, Any]:
    """
    Version SAFE :
    on lit l'extraction pour les métriques et le vocabulaire,
    mais on ne réinjecte pas les phrases historiques brutes.
    """

    extraction = extraction_payload.get("style_extraction") or {}
    examples_count = int(extraction_payload.get("examples_count") or 0)

    metrics = extraction.get("metrics") or {}
    role_distribution = extraction.get("role_distribution") or {}
    raw_vocabulary = extraction.get("vocabulary") or {}
    raw_reasoning_patterns = extraction.get("reasoning_patterns") or []
    raw_comparison_patterns = extraction.get("comparison_patterns") or []
    raw_scientific_moves = extraction.get("scientific_moves") or []
    raw_paragraph_blueprints = extraction.get("paragraph_blueprints") or {}

    intro_patterns = build_safe_template_list(SAFE_INTRO_TEMPLATES, max_items=8)
    transition_patterns = build_safe_template_list(SAFE_TRANSITION_TEMPLATES, max_items=10)
    gap_patterns = build_safe_template_list(SAFE_GAP_TEMPLATES, max_items=10)
    conclusion_patterns = build_safe_template_list(SAFE_CONCLUSION_TEMPLATES, max_items=8)

    fewshot_templates = {
        role: text
        for role, text in SAFE_FEWSHOT_TEMPLATES.items()
        if is_safe_template(text)
    }

    domain_vocabulary = extract_safe_vocabulary(raw_vocabulary)
    key_phrases = extract_safe_key_phrases(raw_vocabulary)

    style_profile = {
        "profile_type": "dynamic_cir_style_profile_v3_reasoning_patterns",
        "profile_strategy": "safe_templates_plus_reasoning_patterns_from_memory_signals",
        "paragraph_order": CANONICAL_PARAGRAPH_ORDER.copy(),

        # Ces champs sont maintenant des templates propres, pas des phrases historiques.
        "intro_patterns": intro_patterns,
        "transition_patterns": transition_patterns,
        "gap_patterns": gap_patterns,
        "conclusion_patterns": conclusion_patterns,

        # Nouveau champ important pour le 4e fichier.
        "fewshot_templates": fewshot_templates,

        # Nouveaux champs EnnoScholar Pro : mémoire de rhétorique scientifique, pas mémoire de phrases.
        "reasoning_patterns": _safe_structured_list_from_extraction(
            raw_reasoning_patterns, SAFE_REASONING_PATTERNS, required_key="pattern_id"
        ),
        "comparison_patterns": _safe_structured_list_from_extraction(
            raw_comparison_patterns, SAFE_COMPARISON_PATTERNS, required_key="pattern_id"
        ),
        "scientific_moves": _safe_structured_list_from_extraction(
            raw_scientific_moves, SAFE_SCIENTIFIC_MOVES, required_key="move_id"
        ),
        "paragraph_blueprints": _safe_blueprints_from_extraction(raw_paragraph_blueprints),

        "domain_vocabulary": domain_vocabulary,
        "key_phrases": key_phrases,

        "tone": "consultant CIR",
        "tone_details": [
            "scientifique",
            "prudent",
            "argumentatif",
            "non promotionnel",
            "orienté justification R&D",
            "centré sur les limites de l’état de l’art",
            "centré sur la non-transposabilité au cas projet",
            "centré sur la justification des travaux R&D nécessaires",
        ],

        "style_constraints": {
            "sentence_style": "phrases explicatives, structurées, prudentes",
            "paragraph_style": "progression : littérature → limite → gap → justification R&D",
            "citation_style": "les citations scientifiques doivent venir uniquement des Article Cards",
            "risk_control": "ne pas transformer un exemple Memory V2 en preuve scientifique",
            "historical_fact_control": "aucune phrase historique brute ne doit être injectée dans le few-shot final",
            "fewshot_generation": "les few-shots doivent être générés depuis fewshot_templates et non depuis les extraits bruts Memory V2",
        },

        "writing_rules": [
            "Commencer par situer le verrou dans un contexte scientifique.",
            "Présenter les travaux existants avec prudence.",
            "Faire apparaître explicitement les limites de l’état de l’art.",
            "Expliquer pourquoi ces limites ne sont pas directement transposables au projet courant.",
            "Relier le gap scientifique aux travaux R&D nécessaires.",
            "Conclure chaque bloc par la justification de l’expérimentation ou de la validation propre au projet.",
            "Ne jamais citer Memory V2.",
            "Ne jamais copier les faits historiques des anciens CIR.",
            "Utiliser Memory V2 uniquement pour le ton, la structure, les transitions et le niveau de prudence.",
            "Utiliser uniquement les Article Cards comme sources scientifiques citables.",
        ],

        "forbidden_patterns": [
            "FICHE DESCRIPTIVE",
            "PAGE",
            "CONFIDENTIEL",
            "Ce document est la propriété",
            "bibliographie brute",
            "références longues copiées depuis les anciens CIR",
            "faits historiques repris comme faits du projet courant",
            "nom d’un ancien projet repris tel quel",
            "année historique reprise telle quelle",
            "outil interne ancien repris comme preuve",
            "phrase issue directement d’un ancien CIR",
            "texte OCR cassé",
            "phrase grammaticale incomplète",
        ],

        "memory_as_proof": False,
        "can_be_cited": False,
        "memory_usage": "style_only",
        "scientific_sources_allowed": "article_cards_only",

        "source_metrics": metrics,
        "role_distribution": role_distribution,

        "raw_extraction_usage": {
            "used_for_metrics": True,
            "used_for_vocabulary_signals": True,
            "raw_sentences_injected": False,
            "raw_patterns_injected": False,
        },
    }

    quality = score_profile_quality(
        profile=style_profile,
        examples_count=examples_count,
    )

    style_profile["quality"] = quality

    return style_profile


# ============================================================
# API publique
# ============================================================

def style_profile_output_path(organisme: str, project: str, year: str) -> Path:
    return style_memory_output_path(organisme, project, year).parent / "style_profile_payload.json"


def build_style_profile_payload(
    organisme: str,
    project: str,
    year: str,
    style_extraction_payload_path: Optional[str | Path] = None,
    output_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """
    Lit style_extraction_payload.json et produit style_profile_payload.json.
    """

    input_path = (
        Path(style_extraction_payload_path)
        if style_extraction_payload_path
        else style_extraction_output_path(organisme, project, year)
    )

    out_path = (
        Path(output_path)
        if output_path
        else style_profile_output_path(organisme, project, year)
    )

    extraction_payload = _read_json(input_path, {}) or {}

    if not extraction_payload or not extraction_payload.get("ok"):
        style_profile = {
            "profile_type": "dynamic_cir_style_profile_v3_reasoning_patterns",
            "profile_strategy": "fallback_safe_templates",
            "paragraph_order": CANONICAL_PARAGRAPH_ORDER.copy(),
            "intro_patterns": SAFE_INTRO_TEMPLATES,
            "transition_patterns": SAFE_TRANSITION_TEMPLATES,
            "gap_patterns": SAFE_GAP_TEMPLATES,
            "conclusion_patterns": SAFE_CONCLUSION_TEMPLATES,
            "fewshot_templates": SAFE_FEWSHOT_TEMPLATES,
            "tone": "consultant CIR",
            "memory_as_proof": False,
            "can_be_cited": False,
            "scientific_sources_allowed": "article_cards_only",
            "quality": {
                "score": 75,
                "level": "usable",
                "warnings": ["Extraction de style absente, fallback templates utilisé."],
                "specific_factual_patterns_count": 0,
                "dirty_patterns_count": 0,
            },
        }

        result = {
            "ok": False,
            "phase": "phase_3_dynamic_fewshot_style",
            "step": "cir_style_profile_builder",
            "status": "missing_or_invalid_extraction",
            "message": "Extraction de style absente ou invalide. Fallback templates utilisé.",
            "input_path": str(input_path),
            "output_path": str(out_path),
            "style_profile": style_profile,
            "rules": {
                "usage": "style_only",
                "memory_as_proof": False,
                "can_be_cited": False,
                "scientific_sources_allowed": "article_cards_only",
            },
        }
        _write_json(out_path, result)
        return result

    style_profile = build_style_profile_from_extraction(
        extraction_payload=extraction_payload,
        organisme=organisme,
        project=project,
    )

    result = {
        "ok": True,
        "phase": "phase_3_dynamic_fewshot_style",
        "step": "cir_style_profile_builder",
        "payload_type": "style_profile_payload_v3_reasoning_patterns",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "input_path": str(input_path),
        "examples_count": extraction_payload.get("examples_count", 0),
        "style_profile": style_profile,
        "rules": {
            "usage": "style_only",
            "memory_as_proof": False,
            "can_be_cited": False,
            "scientific_sources_allowed": "article_cards_only",
            "raw_memory_sentences_injected": False,
            "fewshot_should_use_templates_only": True,
        },
        "output_path": str(out_path),
    }

    _write_json(out_path, result)
    return result
