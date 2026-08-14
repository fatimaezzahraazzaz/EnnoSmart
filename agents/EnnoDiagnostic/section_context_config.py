# -*- coding: utf-8 -*-
from __future__ import annotations

"""Configuration centralisée du context engineering d'EnnoDiagnostic.

Chaque section possède :
- ses rôles RAG autorisés ;
- un budget d'entrée et de sortie ;
- une limite de preuves et de caractères par preuve ;
- un format d'affichage attendu.

Les valeurs peuvent être surchargées dans le .env sans modifier le code.
"""

import math
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, Tuple


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(str(os.getenv(name, default)).strip()))
    except Exception:
        return default


def estimate_tokens(value: Any) -> int:
    """Estimation prudente pour du français, sans dépendance tokenizer."""
    text = str(value or "")
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 3.6))


@dataclass(frozen=True)
class SectionContextConfig:
    key: str
    title: str
    source_keys: Tuple[str, ...]
    max_input_tokens: int
    max_output_tokens: int
    top_k_evidence: int
    max_chars_per_evidence: int
    display_mode: str
    min_items: int = 1
    max_items: int = 4
    temperature: float = 0.05

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_DEFAULTS: Dict[str, SectionContextConfig] = {
    "synthese_strategique": SectionContextConfig(
        key="synthese_strategique",
        title="Synthèse stratégique du projet",
        source_keys=("global", "objectifs", "verrous", "limites"),
        max_input_tokens=2800,
        max_output_tokens=600,
        top_k_evidence=9,
        max_chars_per_evidence=520,
        display_mode="paragraphs",
        min_items=2,
        max_items=3,
    ),
    "objectif_global": SectionContextConfig(
        key="objectif_global",
        title="Objectif global du projet",
        # L'objectif projet peut être formulé dans une contribution, une méthode
        # ou une conclusion et pas uniquement dans un passage NLP rôle=objectif.
        source_keys=(
            "objectif_agent_context",
            "objectifs",
            "global",
            "contributions",
            "methodes",
            "resultats",
        ),
        max_input_tokens=4200,
        max_output_tokens=500,
        top_k_evidence=8,
        max_chars_per_evidence=520,
        display_mode="paragraphs",
        min_items=1,
        max_items=1,
    ),
    "justification_frascati": SectionContextConfig(
        key="justification_frascati",
        title="Justification Frascati du score",
        source_keys=("verrous", "methodes", "resultats", "parametres", "limites"),
        max_input_tokens=5600,
        max_output_tokens=900,
        top_k_evidence=14,
        max_chars_per_evidence=500,
        display_mode="paragraphs",
        min_items=1,
        max_items=1,
        temperature=0.03,
    ),
    "demarche_detectee": SectionContextConfig(
        key="demarche_detectee",
        title="Démarche détectée",
        source_keys=("methodes", "parametres", "resultats"),
        max_input_tokens=4600,
        max_output_tokens=760,
        top_k_evidence=12,
        max_chars_per_evidence=620,
        display_mode="numbered_items",
        min_items=1,
        max_items=5,
    ),
    "resultats_metriques": SectionContextConfig(
        key="resultats_metriques",
        title="Résultats / métriques",
        source_keys=("resultats", "axe_preuves_resultats"),
        max_input_tokens=4600,
        max_output_tokens=680,
        top_k_evidence=12,
        max_chars_per_evidence=620,
        display_mode="paragraphs",
        min_items=2,
        max_items=5,
    ),
    "parametres_contraintes": SectionContextConfig(
        key="parametres_contraintes",
        title="Paramètres et contraintes techniques",
        source_keys=("parametres", "methodes", "limites", "axe_contraintes_transverses"),
        max_input_tokens=4000,
        max_output_tokens=620,
        top_k_evidence=10,
        max_chars_per_evidence=580,
        display_mode="numbered_items",
        min_items=1,
        max_items=5,
    ),
}


def get_section_config(section_key: str) -> SectionContextConfig:
    base = _DEFAULTS[section_key]
    env_key = section_key.upper()
    return SectionContextConfig(
        key=base.key,
        title=base.title,
        source_keys=base.source_keys,
        max_input_tokens=_env_int(
            f"ENNOSMART_DIAG_{env_key}_MAX_INPUT_TOKENS",
            base.max_input_tokens,
            256,
        ),
        max_output_tokens=_env_int(
            f"ENNOSMART_DIAG_{env_key}_MAX_OUTPUT_TOKENS",
            base.max_output_tokens,
            64,
        ),
        top_k_evidence=_env_int(
            f"ENNOSMART_DIAG_{env_key}_TOP_K_EVIDENCE",
            base.top_k_evidence,
            1,
        ),
        max_chars_per_evidence=_env_int(
            f"ENNOSMART_DIAG_{env_key}_MAX_CHARS_PER_EVIDENCE",
            base.max_chars_per_evidence,
            120,
        ),
        display_mode=base.display_mode,
        min_items=base.min_items,
        max_items=base.max_items,
        temperature=base.temperature,
    )


def all_section_configs() -> Dict[str, SectionContextConfig]:
    return {key: get_section_config(key) for key in _DEFAULTS}
