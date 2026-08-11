from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend_api"
for path in (ROOT, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from routers import scholar
from services import ennoscholar_state_of_art_orchestrator as orchestrator


def test_internal_writer_rejection_is_not_an_http_400(monkeypatch) -> None:
    project = SimpleNamespace(
        id=1,
        organisme="Org",
        project_name="Projet",
        year=2026,
    )
    monkeypatch.setattr(
        scholar,
        "get_project_for_user",
        lambda db, project_id, current_user: project,
    )

    def fail_generation(**kwargs):
        raise RuntimeError("Phase 5 bloquée par un contrôle interne")

    monkeypatch.setattr(
        orchestrator,
        "generate_state_of_art_after_consultant_selection",
        fail_generation,
    )

    result = scholar._run_state_of_art_full_pipeline(
        project_id=1,
        force_phase3=False,
        force_article_cards=False,
        enable_polish=False,
        db=object(),
        current_user=object(),
    )

    assert result["ok"] is False
    assert result["status"] == "evidence_revision_required"
    assert result["retryable"] is True
    assert "garde" not in result["assistant_message"].casefold()
    assert "phase 5" not in result["assistant_message"].casefold()


def test_provider_429_is_returned_as_retryable_conversation_state(monkeypatch) -> None:
    project = SimpleNamespace(
        id=1,
        organisme="Org",
        project_name="Projet",
        year=2026,
    )
    monkeypatch.setattr(
        scholar,
        "get_project_for_user",
        lambda db, project_id, current_user: project,
    )

    def fail_generation(**kwargs):
        raise RuntimeError("OpenAI 429 rate limit")

    monkeypatch.setattr(
        orchestrator,
        "generate_state_of_art_after_consultant_selection",
        fail_generation,
    )

    result = scholar._run_state_of_art_full_pipeline(
        project_id=1,
        force_phase3=False,
        force_article_cards=False,
        enable_polish=False,
        db=object(),
        current_user=object(),
    )

    assert result["ok"] is False
    assert result["status"] == "writing_service_temporarily_unavailable"
    assert result["retryable"] is True
    assert "429" not in result["assistant_message"]
