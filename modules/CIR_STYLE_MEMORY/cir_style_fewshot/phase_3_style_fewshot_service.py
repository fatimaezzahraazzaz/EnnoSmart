# -*- coding: utf-8 -*-
from __future__ import annotations

"""
phase_3_style_fewshot_service.py

Orchestrateur Phase 3 — Dynamic Few-shot + Style Memory + Argumentation CIR

Rôle :
- lancer automatiquement les étapes Phase 3 :
    1. cir_style_retriever.py
    2. cir_style_extractor.py
    3. cir_style_profile_builder.py
    4. cir_fewshot_builder.py
    5. cir_argumentation_profile_builder.py

Objectif :
- produire tous les JSON nécessaires à EnnoScholar :
    - style_memory_payload.json
    - style_extraction_payload.json
    - style_profile_payload.json
    - fewshot_payload.json
    - argumentation_profile_payload.json
    - phase_3_style_fewshot_pipeline.json

Important :
- Memory V2 = style_only ;
- Memory V2 ne sert jamais de preuve ;
- les few-shots finaux doivent venir des templates propres ;
- l'argumentation profile sert uniquement à guider la logique consultant CIR ;
- les citations scientifiques doivent venir uniquement des Article Cards.
"""

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


from .cir_style_retriever import (
    retrieve_dynamic_style_memory,
    style_memory_output_path,
    _read_json,
    _write_json,
)

from .cir_style_extractor import (
    extract_style_from_memory_payload,
    style_extraction_output_path,
)

from .cir_style_profile_builder import (
    build_style_profile_payload,
    style_profile_output_path,
)

from .cir_fewshot_builder import (
    build_cir_fewshot_payload,
    fewshot_output_path,
)

from .cir_argumentation_profile_builder import (
    build_argumentation_profile_payload,
    argumentation_profile_output_path,
)


# ============================================================
# Paths
# ============================================================

def phase_3_pipeline_output_path(organisme: str, project: str, year: str) -> Path:
    return style_memory_output_path(organisme, project, year).parent / "phase_3_style_fewshot_pipeline.json"


# ============================================================
# Helpers
# ============================================================

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _elapsed(start: float) -> float:
    return round(time.perf_counter() - start, 3)


def _stage_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {
            "ok": False,
            "status": "invalid_result",
        }

    summary = {
        "ok": result.get("ok"),
        "step": result.get("step"),
        "payload_type": result.get("payload_type"),
        "status": result.get("status"),
        "message": result.get("message"),
        "output_path": result.get("output_path"),
    }

    for key in [
        "style_memory_count",
        "examples_count",
        "fewshot_count",
    ]:
        if key in result:
            summary[key] = result.get(key)

    if isinstance(result.get("quality"), dict):
        summary["quality"] = result.get("quality")

    style_profile = result.get("style_profile")
    if isinstance(style_profile, dict):
        summary["profile_type"] = style_profile.get("profile_type")
        summary["profile_strategy"] = style_profile.get("profile_strategy")
        summary["profile_quality"] = style_profile.get("quality")
        summary["fewshot_templates"] = list((style_profile.get("fewshot_templates") or {}).keys())
        summary["reasoning_patterns_count"] = len(style_profile.get("reasoning_patterns") or [])
        summary["comparison_patterns_count"] = len(style_profile.get("comparison_patterns") or [])
        summary["scientific_moves_count"] = len(style_profile.get("scientific_moves") or [])
        summary["paragraph_blueprints_count"] = len(style_profile.get("paragraph_blueprints") or {})

    argumentation_profile = result.get("argumentation_profile")
    if isinstance(argumentation_profile, dict):
        summary["argumentation_profile_type"] = argumentation_profile.get("profile_type")
        summary["argumentation_strategy"] = argumentation_profile.get("profile_strategy")
        summary["argumentation_quality"] = argumentation_profile.get("quality")
        summary["argumentation_flow_steps"] = len(argumentation_profile.get("consultant_reasoning_flow") or [])
        summary["insufficiency_patterns_count"] = len(argumentation_profile.get("insufficiency_taxonomy") or [])
        summary["reasoning_patterns_count"] = len(argumentation_profile.get("reasoning_patterns") or [])
        summary["paragraph_blueprints_count"] = len(argumentation_profile.get("paragraph_blueprints") or {})

    memory_summary = result.get("memory_summary")
    if isinstance(memory_summary, dict):
        summary["memory_summary"] = memory_summary

    return summary


def _write_pipeline_result(
    output_path: Path,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    _write_json(output_path, result)
    return result


def _failure_result(
    *,
    organisme: str,
    project: str,
    year: str,
    output_path: Path,
    started_at: str,
    start_time: float,
    failed_stage: str,
    stage_result: Dict[str, Any],
    stages: Dict[str, Any],
    output_paths: Dict[str, str],
) -> Dict[str, Any]:
    result = {
        "ok": False,
        "phase": "phase_3_dynamic_fewshot_style",
        "step": "phase_3_style_fewshot_service",
        "payload_type": "phase_3_style_fewshot_pipeline_v3_reasoning_patterns",
        "status": "failed",
        "failed_stage": failed_stage,
        "generated_at": _now(),
        "started_at": started_at,
        "elapsed_seconds": _elapsed(start_time),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "stages": stages,
        "failed_stage_result": _stage_summary(stage_result),
        "output_paths": output_paths,
        "rules": {
            "usage": "style_and_argumentation_only",
            "memory_as_proof": False,
            "can_be_cited": False,
            "scientific_sources_allowed": "article_cards_only",
            "raw_memory_examples_used_in_fewshot": False,
            "argumentation_profile_as_proof": False,
        },
        "output_path": str(output_path),
    }

    return _write_pipeline_result(output_path, result)


# ============================================================
# Orchestrateur principal
# ============================================================

def run_phase_3_style_fewshot_pipeline(
    organisme: str,
    project: str,
    year: str,
    phase1_payload_path: Optional[str | Path] = None,
    phase1_payload: Optional[Dict[str, Any]] = None,
    query_text: Optional[str] = None,
    top_k_per_role: int = 3,
    max_total_examples: int = 12,
    max_fewshot_examples: int = 5,
    output_path: Optional[str | Path] = None,
    run_retrieval: bool = True,
    allow_existing_style_memory: bool = True,
    run_argumentation_profile: bool = True,
    allow_empty_style_memory: bool = False,
) -> Dict[str, Any]:
    """
    Lance la Phase 3 complète.

    Nouveauté :
    - run_argumentation_profile=True ajoute argumentation_profile_payload.json.
    """

    start_time = time.perf_counter()
    started_at = _now()

    pipeline_path = (
        Path(output_path)
        if output_path
        else phase_3_pipeline_output_path(organisme, project, year)
    )

    style_memory_path = style_memory_output_path(organisme, project, year)
    extraction_path = style_extraction_output_path(organisme, project, year)
    profile_path = style_profile_output_path(organisme, project, year)
    fewshot_path = fewshot_output_path(organisme, project, year)
    argumentation_path = argumentation_profile_output_path(organisme, project, year)

    output_paths = {
        "style_memory_payload": str(style_memory_path),
        "style_extraction_payload": str(extraction_path),
        "style_profile_payload": str(profile_path),
        "fewshot_payload": str(fewshot_path),
        "argumentation_profile_payload": str(argumentation_path),
        "pipeline_payload": str(pipeline_path),
    }

    stages: Dict[str, Any] = {}

    # --------------------------------------------------------
    # 1. Retrieval Memory V2
    # --------------------------------------------------------
    if run_retrieval:
        retrieval_result = retrieve_dynamic_style_memory(
            organisme=organisme,
            project=project,
            year=year,
            phase1_payload_path=phase1_payload_path,
            phase1_payload=phase1_payload,
            query_text=query_text,
            top_k_per_role=top_k_per_role,
            max_total_examples=max_total_examples,
            output_path=style_memory_path,
        )
    else:
        retrieval_result = _read_json(style_memory_path, {}) or {}
        if not retrieval_result:
            retrieval_result = {
                "ok": False,
                "step": "cir_style_retriever",
                "status": "missing_existing_style_memory",
                "message": "run_retrieval=False mais style_memory_payload.json est introuvable.",
                "output_path": str(style_memory_path),
            }

    if not retrieval_result.get("ok"):
        existing_memory = _read_json(style_memory_path, {}) or {}

        if allow_existing_style_memory and existing_memory.get("ok"):
            retrieval_result = existing_memory
            retrieval_result["_warning"] = (
                "Le retrieval courant a échoué, mais un ancien style_memory_payload valide a été réutilisé."
            )
        else:
            stages["retrieval"] = _stage_summary(retrieval_result)
            return _failure_result(
                organisme=organisme,
                project=project,
                year=year,
                output_path=pipeline_path,
                started_at=started_at,
                start_time=start_time,
                failed_stage="retrieval",
                stage_result=retrieval_result,
                stages=stages,
                output_paths=output_paths,
            )

    stages["retrieval"] = _stage_summary(retrieval_result)

    # --------------------------------------------------------
    # 2. Style Extraction
    # --------------------------------------------------------
    extraction_result = extract_style_from_memory_payload(
        organisme=organisme,
        project=project,
        year=year,
        style_memory_payload_path=style_memory_path,
        output_path=extraction_path,
    )

    stages["extraction"] = _stage_summary(extraction_result)

    neutral_style_fallback = bool(
        allow_empty_style_memory
        and extraction_result.get("status") == "empty_style_memory"
    )
    if not extraction_result.get("ok") and not neutral_style_fallback:
        return _failure_result(
            organisme=organisme,
            project=project,
            year=year,
            output_path=pipeline_path,
            started_at=started_at,
            start_time=start_time,
            failed_stage="extraction",
            stage_result=extraction_result,
            stages=stages,
            output_paths=output_paths,
        )
    if neutral_style_fallback:
        stages["extraction"]["fallback"] = "neutral_scientific_style"
        stages["extraction"]["blocking"] = False

    # --------------------------------------------------------
    # 3. Style Profile Builder
    # --------------------------------------------------------
    profile_result = build_style_profile_payload(
        organisme=organisme,
        project=project,
        year=year,
        style_extraction_payload_path=extraction_path,
        output_path=profile_path,
    )

    stages["style_profile"] = _stage_summary(profile_result)

    if (
        neutral_style_fallback
        and not profile_result.get("ok")
        and isinstance(profile_result.get("style_profile"), dict)
        and profile_result.get("style_profile")
    ):
        profile_result = dict(profile_result)
        profile_result["ok"] = True
        profile_result["status"] = "neutral_scientific_style_fallback"
        profile_result["message"] = (
            "Nouveau projet sans mémoire de style : templates scientifiques "
            "neutres utilisés."
        )
        profile_result["fallback_reason"] = "empty_style_memory"
        _write_json(profile_path, profile_result)
        stages["style_profile"] = _stage_summary(profile_result)

    if not profile_result.get("ok"):
        return _failure_result(
            organisme=organisme,
            project=project,
            year=year,
            output_path=pipeline_path,
            started_at=started_at,
            start_time=start_time,
            failed_stage="style_profile",
            stage_result=profile_result,
            stages=stages,
            output_paths=output_paths,
        )

    # --------------------------------------------------------
    # 4. Few-shot Builder
    # --------------------------------------------------------
    fewshot_result = build_cir_fewshot_payload(
        organisme=organisme,
        project=project,
        year=year,
        style_memory_payload_path=style_memory_path,
        style_profile_payload_path=profile_path,
        output_path=fewshot_path,
        max_examples=max_fewshot_examples,
    )

    stages["fewshot"] = _stage_summary(fewshot_result)

    if not fewshot_result.get("ok"):
        return _failure_result(
            organisme=organisme,
            project=project,
            year=year,
            output_path=pipeline_path,
            started_at=started_at,
            start_time=start_time,
            failed_stage="fewshot",
            stage_result=fewshot_result,
            stages=stages,
            output_paths=output_paths,
        )

    # --------------------------------------------------------
    # 5. Argumentation Profile Builder
    # --------------------------------------------------------
    argumentation_result = {
        "ok": None,
        "step": "cir_argumentation_profile_builder",
        "status": "skipped",
        "message": "run_argumentation_profile=False",
        "output_path": str(argumentation_path),
    }

    if run_argumentation_profile:
        argumentation_result = build_argumentation_profile_payload(
            organisme=organisme,
            project=project,
            year=year,
            style_extraction_payload_path=extraction_path,
            style_profile_payload_path=profile_path,
            output_path=argumentation_path,
        )

        stages["argumentation_profile"] = _stage_summary(argumentation_result)

        if not argumentation_result.get("ok"):
            return _failure_result(
                organisme=organisme,
                project=project,
                year=year,
                output_path=pipeline_path,
                started_at=started_at,
                start_time=start_time,
                failed_stage="argumentation_profile",
                stage_result=argumentation_result,
                stages=stages,
                output_paths=output_paths,
            )
    else:
        stages["argumentation_profile"] = _stage_summary(argumentation_result)

    # --------------------------------------------------------
    # Résultat final
    # --------------------------------------------------------
    fewshot_quality = fewshot_result.get("quality") or {}
    profile = profile_result.get("style_profile") or {}
    argumentation_profile = argumentation_result.get("argumentation_profile") or {}

    result = {
        "ok": True,
        "phase": "phase_3_dynamic_fewshot_style",
        "step": "phase_3_style_fewshot_service",
        "payload_type": "phase_3_style_fewshot_pipeline_v3_reasoning_patterns",
        "status": (
            "success_neutral_style" if neutral_style_fallback else "success"
        ),
        "generated_at": _now(),
        "started_at": started_at,
        "elapsed_seconds": _elapsed(start_time),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "summary": {
            "style_memory_count": retrieval_result.get("style_memory_count"),
            "examples_count": extraction_result.get("examples_count"),
            "style_profile_type": profile.get("profile_type"),
            "style_profile_strategy": profile.get("profile_strategy"),
            "style_profile_quality": profile.get("quality"),
            "fewshot_count": fewshot_result.get("fewshot_count"),
            "fewshot_quality": fewshot_quality,
            "fewshot_roles_covered": fewshot_quality.get("roles_covered"),
            "argumentation_profile_type": argumentation_profile.get("profile_type"),
            "argumentation_strategy": argumentation_profile.get("profile_strategy"),
            "argumentation_quality": argumentation_profile.get("quality"),
            "argumentation_flow_steps": len(argumentation_profile.get("consultant_reasoning_flow") or []),
            "insufficiency_patterns_count": len(argumentation_profile.get("insufficiency_taxonomy") or []),
            "reasoning_patterns_count": len(profile.get("reasoning_patterns") or []),
            "comparison_patterns_count": len(profile.get("comparison_patterns") or []),
            "scientific_moves_count": len(profile.get("scientific_moves") or []),
            "paragraph_blueprints_count": len(profile.get("paragraph_blueprints") or {}),
            "raw_memory_examples_used_in_fewshot": (
                fewshot_result.get("rules", {}).get("raw_memory_examples_used_in_fewshot")
            ),
            "fewshots_generated_from_style_profile_templates": (
                fewshot_result.get("rules", {}).get("fewshots_generated_from_style_profile_templates")
            ),
            "argumentation_profile_as_proof": False,
            "neutral_style_fallback": neutral_style_fallback,
        },
        "stages": stages,
        "output_paths": output_paths,
        "rules": {
            "usage": "style_and_argumentation_only",
            "memory_as_proof": False,
            "can_be_cited": False,
            "scientific_sources_allowed": "article_cards_only",
            "must_not_copy_historical_facts": True,
            "must_not_cite_memory": True,
            "raw_memory_examples_used_in_fewshot": False,
            "fewshots_generated_from_style_profile_templates": True,
            "argumentation_profile_as_proof": False,
            "argumentation_profile_used_for_reasoning_only": True,
            "reasoning_patterns_available_for_phase5": True,
            "comparison_patterns_available_for_phase5": True,
            "scientific_moves_available_for_phase5": True,
            "paragraph_blueprints_available_for_phase5": True,
            "placeholders_must_be_filled_from_current_project_and_article_cards": True,
            "neutral_style_fallback": neutral_style_fallback,
        },
        "output_path": str(pipeline_path),
    }

    return _write_pipeline_result(pipeline_path, result)

# ============================================================
# Compatibilité EnnoScholar state-of-art orchestrator
# ============================================================

def build_phase_3_style_memory(
    organisme: str,
    project: str,
    year: str,
    phase1_payload_path: Optional[str | Path] = None,
    phase1_payload: Optional[Dict[str, Any]] = None,
    query_text: Optional[str] = None,
    force: bool = True,
    top_k_per_role: int = 3,
    max_total_examples: int = 12,
    max_fewshot_examples: int = 5,
    run_argumentation_profile: bool = True,
    allow_empty_style_memory: bool = False,
) -> Dict[str, Any]:
    """
    Wrapper de compatibilité attendu par l'orchestrateur EnnoScholar.

    L'ancien code appelait build_phase_3_style_memory(...).
    La vraie fonction interne reste run_phase_3_style_fewshot_pipeline(...).

    force=True  : relance le retrieval Memory V2.
    force=False : réutilise un style_memory_payload valide ; s'il manque,
                  le reconstruit automatiquement au lieu de bloquer le pipeline.
    """
    style_path = style_memory_output_path(organisme, project, str(year))
    existing_style_memory = _read_json(style_path, {}) or {}
    retrieval_required = bool(
        force
        or not style_path.is_file()
        or not existing_style_memory.get("ok")
    )
    return run_phase_3_style_fewshot_pipeline(
        organisme=organisme,
        project=project,
        year=str(year),
        phase1_payload_path=phase1_payload_path,
        phase1_payload=phase1_payload,
        query_text=query_text,
        top_k_per_role=top_k_per_role,
        max_total_examples=max_total_examples,
        max_fewshot_examples=max_fewshot_examples,
        run_retrieval=retrieval_required,
        allow_existing_style_memory=True,
        run_argumentation_profile=run_argumentation_profile,
        allow_empty_style_memory=allow_empty_style_memory,
    )


def run_phase_3(
    organisme: str,
    project: str,
    year: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Alias court pour les anciens appels éventuels."""
    return build_phase_3_style_memory(
        organisme=organisme,
        project=project,
        year=str(year),
        **kwargs,
    )

