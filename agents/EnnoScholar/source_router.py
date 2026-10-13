# -*- coding: utf-8 -*-
from __future__ import annotations

"""Universal source routing for EnnoScholar.

Policy V161:
- no domain/client/project hardcoding;
- Semantic Scholar stays disabled when its flags are off;
- every other enabled scientific provider is searched in the initial pass;
- OpenAlex is listed first because it is the preferred bibliographic engine;
- GitHub and Hugging Face remain separate technical-artifact providers.
"""

import os
from typing import Any, Dict, List

from .external_source_base import env_bool


# OpenAlex remains first. Semantic Scholar is re-enabled as the second
# bibliographic engine. IEEE is intentionally removed from the search plan.
# 9 scientific + GitHub/Hugging Face = 11 active provider families.
ALL_SCIENTIFIC = [
    "openalex",
    "semantic_scholar",
    "crossref",
    "doaj",
    "arxiv",
    "hal",
    "core",
    "europe_pmc",
    "zenodo",
]

ARTIFACT_SOURCES = ["github", "huggingface"]


def _enabled_flags() -> Dict[str, bool]:
    return {
        "semantic_scholar": (
            env_bool("ENNOSCHOLAR_USE_SEMANTIC_SCHOLAR", False)
            and env_bool("ENNOSCHOLAR_SEMANTIC_SCHOLAR_ENABLED", False)
        ),
        "openalex": env_bool("ENNOSCHOLAR_USE_OPENALEX", True),
        "doaj": env_bool("ENNOSCHOLAR_USE_DOAJ", True),
        "arxiv": env_bool("ENNOSCHOLAR_USE_ARXIV", True),
        "crossref": env_bool("ENNOSCHOLAR_USE_CROSSREF", True),
        "hal": env_bool("ENNOSCHOLAR_USE_HAL", True),
        "zenodo": env_bool("ENNOSCHOLAR_USE_ZENODO", True),
        "core": env_bool("ENNOSCHOLAR_USE_CORE", True),  # V167.4 public fallback allowed without API key
        # IEEE deliberately not routed by V162. The key may remain in .env for future use.
        "ieee": False,
        "europe_pmc": env_bool("ENNOSCHOLAR_USE_EUROPE_PMC", True),
        "github": env_bool("ENNOSCHOLAR_USE_GITHUB", True),
        "huggingface": env_bool("ENNOSCHOLAR_USE_HUGGINGFACE", True),
    }


def _keep_enabled(names: List[str], flags: Dict[str, bool]) -> List[str]:
    return list(dict.fromkeys(name for name in names if flags.get(name, False)))


def build_source_plan(intent: Dict[str, Any]) -> Dict[str, Any]:
    # Intent is accepted for API compatibility only. Provider choice depends on
    # capabilities/configuration, never on a hard-coded domain vocabulary.
    _ = intent
    flags = _enabled_flags()

    scientific = _keep_enabled(ALL_SCIENTIFIC, flags)
    artifacts = _keep_enabled(ARTIFACT_SOURCES, flags)

    return {
        "version": "v162_openalex_first_semantic_on_ieee_off",
        "profile": "generic_evidence_driven",
        "fast_mode": False,
        "secondary_sources_deferred": False,
        "scientific_sources": scientific,
        "fallback_scientific_sources": [],
        "artifact_sources": artifacts,
        "preferred_scientific_source": "openalex" if "openalex" in scientific else None,
        "providers_requested_count": len(scientific) + len(artifacts),
        "semantic_scholar_enabled": bool(flags.get("semantic_scholar")),
        "reasons": [
            "all_enabled_scientific_sources_initial_pass_semantic_enabled_ieee_removed",
            "openalex_first",
            "technical_artifacts_separate_from_scientific_evidence",
        ],
        "policy": "all_enabled_sources_no_domain_hardcoding",
        "hardcoded_domain_rules": False,
    }

# ENNOSCHOLAR_V167_4_CORE_PUBLIC_ROUTING
