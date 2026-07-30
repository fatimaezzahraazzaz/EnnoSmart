from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.EnnoScholar.state_of_art.phase_5_state_of_art_writer_service import (
    _validate_generated_section,
    apply_writing_source_policy,
    extract_supplemental_source_cards,
)


def test_scientific_abstract_is_not_converted_to_citable_card() -> None:
    payload = {
        "sources": [
            {
                "candidate_id": "SRC-1",
                "candidate_kind": "scientific_article",
                "consultant_decision": "accepted",
                "title": "A scientific article",
                "doi": "10.1234/article",
                "abstract": "A sufficiently long abstract that must not become scientific evidence.",
            }
        ]
    }

    assert extract_supplemental_source_cards(payload, []) == []


def test_documentation_can_be_supplemental_and_keeps_target_verrous() -> None:
    payload = {
        "sources": [
            {
                "candidate_id": "DOC-1",
                "candidate_kind": "official_documentation",
                "consultant_decision": "accepted",
                "title": "Official tool documentation",
                "url": "https://example.org/docs",
                "content_excerpt": (
                    "This official documentation explains the implementation "
                    "procedure and configuration of the technical tool."
                ),
                "target_verrous": ["12", "13"],
                "evidence_scope": ["definition", "procedure"],
            }
        ]
    }

    cards = extract_supplemental_source_cards(payload, [])

    assert len(cards) == 1
    assert cards[0]["documentation_scope_only"] is True
    assert cards[0]["scientific_evidence_eligible"] is False
    assert cards[0]["verrou_ids"] == ["12", "13"]


def test_available_citation_is_not_automatically_required() -> None:
    long_body = " ".join(["Analyse scientifique étayée [A1]."] * 85)
    section = {
        "section_id": "section_1",
        "title": "Section 1",
        "target_words": 350,
        "available_citations": ["A1", "A2"],
        "required_citations": ["A1"],
        "verrous": [],
    }
    generated = {
        "section_id": "section_1",
        "title": "Section 1",
        "content": long_body,
        "subsections": [],
    }

    validation = _validate_generated_section(generated, section)

    assert validation["ok"] is True
    assert validation["missing_required_citations"] == []


def test_baseline_policy_excludes_guided_research_cards() -> None:
    cards = [
        {
            "citation_label": "A1",
            "article_id": 1,
            "title": "Article initial 1",
            "verrou_ids": ["V1"],
            "guided_research_source": False,
        },
        {
            "citation_label": "A2",
            "article_id": 2,
            "title": "Article initial 2",
            "verrou_ids": ["V2"],
            "guided_research_source": False,
        },
        {
            "citation_label": "A3",
            "article_id": 3,
            "title": "Article ajouté par recherche",
            "guided_research_source": True,
        },
    ]
    selected, report = apply_writing_source_policy(
        cards,
        {
            "writing_source_policy": {
                "scope": "baseline_verrou_corpus",
                "requested_source_count": 2,
            }
        },
    )

    assert [card["citation_label"] for card in selected] == ["A1", "A2"]
    assert report["guided_sources_excluded"] is True
    assert report["excluded_source_count"] == 1


def test_section_length_guard_uses_total_plan_size() -> None:
    body = " ".join(["Analyse scientifique étayée [A1]."] * 260)
    section = {
        "section_id": "methods",
        "title": "Méthodes",
        "available_citations": ["A1"],
        "required_citations": ["A1"],
        "verrous": [],
    }
    generated = {
        "section_id": "methods",
        "title": "Méthodes",
        "content": body,
        "subsections": [],
    }

    validation = _validate_generated_section(
        generated,
        section,
        total_sections=10,
    )

    assert validation["target_words"] == 650
    assert validation["word_count"] > validation["maximum_words"]
    assert "section_too_long" in validation["errors"]
