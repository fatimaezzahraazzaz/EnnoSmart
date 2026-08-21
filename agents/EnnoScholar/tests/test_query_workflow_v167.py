# -*- coding: utf-8 -*-
from __future__ import annotations

from agents.EnnoScholar import scientific_query_workflow as w

INTENT = {
    "verrou_id": "regression-v167",
    "verrou_title": "Compresseur LOCAL123 : incertitude sur l’impact du débit d’eau et conditions sévères sur température sortie",
    "source_passages": [
        "Les essais actuels montrent une variation significative de la température d’air en sortie du compresseur selon le débit d’eau et les conditions d’aspiration.",
        "Les conditions de fonctionnement plus sévères n’ont pas encore été explorées ni validées.",
    ],
    "technical_object": "LOCAL123 compresseur impact debit eau",
    "phenomenon": "incertitude impact temperature",
    "key_terms_en": ["compressor", "water flow rate", "air outlet temperature", "suction conditions"],
    "methods": [],
    "constraints": [],
}

INVALID = {
    "scientific_object": [],
    "phenomena": [],
    "independent_variables": [],
    "response_variables": [],
    "operating_conditions": [],
    "methods": [],
    "validation_concepts": [],
    "local_identifiers": [],
    "ambiguities": [],
}

VALID = {
    "scientific_object": [
        {"term_en": "compressor", "source_phrase": "sortie du compresseur"},
    ],
    "phenomena": [
        {"term_en": "outlet air temperature variation", "source_phrase": "variation significative de la température d’air en sortie"},
    ],
    "independent_variables": [
        {"term_en": "water flow rate", "source_phrase": "débit d’eau"},
    ],
    "response_variables": [
        {"term_en": "compressor outlet air temperature", "source_phrase": "température d’air en sortie du compresseur"},
    ],
    "operating_conditions": [
        {"term_en": "suction conditions", "source_phrase": "conditions d’aspiration"},
    ],
    "methods": [],
    "validation_concepts": [
        {"term_en": "experimental validation", "source_phrase": "explorées ni validées"},
    ],
    "local_identifiers": [
        {"value": "LOCAL123", "source_phrase": "Compresseur LOCAL123"},
    ],
    "ambiguities": [
        {"source_term": "débit", "resolved_en": "water flow rate", "source_phrase": "débit d’eau"},
    ],
}


def test_repair_prevents_zero_query(monkeypatch):
    monkeypatch.setattr(w._legacy, "_call_llm_planner", lambda evidence, ids: (INVALID, {"ok": True, "step": "initial"}))
    monkeypatch.setattr(w, "_call_repair_llm", lambda **kwargs: (VALID, {"ok": True, "step": "repair"}))
    result = w.run_query_workflow(INTENT)
    assert result["workflow"]["status"] == w.STATUS_READY
    assert result["workflow"]["search_allowed"] is True
    assert result["workflow"]["attempts"] == 2
    assert result["queries"]
    joined = " ".join(q["query"].lower() for q in result["queries"])
    assert "local123" not in joined
    assert "debit" not in joined
    assert "water" in joined and "compressor" in joined


def test_failure_is_explicit_not_no_articles(monkeypatch):
    monkeypatch.setattr(w._legacy, "_call_llm_planner", lambda evidence, ids: (INVALID, {"ok": True}))
    monkeypatch.setattr(w, "_call_repair_llm", lambda **kwargs: (INVALID, {"ok": True}))
    monkeypatch.setattr(w._legacy, "_fallback_plan", lambda intent, evidence, ids: INVALID)
    result = w.run_query_workflow(INTENT)
    assert result["workflow"]["status"] == w.STATUS_FAILED
    assert result["workflow"]["search_allowed"] is False
    assert result["queries"] == []


def test_provider_adapter_keeps_scientific_meaning(monkeypatch):
    monkeypatch.setattr(w._legacy, "_call_llm_planner", lambda evidence, ids: (VALID, {"ok": True}))
    enriched = w.attach_query_plan(INTENT)
    q = enriched["search_queries"][0]["query"]
    short = w.adapt_query_for_provider(q, "crossref", enriched)
    assert short
    assert len(short.split()) <= 8
    assert "LOCAL123" not in short


def test_query_workflow_metadata_is_attached(monkeypatch):
    monkeypatch.setattr(w._legacy, "_call_llm_planner", lambda evidence, ids: (VALID, {"ok": True}))
    enriched = w.attach_query_plan(INTENT)
    assert enriched["query_builder_version"] == w.WORKFLOW_VERSION
    assert enriched["query_workflow"]["status"] == w.STATUS_READY
    assert enriched["query_workflow"]["query_count"] > 0
    assert enriched["scientific_query_plan"]["workflow_status"] == w.STATUS_READY


def test_validator_distinguishes_planning_failure_from_no_articles():
    from agents.EnnoScholar.verrou_scientific_validator import validate_verrou_scientifically

    result = validate_verrou_scientifically(
        {}, [], [], [],
        search_status={
            "query_planning_failed": True,
            "search_executed": False,
            "query_workflow": {"status": "QUERY_PLANNING_FAILED"},
        },
    )
    assert result["decision"] == "recherche_non_lancee_planification_requetes"
    assert result["search_executed"] is False
    assert result["query_planning_failed"] is True
    assert "n’a pas été lancée" in result["gap_analysis"]
