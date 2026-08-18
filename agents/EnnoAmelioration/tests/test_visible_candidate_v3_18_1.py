
from agents.EnnoAmelioration.application.visible_candidate_policy_v3181 import (
    review_summary,
    visible_candidate_message,
)


def test_candidate_remains_visible_with_source_warning():
    summary = review_summary(["references_perdues:54,55"])
    assert summary["candidate_visible"] is True
    assert summary["active_version_mutated"] is False
    assert summary["requires_consultant_review"] is True


def test_candidate_remains_visible_with_scientific_warning():
    summary = review_summary(["citation_non_etayee:C1:A3:unsupported"])
    assert summary["candidate_visible"] is True
    assert summary["warning_count"] == 1


def test_message_explains_source_warning_without_blocking():
    message = visible_candidate_message(["document_block_missing:figure-p1-1"])
    assert "laisse visible" in message
    assert "version active reste inchangée" in message


def test_message_explains_scientific_warning_without_blocking():
    message = visible_candidate_message(["citation_non_etayee:C1:A3:unsupported"])
    assert "citations" in message.lower()
    assert "visible" in message.lower()


def test_clean_candidate_has_normal_message():
    message = visible_candidate_message([])
    assert "nouvelle version" in message
    assert "comparatif" in message
