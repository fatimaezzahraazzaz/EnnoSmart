# -*- coding: utf-8 -*-
from agents.EnnoScholar.state_of_art.visual_placement_service import (
    build_visual_placements_shared,
    screen_visual_candidate,
)


def _draft(citation="S1"):
    return {
        "title": "État de l'art",
        "sections": [{
            "section_id": "S1",
            "title": "Méthodes",
            "paragraphs": [{
                "text": (
                    "La méthode repose sur une architecture neuronale "
                    "et une validation expérimentale."
                ),
                "citations": [citation],
            }],
        }],
    }


def test_logo_is_rejected():
    ok, reasons = screen_visual_candidate({
        "visual_id": "logo_1",
        "visual_type": "logo",
        "caption": "Publisher logo",
        "width": 240,
        "height": 100,
    })
    assert not ok
    assert "non_scientific_visual_type" in reasons


def test_tiny_crop_is_rejected():
    ok, reasons = screen_visual_candidate({
        "visual_id": "tiny",
        "visual_type": "figure",
        "caption": "Experimental architecture",
        "width": 55,
        "height": 40,
    })
    assert not ok
    assert "tiny_crop" in reasons


def test_scientific_table_with_text_is_allowed():
    ok, reasons = screen_visual_candidate({
        "visual_id": "table_1",
        "visual_type": "table",
        "caption": "Comparison of experimental protocols",
        "width": 900,
        "height": 500,
        "text_density": 0.95,
    })
    assert ok, reasons


def test_useful_visual_from_cited_article_is_placed():
    cards = [{
        "source_id": "S1",
        "title": "Neural architecture study",
        "visual_evidence": [{
            "visual_id": "fig_arch",
            "visual_type": "diagram",
            "figure_label": "Figure 2",
            "caption": "Neural architecture and experimental validation workflow",
            "context": "Architecture neuronale utilisée pour la validation expérimentale",
            "width": 900,
            "height": 600,
            "quality_score": 0.9,
        }],
    }]
    placements, report = build_visual_placements_shared(
        draft=_draft("S1"),
        contract={"sections": [{"section_id": "S1", "title": "Méthodes"}]},
        cards_payload={},
        cards=cards,
        citation_field="source_id",
        article_min_similarity=0.01,
        project_min_similarity=0.01,
        include_project_visuals=False,
    )
    assert len(placements) == 1
    assert placements[0]["visual_id"] == "fig_arch"
    assert report["placed_count"] == 1


def test_visual_from_uncited_article_is_not_placed():
    cards = [{
        "source_id": "S2",
        "title": "Other study",
        "visual_evidence": [{
            "visual_id": "fig_other",
            "visual_type": "diagram",
            "caption": "Neural architecture and experimental validation",
            "width": 900,
            "height": 600,
        }],
    }]
    placements, report = build_visual_placements_shared(
        draft=_draft("S1"),
        contract={"sections": [{"section_id": "S1", "title": "Méthodes"}]},
        cards_payload={},
        cards=cards,
        citation_field="source_id",
        article_min_similarity=0.01,
        include_project_visuals=False,
    )
    assert placements == []
    assert report["no_visual_reason"] == "no_visual_from_cited_sources"


def test_no_visual_is_explicitly_diagnosed():
    placements, report = build_visual_placements_shared(
        draft=_draft("S1"),
        contract={"sections": [{"section_id": "S1", "title": "Méthodes"}]},
        cards_payload={},
        cards=[{"source_id": "S1", "title": "No figure paper"}],
        citation_field="source_id",
        include_project_visuals=False,
    )
    assert placements == []
    assert report["no_visual_reason"] == "no_visual_evidence"
