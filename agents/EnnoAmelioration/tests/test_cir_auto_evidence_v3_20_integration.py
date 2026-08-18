import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_full_cir_service_uses_auto_evidence_selector():
    path = ROOT / "backend_api" / "services" / "improvement_service.py"
    text = path.read_text(encoding="utf-8")
    assert "auto_evidence_selector_v320" in text
    assert "select_sources(" in text
    assert "build_traceable_evidence(" in text


def test_full_cir_research_no_longer_waits_for_human_source_decision():
    path = ROOT / "backend_api" / "services" / "improvement_service.py"
    text = path.read_text(encoding="utf-8")
    start = text.index("def _progressive_launch_current_research(")
    end = text.find("\ndef ", start + 10)
    block = text[start : end if end > 0 else None]
    assert "select_sources(" in block
    assert '_progressive_write_current_unit(' in block
    assert 'unit["status"] = "awaiting_sources"' not in block


def test_full_cir_auto_selection_prepares_fulltext_before_writing():
    path = ROOT / "backend_api" / "services" / "improvement_service.py"
    text = path.read_text(encoding="utf-8")
    assert "def _automatic_prepare_selected_sources(" in text
    assert "decide_guided_research_sources(" in text
    assert 'decision_actor="ennoamel_auto"' in text
    assert 'row.get("article_card_ready") is True' in text
    assert "bind_prepared_sources(" in text
    assert "auto_article_ids=ready_article_ids" in text


def test_writer_failure_after_ready_cards_is_not_reported_as_no_source():
    path = ROOT / "backend_api" / "services" / "improvement_service.py"
    text = path.read_text(encoding="utf-8")
    start = text.index("def _progressive_launch_current_research(")
    end = text.index("\ndef _progressive_write_current_unit(", start)
    block = text[start:end]
    writer_call = block.index("_progressive_write_current_unit(")
    writer_except = block.index("except Exception as exc:", writer_call)
    tail = block[writer_except:]
    assert "_progressive_scientific_write_fallback(" in tail
    assert "_progressive_no_source_fallback(" not in tail


def test_full_cir_has_one_cached_initial_diagnostic_and_scoped_ambiguity_gate():
    path = ROOT / "backend_api" / "services" / "improvement_service.py"
    text = path.read_text(encoding="utf-8")
    assert "def _ensure_progressive_initial_diagnostic(" in text
    assert "ensure_initial_diagnostic_context(" in text
    assert "allow_scoped_diagnostic=ambiguous" in text
    assert "diagnostic_context_override=cached_context" in text


def test_progressive_candidate_exposes_passages_articles_and_advisory_warnings():
    from backend_api.services.improvement_service import _progressive_structured_result

    base = "1.4 Verrou\n\nTexte original documenté."
    workflow = {
        "patches": [
            {
                "unit_id": "u1",
                "mode": "scientific",
                "replacement": "1.4 Verrou\n\nTexte renforcé avec preuve [A1].",
            }
        ],
        "units": [
            {
                "unit_id": "u1",
                "section_id": "s1",
                "section_ref": "1.4",
                "section_title": "Verrou",
                "start": 0,
                "end": len(base),
                "source_sha256": hashlib.sha256(base.encode("utf-8")).hexdigest(),
                "research": {
                    "auto_selection": {
                        "selected": [
                            {
                                "candidate_id": "c1",
                                "article_id": 42,
                                "title": "Article test",
                                "doi": "10.1/test",
                            }
                        ]
                    },
                    "final_evidence": {
                        "auto_accepted": [
                            {
                                "candidate_id": "c1",
                                "article_id": 42,
                                "citation_id": "A1",
                                "title": "Article test",
                                "evidence_excerpt": "Passage scientifique extrait.",
                            }
                        ]
                    },
                },
                "generation": {
                    "unsupported_claims": [
                        {"reason": "Citation à vérifier", "severity": "warning"}
                    ]
                },
            }
        ],
    }

    result = _progressive_structured_result(workflow, base)
    assert result["blocking"] is False
    assert result["quality_control_mode"] == "advisory_only"
    assert result["changes"][0]["before"] == base
    assert "Texte renforcé" in result["changes"][0]["after"]
    assert result["changes"][0]["sources"][0]["title"] == "Article test"
    assert result["sources_used"][0]["evidence_excerpt"] == "Passage scientifique extrait."
    assert result["unsupported_claims"][0]["severity"] == "warning"


def test_frontend_comparison_displays_article_excerpt_and_consult_button():
    path = ROOT / "frontend" / "components" / "ennosmart" / "ennoamelioration-page.tsx"
    text = path.read_text(encoding="utf-8")
    assert "Passage amélioré" in text
    assert "Articles mobilisés pour ce passage" in text
    assert "Passage justificatif extrait" in text
    assert "Consulter l’article" in text


def test_full_cir_uses_new_workflow_version():
    path = (
        ROOT
        / "agents"
        / "EnnoAmelioration"
        / "application"
        / "cir_section_progressive_v320.py"
    )
    text = path.read_text(encoding="utf-8")
    assert "ennoamel_cir_section_auto_evidence_v3_20" in text
    assert '"granularity": "section"' in text
