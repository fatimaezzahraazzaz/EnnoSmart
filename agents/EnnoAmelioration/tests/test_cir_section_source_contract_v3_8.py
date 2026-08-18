from types import SimpleNamespace
import pytest

from agents.EnnoAmelioration.application.research_context_bridge_v38 import (
    build_research_conversation_context,
    enrich_direct_research_context,
    filter_candidates_against_section_context,
)
from agents.EnnoScholar.scientific_intent_builder import build_scientific_intent
from agents.EnnoScholar.query_builder import KIND_PRIORITY

SECTION = """
La base MSTAR constitue un jeu de données de référence pour l'ATR sur images SAR.
Elle contient des images de cibles acquises selon différents angles.
La base SAMPLE associe des données SAR mesurées et des données SAR simulées afin
d'étudier l'apprentissage et la généralisation des méthodes de reconnaissance
automatique de cibles.
"""

def _direct():
    return {
        "research_context": {
            "local_context": "VOISIN INTERDIT",
            "context_text": "AUTRE VOISIN INTERDIT",
            "research_objective": "MESSAGE INTERDIT",
            "search_strategy": {"diagnostic_policy": "not_required"},
            "search_readiness": {"ready": True},
        },
        "research_targets": [{
            "research_target_id": "1.3.1.1",
            "title": "Donnée SAR publiquement disponibles",
            "text": "ancien",
            "raw_item": {
                "text": "ancien",
                "source_text": "ancien",
                "consultant_instruction": "MESSAGE INTERDIT",
                "supporting_passages": [{"text": "VOISIN INTERDIT"}],
            },
            "context": {"local_context": "voisin"},
            "research_context": {"local_context": "voisin"},
        }],
    }

def _request(instruction: str):
    return SimpleNamespace(
        target_text=SECTION,
        target_section_title="Donnée SAR publiquement disponibles",
        target_section_id="1.3.1.1",
        instruction=instruction,
    )

def test_different_messages_same_section_same_scientific_payload():
    a = enrich_direct_research_context(
        _request("Renforce cette partie scientifiquement et trouve des sources."),
        _direct(),
    )
    b = enrich_direct_research_context(
        _request("La recherche précédente ne me plaît pas, recommence."),
        _direct(),
    )
    assert a["research_targets"] == b["research_targets"]
    assert a["research_context"] == b["research_context"]

def test_only_exact_section_reaches_scholar():
    enriched = enrich_direct_research_context(
        _request("Renforce scientifiquement cette section."),
        _direct(),
    )
    rc = enriched["research_context"]
    assert rc["section_text"] == SECTION.strip()
    assert rc["scientific_source_policy"] == "request_target_text_only"
    assert rc["consultant_message_used_for_scientific_context"] is False
    for forbidden in (
        "research_objective",
        "consultant_instruction",
        "consultant_focus_entities",
        "local_context",
        "context_text",
        "local_cir_context",
    ):
        assert forbidden not in rc

    target = enriched["research_targets"][0]
    raw = target["raw_item"]
    assert target["text"] == SECTION.strip()
    assert raw["text"] == SECTION.strip()
    assert raw["source_text"] == SECTION.strip()
    assert raw["supporting_passages"] == []
    assert "consultant_instruction" not in raw
    assert "MESSAGE INTERDIT" not in repr(target)
    assert "VOISIN INTERDIT" not in repr(target)

def test_section_entities_and_queries_are_from_document():
    enriched = enrich_direct_research_context(_request("Je veux la renforcer."), _direct())
    contract = enriched["v3_8_source_section_contract"]
    for value in ("MSTAR", "SAMPLE", "SAR", "ATR"):
        assert value in contract["section_entities"]
    queries = [
        row["query"]
        for row in enriched["research_targets"][0]["suggested_queries"]
    ]
    assert any("MSTAR" in q and "SAMPLE" in q for q in queries)

def test_missing_section_text_stops_instead_of_using_message():
    request = SimpleNamespace(
        target_text="",
        target_section_title="Donnée SAR publiquement disponibles",
        target_section_id="1.3.1.1",
        instruction="MSTAR SAMPLE SAR ATR cherche des sources",
    )
    with pytest.raises(RuntimeError):
        enrich_direct_research_context(request, _direct())

def test_conversation_memory_contains_no_feedback_or_previous_query_text():
    context = {
        "scholar_handoff": {"internal_response": {"queries": ["ancienne requête"]}},
        "research_sources": [{"title": "Ancienne source"}],
    }
    memory = build_research_conversation_context(
        context,
        previous_target_section_id="1.3.1.1",
        current_target_section_id="1.3.1.1",
        current_target_section_title="Donnée SAR publiquement disponibles",
        consultant_feedback="Concentre-toi sur MSTAR et SAMPLE",
    )
    assert memory["same_target"] is True
    assert memory["previous_candidate_count"] == 1
    assert "consultant_feedback" not in memory
    assert "previous_queries" not in memory
    assert "MSTAR" not in repr(memory)
    assert "SAMPLE" not in repr(memory)

def test_scientific_intent_keeps_section_entities_as_strong_anchors():
    intent = build_scientific_intent(
        {
            "research_target_id": "1.3.1.1",
            "research_target_type": "scientific_landscape",
            "title": "Donnée SAR publiquement disponibles",
            "text": SECTION,
            "raw_item": {
                "text": SECTION,
                "source_text": SECTION,
                "section_entities": ["MSTAR", "SAMPLE", "SAR", "ATR"],
                "supporting_passages": [],
            },
            "context": {},
            "research_context": {},
        },
        domain_detection={},
        diagnostic_context={},
    )
    anchors = set(intent.get("strong_anchors") or [])
    assert "MSTAR" in anchors
    assert "SAMPLE" in anchors

def test_query_priority_favors_section_source():
    assert KIND_PRIORITY["section_source_exact_v3_8"] > KIND_PRIORITY["cir_domain_profile_query"]

def test_gate_rejects_insar_soil_and_keeps_sar_atr():
    candidates = [
        {
            "candidate_id": "insar",
            "title": "Phase Linking algorithms for Multi-Temporal Radar Interferometry",
            "abstract": "Multi-temporal InSAR phase estimation for surface displacement.",
            "raw_payloads": [],
        },
        {
            "candidate_id": "soil",
            "title": "Estimation of surface roughness from Sentinel-1 SAR data",
            "abstract": "SAR remote sensing for agricultural soil roughness and moisture.",
            "raw_payloads": [],
        },
        {
            "candidate_id": "mstar",
            "title": "Robust SAR automatic target recognition on MSTAR measured data",
            "abstract": "Synthetic aperture radar automatic target recognition on MSTAR.",
            "raw_payloads": [],
        },
        {
            "candidate_id": "general_atr",
            "title": "Deep learning for synthetic aperture radar automatic target recognition",
            "abstract": "Synthetic aperture radar automatic target recognition datasets.",
            "raw_payloads": [],
        },
    ]
    direct_context = enrich_direct_research_context(
        _request("Renforce scientifiquement cette section."),
        _direct(),
    )
    meta = {
        "scientific_intent": {
            "primary_core_concepts": [
                "synthetic aperture radar",
                "automatic target recognition",
            ],
            "concept_aliases": {
                "synthetic aperture radar": ["synthetic aperture radar", "SAR"],
                "automatic target recognition": [
                    "automatic target recognition", "ATR", "target recognition"
                ],
            },
            "literal_source_acronyms": ["SAR", "ATR"],
        }
    }
    kept, report = filter_candidates_against_section_context(
        candidates,
        direct_context=direct_context,
        search_metadata=meta,
    )
    assert [x["candidate_id"] for x in kept] == ["mstar", "general_atr"]
    assert report["removed_count"] == 2
    assert report["consultant_message_used"] is False
