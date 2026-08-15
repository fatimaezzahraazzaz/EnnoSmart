from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend_api"
for path in (ROOT, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from services import ennoscholar_conversation_state_service as conversation_state


def test_archived_state_of_art_is_isolated_by_conversation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project = SimpleNamespace(id=1)
    monkeypatch.setattr(
        conversation_state,
        "_project_base",
        lambda current: tmp_path / f"project_{current.id}",
    )

    first = conversation_state.archive_conversation_state_of_art(
        project=project,
        session_id="11111111-1111-1111-1111-111111111111",
        markdown="# État de l'art A\n\nContenu de la première conversation.",
        payload={"ok": True, "status": "ready"},
    )
    first_path = Path(first["markdown_path"])
    first_before = first_path.read_text(encoding="utf-8")

    second = conversation_state.archive_conversation_state_of_art(
        project=project,
        session_id="22222222-2222-2222-2222-222222222222",
        markdown="# État de l'art B\n\nContenu de la nouvelle conversation.",
        payload={"ok": True, "status": "ready"},
    )

    assert Path(second["markdown_path"]) != first_path
    assert first_path.read_text(encoding="utf-8") == first_before
    assert conversation_state.list_conversation_versions(
        project,
        "11111111-1111-1111-1111-111111111111",
    )[0]["markdown_sha256"] == first["markdown_sha256"]
    assert conversation_state.list_conversation_versions(
        project,
        "22222222-2222-2222-2222-222222222222",
    )[0]["markdown_sha256"] == second["markdown_sha256"]


def test_conversation_roots_do_not_overlap(monkeypatch, tmp_path: Path) -> None:
    project = SimpleNamespace(id=7)
    monkeypatch.setattr(
        conversation_state,
        "_project_base",
        lambda current: tmp_path / f"project_{current.id}",
    )

    first = conversation_state.conversation_root(
        project,
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )
    second = conversation_state.conversation_root(
        project,
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )

    assert first != second
    assert first not in second.parents
    assert second not in first.parents
