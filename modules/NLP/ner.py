"""
modules/nlp/ner_smart.py
──────────────────────────────────────────────────────────────────────────────
NER intelligent pour dossiers R&D / CIR.

Rôle :
  - Extraire les entités nommées et techniques depuis des chunks normalisés.
  - Utiliser GLiNER si disponible.
  - Ajouter des regex fiables pour les éléments CIR simples.
  - Garder une sortie propre et compatible avec test_nlp.py.

Entités ciblées :
  - PERSONNE
  - ORGANISME
  - LIEU
  - DATE_PERIODE
  - TECHNOLOGIE
  - VERROU_TECH
  - DOMAINE_RD
  - PROJET_AXE
  - MATERIAU
  - EQUIPEMENT
  - COMPOSANT_TECHNIQUE
  - METHODE_RD
  - INDICATEUR_CIR
  - MONTANT_CIR
  - ETP
  - JALON
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

DEFAULT_GLINER_MODEL = "urchade/gliner_multi-v2.1"

GLINER_LABELS = [
    # Entités classiques
    "personne",
    "organisation",
    "entreprise",
    "laboratoire",
    "université",
    "lieu",
    "date",
    "période",

    # R&D / CIR — LABELS SPÉCIFIQUES
    "technologie innovante",
    "technologie logicielle",
    "technologie matérielle",
    "procédé technique",

    "verrou technologique",
    "problème technique non résolu",
    "incertitude technique",

    "domaine de recherche appliquée",
    "champ d'application R&D",

    "projet de recherche",
    "axe de recherche",
    "objectif de recherche",
    "résultat de recherche",
    "livrable",

    # Technique — SPÉCIFIQUE
    "matériau innovant",
    "équipement de mesure",
    "capteur",
    "composant technique",
    "méthode expérimentale",
    "algorithme innovant",
    "modèle numérique",
    "logiciel de simulation",
    "framework logiciel",
    "protocole expérimental",

    # CIR / administratif
    "montant",
    "ETP",
    "jalon",
    "brevet",
    "partenaire",
]



LABEL_MAPPING = {
    "personne": "PERSONNE",
    "organisation": "ORGANISME",
    "entreprise": "ORGANISME",
    "laboratoire": "ORGANISME",
    "université": "ORGANISME",
    "lieu": "LIEU",
    "date": "DATE_PERIODE",
    "période": "DATE_PERIODE",

    # R&D / CIR — labels spécifiques GLiNER
    "technologie innovante": "TECHNOLOGIE",
    "technologie logicielle": "TECHNOLOGIE",
    "technologie matérielle": "TECHNOLOGIE",
    "procédé technique": "TECHNOLOGIE",

    "verrou technologique": "VERROU_TECH",
    "problème technique non résolu": "VERROU_TECH",
    "incertitude technique": "VERROU_TECH",

    "domaine de recherche appliquée": "DOMAINE_RD",
    "champ d'application R&D": "DOMAINE_RD",

    "projet de recherche": "PROJET_AXE",
    "axe de recherche": "PROJET_AXE",
    "objectif de recherche": "OBJECTIF_RD",
    "résultat de recherche": "RESULTAT_RD",
    "livrable": "LIVRABLE",

    # Technique — labels spécifiques GLiNER
    "matériau innovant": "MATERIAU",
    "équipement de mesure": "EQUIPEMENT",
    "capteur": "EQUIPEMENT",
    "composant technique": "COMPOSANT_TECHNIQUE",
    "méthode expérimentale": "METHODE_RD",
    "algorithme innovant": "METHODE_RD",
    "modèle numérique": "METHODE_RD",
    "logiciel de simulation": "TECHNOLOGIE",
    "framework logiciel": "TECHNOLOGIE",
    "protocole expérimental": "METHODE_RD",

    # Compatibilité avec anciens labels éventuels
    "technologie": "TECHNOLOGIE",
    "problème technique": "VERROU_TECH",
    "domaine scientifique": "DOMAINE_RD",
    "domaine technologique": "DOMAINE_RD",
    "matériau": "MATERIAU",
    "équipement": "EQUIPEMENT",
    "méthode scientifique": "METHODE_RD",
    "méthode technique": "METHODE_RD",
    "algorithme": "METHODE_RD",
    "modèle": "METHODE_RD",
    "logiciel": "TECHNOLOGIE",
    "framework": "TECHNOLOGIE",
    "protocole": "METHODE_RD",

    "montant": "MONTANT_CIR",
    "ETP": "ETP",
    "jalon": "JALON",
    "brevet": "BREVET",
    "partenaire": "PARTENAIRE_RD",
}



# Seuils par type.
# Les types techniques sont parfois détectés avec une confiance moyenne,
# donc on ne met pas tout à 0.6.
MIN_CONFIDENCE_BY_TYPE = {
    "PERSONNE": 0.45,
    "ORGANISME": 0.45,
    "LIEU": 0.45,
    "DATE_PERIODE": 0.35,

    "TECHNOLOGIE": 0.30,
    "VERROU_TECH": 0.30,
    "DOMAINE_RD": 0.30,
    "PROJET_AXE": 0.35,
    "OBJECTIF_RD": 0.35,
    "RESULTAT_RD": 0.35,
    "LIVRABLE": 0.35,

    "MATERIAU": 0.35,
    "EQUIPEMENT": 0.30,
    "COMPOSANT_TECHNIQUE": 0.35,
    "METHODE_RD": 0.30,

    "MONTANT_CIR": 0.20,
    "ETP": 0.20,
    "JALON": 0.20,
    "BREVET": 0.30,
    "PARTENAIRE_RD": 0.30,
}


# Mots génériques que GLiNER peut parfois mal classer.
GENERIC_FALSE_POSITIVES = {
    "porteur",
    "distance",
    "orientation",
    "position",
    "mesure",
    "mesures",
    "technologies",
    "tecnologies",
    "protocoles",
    "frameworks",
    "type",
    "section",
    "image",
    "page",
    "qualité",
    "qualite",
    "composants",
    "composantes",
    "flux",
    "description",
}


# Si un terme générique est détecté comme VERROU_TECH ou TECHNOLOGIE seul,
# on le rejette. Mais on garde les expressions complètes :
# "erreur de mesure de distance", "erreur de positionnement", etc.
GENERIC_SINGLE_WORD_TYPES = {
    "TECHNOLOGIE",
    "VERROU_TECH",
    "METHODE_RD",
    "EQUIPEMENT",
    "COMPOSANT_TECHNIQUE",
}


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Entity:
    text: str
    type: str
    start: int
    end: int
    confidence: float
    source: str = "unknown"
    chunk_index: Optional[int] = None
    chunk_source: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "type": self.type,
            "start": self.start,
            "end": self.end,
            "confidence": round(float(self.confidence), 3),
            "source": self.source,
            "chunk_index": self.chunk_index,
            "chunk_source": self.chunk_source,
        }


@dataclass
class ChunkNERResult:
    chunk_index: int
    entities: list[Entity] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chunk_index": self.chunk_index,
            "entities": [e.to_dict() for e in self.entities],
        }


@dataclass
class BatchNERResult:
    results: list[ChunkNERResult] = field(default_factory=list)
    backend_stats: dict[str, int] = field(default_factory=dict)
    total_entities: int = 0

    def to_dict(self) -> dict:
        return {
            "results": [r.to_dict() for r in self.results],
            "backend_stats": self.backend_stats,
            "total_entities": self.total_entities,
        }


# ══════════════════════════════════════════════════════════════════════════════
# GLiNER LOADING
# ══════════════════════════════════════════════════════════════════════════════

_GLINER_MODEL = None


def load_gliner_model(model_name: str = DEFAULT_GLINER_MODEL):
    global _GLINER_MODEL

    if _GLINER_MODEL is not None:
        return _GLINER_MODEL

    try:
        from gliner import GLiNER

        logger.info("Chargement GLiNER : %s", model_name)
        _GLINER_MODEL = GLiNER.from_pretrained(model_name)
        return _GLINER_MODEL

    except Exception as exc:
        logger.warning("GLiNER indisponible : %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _clean_entity_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.strip(" ,.;:()[]{}'\"")


def _is_structural_noise(text: str, entity_type: str) -> bool:
    """
    Rejette les artefacts structurels que GLiNER a pu attraper.

    À appeler après extraction GLiNER et avant ajout à la liste.
    """
    cleaned = _clean_entity_text(text)

    if not cleaned:
        return True

    upper = cleaned.upper()

    # Artefacts d'extraction / balises internes
    structural_noise = {
        "SLIDE", "SLIDES", "NOTE", "NOTES",
        "FORMULE", "FORMULES", "PHYSIQUE", "CHIMIE", "MECANIQUE", "MÉCANIQUE",
        "LATEX", "OMML", "DOMAINE", "CONFIANCE", "EXPLICATION",
        "PRESENTATION", "PRÉSENTATION", "SOUTENANCE",
        "IMAGE", "IMAGES", "PAGE", "SECTION",
        "TABLEAU", "TABLEAUX", "DONNEES", "DONNÉES",
        "QUALITÉ", "QUALITE", "TYPE", "GPU", "CPU", "QWEN",
        "FORMULADOMAIN", "INCONNU",
    }

    if upper in structural_noise:
        return True

    # Préfixes structurels
    if upper.startswith((
        "LATEX:",
        "DOMAINE:",
        "CONFIANCE:",
        "EXPLICATION:",
        "SLIDE ",
        "FORMULE ",
    )):
        return True

    # Types sensibles : un domaine générique seul n'est pas une techno / domaine R&D exploitable.
    if entity_type in {"TECHNOLOGIE", "DOMAINE_RD"}:
        if upper in {"PHYSIQUE", "CHIMIE", "MECANIQUE", "MÉCANIQUE", "INFORMATIQUE"}:
            if len(cleaned.split()) == 1:
                return True

    return False


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def _is_visual_chunk(text: str) -> bool:
    text_start = text.strip()[:120].upper()
    return (
        text_start.startswith("[IMAGE")
        or "[QUALITÉ:" in text_start
        or "QWEN" in text_start
        or "GPU-INTEL" in text_start
    )


def _normalize_type(label: str) -> str:
    label = str(label or "").strip()
    return LABEL_MAPPING.get(label, LABEL_MAPPING.get(label.lower(), label.upper()))


def _is_acronym(text: str) -> bool:
    clean = text.replace("-", "").replace("&", "")
    return clean.isupper() and 2 <= len(clean) <= 12


def _looks_like_valid_entity(text: str, entity_type: str) -> bool:
    text_clean = _clean_entity_text(text)

    if not text_clean:
        return False

    if len(text_clean) < 2:
        return False

    low = text_clean.lower()
    words = _word_count(text_clean)

    # Rejet des faux positifs génériques seuls.
    if words == 1 and low in GENERIC_FALSE_POSITIVES and entity_type in GENERIC_SINGLE_WORD_TYPES:
        return False

    # Évite les phrases trop longues.
    if words > 8:
        return False

    # Évite les morceaux de phrase.
    bad_starts = {
        "exemple", "figure", "illustration", "type", "section",
        "qualité", "qualite", "composants", "composantes",
    }

    first = low.split()[0] if low.split() else ""
    if first in bad_starts:
        return False

    return True


def _passes_confidence(entity_type: str, confidence: float, chunk_source: str) -> bool:
    min_conf = MIN_CONFIDENCE_BY_TYPE.get(entity_type, 0.35)

    # Les chunks visuels Qwen sont utiles mais plus bruités.
    # On demande un seuil plus haut pour leurs entités.
    if chunk_source == "visual":
        min_conf += 0.12

    return confidence >= min_conf


def _deduplicate_entities(entities: list[Entity]) -> list[Entity]:
    """
    Fusionne les doublons.
    Si même texte + type, garde la meilleure confiance.
    """
    grouped: dict[tuple[str, str], Entity] = {}

    for entity in entities:
        key = (entity.text.lower(), entity.type)

        if key not in grouped:
            grouped[key] = entity
        else:
            existing = grouped[key]
            if entity.confidence > existing.confidence:
                grouped[key] = entity

    return sorted(
        grouped.values(),
        key=lambda e: (e.start if e.start is not None else 10**9, -e.confidence),
    )


# ══════════════════════════════════════════════════════════════════════════════
# REGEX ENTITIES
# ══════════════════════════════════════════════════════════════════════════════

DATE_RE = re.compile(
    r"\b(?:"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|20\d{2}"
    r"|19\d{2}"
    r"|T[1-4]\s*20\d{2}"
    r"|S[1-2]\s*20\d{2}"
    r"|janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre"
    r")\b",
    re.IGNORECASE,
)

MONTANT_RE = re.compile(
    r"\b(?:"
    r"\d+(?:[.,]\d+)?\s*(?:k€|K€|M€|€|euros?|EUR|MAD|DH|dirhams?)"
    r"|\d{1,3}(?:\s\d{3})+(?:[.,]\d+)?\s*(?:€|euros?|EUR|MAD|DH|dirhams?)"
    r")\b",
    re.IGNORECASE,
)

ETP_RE = re.compile(
    r"\b(?:"
    r"\d+(?:[.,]\d+)?\s*ETP"
    r"|ETP\s*[:=]?\s*\d+(?:[.,]\d+)?"
    r"|équivalent\s+temps\s+plein"
    r"|equivalent\s+temps\s+plein"
    r")\b",
    re.IGNORECASE,
)

JALON_RE = re.compile(
    r"\b(?:"
    r"jalon\s*\d+"
    r"|milestone\s*\d+"
    r"|phase\s*\d+"
    r"|lot\s*\d+"
    r"|tâche\s*\d+"
    r"|tache\s*\d+"
    r"|WP\s*\d+"
    r")\b",
    re.IGNORECASE,
)

BREVET_RE = re.compile(
    r"\b(?:"
    r"brevet"
    r"|dépôt\s+de\s+brevet"
    r"|depot\s+de\s+brevet"
    r"|propriété\s+intellectuelle"
    r"|EP\d{7}"
    r"|WO\d{4}/\d{6}"
    r"|FR\d{7}"
    r"|US\d{7}[A-Z]?"
    r")\b",
    re.IGNORECASE,
)

PROJECT_AXIS_RE = re.compile(
    r"\b(?:"
    r"projet\s+[A-Z][A-Za-z0-9_\- ]{2,40}"
    r"|axe\s+[\"“”']?[^\"“”'\n]{3,50}[\"“”']?"
    r"|CIR\s+[A-Z][A-Za-z0-9_\- ]{1,30}"
    r")\b",
    re.IGNORECASE,
)


def extract_regex_entities(text: str, chunk_index: int, chunk_source: str) -> list[Entity]:
    entities: list[Entity] = []

    regex_specs = [
        (DATE_RE, "DATE_PERIODE", 0.88),
        (MONTANT_RE, "MONTANT_CIR", 0.92),
        (ETP_RE, "ETP", 0.92),
        (JALON_RE, "JALON", 0.90),
        (BREVET_RE, "BREVET", 0.90),
        (PROJECT_AXIS_RE, "PROJET_AXE", 0.90),
    ]

    for pattern, entity_type, confidence in regex_specs:
        for match in pattern.finditer(text):
            ent_text = _clean_entity_text(match.group(0))

            if not ent_text:
                continue

            entities.append(
                Entity(
                    text=ent_text,
                    type=entity_type,
                    start=match.start(),
                    end=match.end(),
                    confidence=confidence,
                    source="regex",
                    chunk_index=chunk_index,
                    chunk_source=chunk_source,
                )
            )

    return entities


# ══════════════════════════════════════════════════════════════════════════════
# GLiNER EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def extract_gliner_entities(
    text: str,
    chunk_index: int,
    chunk_source: str,
    model: Any,
) -> list[Entity]:
    if model is None:
        return []

    entities: list[Entity] = []

    try:
        predictions = model.predict_entities(text, GLINER_LABELS)

        for pred in predictions:
            raw_text = _clean_entity_text(pred.get("text", ""))
            raw_label = pred.get("label", "")
            entity_type = _normalize_type(raw_label)
            confidence = float(pred.get("score", 0.0) or 0.0)

            start = int(pred.get("start", -1))
            end = int(pred.get("end", -1))

            if not raw_text:
                continue

            # Filtre renforcé : évite que GLiNER garde des artefacts
            # comme FORMULES, PHYSIQUE, LaTeX:, Domaine:, etc.
            if _is_structural_noise(raw_text, entity_type):
                continue

            # Baisse douce de confiance pour visuels.
            # Mais on garde quand même dans inspection.
            if chunk_source == "visual":
                confidence *= 0.75

            if not _looks_like_valid_entity(raw_text, entity_type):
                continue

            if not _passes_confidence(entity_type, confidence, chunk_source):
                continue

            entities.append(
                Entity(
                    text=raw_text,
                    type=entity_type,
                    start=start,
                    end=end,
                    confidence=confidence,
                    source="gliner",
                    chunk_index=chunk_index,
                    chunk_source=chunk_source,
                )
            )

    except Exception as exc:
        logger.warning("Erreur GLiNER chunk %s : %s", chunk_index, exc)

    return entities


# ══════════════════════════════════════════════════════════════════════════════
# SPACY OPTIONNEL
# ══════════════════════════════════════════════════════════════════════════════

_SPACY_MODEL = None


def load_spacy_model():
    global _SPACY_MODEL

    if _SPACY_MODEL is not None:
        return _SPACY_MODEL

    try:
        import spacy

        try:
            _SPACY_MODEL = spacy.load("fr_core_news_md")
        except Exception:
            _SPACY_MODEL = spacy.load("fr_core_news_sm")

        return _SPACY_MODEL

    except Exception as exc:
        logger.warning("spaCy indisponible : %s", exc)
        return None


def extract_spacy_entities(
    text: str,
    chunk_index: int,
    chunk_source: str,
    nlp: Any,
) -> list[Entity]:
    if nlp is None:
        return []

    mapping = {
        "PER": "PERSONNE",
        "PERSON": "PERSONNE",
        "ORG": "ORGANISME",
        "LOC": "LIEU",
        "GPE": "LIEU",
        "DATE": "DATE_PERIODE",
    }

    entities: list[Entity] = []

    try:
        doc = nlp(text)

        for ent in doc.ents:
            entity_type = mapping.get(ent.label_)

            if not entity_type:
                continue

            ent_text = _clean_entity_text(ent.text)

            if not ent_text:
                continue

            entities.append(
                Entity(
                    text=ent_text,
                    type=entity_type,
                    start=ent.start_char,
                    end=ent.end_char,
                    confidence=0.70,
                    source="spacy",
                    chunk_index=chunk_index,
                    chunk_source=chunk_source,
                )
            )

    except Exception as exc:
        logger.warning("Erreur spaCy chunk %s : %s", chunk_index, exc)

    return entities


# ══════════════════════════════════════════════════════════════════════════════
# POINTS D’ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def extract_entities(
    text: str,
    chunk_index: int = 0,
    use_gliner: bool = True,
    use_spacy: bool = False,
    use_regex: bool = True,
    chunk_source: Optional[str] = None,
) -> ChunkNERResult:
    """
    Extrait les entités d'un chunk.
    """
    if chunk_source is None:
        chunk_source = "visual" if _is_visual_chunk(text) else "text"

    all_entities: list[Entity] = []

    if use_gliner:
        model = load_gliner_model()
        all_entities.extend(
            extract_gliner_entities(
                text=text,
                chunk_index=chunk_index,
                chunk_source=chunk_source,
                model=model,
            )
        )

    if use_spacy:
        nlp = load_spacy_model()
        all_entities.extend(
            extract_spacy_entities(
                text=text,
                chunk_index=chunk_index,
                chunk_source=chunk_source,
                nlp=nlp,
            )
        )

    if use_regex:
        all_entities.extend(
            extract_regex_entities(
                text=text,
                chunk_index=chunk_index,
                chunk_source=chunk_source,
            )
        )

    all_entities = _deduplicate_entities(all_entities)

    return ChunkNERResult(
        chunk_index=chunk_index,
        entities=all_entities,
    )


def extract_entities_batch(
    chunks: list[str],
    use_gliner: bool = True,
    use_spacy: bool = False,
    use_regex: bool = True,
    chunk_sources: Optional[list[str]] = None,
) -> BatchNERResult:
    """
    Extrait les entités de plusieurs chunks.

    Compatible avec test_nlp.py :
      extract_entities_batch(
          normalized_chunks,
          use_gliner=True,
          use_spacy=False,
          use_regex=True,
      )
    """
    results: list[ChunkNERResult] = []
    backend_stats = {
        "gliner": 0,
        "spacy": 0,
        "regex": 0,
    }

    for i, chunk in enumerate(chunks):
        source = None

        if chunk_sources and i < len(chunk_sources):
            source = chunk_sources[i]

        result = extract_entities(
            text=chunk,
            chunk_index=i,
            use_gliner=use_gliner,
            use_spacy=use_spacy,
            use_regex=use_regex,
            chunk_source=source,
        )

        for entity in result.entities:
            if entity.source in backend_stats:
                backend_stats[entity.source] += 1
            else:
                backend_stats[entity.source] = backend_stats.get(entity.source, 0) + 1

        results.append(result)

    total_entities = sum(len(r.entities) for r in results)

    return BatchNERResult(
        results=results,
        backend_stats=backend_stats,
        total_entities=total_entities,
    )