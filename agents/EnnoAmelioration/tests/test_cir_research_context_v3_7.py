from types import SimpleNamespace

from agents.EnnoAmelioration.application.research_context_bridge_v37 import (
    build_focus_queries,
    build_research_conversation_context,
    enrich_direct_research_context,
    extract_named_entities,
    filter_candidates_against_section_context,
)


def test_focus_entities_are_dynamic():
    values = extract_named_entities(
        "C'est trop general. Concentre-toi sur MSTAR et SAMPLE pour SAR ATR."
    )
    assert "MSTAR" in values
    assert "SAMPLE" in values
    assert "SAR" in values
    assert "ATR" in values


def test_exact_section_is_transmitted_as_primary_context():
    request = SimpleNamespace(
        target_text="MSTAR est une base SAR. SAMPLE contient des donnees mesurees et simulees.",
        target_section_title="Donnee SAR publiquement disponibles",
        target_section_id="s1",
        instruction="Concentre-toi sur MSTAR et SAMPLE.",
    )
    direct = {
        "research_context": {"local_context": "Contexte voisin du CIR"},
        "research_targets": [{
            "research_target_id": "s1",
            "title": "Donnee SAR publiquement disponibles",
            "raw_item": {},
        }],
    }
    enriched = enrich_direct_research_context(request, direct)
    rc = enriched["research_context"]
    assert rc["section_text"] == request.target_text
    assert rc["section_context_priority"] == "exact_selected_section_is_primary"
    raw = enriched["research_targets"][0]["raw_item"]
    assert raw["source_text"] == request.target_text
    assert "MSTAR" in raw["consultant_focus_entities"]


def test_refinement_memory_is_kept_only_for_same_section():
    ctx = {
        "scholar_handoff": {
            "internal_response": {
                "queries": ["old query"],
                "research_context": {"x": 1},
            }
        },
        "research_sources": [{"title": "Old candidate"}],
    }
    same = build_research_conversation_context(
        ctx,
        previous_target_section_id="s1",
        current_target_section_id="s1",
        current_target_section_title="Section 1",
        consultant_feedback="Trop general",
    )
    assert same["same_target"] is True
    assert same["previous_queries"] == ["old query"]

    other = build_research_conversation_context(
        ctx,
        previous_target_section_id="s1",
        current_target_section_id="s2",
        current_target_section_title="Section 2",
        consultant_feedback="Nouvelle recherche",
    )
    assert other["same_target"] is False
    assert "previous_queries" not in other


def test_context_gate_rejects_smartphone_and_keeps_sar_target_article():
    candidates = [
        {
            "candidate_id": "bad",
            "title": "Smartphone camera based assessment of adiposity",
            "abstract": "A CNN estimates body fat from smartphone photographs in a clinical sample.",
            "tag": "Connexe",
            "raw_payloads": [],
        },
        {
            "candidate_id": "good",
            "title": "Highly Robust Synthetic Aperture Radar Target Recognition",
            "abstract": "SAR ATR is evaluated on MSTAR measured data using simulated SAR training images.",
            "tag": "Connexe",
            "raw_payloads": [],
        },
    ]
    direct = {
        "research_context": {
            "consultant_focus_entities": ["MSTAR", "SAMPLE", "SAR", "ATR"],
        }
    }
    meta = {
        "scientific_intent": {
            "primary_core_concepts": [
                "synthetic aperture radar",
                "automatic target recognition",
            ],
            "concept_aliases": {
                "synthetic aperture radar": ["synthetic aperture radar", "SAR"],
                "automatic target recognition": ["automatic target recognition", "ATR", "target recognition"],
            },
            "literal_source_acronyms": ["SAR", "ATR", "MSTAR", "SAMPLE"],
        }
    }
    kept, report = filter_candidates_against_section_context(
        candidates, direct_context=direct, search_metadata=meta
    )
    assert [row["candidate_id"] for row in kept] == ["good"]
    assert report["removed_count"] == 1
