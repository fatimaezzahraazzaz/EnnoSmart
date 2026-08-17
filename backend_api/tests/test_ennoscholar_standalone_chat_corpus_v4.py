# -*- coding: utf-8 -*-
from services.guided_research_source_preparation_service import (
    _guided_terminal_evidence_payload,
)


def test_ready_card_is_terminal_fulltext_ready():
    payload = _guided_terminal_evidence_payload(
        {
            "status": "fulltext_ready",
            "retrieval_stage": "direct",
            "fulltext_ready": True,
            "article_card_ready": True,
            "ready_for_writing": True,
            "mcp_called": False,
        }
    )
    assert payload["evidence_status"] == "FULLTEXT_READY"
    assert payload["fulltext_ready"] is True
    assert payload["evidence_usable"] is True
    assert payload["candidate_only"] is False
    assert payload["access_check_status"] == "completed"


def test_fulltext_ready_but_card_missing_is_not_pending():
    payload = _guided_terminal_evidence_payload(
        {
            "status": "article_card_unavailable",
            "retrieval_stage": "direct",
            "fulltext_ready": True,
            "article_card_ready": False,
            "ready_for_writing": False,
            "mcp_called": False,
        }
    )
    assert payload["evidence_status"] == "FULLTEXT_READY"
    assert payload["fulltext_ready"] is True
    assert payload["evidence_usable"] is False
    assert payload["article_card_ready"] is False
    assert payload["candidate_only"] is True


def test_unavailable_after_mcp_is_terminal_not_pending():
    payload = _guided_terminal_evidence_payload(
        {
            "status": "fulltext_unavailable_after_mcp",
            "retrieval_stage": "unavailable",
            "fulltext_ready": False,
            "article_card_ready": False,
            "ready_for_writing": False,
            "mcp_called": True,
        }
    )
    assert payload["evidence_status"] == "ACCESS_UNAVAILABLE"
    assert payload["access_check_status"] == "completed"
    assert payload["fulltext_ready"] is False
    assert payload["candidate_only"] is True
    assert payload["recommended_action"] == "import_authorized_pdf"


def test_preparation_exception_is_terminal_failure():
    payload = _guided_terminal_evidence_payload(
        {
            "status": "source_preparation_exception",
            "retrieval_stage": "unavailable",
            "fulltext_ready": False,
            "article_card_ready": False,
            "ready_for_writing": False,
            "mcp_called": False,
        }
    )
    assert payload["evidence_status"] == "EXTRACTION_FAILED"
    assert payload["access_check_status"] == "completed"
