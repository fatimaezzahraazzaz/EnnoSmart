# -*- coding: utf-8 -*-
"""Modèles JSON-sérialisables du graphe de système technique.

Cette couche décrit le projet avant toute décision de verrou ou d'éligibilité
Frascati. Les dataclasses sont volontairement simples : aucune dépendance
externe et aucune logique métier propre à un domaine.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class EvidenceNode:
    evidence_id: str
    passage_id: str
    document_id: str
    document: str
    source_path: str
    document_type: str
    semantic_role: str
    evidence_type: str
    text: str
    section_title: str = ""
    confidence: float = 0.0


@dataclass
class ConceptNode:
    concept_id: str
    kind: str
    label: str
    canonical_label: str
    aliases: List[str] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)
    document_ids: List[str] = field(default_factory=list)
    source_kinds: List[str] = field(default_factory=list)
    score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RelationEdge:
    edge_id: str
    source_id: str
    target_id: str
    relation: str
    weight: float = 1.0
    evidence_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProvisionalSubsystem:
    subsystem_id: str
    label: str
    object_ids: List[str] = field(default_factory=list)
    reference_ids: List[str] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)
    document_ids: List[str] = field(default_factory=list)
    status: str = "candidate_system_family_not_lock"
    confidence: float = 0.0
    reason: str = ""


def to_dict(value: Any) -> Dict[str, Any]:
    return asdict(value)
