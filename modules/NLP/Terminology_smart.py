"""
modules/nlp/terminology_smart.py
──────────────────────────────────────────────────────────────────────────────
Terminologie intelligente R&D / CIR.

Ce module reste dans NLP.
Il prend :
  - les chunks normalisés
  - les entités brutes extraites par ner_smart.py
  - le doc_type : pptx, docx, pdf, email, excel...

Et produit une structure métier claire :
  - domaine_principal
  - objectifs_rd
  - resultats_rd
  - livrables
  - depenses_eligibles
  - brevets
  - partenaires_rd
  - personnes
  - organismes
  - lieux
  - dates_periodes
  - materiaux
  - equipements
  - indicateurs_cir
      - etp
      - montants
      - jalons
  - rag_preparation

Important :
  Ce module ne fait pas encore le RAG.
  Il prépare une sortie NLP propre pour les agents et le futur RAG.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    from modules.NLP.rag_ranker import RagRankerResult, rank_entities_for_rag
except Exception:
    from modules.nlp.rag_ranker import RagRankerResult, rank_entities_for_rag

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# DOMAINES TECHNOLOGIQUES
# ══════════════════════════════════════════════════════════════════════════════

DOMAIN_MIN_SCORE = 2

DOMAIN_MARKERS = {
    "intelligence_artificielle": [
        "machine learning", "deep learning", "neural network", "llm", "gpt",
        "transformer", "computer vision", "nlp", "apprentissage", "modèle",
        "réseau de neurones", "ia générative", "classification", "segmentation",
        "vision par ordinateur", "traitement du langage", "embedding",
    ],
    "robotique_navigation": [
        "robotique", "slam", "lidar", "gps", "gnss", "rtk", "navigation",
        "odométrie", "capteur", "trajectoire", "localisation", "cartographie",
        "autonome", "centrale inertielle", "graphe de facteurs",
        "fusion multi-capteurs", "fusion de données", "récepteur gps",
        "antenne gps", "navigation autonome",
    ],
    "mecanique_materiaux": [
        "mécanique", "matériau", "matériaux", "alliage", "composite",
        "polymère", "contrainte", "déformation", "fatigue", "rupture",
        "simulation", "éléments finis", "mef", "impression 3d",
        "usinage", "vibration", "thermomécanique", "young", "poisson",
        "module d'young", "coefficient de poisson",
    ],
    "electronique_signal": [
        "électronique", "signal", "capteur", "microcontrôleur", "fpga",
        "dsp", "filtrage", "acquisition", "embarqué", "firmware",
        "temps réel", "carte électronique", "fréquence", "modulation",
    ],
    "logiciel_informatique": [
        "logiciel", "algorithme", "cloud", "api", "base de données",
        "cybersécurité", "architecture", "gpu", "optimisation",
        "parallélisation", "backend", "frontend", "microservice",
        "pipeline", "framework", "protocole",
    ],
    "chimie_biotech": [
        "chimie", "molécule", "synthèse", "réaction", "catalyse",
        "biotechnologie", "biologie", "protéine", "cellule", "crispr",
        "génomique", "fermentation", "enzyme", "culture cellulaire",
        "pharmacocinétique", "principe actif",
    ],
    "energie_environnement": [
        "énergie", "photovoltaïque", "batterie", "hydrogène",
        "pile à combustible", "stockage", "rendement", "décarbonation",
        "thermique", "co2", "environnement", "recyclage", "électrolyse",
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
# ARTEFACTS STRUCTURELS À NE PAS STRUCTURER
# ══════════════════════════════════════════════════════════════════════════════

STRUCTURAL_ARTIFACTS = {
    "image", "images", "page", "section", "type", "flux",
    "composants", "composantes", "qwen", "gpu", "cpu",
    "qualité", "qualite", "research and development",

    "slide", "slides", "note", "notes",
    "notes présentateur", "notes presentateur",
    "présentation", "presentation", "soutenance",
    "master", "merci", "attention", "sommaire", "conclusion",
    "objectif", "objectifs", "méthodologie", "methodologie",
    "résultat", "resultat", "résultats", "resultats",
    "essai", "essais", "tableau", "tableaux",
    "données", "donnees", "tabulaires",

    "formule", "formules", "formules détectées", "formules detectees",
    "latex", "domaine", "confiance", "explication",
    "physique", "mecanique", "mécanique", "chimie", "inconnu",
    "omml", "llm", "heuristic", "formuladomain",
}

STRUCTURAL_PREFIXES = (
    "latex",
    "domaine",
    "confiance",
    "explication",
    "formuladomain",
    "slide",
    "notes",
    "formules",
    "image",
    "tableau",
    "qualité",
    "qualite",
)


def _is_structural_artifact_text(text: str) -> bool:
    clean = _clean_text(text)
    low = clean.lower()

    if not clean:
        return True

    if low in STRUCTURAL_ARTIFACTS:
        return True

    if any(low.startswith(prefix) for prefix in STRUCTURAL_PREFIXES):
        return True

    if "formuladomain" in low:
        return True

    if re.match(
        r"^(slide|notes?|formules?|latex|domaine|confiance|explication|tableau|image|qualit[ée])\b",
        low,
        flags=re.IGNORECASE,
    ):
        return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# PATTERNS MÉTIER CIR / R&D
# ══════════════════════════════════════════════════════════════════════════════

RESULTAT_RE = re.compile(
    r"\b(?:"
    r"résultat(?:s)?\s+(?:de\s+)?(?:recherche|simulation|essai|test|mesure)"
    r"|démonstrateur|prototype|preuve\s+de\s+concept|POC"
    r"|publication|article\s+scientifique|conférence"
    r"|performance\s+(?:obtenue|mesurée|atteinte)"
    r"|précision\s+(?:centimétrique|millimétrique)"
    r"|taux\s+de\s+(?:réussite|erreur|précision)"
    r"|nous\s+avons\s+(?:obtenu|démontré|validé|atteint|développé)"
    r"|nos\s+travaux\s+ont\s+permis"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

LIVRABLE_RE = re.compile(
    r"\b(?:"
    r"rapport\s+(?:technique|d['’]avancement|final|intermédiaire)"
    r"|livrable"
    r"|milestone"
    r"|deliverable"
    r"|logiciel\s+\w{3,}"
    r"|outil\s+\w{3,}"
    r"|module\s+\w{3,}"
    r"|bibliothèque\s+\w{3,}"
    r"|librairie\s+logicielle"
    r"|base\s+de\s+données\s+(?:de\s+)?(?:mesures|résultats)"
    r"|jeu\s+de\s+données"
    r"|dataset"
    r"|documentation\s+technique"
    r"|cahier\s+des\s+charges"
    r"|prototype"
    r"|démonstrateur"
    r"|preuve\s+de\s+concept"
    r"|POC"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

DEPENSE_RE = re.compile(
    r"\b(?:"
    r"(?:coût|dépense|charge|budget)s?\s+(?:de\s+)?(?:personnel|R&D|recherche)"
    r"|sous[\-\s]traitance"
    r"|prestataire\s+agréé"
    r"|OST\b"
    r"|ETP"
    r"|équivalent\s+temps\s+plein"
    r"|dotation\s+(?:aux\s+)?amortissement"
    r"|frais\s+(?:de\s+)?(?:fonctionnement|personnel|brevets)"
    r"|investissement\s+(?:en\s+)?R&D"
    r"|assiette\s+CIR"
    r"|base\s+de\s+calcul"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

BREVET_RE = re.compile(
    r"\b(?:"
    r"brevet(?:s)?(?:\s+d['’]invention)?"
    r"|dépôt\s+de\s+brevet"
    r"|depot\s+de\s+brevet"
    r"|propriété\s+intellectuelle"
    r"|PI\b"
    r"|IP\b"
    r"|EP\d{7}"
    r"|WO\d{4}/\d{6}"
    r"|FR\d{7}"
    r"|US\d{7}[A-Z]?"
    r"|antériorité"
    r"|état\s+de\s+l['’]art\s+brevet"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

PARTENAIRE_RE = re.compile(
    r"\b(?:"
    r"partenaire(?:s)?\s+(?:de\s+)?(?:recherche|R&D|projet)"
    r"|co[\-\s](?:contractant|développement|financement)"
    r"|laboratoire\s+(?:partenaire|associé|public)"
    r"|université(?:s)?"
    r"|école\s+(?:d['’]ingénieur|polytechnique|normale)"
    r"|CNRS"
    r"|INRIA"
    r"|CEA"
    r"|ONERA"
    r"|INSERM"
    r"|IFPEN"
    r"|accord\s+de\s+(?:collaboration|partenariat|consortium)"
    r"|consortium"
    r"|groupement"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

OBJECTIVE_TRIGGERS = [
    r"notre\s+finalité\s+est\s+de",
    r"la\s+finalité\s+est\s+de",
    r"l['’]objectif\s+est\s+de",
    r"objectif\s*:\s*",
    r"nous\s+visons\s+à",
    r"nous\s+visons\s+",
    r"le\s+but\s+est\s+de",
    r"il\s+s['’]agit\s+de",
    r"l['’]enjeu\s+est\s+de",
]

ETP_RE = re.compile(
    r"\b(?:"
    r"\d+(?:[.,]\d+)?\s*ETP"
    r"|ETP\s*[:=]?\s*\d+(?:[.,]\d+)?"
    r"|équivalent\s+temps\s+plein"
    r"|equivalent\s+temps\s+plein"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

MONTANT_RE = re.compile(
    r"\b(?:"
    r"\d+(?:[.,]\d+)?\s*(?:k€|K€|M€|€|euros?|EUR|MAD|DH|dirhams?)"
    r"|\d{1,3}(?:\s\d{3})+(?:[.,]\d+)?\s*(?:€|euros?|EUR|MAD|DH|dirhams?)"
    r")\b",
    re.IGNORECASE | re.UNICODE,
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
    r"|work\s*package\s*\d+"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

DATE_PERIOD_RE = re.compile(
    r"\b(?:"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|20\d{2}"
    r"|19\d{2}"
    r"|T[1-4]\s*20\d{2}"
    r"|S[1-2]\s*20\d{2}"
    r"|janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TermEntity:
    text: str
    type: str
    confidence: float
    frequency: int = 1
    context: Optional[str] = None
    source: str = "terminology"

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "type": self.type,
            "confidence": round(float(self.confidence), 3),
            "frequency": self.frequency,
            "context": self.context,
            "source": self.source,
        }


@dataclass
class IndicateursCIR:
    etp: list[TermEntity] = field(default_factory=list)
    montants: list[TermEntity] = field(default_factory=list)
    jalons: list[TermEntity] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "etp": [e.to_dict() for e in self.etp],
            "montants": [e.to_dict() for e in self.montants],
            "jalons": [e.to_dict() for e in self.jalons],
        }

    def to_rag_ready_dict(self) -> dict:
        return {
            "etp": [e.text for e in self.etp],
            "montants": [e.text for e in self.montants],
            "jalons": [e.text for e in self.jalons],
        }


@dataclass
class TerminologySmartResult:
    domaine_principal: str = "non_classifié"
    domaines_scores: dict[str, int] = field(default_factory=dict)

    objectifs_rd: list[TermEntity] = field(default_factory=list)
    resultats_rd: list[TermEntity] = field(default_factory=list)
    livrables: list[TermEntity] = field(default_factory=list)
    depenses_eligibles: list[TermEntity] = field(default_factory=list)
    brevets: list[TermEntity] = field(default_factory=list)
    partenaires_rd: list[TermEntity] = field(default_factory=list)

    personnes: list[TermEntity] = field(default_factory=list)
    organismes: list[TermEntity] = field(default_factory=list)
    lieux: list[TermEntity] = field(default_factory=list)
    dates_periodes: list[TermEntity] = field(default_factory=list)
    materiaux: list[TermEntity] = field(default_factory=list)
    equipements: list[TermEntity] = field(default_factory=list)

    indicateurs_cir: IndicateursCIR = field(default_factory=IndicateursCIR)
    rag_preparation: Optional[RagRankerResult] = None

    total_entities: int = 0
    chunks_processed: int = 0
    doc_type: str = "unknown"

    def to_dict(self, debug: bool = True) -> dict:
        return {
            "doc_type": self.doc_type,
            "domaine_principal": self.domaine_principal,
            "domaines_scores": self.domaines_scores,

            "entites_metier_cir": {
                "OBJECTIF_RD": [e.to_dict() for e in self.objectifs_rd],
                "RESULTAT_RD": [e.to_dict() for e in self.resultats_rd],
                "LIVRABLE": [e.to_dict() for e in self.livrables],
                "DEPENSE_ELIGIBLE": [e.to_dict() for e in self.depenses_eligibles],
                "BREVET": [e.to_dict() for e in self.brevets],
                "PARTENAIRE_RD": [e.to_dict() for e in self.partenaires_rd],
            },

            "entites_generales": {
                "PERSONNE": [e.to_dict() for e in self.personnes],
                "ORGANISME": [e.to_dict() for e in self.organismes],
                "LIEU": [e.to_dict() for e in self.lieux],
                "DATE_PERIODE": [e.to_dict() for e in self.dates_periodes],
                "MATERIAU": [e.to_dict() for e in self.materiaux],
                "EQUIPEMENT": [e.to_dict() for e in self.equipements],
            },

            "indicateurs_cir": self.indicateurs_cir.to_dict(),

            "rag_preparation": (
                self.rag_preparation.to_dict(debug=debug)
                if self.rag_preparation else {}
            ),

            "stats": {
                "chunks_processed": self.chunks_processed,
                "total_entities_structured": self.total_entities,
            },
        }

    def to_rag_ready_dict(self) -> dict:
        rag_data = (
            self.rag_preparation.to_rag_ready_dict()
            if self.rag_preparation else {}
        )

        return {
            "domaine_principal": self.domaine_principal,
            "domaines_scores": self.domaines_scores,

            "technologies": rag_data.get("technologies", []),
            "mots_cles_projet": rag_data.get(
                "mots_cles_projet",
                {"high_confidence": [], "candidates": []},
            ),
            "verrous_techniques": rag_data.get("verrous_techniques", []),
            "axes_projet": rag_data.get("axes_projet", []),

            "objectifs_rd": [e.text for e in self.objectifs_rd],
            "resultats_rd": [e.text for e in self.resultats_rd],
            "livrables": [e.text for e in self.livrables],
            "depenses_eligibles": [e.text for e in self.depenses_eligibles],
            "brevets": [e.text for e in self.brevets],
            "partenaires_rd": [e.text for e in self.partenaires_rd],

            "personnes": [e.text for e in self.personnes],
            "organismes": [e.text for e in self.organismes],
            "lieux": [e.text for e in self.lieux],
            "dates_periodes": [e.text for e in self.dates_periodes],
            "materiaux": [e.text for e in self.materiaux],
            "equipements": [e.text for e in self.equipements],

            "indicateurs_cir": self.indicateurs_cir.to_rag_ready_dict(),
        }


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _get_attr(entity: Any, name: str, default=None):
    if isinstance(entity, dict):
        return entity.get(name, default)
    return getattr(entity, name, default)


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.strip(" ,.;:()[]{}'\"")


def _extract_context(text: str, start: int, end: int, window: int = 90) -> str:
    s = max(0, start - window)
    e = min(len(text), end + window)
    return re.sub(r"\s+", " ", text[s:e]).strip()


def _normalize_key(text: str) -> str:
    return _clean_text(text).lower()


def _is_bad_result_text(text: str) -> bool:
    low = _clean_text(text).lower()

    if not low:
        return True

    if _is_structural_artifact_text(low):
        return True

    bad_exact = {
        "précision de l'ordre",
        "precision de l'ordre",
        "nos travaux de 2022",
        "nos travaux de 2023",
        "module",
        "outil",
        "logiciel",
    }

    return low in bad_exact


def _deduplicate_terms(terms: list[TermEntity]) -> list[TermEntity]:
    grouped: dict[tuple[str, str], TermEntity] = {}

    for term in terms:
        clean = _clean_text(term.text)

        if not clean:
            continue

        if _is_structural_artifact_text(clean):
            continue

        term.text = clean
        key = (_normalize_key(term.text), term.type)

        if key not in grouped:
            grouped[key] = term
        else:
            existing = grouped[key]
            existing.frequency += term.frequency
            existing.confidence = max(existing.confidence, term.confidence)

            if term.context and not existing.context:
                existing.context = term.context

    return sorted(
        grouped.values(),
        key=lambda e: (-e.confidence, e.text.lower()),
    )


def _extract_by_pattern(
    text: str,
    pattern: re.Pattern,
    entity_type: str,
    confidence: float,
    source: str = "regex_cir",
) -> list[TermEntity]:
    entities = []

    for match in pattern.finditer(text):
        span = _clean_text(match.group(0))

        if len(span) < 2:
            continue

        if _is_structural_artifact_text(span):
            continue

        if entity_type in {"RESULTAT_RD", "LIVRABLE"} and _is_bad_result_text(span):
            continue

        entities.append(
            TermEntity(
                text=span,
                type=entity_type,
                confidence=confidence,
                context=_extract_context(text, match.start(), match.end()),
                source=source,
            )
        )

    return _deduplicate_terms(entities)


def _entity_to_term(entity: Any, forced_type: Optional[str] = None) -> Optional[TermEntity]:
    text = _clean_text(str(_get_attr(entity, "text", "")))
    entity_type = forced_type or str(_get_attr(entity, "type", "UNKNOWN"))
    confidence = float(_get_attr(entity, "confidence", 0.5) or 0.5)
    source = str(_get_attr(entity, "source", "ner"))

    if not text:
        return None

    if _is_structural_artifact_text(text):
        return None

    return TermEntity(
        text=text,
        type=entity_type,
        confidence=confidence,
        context=None,
        source=source,
    )


def _filter_entities_by_type(ner_entities: list[Any], wanted_types: set[str]) -> list[TermEntity]:
    result = []

    for entity in ner_entities:
        entity_type = str(_get_attr(entity, "type", "UNKNOWN"))

        if entity_type not in wanted_types:
            continue

        term = _entity_to_term(entity)

        if term:
            result.append(term)

    return _deduplicate_terms(result)


def _clean_objective_phrase(text: str) -> str:
    text = _clean_text(text)

    if not text:
        return ""

    text = re.split(r"[.;:\n\r]", text)[0]
    text = _clean_text(text)

    text = re.split(
        r"\s*,\s*(?:en|afin|pour|avec)\s+",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    text = _clean_text(text)

    words = text.split()
    if len(words) > 18:
        text = " ".join(words[:18])
        text = _clean_text(text)

    text = re.sub(
        r"\s+(de|du|des|d'|à|pour|avec|sans|dans|sur)$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = _clean_text(text)

    if len(text) < 10:
        return ""

    if _is_structural_artifact_text(text):
        return ""

    return text


def extract_objectifs_rd(text: str) -> list[TermEntity]:
    entities: list[TermEntity] = []

    for trigger in OBJECTIVE_TRIGGERS:
        pattern = re.compile(
            rf"\b{trigger}\s*(?P<objective>.{{10,220}})",
            re.IGNORECASE | re.UNICODE,
        )

        for match in pattern.finditer(text):
            obj = _clean_objective_phrase(match.group("objective"))

            if not obj:
                continue

            entities.append(
                TermEntity(
                    text=obj,
                    type="OBJECTIF_RD",
                    confidence=0.80,
                    context=_extract_context(text, match.start(), match.end()),
                    source="regex_objectif",
                )
            )

    return _deduplicate_terms(entities)


# ══════════════════════════════════════════════════════════════════════════════
# DOMAINE
# ══════════════════════════════════════════════════════════════════════════════

def detect_domain_from_text_and_entities(
    chunks: list[str],
    ner_entities: list[Any],
) -> tuple[str, dict[str, int]]:
    full_text = " ".join(chunks).lower()
    scores = {domain: 0 for domain in DOMAIN_MARKERS}

    for domain, markers in DOMAIN_MARKERS.items():
        for marker in markers:
            if marker.lower() in full_text:
                scores[domain] += 1

    entity_texts = []

    for entity in ner_entities:
        etype = str(_get_attr(entity, "type", ""))
        txt = str(_get_attr(entity, "text", "")).lower()

        if not txt or _is_structural_artifact_text(txt):
            continue

        if etype in {
            "TECHNOLOGIE",
            "DOMAINE_RD",
            "PROJET_AXE",
            "MATERIAU",
            "EQUIPEMENT",
            "COMPOSANT_TECHNIQUE",
            "METHODE_RD",
        }:
            if txt in {"r&d", "rd", "recherche et développement"}:
                continue
            entity_texts.append(txt)

    for domain, markers in DOMAIN_MARKERS.items():
        for marker in markers:
            marker_low = marker.lower()

            for ent in entity_texts:
                if marker_low in ent or ent in marker_low:
                    scores[domain] += 2

    active_scores = {d: s for d, s in scores.items() if s > 0}

    if not active_scores:
        return "non_classifié", {}

    best_domain = max(active_scores, key=active_scores.get)

    if active_scores[best_domain] < DOMAIN_MIN_SCORE:
        return "non_classifié", active_scores

    return best_domain, active_scores


# ══════════════════════════════════════════════════════════════════════════════
# STRUCTURATION DEPUIS NER
# ══════════════════════════════════════════════════════════════════════════════

def structure_entities_from_ner(ner_entities: list[Any]) -> dict[str, list[TermEntity]]:
    personnes = _filter_entities_by_type(ner_entities, {"PERSONNE"})
    organismes = _filter_entities_by_type(ner_entities, {"ORGANISME"})
    lieux = _filter_entities_by_type(ner_entities, {"LIEU"})
    dates = _filter_entities_by_type(ner_entities, {"DATE_PERIODE"})
    materiaux = _filter_entities_by_type(ner_entities, {"MATERIAU"})

    equipements = _filter_entities_by_type(
        ner_entities,
        {"EQUIPEMENT", "COMPOSANT_TECHNIQUE"},
    )

    partenaires = _filter_entities_by_type(
        ner_entities,
        {"PARTENAIRE_RD"},
    )

    brevets = _filter_entities_by_type(
        ner_entities,
        {"BREVET"},
    )

    return {
        "personnes": personnes,
        "organismes": organismes,
        "lieux": lieux,
        "dates_periodes": dates,
        "materiaux": materiaux,
        "equipements": equipements,
        "partenaires_rd": partenaires,
        "brevets": brevets,
    }


def extract_indicateurs_cir(text: str, ner_entities: list[Any]) -> IndicateursCIR:
    etp = _extract_by_pattern(text, ETP_RE, "ETP", 0.92, source="regex_indicateur")
    montants = _extract_by_pattern(text, MONTANT_RE, "MONTANT_CIR", 0.92, source="regex_indicateur")
    jalons = _extract_by_pattern(text, JALON_RE, "JALON", 0.90, source="regex_indicateur")

    for entity in ner_entities:
        etype = str(_get_attr(entity, "type", ""))
        term = _entity_to_term(entity)

        if not term:
            continue

        if etype == "ETP":
            etp.append(term)
        elif etype == "MONTANT_CIR":
            montants.append(term)
        elif etype == "JALON":
            jalons.append(term)

    return IndicateursCIR(
        etp=_deduplicate_terms(etp),
        montants=_deduplicate_terms(montants),
        jalons=_deduplicate_terms(jalons),
    )


# ══════════════════════════════════════════════════════════════════════════════
# POINT D’ENTRÉE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def analyze_terminology_smart(
    chunks: list[str],
    ner_entities: list[Any],
    top_rag_keywords: int = 20,
    doc_type: str = "unknown",
) -> TerminologySmartResult:
    """
    Analyse terminologique complète.

    doc_type est transmis au rag_ranker pour éviter les artefacts :
      - pptx : SLIDE, NOTES, PRESENTATION...
      - docx/pdf : FORMULES, LaTeX, Domaine, Confiance...
      - excel : TOTAL, CELL, SHEET...
      - email : FROM, TO, SUBJECT...
    """
    if not chunks:
        return TerminologySmartResult(doc_type=doc_type)

    doc_type = (doc_type or "unknown").lower()
    full_text = "\n".join(chunks)

    domaine, scores = detect_domain_from_text_and_entities(chunks, ner_entities)

    objectifs = extract_objectifs_rd(full_text)
    resultats = _extract_by_pattern(full_text, RESULTAT_RE, "RESULTAT_RD", 0.82)
    livrables = _extract_by_pattern(full_text, LIVRABLE_RE, "LIVRABLE", 0.85)
    depenses = _extract_by_pattern(full_text, DEPENSE_RE, "DEPENSE_ELIGIBLE", 0.88)
    brevets_regex = _extract_by_pattern(full_text, BREVET_RE, "BREVET", 0.90)
    partenaires_regex = _extract_by_pattern(full_text, PARTENAIRE_RE, "PARTENAIRE_RD", 0.83)

    structured = structure_entities_from_ner(ner_entities)

    personnes = structured["personnes"]
    organismes = structured["organismes"]
    lieux = structured["lieux"]
    dates_periodes = structured["dates_periodes"]
    materiaux = structured["materiaux"]
    equipements = structured["equipements"]

    partenaires = _deduplicate_terms(
        partenaires_regex + structured["partenaires_rd"]
    )

    brevets = _deduplicate_terms(
        brevets_regex + structured["brevets"]
    )

    dates_regex = _extract_by_pattern(
        full_text,
        DATE_PERIOD_RE,
        "DATE_PERIODE",
        0.88,
        source="regex_date",
    )

    dates_periodes = _deduplicate_terms(dates_periodes + dates_regex)

    indicateurs_cir = extract_indicateurs_cir(full_text, ner_entities)

    rag_preparation = rank_entities_for_rag(
        chunks=chunks,
        ner_entities=ner_entities,
        domain_name=domaine,
        top_k=top_rag_keywords,
        min_score=3.5,
        doc_type=doc_type,
    )

    total = (
        len(objectifs)
        + len(resultats)
        + len(livrables)
        + len(depenses)
        + len(brevets)
        + len(partenaires)
        + len(personnes)
        + len(organismes)
        + len(lieux)
        + len(dates_periodes)
        + len(materiaux)
        + len(equipements)
        + len(indicateurs_cir.etp)
        + len(indicateurs_cir.montants)
        + len(indicateurs_cir.jalons)
    )

    logger.info(
        "Terminology smart : doc_type=%s | domaine=%s | objectifs=%d | resultats=%d | equipements=%d | dates=%d",
        doc_type,
        domaine,
        len(objectifs),
        len(resultats),
        len(equipements),
        len(dates_periodes),
    )

    return TerminologySmartResult(
        doc_type=doc_type,
        domaine_principal=domaine,
        domaines_scores=scores,

        objectifs_rd=objectifs,
        resultats_rd=resultats,
        livrables=livrables,
        depenses_eligibles=depenses,
        brevets=brevets,
        partenaires_rd=partenaires,

        personnes=personnes,
        organismes=organismes,
        lieux=lieux,
        dates_periodes=dates_periodes,
        materiaux=materiaux,
        equipements=equipements,

        indicateurs_cir=indicateurs_cir,
        rag_preparation=rag_preparation,

        total_entities=total,
        chunks_processed=len(chunks),
    )


def analyze_chunk_smart(
    chunk: str,
    ner_entities: list[Any],
    doc_type: str = "unknown",
) -> TerminologySmartResult:
    return analyze_terminology_smart(
        chunks=[chunk],
        ner_entities=ner_entities,
        top_rag_keywords=15,
        doc_type=doc_type,
    )