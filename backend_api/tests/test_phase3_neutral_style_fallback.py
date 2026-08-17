# -*- coding: utf-8 -*-
from pathlib import Path

from modules.CIR_STYLE_MEMORY.cir_style_fewshot import (
    phase_3_style_fewshot_service as service,
)


def _configure_empty_style_pipeline(monkeypatch, tmp_path: Path) -> None:
    paths = {
        "memory": tmp_path / "style_memory_payload.json",
        "extraction": tmp_path / "style_extraction_payload.json",
        "profile": tmp_path / "style_profile_payload.json",
        "fewshot": tmp_path / "fewshot_payload.json",
        "argumentation": tmp_path / "argumentation_profile_payload.json",
        "pipeline": tmp_path / "phase_3_style_fewshot_pipeline.json",
    }
    monkeypatch.setattr(service, "style_memory_output_path", lambda *args: paths["memory"])
    monkeypatch.setattr(service, "style_extraction_output_path", lambda *args: paths["extraction"])
    monkeypatch.setattr(service, "style_profile_output_path", lambda *args: paths["profile"])
    monkeypatch.setattr(service, "fewshot_output_path", lambda *args: paths["fewshot"])
    monkeypatch.setattr(service, "argumentation_profile_output_path", lambda *args: paths["argumentation"])
    monkeypatch.setattr(service, "phase_3_pipeline_output_path", lambda *args: paths["pipeline"])
    monkeypatch.setattr(
        service,
        "retrieve_dynamic_style_memory",
        lambda **kwargs: {"ok": True, "style_memory_count": 0},
    )
    monkeypatch.setattr(
        service,
        "extract_style_from_memory_payload",
        lambda **kwargs: {
            "ok": False,
            "step": "cir_style_extractor",
            "status": "empty_style_memory",
            "message": "Aucun exemple style_memory trouvé.",
        },
    )
    monkeypatch.setattr(
        service,
        "build_style_profile_payload",
        lambda **kwargs: {
            "ok": False,
            "step": "cir_style_profile_builder",
            "status": "missing_or_invalid_extraction",
            "style_profile": {
                "profile_type": "neutral_scientific_style",
                "fewshot_templates": {"introduction": "Présenter le contexte scientifique."},
                "quality": {"level": "usable"},
            },
        },
    )
    monkeypatch.setattr(
        service,
        "build_cir_fewshot_payload",
        lambda **kwargs: {
            "ok": True,
            "fewshot_count": 1,
            "quality": {"level": "usable"},
        },
    )
    monkeypatch.setattr(
        service,
        "build_argumentation_profile_payload",
        lambda **kwargs: {
            "ok": True,
            "argumentation_profile": {
                "profile_type": "canonical_scientific_argumentation",
                "consultant_reasoning_flow": ["claim", "evidence", "limit"],
            },
        },
    )


def test_empty_style_memory_remains_blocking_by_default(monkeypatch, tmp_path: Path) -> None:
    _configure_empty_style_pipeline(monkeypatch, tmp_path)

    result = service.run_phase_3_style_fewshot_pipeline(
        "Org", "Projet", "2026"
    )

    assert result["ok"] is False
    assert result["failed_stage"] == "extraction"


def test_chat_can_use_neutral_style_for_a_new_project(monkeypatch, tmp_path: Path) -> None:
    _configure_empty_style_pipeline(monkeypatch, tmp_path)

    result = service.run_phase_3_style_fewshot_pipeline(
        "Org",
        "Projet",
        "2026",
        allow_empty_style_memory=True,
    )

    assert result["ok"] is True
    assert result["status"] == "success_neutral_style"
    assert result["summary"]["neutral_style_fallback"] is True
