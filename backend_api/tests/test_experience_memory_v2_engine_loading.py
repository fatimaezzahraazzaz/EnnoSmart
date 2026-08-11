from __future__ import annotations

from pathlib import Path
import json
import sys

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))


def test_root_memory_engine_is_not_shadowed_by_backend_scripts_package():
    # Reproduit le contexte d'un lancement uvicorn depuis backend_api.
    import scripts

    assert Path(scripts.__file__).resolve() == (BACKEND_DIR / "scripts" / "__init__.py").resolve()

    from services.experience_memory_v2_service import _load_engine

    engine = _load_engine()
    assert Path(engine.__file__).resolve() == (ROOT_DIR / "scripts" / "experience_memory_v2_engine.py").resolve()
    assert callable(engine.build_cir_final_v2)


class _FakeEngine:
    def __init__(self, catalog_path: Path, *, fail: bool = False):
        self.catalog_path = catalog_path
        self.fail = fail

    def rebuild_global_graph_and_catalog(self, *, reset_chroma: bool):
        assert reset_chroma is True
        if self.fail:
            raise RuntimeError("rebuild failed")
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self.catalog_path.write_text(
            json.dumps({"ok": True, "projects": [], "chunks_count": 0, "cards_count": 0}),
            encoding="utf-8",
        )
        return {"ok": True, "projects_count": 0}


def _isolated_memory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from services import experience_memory_v2_service as service

    memory_root = tmp_path / "memory_v2"
    library_root = tmp_path / "organismes"
    monkeypatch.setattr(service, "V2_ROOT", memory_root)
    monkeypatch.setattr(service, "ORGANISMES_DIR", library_root)
    monkeypatch.setattr(service, "V2_CATALOG", memory_root / "catalog_v2.json")
    monkeypatch.setattr(service, "V2_CHROMA_DIR", memory_root / "chroma")
    monkeypatch.setattr(service, "V2_RUNS_DIR", memory_root / "runs")
    monkeypatch.setattr(service, "V2_CARDS_DIR", memory_root / "cards")

    source = library_root / "Acme" / "projects" / "Radar" / "years" / "2024" / "cir_final_consultant" / "current" / "final.txt"
    source.parent.mkdir(parents=True)
    source.write_text("CIR final", encoding="utf-8")
    run = memory_root / "runs" / "radar_123.run_v2.json"
    run.parent.mkdir(parents=True)
    run.write_text(json.dumps({"ok": True, "source_id": "radar_123", "organisme": "Acme", "project": "Radar", "year": "2024"}), encoding="utf-8")
    cards = memory_root / "cards" / "radar_123.cards.json"
    cards.parent.mkdir(parents=True)
    cards.write_text("[]", encoding="utf-8")
    return service, source, run, cards


def test_remove_project_archives_library_and_artifacts_then_rebuilds(monkeypatch, tmp_path: Path):
    service, source, run, cards = _isolated_memory(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "_load_engine", lambda: _FakeEngine(service.V2_CATALOG))

    with pytest.raises(PermissionError):
        service.remove_memory_v2_project("Acme", "Radar", "2024", confirmation="oui")

    result = service.remove_memory_v2_project(
        "Acme", "Radar", "2024", confirmation="SUPPRIMER_DE_MEMORY_V2"
    )

    assert result["ok"] is True
    assert result["recoverable"] is True
    assert result["sharepoint_modified"] is False
    assert result["power_automate_inbox_modified"] is False
    assert not source.exists()
    assert not run.exists()
    assert not cards.exists()
    archive = Path(result["archive_root"])
    assert (archive / "removal_report.json").is_file()
    assert any(path.name == "final.txt" for path in archive.rglob("*"))
    assert any(path.name == "radar_123.run_v2.json" for path in archive.rglob("*"))


def test_remove_project_rolls_back_when_vector_rebuild_fails(monkeypatch, tmp_path: Path):
    service, source, run, cards = _isolated_memory(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "_load_engine", lambda: _FakeEngine(service.V2_CATALOG, fail=True))

    with pytest.raises(RuntimeError, match="rebuild failed"):
        service.remove_memory_v2_project(
            "Acme", "Radar", "2024", confirmation="SUPPRIMER_DE_MEMORY_V2"
        )

    assert source.is_file()
    assert run.is_file()
    assert cards.is_file()
