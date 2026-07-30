from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend_api"
for path in (ROOT, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from services import guided_research_source_preparation_service as service
from services.article_card_builder import _sync_card_source_context


def test_zenodo_publication_requested_as_scientific_is_a_publication() -> None:
    source = {
        "candidate_kind": "research_output",
        "query_kind": "scientific_evidence",
        "title": "A scientific publication",
        "doi": "10.1234/example",
        "publication_types": ["publication"],
    }
    assert service.is_scientific_publication_source(source) is True


def test_technical_repository_is_not_a_scientific_publication() -> None:
    source = {
        "candidate_kind": "software_repository",
        "query_kind": "scientific_evidence",
        "title": "Implementation repository",
        "doi": "10.1234/example",
        "publication_types": ["publication"],
    }
    assert service.is_scientific_publication_source(source) is False


def test_direct_fulltext_success_does_not_call_mcp(monkeypatch) -> None:
    calls = {"mcp": 0}

    monkeypatch.setattr(
        service,
        "resolve_and_extract_fulltext_for_article",
        lambda *args, **kwargs: {
            "ok": True,
            "status": "text_extracted_pdf",
            "full_text_status": "text_extracted",
        },
    )

    def fail_if_called(*args, **kwargs):
        calls["mcp"] += 1
        raise AssertionError("Le MCP ne doit pas être appelé après un succès direct.")

    monkeypatch.setattr(
        service,
        "recover_legal_fulltext_for_article",
        fail_if_called,
    )

    result = service.prepare_article_fulltext_with_mcp_fallback(
        object(),
        object(),
        SimpleNamespace(id=42),
    )

    assert result["ok"] is True
    assert result["retrieval_stage"] == "direct"
    assert result["mcp_called"] is False
    assert calls["mcp"] == 0


def test_missing_direct_pdf_always_calls_mcp(monkeypatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        service,
        "resolve_and_extract_fulltext_for_article",
        lambda *args, **kwargs: {
            "ok": False,
            "status": "direct_known_urls_exhausted",
            "full_text_status": "missing_or_blocked_fulltext",
            "needs_legal_recovery": True,
        },
    )

    def legal(*args, **kwargs):
        observed.update(kwargs)
        return {
            "ok": True,
            "status": "legal_pdf_fulltext_extracted",
            "full_text_status": "text_extracted",
            "retrieved_via_mcp": True,
            "mcp_called": True,
        }

    monkeypatch.setattr(service, "recover_legal_fulltext_for_article", legal)

    result = service.prepare_article_fulltext_with_mcp_fallback(
        object(),
        object(),
        SimpleNamespace(id=73),
    )

    assert result["ok"] is True
    assert result["retrieval_stage"] == "legal_mcp"
    assert result["mcp_called"] is True
    assert observed["force_refresh"] is True
    assert observed["search_all"] is False


def test_source_stays_forbidden_when_direct_and_mcp_fail(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "resolve_and_extract_fulltext_for_article",
        lambda *args, **kwargs: {
            "ok": False,
            "status": "direct_known_urls_exhausted",
            "full_text_status": "missing_or_blocked_fulltext",
        },
    )
    monkeypatch.setattr(
        service,
        "recover_legal_fulltext_for_article",
        lambda *args, **kwargs: {
            "ok": False,
            "status": "no_verified_legal_fulltext",
            "full_text_status": "missing_or_blocked_fulltext",
            "mcp_called": True,
        },
    )

    result = service.prepare_article_fulltext_with_mcp_fallback(
        object(),
        object(),
        SimpleNamespace(id=99),
    )

    assert result["ok"] is False
    assert result["status"] == "fulltext_unavailable_after_mcp"
    assert result["mcp_called"] is True


def test_reused_article_card_keeps_guided_source_context() -> None:
    article = SimpleNamespace(
        source_json={
            "guided_research_source": True,
            "guided_candidate_id": "SRC-42",
            "candidate_kind": "scientific_article",
            "section_ids": ["methods"],
            "target_verrous": ["12", "13"],
        }
    )

    card = _sync_card_source_context({"citation_label": "A1"}, article)

    assert card["guided_research_source"] is True
    assert card["guided_candidate_id"] == "SRC-42"
    assert card["section_ids"] == ["methods"]
    assert card["target_verrous"] == ["12", "13"]
    assert card["verrou_ids"] == ["12", "13"]
