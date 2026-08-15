# -*- coding: utf-8 -*-
from __future__ import annotations

"""Estimateur de coût EnnoScholar V3 — sans appel LLM.

Principes:
- le plan consultant n'a aucune limite artificielle de sections ;
- le coût total est estimé ;
- le hard limit est une TRANCHE de dépense, pas une limite de plan ;
- les sections acceptées sont reprises par checkpoint lors d'une relance ;
- les ratios verifier/escalation peuvent être recalibrés depuis le dernier run.
"""

import csv
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, Mapping

from modules.LLM.usage_budget import compute_cost
from services.article_card_builder import get_article_cards_payload
from services.ennoscholar_state_of_art_orchestrator import _phase_paths, _read_json


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(str(os.getenv(name, default)).strip()))
    except Exception:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(str(os.getenv(name, default)).strip()))
    except Exception:
        return default


def _usage(input_tokens: int, output_tokens: int) -> Dict[str, int]:
    return {
        "input_tokens": int(input_tokens),
        "uncached_input_tokens": int(input_tokens),
        "cached_input_tokens": 0,
        "output_tokens": int(output_tokens),
        "total_tokens": int(input_tokens) + int(output_tokens),
    }


def _cost(model: str, input_tokens: int, output_tokens: int) -> float:
    result = compute_cost(model, "openai", _usage(input_tokens, output_tokens))
    return float(result.get("cost_usd") or 0.0)


def _extract_plan_sections(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    for key in (
        "sections",
        "plan",
        "approved_plan",
        "current_plan",
        "consultant_plan",
    ):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            return [
                dict(row)
                for row in candidate
                if isinstance(row, Mapping)
            ]
        if isinstance(candidate, Mapping):
            nested = _extract_plan_sections(candidate)
            if nested:
                return nested
    return []


def _budget_dir(project: Any) -> Path:
    paths = _phase_paths(project)
    return paths["base"].parent / "budget_logs"


def _latest_budget_summary(project: Any) -> Dict[str, Any] | None:
    budget_dir = _budget_dir(project)
    if not budget_dir.exists():
        return None

    files = sorted(
        budget_dir.glob("budget_*_summary.json"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for path in files[:20]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        totals = payload.get("totals")
        if not isinstance(totals, dict):
            continue
        return {
            "run_id": payload.get("run_id"),
            "status": payload.get("status"),
            "calls": totals.get("calls"),
            "total_tokens": totals.get("total_tokens"),
            "cost_usd": totals.get("cost_usd"),
            "finished_at": payload.get("finished_at"),
            "summary_path": str(path),
        }
    return None


def _latest_phase5_calibration(
    project: Any,
    *,
    draft_model: str,
    escalation_model: str,
) -> Dict[str, Any]:
    """Calibre les ratios sur le dernier CSV réel, gratuitement."""
    budget_dir = _budget_dir(project)
    if not budget_dir.exists():
        return {}

    files = sorted(
        budget_dir.glob("budget_*.csv"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for path in files[:20]:
        distinct_sections: set[int] = set()
        verifier_calls = 0
        escalation_calls = 0
        phase5_calls = 0
        phase5_cost = 0.0

        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except Exception:
            continue

        for row in rows:
            if str(row.get("phase") or "") != "phase_5_writer":
                continue

            phase5_calls += 1
            try:
                phase5_cost += float(row.get("cost_usd") or 0.0)
            except Exception:
                pass

            request_name = str(row.get("request_name") or "")
            model = str(row.get("model") or "")

            match = re.search(r":section:(\d+)(?:$|:)", request_name)
            if match:
                distinct_sections.add(int(match.group(1)))

            if "independent_semantic_verifier" in request_name:
                verifier_calls += 1

            if (
                match
                and escalation_model
                and model == escalation_model
                and escalation_model != draft_model
            ):
                escalation_calls += 1

        if not distinct_sections:
            continue

        section_count = len(distinct_sections)
        return {
            "source_csv": str(path),
            "sections_observed": section_count,
            "phase5_calls_observed": phase5_calls,
            "phase5_cost_observed_usd": round(phase5_cost, 6),
            "verifier_calls_observed": verifier_calls,
            "escalation_calls_observed": escalation_calls,
            "verifier_ratio_observed": round(
                verifier_calls / section_count,
                4,
            ),
            "escalation_ratio_observed": round(
                escalation_calls / section_count,
                4,
            ),
        }

    return {}


def _slice_count(cost: float, hard_limit: float) -> int:
    if cost <= 0:
        return 1
    if hard_limit <= 0:
        return 1
    return max(1, int(math.ceil(cost / hard_limit)))


def estimate_state_of_art_cost(*, db: Any, project: Any) -> Dict[str, Any]:
    """Estimation gratuite et prudente, sans contacter OpenAI."""
    paths = _phase_paths(project)

    plan_payload = _read_json(paths["consultant_plan_contract"], {})
    sections = _extract_plan_sections(plan_payload)
    if not sections:
        narrative = _read_json(
            paths["phase47_scientific_narrative_payload"],
            {},
        )
        sections = _extract_plan_sections(narrative)

    section_count = max(1, len(sections))

    cards_payload = get_article_cards_payload(project, db=db)
    cards = (
        cards_payload.get("cards")
        if isinstance(cards_payload, dict)
        else []
    ) or []
    cards_count = len(cards) if isinstance(cards, list) else 0

    draft_model = str(
        os.getenv("ENNOSCHOLAR_PHASE5_DRAFT_MODEL")
        or "gpt-4.1-mini"
    ).strip()
    verifier_model = str(
        os.getenv("ENNOSCHOLAR_PHASE5_VERIFIER_MODEL")
        or "gpt-4.1-mini"
    ).strip()
    escalation_model = str(
        os.getenv("ENNOSCHOLAR_PHASE5_ESCALATION_MODEL")
        or os.getenv("ENNOSCHOLAR_PHASE5_WRITER_MODEL")
        or "gpt-4.1"
    ).strip()

    writer_input = _int_env(
        "ENNOSCHOLAR_COST_EST_WRITER_INPUT_TOKENS", 7000
    )
    writer_output = _int_env(
        "ENNOSCHOLAR_COST_EST_WRITER_OUTPUT_TOKENS", 1200
    )
    verifier_input = _int_env(
        "ENNOSCHOLAR_COST_EST_VERIFIER_INPUT_TOKENS", 5000
    )
    verifier_output = _int_env(
        "ENNOSCHOLAR_COST_EST_VERIFIER_OUTPUT_TOKENS", 450
    )

    configured_risk_ratio = min(
        1.0,
        _float_env(
            "ENNOSCHOLAR_COST_EST_RISK_VERIFIER_RATIO", 0.20
        ),
    )
    configured_escalation_ratio = min(
        1.0,
        _float_env(
            "ENNOSCHOLAR_COST_EST_ESCALATION_RATIO", 0.10
        ),
    )

    calibration = _latest_phase5_calibration(
        project,
        draft_model=draft_model,
        escalation_model=escalation_model,
    )

    observed_risk_ratio = float(
        calibration.get("verifier_ratio_observed") or 0.0
    )
    observed_escalation_ratio = float(
        calibration.get("escalation_ratio_observed") or 0.0
    )

    # Prudence: jamais moins que la config de base.
    risk_ratio = min(
        1.0,
        max(configured_risk_ratio, observed_risk_ratio),
    )
    escalation_ratio = min(
        1.0,
        max(
            configured_escalation_ratio,
            observed_escalation_ratio,
        ),
    )

    expected_verifier_calls = section_count * risk_ratio
    expected_escalation_calls = section_count * escalation_ratio

    writer_unit = _cost(
        draft_model, writer_input, writer_output
    )
    verifier_unit = _cost(
        verifier_model, verifier_input, verifier_output
    )
    escalation_unit = _cost(
        escalation_model, writer_input, writer_output
    )

    writer_cost = section_count * writer_unit
    expected = (
        writer_cost
        + expected_verifier_calls * verifier_unit
        + expected_escalation_calls * escalation_unit
    )
    low = (
        writer_cost
        + section_count
        * configured_risk_ratio
        * 0.5
        * verifier_unit
    )
    high = (
        writer_cost
        + section_count
        * max(risk_ratio, 0.35)
        * verifier_unit
        + section_count
        * max(escalation_ratio, 0.20)
        * escalation_unit
    )

    hard_limit = _float_env(
        "ENNOSMART_BUDGET_HARD_LIMIT_USD", 0.35
    )
    warn_limit = _float_env(
        "ENNOSMART_BUDGET_WARN_USD", 0.20
    )

    expected_slices = _slice_count(expected, hard_limit)
    high_slices = _slice_count(high, hard_limit)

    return {
        "ok": True,
        "cost_estimate_only": True,
        "provider_call_made": False,
        "project_id": getattr(project, "id", None),
        "plan_sections_count": section_count,
        "article_cards_count": cards_count,
        "plan_policy": {
            "consultant_plan_unrestricted": True,
            "fixed_section_limit": None,
            "fixed_llm_call_limit": None,
            "cost_controls_plan": False,
            "message": (
                "Le consultant garde son plan complet. "
                "Le budget découpe seulement l'exécution en tranches "
                "reprises par checkpoints si nécessaire."
            ),
        },
        "models": {
            "draft": draft_model,
            "verifier": verifier_model,
            "escalation": escalation_model,
        },
        "estimated_calls": {
            "draft": section_count,
            "risk_verifier": round(expected_verifier_calls, 2),
            "premium_escalation": round(
                expected_escalation_calls, 2
            ),
            "total_expected": round(
                section_count
                + expected_verifier_calls
                + expected_escalation_calls,
                2,
            ),
            "informational_only": True,
            "not_a_blocking_limit": True,
        },
        "estimated_cost_usd": {
            "low": round(low, 4),
            "expected": round(expected, 4),
            "high": round(high, 4),
        },
        "budget": {
            "warning_usd": warn_limit,
            "hard_limit_per_slice_usd": hard_limit,
            "hard_limit_is_execution_slice": True,
            "expected_slices": expected_slices,
            "high_slices": high_slices,
            "resume_checkpoints_between_slices": True,
            "expected_within_one_slice": (
                expected <= hard_limit
                if hard_limit > 0
                else True
            ),
        },
        "calibration": {
            "configured_risk_verifier_ratio":
                configured_risk_ratio,
            "configured_escalation_ratio":
                configured_escalation_ratio,
            "effective_risk_verifier_ratio": risk_ratio,
            "effective_escalation_ratio": escalation_ratio,
            "latest_real_run": calibration or None,
        },
        "assumptions": {
            "writer_input_tokens_per_section": writer_input,
            "writer_output_tokens_per_section": writer_output,
            "verifier_input_tokens_per_risky_section":
                verifier_input,
            "verifier_output_tokens_per_risky_section":
                verifier_output,
            "deterministic_controls_always_run": True,
            "llm_verifier_only_on_risk": True,
            "figure_matching_additional_llm_calls": 0,
        },
        "latest_actual_run": _latest_budget_summary(project),
    }
