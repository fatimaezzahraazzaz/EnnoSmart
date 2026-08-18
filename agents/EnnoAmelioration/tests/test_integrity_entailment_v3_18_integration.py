from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_agent_uses_integrity_as_advisory_audit():
    path = ROOT / "agents" / "EnnoAmelioration" / "application" / "agent.py"
    text = path.read_text(encoding="utf-8")
    assert "prepare_writer_request" in text
    assert "verify_revision_integrity" in text
    assert '"quality_control_mode": "advisory_only"' in text
    assert '"automatic_integrity_retry": False' in text


def test_anchored_enrichment_checks_entailment():
    path = (
        ROOT
        / "agents"
        / "EnnoScholar"
        / "state_of_art"
        / "existing_review_enrichment_service.py"
    )
    text = path.read_text(encoding="utf-8")
    assert "verify_additions_entailment" in text
    assert "[V3.25][AnchoredEntailment][ADVISORY]" in text
    assert "errors.extend(_v318_issues)" not in text
    assert '"integrity_warnings": _v318_warnings' in text
    assert '"controls_blocked_candidate": False' in text
    assert "errors = [] if accepted else list(validation_findings)" in text


def test_v315_coverage_still_present():
    path = ROOT / "agents" / "EnnoAmelioration" / "application" / "agent.py"
    text = path.read_text(encoding="utf-8")
    assert "build_coverage_report" in text
    assert "preuves_validees_non_utilisees" in text
