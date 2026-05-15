"""
modules/NLP/aggregator.py
──────────────────────────────────────────────────────────────────────────────
Agrégation des preuves par rôle CIR.

  evidence_mapper.py ──► aggregator.py ──► domain_classifier.py + synthesizer.py

RÔLE :
  - Regrouper toutes les Evidence par rôle CIR (objectif, verrou, démarche...).
  - Dédupliquer les phrases-preuves quasi-identiques.
  - Dédupliquer et compter les concepts techniques.
  - Produire une structure compacte que domain_classifier.py et
    synthesizer.py consommeront.

CE MODULE NE CONTIENT :
  - AUCUN appel LLM.
  - AUCUNE règle métier (pas de liste d'outils, pas de mots-clés de domaine).
  - AUCUNE promotion/reclassement par mot-clé.

Il fait juste du regroupement et de la déduplication. C'est tout.

Version : 1.0.0
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

logger = logging.getLogger(__name__)

# Rôles diagnostic (ceux qui décrivent réellement la R&D).
DIAGNOSTIC_ROLES = [
    "contexte", "objectif", "verrou", "etat_art",
    "demarche", "essai", "resultat", "preuve",
]
# Rôles conservés mais hors diagnostic.
OTHER_ROLES = ["administratif", "hors_sujet"]

# Seuil de similarité pour considérer deux phrases comme des doublons.
DEDUP_SIMILARITY = 0.88


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EvidenceItem:
    """Une preuve agrégée (après déduplication)."""
    phrase: str
    passage_ids: list[str] = field(default_factory=list)   # passages où elle apparaît
    section_roles: list[str] = field(default_factory=list)  # indices de section associés
    frequency: int = 1
    confidence: float = 0.7

    def to_dict(self) -> dict:
        return {
            "phrase": self.phrase,
            "passage_ids": self.passage_ids,
            "section_roles": list(dict.fromkeys(self.section_roles)),
            "frequency": self.frequency,
            "confidence": round(float(self.confidence), 3),
        }


@dataclass
class ConceptItem:
    """Un concept technique agrégé."""
    text: str
    frequency: int = 1
    passage_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "frequency": self.frequency,
            "passage_ids": self.passage_ids,
        }


@dataclass
class AggregatedEvidence:
    """Résultat d'agrégation : preuves regroupées par rôle + concepts."""
    # Dictionnaire rôle -> liste de preuves dédupliquées.
    by_role: dict[str, list[EvidenceItem]] = field(default_factory=dict)
    # Concepts techniques agrégés, triés par fréquence.
    concepts: list[ConceptItem] = field(default_factory=list)
    # Stats.
    total_evidences_before_dedup: int = 0
    total_evidences_after_dedup: int = 0
    total_passages: int = 0

    def get(self, role: str) -> list[EvidenceItem]:
        return self.by_role.get(role, [])

    def has_diagnostic_content(self) -> bool:
        """True si au moins une preuve diagnostic existe."""
        return any(self.by_role.get(r) for r in DIAGNOSTIC_ROLES)

    def to_dict(self) -> dict:
        return {
            "by_role": {
                role: [e.to_dict() for e in items]
                for role, items in self.by_role.items()
            },
            "concepts": [c.to_dict() for c in self.concepts],
            "stats": {
                "total_evidences_before_dedup": self.total_evidences_before_dedup,
                "total_evidences_after_dedup": self.total_evidences_after_dedup,
                "total_passages": self.total_passages,
                "roles_present": sorted(
                    r for r, items in self.by_role.items() if items
                ),
            },
        }

    def summary_for_llm(self, max_per_role: int = 12, max_concepts: int = 30) -> str:
        """
        Produit un résumé textuel compact des preuves, destiné à être passé
        à domain_classifier.py et synthesizer.py.
        On ne passe PAS tout le document — seulement les preuves validées.
        """
        lines: list[str] = []
        role_labels = {
            "contexte": "CONTEXTE",
            "objectif": "OBJECTIFS",
            "verrou": "VERROUS / INCERTITUDES",
            "etat_art": "ÉTAT DE L'ART",
            "demarche": "DÉMARCHE / MÉTHODE",
            "essai": "ESSAIS / EXPÉRIMENTATIONS",
            "resultat": "RÉSULTATS",
            "preuve": "PREUVES / DONNÉES",
        }
        for role in DIAGNOSTIC_ROLES:
            items = self.by_role.get(role, [])
            if not items:
                continue
            lines.append(f"\n## {role_labels.get(role, role.upper())}")
            for item in items[:max_per_role]:
                lines.append(f"- {item.phrase}")

        if self.concepts:
            lines.append("\n## CONCEPTS TECHNIQUES RÉCURRENTS")
            top = sorted(self.concepts, key=lambda c: -c.frequency)[:max_concepts]
            lines.append(", ".join(c.text for c in top))

        return "\n".join(lines).strip()


# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRES DE DÉDUPLICATION
# ══════════════════════════════════════════════════════════════════════════════

def _norm(text: str) -> str:
    """Normalisation pour comparer deux phrases."""
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _dedup_evidences(evidences: list[Any]) -> list[EvidenceItem]:
    """
    Déduplique une liste d'Evidence (même rôle) en EvidenceItem.
    Fusionne les phrases quasi-identiques, garde la plus longue.
    """
    items: list[EvidenceItem] = []

    for ev in evidences:
        phrase = str(getattr(ev, "phrase_source", "") or "").strip()
        if not phrase:
            continue
        passage_id = str(getattr(ev, "passage_id", "") or "")
        section_role = str(getattr(ev, "section_role", "unknown") or "unknown")
        conf = float(getattr(ev, "confidence", 0.7) or 0.7)

        merged = False
        for item in items:
            if _norm(item.phrase) == _norm(phrase) or _similar(item.phrase, phrase) >= DEDUP_SIMILARITY:
                # Doublon : on fusionne.
                item.frequency += 1
                if passage_id and passage_id not in item.passage_ids:
                    item.passage_ids.append(passage_id)
                if section_role:
                    item.section_roles.append(section_role)
                item.confidence = max(item.confidence, conf)
                # Garder la formulation la plus complète.
                if len(phrase) > len(item.phrase):
                    item.phrase = phrase
                merged = True
                break

        if not merged:
            items.append(
                EvidenceItem(
                    phrase=phrase,
                    passage_ids=[passage_id] if passage_id else [],
                    section_roles=[section_role],
                    frequency=1,
                    confidence=conf,
                )
            )

    # Tri : fréquence puis confiance.
    items.sort(key=lambda x: (-x.frequency, -x.confidence, x.phrase.lower()))
    return items


def _dedup_concepts(concept_pairs: list[tuple[str, str]]) -> list[ConceptItem]:
    """
    Déduplique les concepts. concept_pairs = [(concept_text, passage_id), ...].
    """
    by_key: dict[str, ConceptItem] = {}
    for text, passage_id in concept_pairs:
        text = str(text or "").strip()
        if not text or len(text) < 2:
            continue
        key = _norm(text)
        if not key:
            continue
        if key in by_key:
            by_key[key].frequency += 1
            if passage_id and passage_id not in by_key[key].passage_ids:
                by_key[key].passage_ids.append(passage_id)
            # Garder la casse/forme la plus longue.
            if len(text) > len(by_key[key].text):
                by_key[key].text = text
        else:
            by_key[key] = ConceptItem(
                text=text,
                frequency=1,
                passage_ids=[passage_id] if passage_id else [],
            )

    concepts = list(by_key.values())
    concepts.sort(key=lambda c: (-c.frequency, c.text.lower()))
    return concepts


# ══════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def aggregate(evidence_map_result: Any) -> AggregatedEvidence:
    """
    Agrège un EvidenceMapResult (depuis evidence_mapper.map_evidence).

    Paramètres
    ----------
    evidence_map_result : objet avec .mappings (list[PassageMapping])
                          chaque mapping a .evidences et .concepts

    Retourne
    --------
    AggregatedEvidence
    """
    result = AggregatedEvidence()

    mappings = list(getattr(evidence_map_result, "mappings", []) or [])
    result.total_passages = len(mappings)

    # 1. Collecter toutes les Evidence par rôle.
    by_role_raw: dict[str, list] = {r: [] for r in DIAGNOSTIC_ROLES + OTHER_ROLES}
    concept_pairs: list[tuple[str, str]] = []

    for mapping in mappings:
        passage_id = str(getattr(mapping, "passage_id", "") or "")

        for ev in getattr(mapping, "evidences", []) or []:
            role = str(getattr(ev, "role", "") or "").strip().lower()
            if role not in by_role_raw:
                # Rôle inconnu : on l'ignore proprement.
                continue
            by_role_raw[role].append(ev)
            result.total_evidences_before_dedup += 1

        for concept in getattr(mapping, "concepts", []) or []:
            concept_pairs.append((str(concept or ""), passage_id))

    # 2. Dédupliquer chaque rôle.
    for role, evidences in by_role_raw.items():
        if not evidences:
            continue
        deduped = _dedup_evidences(evidences)
        result.by_role[role] = deduped
        result.total_evidences_after_dedup += len(deduped)

    # 3. Dédupliquer les concepts.
    result.concepts = _dedup_concepts(concept_pairs)

    logger.info(
        "Agrégation : %d passages | %d preuves → %d après dédup | %d concepts | rôles=%s",
        result.total_passages,
        result.total_evidences_before_dedup,
        result.total_evidences_after_dedup,
        len(result.concepts),
        sorted(r for r, v in result.by_role.items() if v),
    )

    return result


# ── Interface rapide (debug / tests) ──────────────────────────────────────────

if __name__ == "__main__":
    import json
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO)

    # Faux EvidenceMapResult pour tester l'agrégation/déduplication.
    class _FakeEv:
        def __init__(self, role, phrase, pid, srole="unknown", conf=0.7):
            self.role = role
            self.phrase_source = phrase
            self.passage_id = pid
            self.section_role = srole
            self.confidence = conf

    class _FakeMapping:
        def __init__(self, pid, evidences, concepts):
            self.passage_id = pid
            self.evidences = evidences
            self.concepts = concepts

    class _FakeResult:
        def __init__(self, mappings):
            self.mappings = mappings

    fake = _FakeResult([
        _FakeMapping("p0", [
            _FakeEv("objectif", "Développer un emballage médical recyclable.", "p0", "objectifs"),
            _FakeEv("verrou", "Incapacité de résoudre simultanément chocs et recyclabilité.", "p0", "verrous"),
        ], ["emballage médical", "recyclabilité", "tenue aux chocs"]),
        _FakeMapping("p1", [
            # Doublon quasi-identique du verrou de p0 → doit fusionner.
            _FakeEv("verrou", "Incapacité de résoudre simultanément les chocs et la recyclabilité.", "p1"),
            _FakeEv("essai", "Essais de chute réalisés sur trois prototypes.", "p1"),
        ], ["recyclabilité", "prototypes", "essais de chute"]),
    ])

    agg = aggregate(fake)
    print(json.dumps(agg.to_dict(), ensure_ascii=False, indent=2))
    print("\n--- summary_for_llm ---")
    print(agg.summary_for_llm())