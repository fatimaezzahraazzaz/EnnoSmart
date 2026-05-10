"""
modules/nlp/rag_ranker.py
──────────────────────────────────────────────────────────────────────────────
Ranker NLP intelligent pour préparer les métadonnées exploitables par le futur RAG.

Important :
  Ce module reste dans la partie NLP.
  Il ne fait pas encore le RAG.

Objectifs :
  - Utiliser GLiNER comme générateur principal de candidats
  - Réduire le bruit sans blacklist métier fixe
  - Supprimer les artefacts d’extraction : IMAGE, PAGE, SLIDE, NOTES,
    FORMULES, LaTeX, Domaine, Confiance...
  - Supprimer les expressions coupées : "précision de l", "distance d"
  - Éviter les phrases longues prises comme mots-clés
  - Ne pas expandre les acronymes / technologies courtes
  - Ajouter un extracteur dédié aux mots-clés projet
  - Séparer les mots-clés projet en :
      high_confidence : directement exploitables
      candidates      : utiles mais à raffiner plus tard par agent / LLM

Sortie RAG-ready :
  - technologies
  - verrous_techniques
  - mots_cles_projet
      - high_confidence
      - candidates
  - axes_projet
  - domaines
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_TOP_K = 20
MIN_RAG_SCORE = 3.5

PROJECT_KEYWORD_HIGH_CONFIDENCE_SCORE = 8.8
PROJECT_KEYWORD_CANDIDATE_MIN_SCORE = 7.5


TYPE_WEIGHTS = {
    "VERROU_TECH": 3.2,
    "TECHNOLOGIE": 2.8,
    "DOMAINE_RD": 2.2,
    "PROJET_AXE": 1.8,
    "EXPRESSION_TECHNIQUE": 2.4,
    "MOT_CLE_PROJET": 2.6,
    "METHODE_RD": 2.4,
    "EQUIPEMENT": 2.0,
    "MATERIAU": 2.0,
    "COMPOSANT_TECHNIQUE": 1.8,
    "ORGANISME": 1.0,
    "PERSONNE": 0.7,
    "PERSONNE_RD": 0.7,
    "DATE_PERIODE": 0.5,
    "MONTANT_CIR": 0.5,
}


# Artefacts extraction/pipeline.
# Ce n'est PAS une blacklist métier.
# Ce sont des mots structurels qui ne doivent jamais devenir technologies RAG.
EXTRACTION_ARTIFACT_TERMS = {
    # Vision / Qwen / pipeline
    "IMAGE", "IMAGES", "PAGE", "SECTION", "QUALITÉ", "QUALITE",
    "GPU", "CPU", "QWEN", "QWEN2", "VL", "VL-7B",
    "TYPE", "COMPOSANTS", "COMPOSANTES",
    "SUJET", "PRINCIPAUX", "DESCRIPTION",
    "DETAILS", "DÉTAILS", "FLUX",
    "RESEARCH", "DEVELOPMENT",
    "FULL", "CACHE", "BACKEND", "SOURCE",

    # Formules
    "FORMULE", "FORMULES", "PHYSIQUE", "INCONNU",
    "MECANIQUE", "MÉCANIQUE", "CHIMIE",
    "OMML", "LLM", "HEURISTIC",
    "CONFIANCE", "EXPLICATION", "DOMAINE",
    "LATEX", "DÉTECTÉES", "DETECTEES",
    "FORMULADOMAIN",

    # PPTX / Office
    "SLIDE", "SLIDES", "NOTE", "NOTES",
    "PRÉSENTATEUR", "PRESENTATEUR",
    "PRESENTATION", "PRÉSENTATION",
    "SOUTENANCE", "MASTER",
    "MERCI", "POUR", "VOTRE", "ATTENTION",
    "CONCLUSION", "SOMMAIRE",
    "OBJECTIF", "OBJECTIFS",
    "METHODOLOGIE", "MÉTHODOLOGIE",
    "PERPECTIVES", "PERSPECTIVES",
    "CARACTERISATION", "CARACTÉRISATION",
    "RESULTAT", "RÉSULTAT", "RESULTATS", "RÉSULTATS",
    "ESSAI", "ESSAIS",
    "TABLEAU", "TABLEAUX",
    "DONNÉES", "DONNEES", "TABULAIRES",
    "ANNEXE", "FIGURE", "TABLE", "INDEX",

    # Bruit fréquent
    "TYPE:", "DOMAINE:", "LATEX:", "CONFIANCE:", "EXPLICATION:",
}


EXTRACTION_CONTEXT_MARKERS = [
    "[IMAGE",
    "[QUALITÉ",
    "[QUALITE",
    "[SLIDE",
    "[NOTES",
    "[FORMULES",
    "[IMAGES",
    "[TABLEAU",
    "Qwen",
    "Qwen2",
    "GPU-Intel",
    "VL-7B",
    "FormulaDomain",
    "LaTeX:",
    "Domaine:",
    "Confiance:",
    "Explication:",
    "TYPE:",
    "COMPOSANTS:",
    "COMPOSANTES:",
    "FLUX ET INTERACTION",
]


DOC_TYPE_ARTIFACTS = {
    "pptx": {
        "SLIDE", "SLIDES", "NOTE", "NOTES", "PRÉSENTATEUR", "PRESENTATEUR",
        "PRESENTATION", "PRÉSENTATION", "SOUTENANCE", "MASTER",
        "MERCI", "POUR", "VOTRE", "ATTENTION",
        "SOMMAIRE", "CONCLUSION", "OBJECTIF", "OBJECTIFS",
        "METHODOLOGIE", "MÉTHODOLOGIE",
        "RESULTAT", "RÉSULTAT", "RESULTATS", "RÉSULTATS",
        "TABLEAU", "TABLEAUX", "DONNEES", "DONNÉES", "TABULAIRES",
    },
    "docx": {
        "FORMULE", "FORMULES", "OMML", "LATEX", "DOMAINE", "CONFIANCE",
        "EXPLICATION", "PHYSIQUE", "MECANIQUE", "MÉCANIQUE", "CHIMIE",
    },
    "pdf": {
        "FORMULE", "FORMULES", "LATEX", "DOMAINE", "CONFIANCE",
        "EXPLICATION", "PHYSIQUE", "MECANIQUE", "MÉCANIQUE", "CHIMIE",
    },
    "email": {
        "FROM", "TO", "CC", "BCC", "RE", "FW", "FWD",
        "INBOX", "SENT", "DRAFT", "REPLY", "FORWARD",
        "SUBJECT", "DATE", "MIME", "SMTP",
    },
    "excel": {
        "TOTAL", "SOUS-TOTAL", "SOMME", "SUM", "AVG", "MAX", "MIN",
        "SHEET", "TAB", "CELL", "ROW", "COL", "REF",
        "TRUE", "FALSE", "NULL", "N/A", "NA",
    },
}


RND_CONTEXT_MARKERS = [
    "verrou", "incertitude", "limitation", "complexité", "problème",
    "difficulté", "risque", "modèle", "algorithme", "capteur",
    "mesure", "simulation", "prototype", "démonstrateur", "performance",
    "précision", "validation", "expérimentation", "optimisation",
    "état de l'art", "recherche", "r&d", "cir",
    "données", "résultat", "résultats", "erreur", "erreurs",
    "modélisation", "fusion", "trajectoire", "solveur", "graphe",
    "matériau", "matériaux", "thermique", "vibration", "mécanique",
    "chimique", "biologique", "logiciel", "électronique",
]


TRAILING_BAD_ENDINGS = [
    r"\s+d$",
    r"\s+d'$",
    r"\s+de$",
    r"\s+du$",
    r"\s+des$",
    r"\s+de\s+l$",
    r"\s+de\s+l'$",
    r"\s+à$",
    r"\s+par$",
    r"\s+pour$",
    r"\s+avec$",
    r"\s+sans$",
    r"\s+sur$",
    r"\s+dans$",
]


PHRASE_LIKE_MARKERS = {
    "devra", "doit", "peut", "peuvent", "permet", "permettent",
    "consiste", "réclame", "réclament", "montre", "montrent",
    "fournit", "fournissent", "repose", "reposent",
    "dépend", "dépendre", "obtenir", "atteindre",
    "assurée", "assuré", "décomposer", "conditionnée",
    "utilisant", "conduisant", "créant", "produisant",
    "améliorer", "réduire", "augmenter", "identifier",
    "analyser", "déterminer", "évaluer", "valider",
    "visons", "vise", "visent",
}


BAD_START_PATTERNS = [
    r"^exemple\s+de\b",
    r"^illustration\s+des?\b",
    r"^figure\s+\d*\b",
    r"^objet\s+des?\b",
    r"^augmentation\s+du\b",
    r"^identification\s+des?\b",
    r"^ensemble\s+des?\b",
    r"^effet\s+des?\b",
    r"^nature\s+des?\b",
    r"^fournissant\s+des?\b",
    r"^utilisation\s+des?\b",
    r"^mise\s+en\b",
    r"^point\s+de\b",
    r"^partie\s+de\b",
    r"^type\s+de\b",
    r"^cas\s+de\b",
    r"^fois\s+de\b",
    r"^valeur\s+de\b",
    r"^objet\s+de\b",
    r"^travaux\s+de\b",
    r"^nos\s+travaux\b",
    r"^calcul\s+de\b",
    r"^calcul\s+des\b",
    r"^calcul\s+du\b",

    # artefacts structurels
    r"^slide\b",
    r"^notes?\b",
    r"^formules?\b",
    r"^latex\b",
    r"^domaine\b",
    r"^confiance\b",
    r"^explication\b",
    r"^tableau\b",
    r"^image\b",
    r"^qualit[ée]\b",
]


BAD_FIRST_WORDS = {
    "exemple", "illustration", "figure", "objet", "augmentation",
    "identification", "ensemble", "effet", "nature", "fournissant",
    "utilisation", "partie", "type", "cas", "fois", "valeur",
    "remarque", "contexte", "détails", "details", "travaux",
    "calcul",

    # artefacts structurels
    "slide", "slides", "note", "notes", "formule", "formules",
    "latex", "domaine", "confiance", "explication", "tableau",
    "image", "qualité", "qualite", "présentation", "presentation",
    "soutenance",
}


TECH_RELATION_PATTERNS = [
    r"\b[a-zA-ZÀ-ÿ0-9\-]{4,}\s+(?:de|du|des|d')\s+[a-zA-ZÀ-ÿ0-9\-]{4,}(?:\s+[a-zA-ZÀ-ÿ0-9\-]{4,}){0,2}",
    r"\b[a-zA-ZÀ-ÿ0-9\-]{5,}\s+[a-zA-ZÀ-ÿ0-9\-]{5,}(?:\s+[a-zA-ZÀ-ÿ0-9\-]{5,}){0,1}",
]


PROJECT_KEYWORD_PATTERNS = [
    r"\b[a-zA-ZÀ-ÿ0-9\-]{4,}\s+(?:de|du|des|d')\s+[a-zA-ZÀ-ÿ0-9\-]{4,}(?:\s+[a-zA-ZÀ-ÿ0-9\-]{4,}){0,2}",
    r"\b[a-zA-ZÀ-ÿ0-9\-]{5,}\s+[a-zA-ZÀ-ÿ0-9\-]{5,}\b",
    r"[\"“”']([^\"“”']{4,60})[\"“”']",
]


WEAK_KEYWORD_HEADS = {
    "exemple", "figure", "illustration", "objet", "augmentation",
    "identification", "ensemble", "effet", "nature", "utilisation",
    "calcul", "travaux", "contexte", "remarque", "partie",
    "fonction", "fonctions", "informations", "information",
    "slide", "note", "formule", "tableau", "image",
}


PROJECT_KEYWORD_STRONG_MARKERS = {
    "fusion", "slam", "graphe", "graphes", "facteurs",
    "modèle", "modèles", "erreur", "incertitude",
    "brutes", "pseudo", "pseudo-distances",
    "cartographie", "localisation", "centimétrique",
    "précision", "solveur", "capteurs", "multi-capteurs",
    "données", "trajectoire", "navigation",
    "thermique", "vibration", "contrainte", "déformation",
    "matériau", "matériaux", "alliage", "simulation",
    "transmission", "mécanique", "fréquence", "amortissement",
}


PROJECT_KEYWORD_CANDIDATE_HEADS = {
    "erreur", "mesure", "accumulation", "boîte", "trois",
    "aide", "capable", "capteur", "brutes",
}


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class RagCandidate:
    text: str
    type: str
    confidence: float = 0.5
    frequency: int = 1
    score: float = 0.0
    source: str = "ranker"
    original_text: Optional[str] = None
    context: Optional[str] = None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self, debug: bool = True) -> dict:
        base = {
            "text": self.text,
            "type": self.type,
            "confidence": round(float(self.confidence), 3),
            "frequency": self.frequency,
            "score": round(float(self.score), 3),
            "source": self.source,
        }

        if debug:
            base.update(
                {
                    "original_text": self.original_text,
                    "context": self.context,
                    "reasons": self.reasons,
                }
            )

        return base


@dataclass
class RagRankerResult:
    technologies: list[RagCandidate] = field(default_factory=list)
    mots_cles_projet_high_confidence: list[RagCandidate] = field(default_factory=list)
    mots_cles_projet_candidates: list[RagCandidate] = field(default_factory=list)
    verrous: list[RagCandidate] = field(default_factory=list)
    domaines: list[RagCandidate] = field(default_factory=list)
    projets_axes: list[RagCandidate] = field(default_factory=list)
    autres: list[RagCandidate] = field(default_factory=list)
    all_candidates_before_filter: int = 0
    all_candidates_after_filter: int = 0

    def to_dict(self, debug: bool = True) -> dict:
        return {
            "entities_for_rag": {
                "TECHNOLOGIE": [c.to_dict(debug=debug) for c in self.technologies],
                "MOT_CLE_PROJET": {
                    "high_confidence": [
                        c.to_dict(debug=debug)
                        for c in self.mots_cles_projet_high_confidence
                    ],
                    "candidates": [
                        c.to_dict(debug=debug)
                        for c in self.mots_cles_projet_candidates
                    ],
                },
                "VERROU_TECH": [c.to_dict(debug=debug) for c in self.verrous],
                "DOMAINE_RD": [c.to_dict(debug=debug) for c in self.domaines],
                "PROJET_AXE": [c.to_dict(debug=debug) for c in self.projets_axes],
                "AUTRES": [c.to_dict(debug=debug) for c in self.autres],
            },
            "stats": {
                "candidates_before_filter": self.all_candidates_before_filter,
                "candidates_after_filter": self.all_candidates_after_filter,
                "mots_cles_high_confidence": len(self.mots_cles_projet_high_confidence),
                "mots_cles_candidates": len(self.mots_cles_projet_candidates),
            },
        }

    def to_rag_ready_dict(self) -> dict:
        return {
            "technologies": [c.text for c in self.technologies[:12]],
            "mots_cles_projet": {
                "high_confidence": [
                    c.text for c in self.mots_cles_projet_high_confidence[:12]
                ],
                "candidates": [
                    c.text for c in self.mots_cles_projet_candidates[:15]
                ],
            },
            "verrous_techniques": [c.text for c in self.verrous[:10]],
            "domaines": [c.text for c in self.domaines[:3]],
            "axes_projet": [c.text for c in self.projets_axes[:5]],
            "autres": [c.text for c in self.autres[:5]],
        }


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS TEXTE
# ══════════════════════════════════════════════════════════════════════════════

def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.strip(" ,.;:()[]{}'\"")


def _normalize_key(text: str) -> str:
    text = _clean_text(text).lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def _is_acronym(text: str) -> bool:
    clean = text.replace("-", "").replace("&", "")
    return clean.isupper() and 2 <= len(clean) <= 12


def _is_short_technology_token(text: str) -> bool:
    clean = _clean_text(text)

    if not clean:
        return False

    if _is_acronym(clean):
        return True

    if len(clean) <= 12 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-+/]{1,11}", clean):
        has_upper = any(c.isupper() for c in clean)
        has_digit_or_symbol = bool(re.search(r"\d|[-+/]", clean))
        return has_upper or has_digit_or_symbol

    return False


def _contains_digit_or_symbol(text: str) -> bool:
    return bool(re.search(r"\d|[+/#&\-]", text))


def _get_attr(entity: Any, name: str, default=None):
    if isinstance(entity, dict):
        return entity.get(name, default)
    return getattr(entity, name, default)


def _get_context(text: str, start: Optional[int], end: Optional[int], window: int = 90) -> str:
    if start is None or end is None:
        return ""

    if start < 0 or end < 0:
        return ""

    s = max(0, start - window)
    e = min(len(text), end + window)
    return re.sub(r"\s+", " ", text[s:e]).strip()


def _doc_type_artifacts(doc_type: str) -> set[str]:
    doc_type = (doc_type or "unknown").lower()
    return DOC_TYPE_ARTIFACTS.get(doc_type, set())


# ══════════════════════════════════════════════════════════════════════════════
# FILTRES GÉNÉRIQUES
# ══════════════════════════════════════════════════════════════════════════════

def has_incomplete_ending(text: str) -> bool:
    text = _clean_text(text).lower()

    if not text:
        return True

    for pattern in TRAILING_BAD_ENDINGS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True

    return False


def remove_incomplete_endings(text: str) -> str:
    text = _clean_text(text).replace("’", "'")

    changed = True

    while changed:
        changed = False

        for pattern in TRAILING_BAD_ENDINGS:
            new_text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()

            if new_text != text:
                text = new_text
                changed = True

    return _clean_text(text)


def looks_like_sentence(text: str) -> bool:
    text = _clean_text(text).lower()
    words = re.findall(r"\b[a-zA-ZÀ-ÿ']+\b", text)

    if len(words) > 6:
        return True

    if set(words).intersection(PHRASE_LIKE_MARKERS):
        return True

    function_words = {
        "qui", "que", "dont", "où", "avec", "sans", "pour",
        "dans", "sur", "par", "vers", "entre", "lorsque",
        "quand", "comme", "afin", "alors", "mais", "donc",
    }

    count_function_words = sum(1 for w in words if w in function_words)

    if count_function_words >= 2 and len(words) >= 5:
        return True

    return False


def has_bad_start(text: str) -> bool:
    text = _clean_text(text).lower()

    if not text:
        return True

    for pattern in BAD_START_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True

    parts = text.split()
    first = parts[0] if parts else ""

    return first in BAD_FIRST_WORDS


def has_good_keyword_shape(text: str) -> bool:
    text = _clean_text(text)

    if not text:
        return False

    words = text.split()
    n = len(words)

    if _is_acronym(text):
        return True

    if _contains_digit_or_symbol(text) and len(text) >= 3:
        return True

    if n == 1:
        return len(text) >= 8 or _is_short_technology_token(text)

    if 2 <= n <= 5:
        return True

    return False


def post_process_candidate_text(text: str) -> str:
    text = _clean_text(text).replace("’", "'")

    if not text:
        return ""

    text = re.split(r"[.;:\n\r\t]", text)[0]
    text = _clean_text(text)
    text = remove_incomplete_endings(text)
    text = re.sub(r"\s+d'$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s+l'$", "", text, flags=re.IGNORECASE).strip()
    text = _clean_text(text)

    if not text:
        return ""

    if looks_like_sentence(text):
        return ""

    if has_bad_start(text):
        return ""

    if not has_good_keyword_shape(text):
        return ""

    return text


# ══════════════════════════════════════════════════════════════════════════════
# FILTRES ARTEFACTS EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def _is_extraction_artifact(
    text: str,
    context: Optional[str] = None,
    doc_type: str = "unknown",
) -> bool:
    clean = _clean_text(text)

    if not clean:
        return True

    upper = clean.upper()
    doc_artifacts = _doc_type_artifacts(doc_type)

    if upper in EXTRACTION_ARTIFACT_TERMS:
        return True

    if upper in doc_artifacts:
        return True

    if "FORMULADOMAIN" in upper:
        return True

    if re.match(
        r"^(SLIDE|NOTES?|FORMULES?|LATEX|DOMAINE|CONFIANCE|EXPLICATION|TABLEAU|IMAGE|QUALIT[ÉE])\b",
        upper,
    ):
        return True

    if context:
        ctx = context.upper()

        if any(marker.upper() in ctx for marker in EXTRACTION_CONTEXT_MARKERS):
            if upper in EXTRACTION_ARTIFACT_TERMS or upper in doc_artifacts:
                return True

            if re.match(
                r"^(VL-\d+B|QWEN\d*|GPU|CPU|PAGE|IMAGE|SECTION|SLIDE|NOTES?|FORMULES?)$",
                upper,
            ):
                return True

    return False


def _is_allowed_acronym(
    text: str,
    context: Optional[str] = None,
    doc_type: str = "unknown",
) -> bool:
    clean = _clean_text(text)
    upper = clean.upper()

    if _is_extraction_artifact(clean, context=context, doc_type=doc_type):
        return False

    if len(upper) > 12:
        return False

    ctx = (context or "").lower()

    if any(marker in ctx for marker in RND_CONTEXT_MARKERS):
        return True

    if re.match(r"^[A-Z]{2,8}$", upper):
        return True

    if re.match(r"^[A-Z]{2,6}[-/][A-Z0-9]{1,6}$", upper):
        return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# EXPANSION CONTEXTUELLE
# ══════════════════════════════════════════════════════════════════════════════

def expand_entity_contextually(
    entity: Any,
    full_text: str,
    window: int = 90,
    doc_type: str = "unknown",
) -> tuple[str, str]:
    raw = _clean_text(_get_attr(entity, "text", ""))
    entity_type = _get_attr(entity, "type", "")
    start = _get_attr(entity, "start", None)
    end = _get_attr(entity, "end", None)

    if not raw:
        return raw, ""

    context = _get_context(full_text, start, end, window=window)

    if not context:
        match = re.search(re.escape(raw), full_text, flags=re.IGNORECASE)
        if match:
            context = _get_context(full_text, match.start(), match.end(), window=window)

    if _is_extraction_artifact(raw, context=context, doc_type=doc_type):
        return "", context

    if entity_type == "TECHNOLOGIE" and _is_short_technology_token(raw):
        clean_raw = post_process_candidate_text(raw)

        if _is_extraction_artifact(clean_raw, context=context, doc_type=doc_type):
            return "", context

        return clean_raw, context

    if _word_count(raw) >= 2 and not has_bad_start(raw) and not looks_like_sentence(raw):
        clean_raw = post_process_candidate_text(raw)

        if clean_raw and not _is_extraction_artifact(clean_raw, context=context, doc_type=doc_type):
            return clean_raw, context

    if not context:
        clean_raw = post_process_candidate_text(raw)

        if _is_extraction_artifact(clean_raw, context="", doc_type=doc_type):
            return "", ""

        return clean_raw, ""

    raw_escaped = re.escape(raw)

    centered_patterns = [
        rf"([a-zA-ZÀ-ÿ0-9\-]{{4,}}\s+(?:de|du|des|d')\s+[a-zA-ZÀ-ÿ0-9\-\s]{{0,35}}{raw_escaped}[a-zA-ZÀ-ÿ0-9\-\s]{{0,15}})",
        rf"({raw_escaped}\s+(?:de|du|des|d')\s+[a-zA-ZÀ-ÿ0-9\-\s]{{4,40}})",
        rf"([a-zA-ZÀ-ÿ0-9\-]{{4,}}\s+{raw_escaped}\s+[a-zA-ZÀ-ÿ0-9\-]{{4,}})",
    ]

    best = raw

    for pattern in centered_patterns:
        match = re.search(pattern, context, flags=re.IGNORECASE)

        if not match:
            continue

        candidate = post_process_candidate_text(match.group(1))

        if candidate and 2 <= _word_count(candidate) <= 5:
            if not has_bad_start(candidate):
                best = candidate
                break

    clean_best = post_process_candidate_text(best)

    if not clean_best:
        clean_best = post_process_candidate_text(raw)

    if _is_extraction_artifact(clean_best, context=context, doc_type=doc_type):
        return "", context

    return clean_best, context


# ══════════════════════════════════════════════════════════════════════════════
# EXTRACTION CANDIDATS DEPUIS TEXTE
# ══════════════════════════════════════════════════════════════════════════════

def extract_relation_candidates_from_text(
    chunks: list[str],
    doc_type: str = "unknown",
) -> list[RagCandidate]:
    candidates: list[RagCandidate] = []

    for chunk in chunks:
        text = re.sub(r"\[[^\]]+\]", " ", chunk)
        text = re.sub(r"\s+", " ", text)

        for pattern in TECH_RELATION_PATTERNS:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                candidate_text = post_process_candidate_text(match.group(0))
                context = _get_context(text, match.start(), match.end(), window=70)

                if not candidate_text:
                    continue

                if _is_extraction_artifact(candidate_text, context=context, doc_type=doc_type):
                    continue

                if has_bad_start(candidate_text):
                    continue

                if 2 <= _word_count(candidate_text) <= 5:
                    candidates.append(
                        RagCandidate(
                            text=candidate_text,
                            type="EXPRESSION_TECHNIQUE",
                            confidence=0.55,
                            source="relation_pattern",
                            context=context,
                            reasons=["relation_technique"],
                        )
                    )

    return candidates


def extract_project_keywords_from_text(
    chunks: list[str],
    doc_type: str = "unknown",
) -> list[RagCandidate]:
    candidates: list[RagCandidate] = []

    full_text = "\n".join(chunks)
    clean_text = re.sub(r"\[[^\]]+\]", " ", full_text)
    clean_text = re.sub(r"\s+", " ", clean_text)

    for pattern in PROJECT_KEYWORD_PATTERNS:
        for match in re.finditer(pattern, clean_text, flags=re.IGNORECASE):
            raw = match.group(1) if match.lastindex else match.group(0)
            candidate_text = post_process_candidate_text(raw)
            context = _get_context(clean_text, match.start(), match.end(), window=90)

            if not candidate_text:
                continue

            if _is_extraction_artifact(candidate_text, context=context, doc_type=doc_type):
                continue

            if has_bad_start(candidate_text):
                continue

            if looks_like_sentence(candidate_text):
                continue

            words = _word_count(candidate_text)

            if not (2 <= words <= 5):
                continue

            first_word = candidate_text.split()[0].lower()

            if first_word in WEAK_KEYWORD_HEADS:
                continue

            ctx_low = context.lower()
            has_rnd_context = any(marker in ctx_low for marker in RND_CONTEXT_MARKERS)

            shape_bonus = False

            if re.search(r"\b(de|du|des|d')\b", candidate_text.lower()):
                shape_bonus = True

            if "-" in candidate_text:
                shape_bonus = True

            if re.search(r"\b[A-Z]{2,8}\b", context):
                shape_bonus = True

            frequency = len(
                re.findall(
                    re.escape(candidate_text),
                    clean_text,
                    flags=re.IGNORECASE,
                )
            )

            if not has_rnd_context and frequency < 2 and not shape_bonus:
                continue

            candidates.append(
                RagCandidate(
                    text=candidate_text,
                    type="MOT_CLE_PROJET",
                    confidence=0.62,
                    frequency=max(1, frequency),
                    source="keyword_extractor",
                    context=context,
                    reasons=["project_keyword_extractor"],
                )
            )

    return candidates


def extract_uppercase_acronyms(
    chunks: list[str],
    doc_type: str = "unknown",
) -> list[RagCandidate]:
    candidates: list[RagCandidate] = []
    pattern = re.compile(r"\b[A-Z][A-Z0-9]{1,12}(?:[-/][A-Z0-9]{1,8})?\b")

    for chunk in chunks:
        for match in pattern.finditer(chunk):
            text = match.group(0).strip()
            context = _get_context(chunk, match.start(), match.end(), window=70)

            if not _is_allowed_acronym(text, context=context, doc_type=doc_type):
                continue

            candidate_text = post_process_candidate_text(text)

            if not candidate_text:
                continue

            if _is_extraction_artifact(candidate_text, context=context, doc_type=doc_type):
                continue

            candidates.append(
                RagCandidate(
                    text=candidate_text,
                    type="TECHNOLOGIE",
                    confidence=0.6,
                    source="acronym",
                    context=context,
                    reasons=["acronyme_technique"],
                )
            )

    return candidates


# ══════════════════════════════════════════════════════════════════════════════
# SCORING
# ══════════════════════════════════════════════════════════════════════════════

def score_candidate(
    candidate: RagCandidate,
    doc_type: str = "unknown",
) -> RagCandidate:
    text = post_process_candidate_text(candidate.text)
    words = _word_count(text)
    context = candidate.context or ""

    candidate.text = text

    if not text:
        candidate.score = -999
        candidate.reasons.append("empty_after_post_process")
        return candidate

    if has_incomplete_ending(text):
        candidate.score = -999
        candidate.reasons.append("incomplete_ending")
        return candidate

    if looks_like_sentence(text):
        candidate.score = -999
        candidate.reasons.append("sentence_like_candidate")
        return candidate

    if has_bad_start(text):
        candidate.score = -999
        candidate.reasons.append("bad_start_candidate")
        return candidate

    if not has_good_keyword_shape(text):
        candidate.score = -999
        candidate.reasons.append("bad_keyword_shape")
        return candidate

    if _is_extraction_artifact(text, context=context, doc_type=doc_type):
        candidate.score = -999
        candidate.reasons.append("rejected_extraction_artifact")
        return candidate

    score = 0.0
    reasons = list(candidate.reasons)

    score += candidate.confidence * 2.0
    reasons.append("confidence")

    type_weight = TYPE_WEIGHTS.get(candidate.type, 1.3)
    score += type_weight
    reasons.append(f"type_weight:{candidate.type}")

    if words >= 2:
        score += 2.0
        reasons.append("multi_word")

    if words >= 3:
        score += 1.0
        reasons.append("long_expression")

    if _is_acronym(text):
        if _is_allowed_acronym(text, context=context, doc_type=doc_type):
            score += 1.8
            reasons.append("acronym")
        else:
            score -= 5.0
            reasons.append("bad_acronym")

    if _contains_digit_or_symbol(text):
        score += 0.5
        reasons.append("digit_or_symbol")

    if candidate.frequency >= 2:
        score += min(candidate.frequency * 0.35, 1.8)
        reasons.append("frequency")

    ctx = context.lower()

    if any(marker in ctx for marker in RND_CONTEXT_MARKERS):
        score += 1.2
        reasons.append("rnd_context")

    if words == 1 and not _is_acronym(text):
        score -= 1.8
        reasons.append("single_word_penalty")

    if len(text) < 5 and not _is_acronym(text):
        score -= 1.5
        reasons.append("too_short_penalty")

    if words == 1 and candidate.confidence < 0.45 and not _is_acronym(text):
        score -= 1.2
        reasons.append("low_conf_single_word")

    if candidate.type in {"EXPRESSION_TECHNIQUE", "MOT_CLE_PROJET"}:
        if candidate.frequency < 2 and score < PROJECT_KEYWORD_CANDIDATE_MIN_SCORE:
            candidate.score = -999
            candidate.reasons = reasons + ["low_freq_keyword_rejected"]
            return candidate

    candidate.score = score
    candidate.reasons = reasons

    return candidate


# ══════════════════════════════════════════════════════════════════════════════
# DÉDUPLICATION
# ══════════════════════════════════════════════════════════════════════════════

def deduplicate_candidates(candidates: list[RagCandidate]) -> list[RagCandidate]:
    grouped: dict[str, RagCandidate] = {}

    for candidate in candidates:
        candidate.text = post_process_candidate_text(candidate.text)

        if not candidate.text:
            continue

        key = _normalize_key(candidate.text)

        if not key:
            continue

        if key not in grouped:
            grouped[key] = candidate
        else:
            existing = grouped[key]
            existing.frequency += candidate.frequency
            existing.confidence = max(existing.confidence, candidate.confidence)

            if candidate.score > existing.score:
                existing.score = candidate.score
                existing.context = candidate.context or existing.context
                existing.source = candidate.source
                existing.original_text = candidate.original_text or existing.original_text
                existing.reasons = list(set(existing.reasons + candidate.reasons))

    return list(grouped.values())


def remove_nested_weaker_candidates(candidates: list[RagCandidate]) -> list[RagCandidate]:
    sorted_candidates = sorted(candidates, key=lambda c: (-c.score, -_word_count(c.text)))
    kept: list[RagCandidate] = []

    for candidate in sorted_candidates:
        c_key = _normalize_key(candidate.text)
        c_words = _word_count(c_key)
        is_nested = False

        for selected in kept:
            s_key = _normalize_key(selected.text)

            if c_words == 1 and c_key in s_key and selected.score >= candidate.score:
                is_nested = True
                break

            if c_key != s_key and (c_key in s_key or s_key in c_key):
                if selected.score >= candidate.score and abs(len(s_key) - len(c_key)) < 15:
                    is_nested = True
                    break

        if not is_nested:
            kept.append(candidate)

    return kept


def split_project_keyword(candidate: RagCandidate) -> str:
    score = candidate.score
    frequency = candidate.frequency
    context = (candidate.context or "").lower()
    text = _normalize_key(candidate.text)

    words = text.split()
    first = words[0] if words else ""

    has_rnd_context = any(marker in context for marker in RND_CONTEXT_MARKERS)
    has_strong_marker = any(marker in text for marker in PROJECT_KEYWORD_STRONG_MARKERS)

    if first in PROJECT_KEYWORD_CANDIDATE_HEADS:
        if score >= 9.2 and frequency >= 5 and has_strong_marker:
            return "high_confidence"
        return "candidates"

    if score >= PROJECT_KEYWORD_HIGH_CONFIDENCE_SCORE and has_strong_marker:
        return "high_confidence"

    if frequency >= 4 and has_rnd_context and has_strong_marker:
        return "high_confidence"

    if score >= PROJECT_KEYWORD_CANDIDATE_MIN_SCORE:
        return "candidates"

    return "candidates"


# ══════════════════════════════════════════════════════════════════════════════
# POINT D’ENTRÉE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def rank_entities_for_rag(
    chunks: list[str],
    ner_entities: list[Any],
    domain_name: Optional[str] = None,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = MIN_RAG_SCORE,
    doc_type: str = "unknown",
) -> RagRankerResult:
    """
    Prépare les métadonnées NLP pour le futur RAG.

    doc_type est important :
      - pptx  : filtre SLIDE / NOTES / PRESENTATION / etc.
      - docx  : filtre FORMULES / OMML / etc.
      - pdf   : filtre formules / pages / artefacts
      - email : filtre FROM / TO / SUBJECT...
      - excel : filtre TOTAL / CELL / ROW...
    """
    doc_type = (doc_type or "unknown").lower()
    full_text = "\n".join(chunks)
    candidates: list[RagCandidate] = []

    # 1. Candidats venant de GLiNER / NER.
    for entity in ner_entities:
        raw_text = _clean_text(_get_attr(entity, "text", ""))
        entity_type = _get_attr(entity, "type", "UNKNOWN")
        confidence = float(_get_attr(entity, "confidence", 0.5) or 0.5)
        source = _get_attr(entity, "source", "ner")

        if not raw_text:
            continue

        if _is_extraction_artifact(raw_text, context="", doc_type=doc_type):
            continue

        if entity_type == "DOMAINE_RD" and raw_text.upper() in {
            "R&D",
            "RD",
            "RECHERCHE ET DÉVELOPPEMENT",
        }:
            continue

        expanded_text, context = expand_entity_contextually(
            entity,
            full_text,
            doc_type=doc_type,
        )

        if not expanded_text:
            continue

        if _is_extraction_artifact(expanded_text, context=context, doc_type=doc_type):
            continue

        candidates.append(
            RagCandidate(
                text=expanded_text,
                type=entity_type,
                confidence=confidence,
                frequency=1,
                source=source,
                original_text=raw_text,
                context=context,
                reasons=[
                    "ner_candidate",
                    "context_expansion" if expanded_text != raw_text else "no_expansion",
                ],
            )
        )

    # 2. Expressions candidates extraites du texte.
    candidates.extend(
        extract_relation_candidates_from_text(
            chunks,
            doc_type=doc_type,
        )
    )

    # 3. Mots-clés projet extraits du texte.
    candidates.extend(
        extract_project_keywords_from_text(
            chunks,
            doc_type=doc_type,
        )
    )

    # 4. Acronymes.
    # Correction importante :
    # il faut passer doc_type ici, sinon les artefacts PPTX/DOCX peuvent passer.
    candidates.extend(
        extract_uppercase_acronyms(
            chunks,
            doc_type=doc_type,
        )
    )

    # 5. Domaine détecté.
    if domain_name and domain_name != "non_classifié":
        candidates.append(
            RagCandidate(
                text=domain_name,
                type="DOMAINE_RD",
                confidence=0.95,
                frequency=1,
                source="domain_detector",
                reasons=["domain_principal"],
            )
        )

    before = len(candidates)

    scored = [score_candidate(c, doc_type=doc_type) for c in candidates]
    scored = [c for c in scored if c.score > -100]

    deduped = deduplicate_candidates(scored)

    rescored = [score_candidate(c, doc_type=doc_type) for c in deduped]

    filtered = [c for c in rescored if c.score >= min_score]

    filtered = [
        c for c in filtered
        if not _is_extraction_artifact(c.text, context=c.context, doc_type=doc_type)
    ]

    filtered = remove_nested_weaker_candidates(filtered)
    filtered.sort(key=lambda c: (-c.score, -c.frequency, c.text.lower()))

    result = RagRankerResult(
        all_candidates_before_filter=before,
        all_candidates_after_filter=len(filtered),
    )

    for c in filtered:
        if c.type == "TECHNOLOGIE":
            result.technologies.append(c)

        elif c.type in {"EXPRESSION_TECHNIQUE", "MOT_CLE_PROJET"}:
            bucket = split_project_keyword(c)

            if bucket == "high_confidence":
                result.mots_cles_projet_high_confidence.append(c)
            else:
                result.mots_cles_projet_candidates.append(c)

        elif c.type == "VERROU_TECH":
            result.verrous.append(c)

        elif c.type == "DOMAINE_RD":
            result.domaines.append(c)

        elif c.type == "PROJET_AXE":
            result.projets_axes.append(c)

        else:
            result.autres.append(c)

    result.technologies = result.technologies[:top_k]
    result.mots_cles_projet_high_confidence = result.mots_cles_projet_high_confidence[:12]
    result.mots_cles_projet_candidates = result.mots_cles_projet_candidates[:15]
    result.verrous = result.verrous[:top_k]
    result.domaines = result.domaines[:5]
    result.projets_axes = result.projets_axes[:10]
    result.autres = result.autres[:10]

    logger.info(
        "NLP RAG preparation : doc_type=%s before=%d after=%d tech=%d kw_high=%d kw_candidates=%d verrous=%d domaines=%d",
        doc_type,
        before,
        len(filtered),
        len(result.technologies),
        len(result.mots_cles_projet_high_confidence),
        len(result.mots_cles_projet_candidates),
        len(result.verrous),
        len(result.domaines),
    )

    return result