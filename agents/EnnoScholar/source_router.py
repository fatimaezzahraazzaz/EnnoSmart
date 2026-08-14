# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Any, Dict, List, Set
from .external_source_base import env_bool, safe

FAST_SCIENTIFIC = ["openalex", "semantic_scholar", "doaj", "crossref"]
COMPLETE_SCIENTIFIC = FAST_SCIENTIFIC + ["hal", "zenodo"]

ARXIV_PROFILES = {
    "automation_robotics_embedded","signal_image_vision","software_ai_data_cyber",
    "mathematics_modeling_simulation","electronics_telecom_networks","electrical_power_energy",
    "physics_instrumentation","energy_process_environment","earth_ocean_atmosphere",
}
IEEE_PROFILES = {
    "automation_robotics_embedded","signal_image_vision","software_ai_data_cyber",
    "electronics_telecom_networks","electrical_power_energy","physics_instrumentation",
}
BIOMED_PROFILES = {
    "cell_molecular_biology","human_animal_biology","pharma_cosmetics","clinical_trials",
    "medical_devices_ehealth","biotechnology","food_feed","agronomy_environment",
}
GITHUB_PROFILES = {
    "automation_robotics_embedded","signal_image_vision","software_ai_data_cyber",
    "mathematics_modeling_simulation","electronics_telecom_networks","electrical_power_energy",
    "physics_instrumentation","energy_process_environment","mechanical_civil_engineering",
    "chemistry_process","materials_metallurgy","building_multiphysics_comfort",
}
HF_PROFILES = {"signal_image_vision","software_ai_data_cyber","medical_devices_ehealth","biotechnology"}


def _profile(intent: Dict[str, Any]) -> str:
    return safe(intent.get("backend_enrichment_profile") or intent.get("enrichment_profile") or ((intent.get("cir_domain_profile") or {}).get("profile_id") if isinstance(intent.get("cir_domain_profile"),dict) else "") or "generic", 120)


def _intent_text(intent: Dict[str, Any]) -> str:
    parts=[intent.get("verrou_title"),intent.get("scientific_problem"),intent.get("technical_object"),intent.get("phenomenon")," ".join(map(str,intent.get("key_terms_en") or []))," ".join(map(str,intent.get("key_terms_fr") or []))]
    return " ".join(str(x or "") for x in parts).lower()


def build_source_plan(intent: Dict[str, Any]) -> Dict[str, Any]:
    profile=_profile(intent)
    text=_intent_text(intent)
    fast_mode=env_bool("ENNOSCHOLAR_FAST_MODE",True)
    include_secondary=env_bool("ENNOSCHOLAR_FAST_INCLUDE_SECONDARY_SOURCES",False)
    scientific=list(FAST_SCIENTIFIC if fast_mode and not include_secondary else COMPLETE_SCIENTIFIC)
    fallback_scientific=[]
    artifacts=[]
    reasons=[]

    if fast_mode:
        reasons.append("fast_primary_sources")
        if not include_secondary:
            reasons.append("hal_zenodo_deferred")
            fallback_scientific.extend(["hal", "zenodo"])

    if profile in ARXIV_PROFILES:
        scientific.append("arxiv"); reasons.append("arxiv_profile_match")
    if profile in IEEE_PROFILES or any(x in text for x in ["radar","antenna","electromagnetic","telecom","signal processing","embedded"]):
        scientific.append("ieee"); reasons.append("ieee_domain_match")
    if profile in BIOMED_PROFILES or any(x in text for x in ["biomedical","clinical","protein","cell","pharma","medical device"]):
        scientific.append("europe_pmc"); reasons.append("europe_pmc_domain_match")
    search_artifacts=(not fast_mode) or env_bool("ENNOSCHOLAR_FAST_SEARCH_ARTIFACTS",False)
    github_relevance_hints = {
        "software", "code", "algorithm", "simulation", "simulator", "model",
        "dataset", "repository", "open source", "reproducibility", "benchmark",
        "pipeline", "framework", "implementation",
    }
    if search_artifacts and (
        profile in GITHUB_PROFILES
        or any(hint in text for hint in github_relevance_hints)
    ):
        artifacts.append("github")
    if search_artifacts and (profile in HF_PROFILES or any(x in text for x in ["machine learning","deep learning","dataset","neural network","classification","segmentation"])):
        artifacts.append("huggingface")

    # CORE est utile mais plus lent et soumis à quota : en mode rapide, il reste
    # dans le second niveau avec HAL/Zenodo.
    if os.getenv("CORE_API_KEY") and (not fast_mode or include_secondary):
        scientific.append("core")
    elif os.getenv("CORE_API_KEY") and fast_mode:
        fallback_scientific.append("core")
        reasons.append("core_deferred")
    # IEEE requires a key.
    if "ieee" in scientific and not os.getenv("IEEE_XPLORE_API_KEY"):
        scientific=[x for x in scientific if x!="ieee"]
        reasons.append("ieee_disabled_missing_key")

    flags={
        "semantic_scholar":env_bool("ENNOSCHOLAR_USE_SEMANTIC_SCHOLAR",True),
        "openalex":env_bool("ENNOSCHOLAR_USE_OPENALEX",True),
        "doaj":env_bool("ENNOSCHOLAR_USE_DOAJ",True),
        "arxiv":env_bool("ENNOSCHOLAR_USE_ARXIV",True),
        "crossref":env_bool("ENNOSCHOLAR_USE_CROSSREF",True),
        "hal":env_bool("ENNOSCHOLAR_USE_HAL",True),
        "zenodo":env_bool("ENNOSCHOLAR_USE_ZENODO",True),
        "core":env_bool("ENNOSCHOLAR_USE_CORE",True),
        "ieee":env_bool("ENNOSCHOLAR_USE_IEEE",True),
        "europe_pmc":env_bool("ENNOSCHOLAR_USE_EUROPE_PMC",True),
        "github":env_bool("ENNOSCHOLAR_USE_GITHUB",True),
        "huggingface":env_bool("ENNOSCHOLAR_USE_HUGGINGFACE",True),
    }
    scientific=list(dict.fromkeys(x for x in scientific if flags.get(x,False)))
    fallback_scientific=list(dict.fromkeys(
        x for x in fallback_scientific
        if flags.get(x,False) and x not in scientific
    ))
    artifacts=list(dict.fromkeys(x for x in artifacts if flags.get(x,False)))
    return {
        "version":"v149_relevance_routed_technical_artifacts",
        "profile":profile,
        "fast_mode":fast_mode,
        "secondary_sources_deferred":bool(fast_mode and not include_secondary),
        "scientific_sources":scientific,
        "fallback_scientific_sources":fallback_scientific,
        "artifact_sources":artifacts,
        "reasons":reasons,
        "policy":"scientific_sources_are_ranked_artifacts_are_separate_and_relevance_routed",
    }
