from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _limited_list(value: Any, *, limit: int = 8) -> list[Any]:
    return list(value)[:limit] if isinstance(value, list) else []


def cir_style_context(project: Any) -> dict[str, Any]:
    """Charge uniquement les artefacts Phase 3 déjà assainis.

    Les extraits bruts de Memory V2 et les mémoires factuelles d'anciens CIR ne
    sont jamais retournés. Un artefact absent conduit à un fallback rédactionnel
    neutre, pas à la lecture directe de l'ancien dossier.
    """

    try:
        from modules.CIR_STYLE_MEMORY.cir_style_fewshot.cir_argumentation_profile_builder import (
            argumentation_profile_output_path,
        )
        from modules.CIR_STYLE_MEMORY.cir_style_fewshot.cir_fewshot_builder import (
            fewshot_output_path,
        )
        from modules.CIR_STYLE_MEMORY.cir_style_fewshot.cir_style_profile_builder import (
            style_profile_output_path,
        )

        args = (
            str(getattr(project, "organisme", "") or ""),
            str(getattr(project, "project_name", "") or ""),
            str(getattr(project, "year", "") or ""),
        )
        profile_path = style_profile_output_path(*args)
        fewshot_path = fewshot_output_path(*args)
        argumentation_path = argumentation_profile_output_path(*args)
    except Exception as exc:
        return {
            "available": False,
            "agent": "CIRStyleMemory",
            "usage": "style_only",
            "fact_eligible": False,
            "reason": f"Adaptateur de style indisponible ({type(exc).__name__}).",
        }

    profile_payload = _read_json(profile_path)
    fewshot_payload = _read_json(fewshot_path)
    argumentation_payload = _read_json(argumentation_path)
    profile = profile_payload.get("style_profile") or {}
    argumentation = argumentation_payload.get("argumentation_profile") or {}
    fewshots = []
    for row in _limited_list(fewshot_payload.get("fewshot_examples"), limit=6):
        if not isinstance(row, dict):
            continue
        # Ces champs proviennent de templates SAFE_* avec placeholders. Aucun
        # champ source/raw/memory_text n'est exposé au writer.
        fewshots.append(
            {
                "role": str(row.get("role") or ""),
                "input_hint": str(row.get("input_hint") or "")[:600],
                "output_style_example": str(row.get("output_style_example") or "")[:1800],
            }
        )

    safe_profile = {
        "profile_strategy": str(profile.get("profile_strategy") or ""),
        "tone": profile.get("tone"),
        "tone_details": _limited_list(profile.get("tone_details"), limit=8),
        "style_constraints": dict(profile.get("style_constraints") or {}),
        "writing_rules": _limited_list(profile.get("writing_rules"), limit=12),
        "reasoning_patterns": _limited_list(profile.get("reasoning_patterns"), limit=10),
        "comparison_patterns": _limited_list(profile.get("comparison_patterns"), limit=8),
        "scientific_moves": _limited_list(profile.get("scientific_moves"), limit=10),
        "paragraph_blueprints": dict(profile.get("paragraph_blueprints") or {}),
        "forbidden_patterns": _limited_list(profile.get("forbidden_patterns"), limit=12),
    }
    safe_argumentation = {
        "consultant_reasoning_flow": _limited_list(
            argumentation.get("consultant_reasoning_flow"), limit=12
        ),
        "insufficiency_taxonomy": _limited_list(
            argumentation.get("insufficiency_taxonomy"), limit=12
        ),
        "reasoning_patterns": _limited_list(argumentation.get("reasoning_patterns"), limit=10),
        "section_blueprints": dict(argumentation.get("section_blueprints") or {}),
    }
    available = bool(
        profile_payload.get("ok")
        and (safe_profile["writing_rules"] or safe_profile["reasoning_patterns"] or fewshots)
    )
    return {
        "available": available,
        "agent": "CIRStyleMemory",
        "usage": "style_and_argumentation_patterns_only",
        "fact_eligible": False,
        "can_be_cited": False,
        "raw_memory_included": False,
        "must_not_copy_historical_facts": True,
        "style_profile": safe_profile if available else {},
        "fewshot_templates": fewshots if available else [],
        "argumentation_profile": safe_argumentation if available else {},
        "provenance": {
            "type": "safe_phase_3_templates",
            "profile_path": str(profile_path) if available else None,
        },
        "reason": None if available else "Aucun profil Phase 3 assaini n'est encore disponible pour ce projet.",
    }


def has_safe_cir_style_memory(project: Any) -> bool:
    return bool(cir_style_context(project).get("available"))
