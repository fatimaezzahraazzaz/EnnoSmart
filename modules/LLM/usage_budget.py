# -*- coding: utf-8 -*-
from __future__ import annotations

import contextvars
import csv
import functools
import json
import math
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

# USD par million de tokens, tarifs OpenAI vérifiés le 31/07/2026.
PRICES = {
    "gpt-4.1": {"input": 2.00, "cached": 0.50, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "cached": 0.10, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "cached": 0.025, "output": 0.40},
    "gpt-5.6": {"input": 5.00, "cached": 0.50, "output": 30.00},
    "gpt-5.6-sol": {"input": 5.00, "cached": 0.50, "output": 30.00},
    "gpt-5.6-terra": {"input": 2.50, "cached": 0.25, "output": 15.00},
    "gpt-5.6-luna": {"input": 1.00, "cached": 0.10, "output": 6.00},
}

PHASES = [
    "phase_1_selection",
    "phase_2d_article_cards",
    "phase_3_style_memory",
    "phase_4_scientific_gap",
    "phase_4_5_scientific_reasoning",
    "phase_4_6_project_argumentation",
    "phase_4_7_scientific_narrative",
    "phase_5_writer",
    "guided_research",
    "other",
]

_CURRENT_RUN: contextvars.ContextVar[Optional["BudgetRun"]] = contextvars.ContextVar(
    "ennosmart_budget_run", default=None
)
_HOOK_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "ennosmart_budget_hook_depth", default=0
)
_LOCK = threading.RLock()


def _enabled() -> bool:
    return os.getenv("ENNOSMART_BUDGET_LOG_ENABLED", "1").lower() in {
        "1", "true", "yes", "oui", "on"
    }


def _root() -> Path:
    return Path(os.getenv("ENNOSMART_ROOT") or "C:/EnnoSmart")


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _slug(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    for source, target in {
        "à": "a", "â": "a", "ä": "a", "ç": "c", "é": "e", "è": "e",
        "ê": "e", "ë": "e", "î": "i", "ï": "i", "ô": "o", "ö": "o",
        "ù": "u", "û": "u", "ü": "u", "-": "_", " ": "_", "'": "_",
        "’": "_",
    }.items():
        text = text.replace(source, target)
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_") or "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _canonical_model(value: Any) -> str:
    model = str(value or "unknown").lower().strip().split("/")[-1]
    for name in sorted(PRICES, key=len, reverse=True):
        if model == name or model.startswith(name + "-"):
            return name
    return model


def infer_phase(request_name: Any) -> str:
    name = str(request_name or "").lower().replace("-", "_")
    if any(x in name for x in ("phase5", "phase_5", "full_writer", "section_writer", "evidence_verifier", "state_of_art_writer")):
        return "phase_5_writer"
    if any(x in name for x in ("phase4_7", "phase_4_7", "scientific_narrative", "narrative_architect", "guided_narrative")):
        return "phase_4_7_scientific_narrative"
    if any(x in name for x in ("phase4_6", "phase_4_6", "project_rd_argumentation")):
        return "phase_4_6_project_argumentation"
    if any(x in name for x in ("phase4_5", "phase_4_5", "scientific_reasoning")):
        return "phase_4_5_scientific_reasoning"
    if any(x in name for x in ("phase4", "phase_4", "scientific_gap", "gap_builder")):
        return "phase_4_scientific_gap"
    if any(x in name for x in ("phase3", "phase_3", "fewshot", "style_memory", "style_signature")):
        return "phase_3_style_memory"
    if any(x in name for x in ("article_card", "phase2d", "phase_2d")):
        return "phase_2d_article_cards"
    if any(x in name for x in ("selection_payload", "phase1", "phase_1")):
        return "phase_1_selection"
    if "guided_research" in name:
        return "guided_research"
    return "other"


def _nested(meta: Mapping[str, Any], *paths: tuple[str, ...]) -> int:
    for path in paths:
        value: Any = meta
        valid = True
        for key in path:
            if not isinstance(value, Mapping) or key not in value:
                valid = False
                break
            value = value[key]
        if valid:
            result = _safe_int(value)
            if result >= 0:
                return result
    return 0


def normalize_usage(meta: Mapping[str, Any]) -> Dict[str, Any]:
    input_tokens = _nested(meta, ("input_tokens",), ("prompt_tokens",), ("usage", "input_tokens"), ("usage", "prompt_tokens"))
    output_tokens = _nested(meta, ("output_tokens",), ("completion_tokens",), ("usage", "output_tokens"), ("usage", "completion_tokens"))
    total_tokens = _nested(meta, ("total_tokens",), ("usage", "total_tokens"))
    cached_tokens = _nested(
        meta,
        ("cached_input_tokens",),
        ("cached_tokens",),
        ("input_tokens_details", "cached_tokens"),
        ("prompt_tokens_details", "cached_tokens"),
        ("usage", "input_tokens_details", "cached_tokens"),
        ("usage", "prompt_tokens_details", "cached_tokens"),
    )
    estimated = False
    if input_tokens == 0 and output_tokens == 0 and total_tokens > 0:
        input_tokens = round(total_tokens * 0.80)
        output_tokens = total_tokens - input_tokens
        estimated = True
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    cached_tokens = min(cached_tokens, input_tokens)
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "uncached_input_tokens": max(0, input_tokens - cached_tokens),
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "usage_split_estimated": estimated,
    }


def compute_cost(model: Any, provider: Any, usage: Mapping[str, Any]) -> Dict[str, Any]:
    canonical = _canonical_model(model)
    provider_name = str(provider or "unknown").lower()
    price = PRICES.get(canonical)
    if provider_name != "openai" or price is None:
        return {"canonical_model": canonical, "price_known": False, "cost_usd": 0.0}

    input_rate = price["input"]
    cached_rate = price["cached"]
    output_rate = price["output"]
    long_context = canonical.startswith("gpt-5.6") and _safe_int(usage.get("input_tokens")) > 272000
    if long_context:
        input_rate *= 2
        cached_rate *= 2
        output_rate *= 1.5

    cost = (
        _safe_int(usage.get("uncached_input_tokens")) * input_rate
        + _safe_int(usage.get("cached_input_tokens")) * cached_rate
        + _safe_int(usage.get("output_tokens")) * output_rate
    ) / 1_000_000
    return {
        "canonical_model": canonical,
        "price_known": True,
        "cost_usd": round(cost, 8),
        "prices_per_mtok": {"input": input_rate, "cached_input": cached_rate, "output": output_rate},
        "long_context_multiplier_applied": long_context,
    }

# BEGIN ENNOSMART_COST_GUARD_V1
class BudgetLimitExceeded(RuntimeError):
    """Arrêt volontaire avant un nouvel appel LLM payant."""


def _budget_hard_limit_usd() -> float:
    return _safe_float(
        os.getenv("ENNOSMART_BUDGET_HARD_LIMIT_USD", "0.35")
    )


def _budget_max_calls() -> int:
    try:
        return max(
            0,
            int(
                os.getenv(
                    "ENNOSMART_BUDGET_MAX_LLM_CALLS_PER_RUN",
                    "0",
                )
            ),
        )
    except Exception:
        return 0
def _estimate_next_call_cost(
    *,
    llm_client: Any,
    prompt: Any,
    request_name: Any,
    max_output_tokens: Any,
) -> Dict[str, Any]:
    provider = str(
        getattr(llm_client, "provider", "unknown") or "unknown"
    )
    model_getter = getattr(
        llm_client,
        "_default_model_for_request",
        None,
    )
    if callable(model_getter):
        try:
            model = str(model_getter(request_name) or "unknown")
        except Exception:
            model = str(
                getattr(llm_client, "model_name", "unknown")
            )
    else:
        model = str(
            getattr(llm_client, "model_name", "unknown")
        )

    estimated_input = max(
        1,
        int(math.ceil(len(str(prompt or "")) / 3.6)),
    )
    try:
        max_output = max(1, int(max_output_tokens or 1400))
    except Exception:
        max_output = 1400

    reserve_ratio = _safe_float(
        os.getenv(
            "ENNOSMART_BUDGET_OUTPUT_RESERVE_RATIO",
            "0.35",
        )
    )
    reserve_ratio = min(1.0, max(0.10, reserve_ratio))
    estimated_output = max(
        64,
        int(max_output * reserve_ratio),
    )

    usage = {
        "input_tokens": estimated_input,
        "uncached_input_tokens": estimated_input,
        "cached_input_tokens": 0,
        "output_tokens": estimated_output,
        "total_tokens": estimated_input + estimated_output,
    }
    priced = compute_cost(model, provider, usage)
    return {
        "provider": provider,
        "model": model,
        "estimated_input_tokens": estimated_input,
        "estimated_output_tokens": estimated_output,
        "estimated_cost_usd": _safe_float(
            priced.get("cost_usd")
        ),
        "price_known": bool(priced.get("price_known")),
    }


def get_current_budget_snapshot() -> Dict[str, Any]:
    run = _CURRENT_RUN.get()
    hard = _budget_hard_limit_usd()
    if run is None:
        return {
            "active": False,
            "cost_usd": 0.0,
            "hard_limit_usd": hard,
            "remaining_usd": hard if hard > 0 else None,
        }
    current = run.total_cost()
    return {
        "active": True,
        "run_id": run.run_id,
        "calls": len(run.events),
        "cost_usd": current,
        "hard_limit_usd": hard,
        "remaining_usd": (
            round(max(0.0, hard - current), 8)
            if hard > 0
            else None
        ),
    }
# END ENNOSMART_COST_GUARD_V1



# BEGIN ENNOSMART_DEV_WALLET_ENV_READER_V1
_DEV_WALLET_ENV_CACHE: Dict[str, str] | None = None


def _dev_wallet_env_values() -> Dict[str, str]:
    global _DEV_WALLET_ENV_CACHE
    if _DEV_WALLET_ENV_CACHE is not None:
        return dict(_DEV_WALLET_ENV_CACHE)

    merged: Dict[str, str] = {}
    root = _root()

    for path in (
        root / "backend_api" / ".env",
        root / ".env",
    ):
        if not path.exists():
            continue
        try:
            for raw_line in path.read_text(
                encoding="utf-8",
                errors="ignore",
            ).splitlines():
                line = raw_line.strip()
                if (
                    not line
                    or line.startswith("#")
                    or "=" not in line
                ):
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    merged[key] = value
        except Exception:
            continue

    _DEV_WALLET_ENV_CACHE = merged
    return dict(merged)


def _dev_wallet_env(name: str, default: Any = "") -> str:
    process_value = os.getenv(name)
    if process_value is not None:
        return str(process_value).strip()

    file_value = _dev_wallet_env_values().get(name)
    if file_value is not None:
        return str(file_value).strip()

    return str(default).strip()


def reload_dev_wallet_config() -> Dict[str, str]:
    global _DEV_WALLET_ENV_CACHE
    _DEV_WALLET_ENV_CACHE = None
    return _dev_wallet_env_values()
# END ENNOSMART_DEV_WALLET_ENV_READER_V1

# BEGIN ENNOSMART_DEV_WALLET_V1
def _dev_wallet_enabled() -> bool:
    return str(
        _dev_wallet_env("ENNOSMART_DEV_WALLET_ENABLED", "0")
    ).strip().casefold() in {"1", "true", "yes", "oui", "on"}


def _dev_wallet_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(str(_dev_wallet_env(name, default)).strip()))
    except Exception:
        return default


def _dev_wallet_root() -> Path:
    root = _root() / "storage" / "budget_control"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _dev_wallet_ledger_path() -> Path:
    return _dev_wallet_root() / "global_llm_ledger.jsonl"


def _dev_wallet_parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _dev_wallet_baseline() -> Optional[datetime]:
    return _dev_wallet_parse_time(
        _dev_wallet_env("ENNOSMART_DEV_WALLET_BASELINE_AT")
    )


def _dev_wallet_read_events() -> list[Dict[str, Any]]:
    path = _dev_wallet_ledger_path()
    if not path.exists():
        return []
    baseline = _dev_wallet_baseline()
    rows: list[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                when = _dev_wallet_parse_time(row.get("timestamp"))
                if baseline and when and when < baseline:
                    continue
                rows.append(row)
    except Exception:
        return []
    return rows


def get_dev_wallet_snapshot() -> Dict[str, Any]:
    enabled = _dev_wallet_enabled()
    start = _dev_wallet_float("ENNOSMART_DEV_WALLET_START_USD", 17.0)
    reserve = _dev_wallet_float("ENNOSMART_DEV_WALLET_RESERVE_USD", 5.0)
    daily_limit = _dev_wallet_float(
        "ENNOSMART_DEV_WALLET_DAILY_LIMIT_USD", 2.0
    )
    low_warn = _dev_wallet_float(
        "ENNOSMART_DEV_WALLET_LOW_WARN_USD", 8.0
    )

    rows = _dev_wallet_read_events()
    spent = round(
        sum(_safe_float(row.get("cost_usd")) for row in rows),
        8,
    )

    today = datetime.now(timezone.utc).date()
    spent_today = 0.0
    for row in rows:
        when = _dev_wallet_parse_time(row.get("timestamp"))
        if when is not None and when.astimezone(timezone.utc).date() == today:
            spent_today += _safe_float(row.get("cost_usd"))
    spent_today = round(spent_today, 8)

    estimated_balance = round(max(0.0, start - spent), 8)
    spendable = round(max(0.0, estimated_balance - reserve), 8)
    daily_remaining = (
        round(max(0.0, daily_limit - spent_today), 8)
        if daily_limit > 0
        else spendable
    )

    return {
        "enabled": enabled,
        "mode": "local_dev_ledger",
        "starting_balance_usd": start,
        "baseline_at": _dev_wallet_env("ENNOSMART_DEV_WALLET_BASELINE_AT"),
        "tracked_spend_usd": spent,
        "tracked_spend_today_usd": spent_today,
        "estimated_openai_balance_usd": estimated_balance,
        "protected_reserve_usd": reserve,
        "spendable_remaining_usd": spendable,
        "daily_limit_usd": daily_limit,
        "daily_remaining_usd": daily_remaining,
        "low_balance_warning": estimated_balance <= low_warn,
        "calls_tracked": len(rows),
        "ledger_path": str(_dev_wallet_ledger_path()),
        "authoritative_openai_balance": False,
        "warning": (
            "Estimation locale EnnoSmart uniquement. Toute dépense OpenAI "
            "faite hors de ce backend n'est pas automatiquement déduite."
        ),
    }


def _dev_wallet_estimated_call_cost(
    llm_client: Any,
    *,
    prompt: Any,
    request_name: Any,
    max_output_tokens: Any,
) -> Dict[str, Any]:
    estimator = globals().get("_estimate_next_call_cost")
    if callable(estimator):
        result = estimator(
            llm_client=llm_client,
            prompt=prompt,
            request_name=request_name,
            max_output_tokens=max_output_tokens,
        )
        if isinstance(result, dict):
            return result

    unknown = _dev_wallet_float(
        "ENNOSMART_DEV_WALLET_UNKNOWN_CALL_RESERVE_USD", 0.10
    )
    return {
        "provider": str(
            getattr(llm_client, "provider", "unknown") or "unknown"
        ),
        "model": str(
            getattr(llm_client, "model_name", "unknown") or "unknown"
        ),
        "price_known": False,
        "estimated_cost_usd": unknown,
    }


def _dev_wallet_preflight_call(
    llm_client: Any,
    *,
    prompt: Any,
    request_name: Any,
    max_output_tokens: Any,
) -> Dict[str, Any]:
    snapshot = get_dev_wallet_snapshot()
    if not snapshot.get("enabled"):
        return snapshot

    estimate = _dev_wallet_estimated_call_cost(
        llm_client,
        prompt=prompt,
        request_name=request_name,
        max_output_tokens=max_output_tokens,
    )
    next_cost = _safe_float(estimate.get("estimated_cost_usd"))
    if not estimate.get("price_known"):
        next_cost = max(
            next_cost,
            _dev_wallet_float(
                "ENNOSMART_DEV_WALLET_UNKNOWN_CALL_RESERVE_USD",
                0.10,
            ),
        )

    if next_cost > _safe_float(snapshot.get("spendable_remaining_usd")):
        raise BudgetLimitExceeded(
            "Portefeuille DEV protégé : ce nouvel appel pourrait entamer "
            f"la réserve de ${snapshot.get('protected_reserve_usd'):.2f}. "
            f"Solde EnnoSmart estimé="
            f"${snapshot.get('estimated_openai_balance_usd'):.4f}, "
            f"prochain appel≈${next_cost:.4f}."
        )

    daily_remaining = _safe_float(snapshot.get("daily_remaining_usd"))
    if next_cost > daily_remaining:
        raise BudgetLimitExceeded(
            "Limite journalière DEV atteinte : "
            f"reste aujourd'hui=${daily_remaining:.4f}, "
            f"prochain appel≈${next_cost:.4f}."
        )

    return {
        **snapshot,
        "next_call_estimate": estimate,
    }


def dev_wallet_preflight_run(
    *,
    estimated_run_cost_usd: float,
    reason: str = "run",
) -> Dict[str, Any]:
    snapshot = get_dev_wallet_snapshot()
    if not snapshot.get("enabled"):
        return snapshot

    expected = max(0.0, float(estimated_run_cost_usd or 0.0))
    if expected > _safe_float(snapshot.get("spendable_remaining_usd")):
        raise BudgetLimitExceeded(
            f"Run bloqué avant démarrage ({reason}) : coût attendu "
            f"≈${expected:.4f}, budget dépensable restant="
            f"${snapshot.get('spendable_remaining_usd'):.4f}. "
            f"La réserve de ${snapshot.get('protected_reserve_usd'):.2f} "
            "reste protégée."
        )

    if expected > _safe_float(snapshot.get("daily_remaining_usd")):
        raise BudgetLimitExceeded(
            f"Run bloqué avant démarrage ({reason}) : coût attendu "
            f"≈${expected:.4f}, budget journalier restant="
            f"${snapshot.get('daily_remaining_usd'):.4f}."
        )
    return snapshot


def _dev_wallet_record_success(
    llm_client: Any,
    *,
    request_name: Any,
    meta: Mapping[str, Any],
    source: str = "generate",
) -> Dict[str, Any]:
    if not _dev_wallet_enabled():
        return {"recorded": False}

    usage = normalize_usage(meta)
    provider = str(
        meta.get("provider")
        or getattr(llm_client, "provider", None)
        or "unknown"
    )
    model = str(
        meta.get("model")
        or getattr(llm_client, "model_name", None)
        or "unknown"
    )
    priced = compute_cost(model, provider, usage)
    event = {
        "timestamp": _now(),
        "source": source,
        "request_name": str(
            request_name or meta.get("request_name") or "-"
        ),
        "provider": provider,
        "model": model,
        **usage,
        **priced,
    }

    path = _dev_wallet_ledger_path()
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    snapshot = get_dev_wallet_snapshot()
    print(
        "[DEV-WALLET] "
        f"request={event['request_name']} "
        f"cost=${_safe_float(event.get('cost_usd')):.6f} "
        f"balance_est=${snapshot['estimated_openai_balance_usd']:.4f} "
        f"spendable=${snapshot['spendable_remaining_usd']:.4f} "
        f"daily_left=${snapshot['daily_remaining_usd']:.4f}",
        flush=True,
    )
    return event
# END ENNOSMART_DEV_WALLET_V1


@dataclass
class BudgetRun:
    project_id: Any
    organisme: str
    project_name: str
    year: Any
    run_type: str = "ennoscholar_state_of_art"
    run_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8])
    events: list[Dict[str, Any]] = field(default_factory=list)
    started_at: str = field(default_factory=_now)
    started_perf: float = field(default_factory=time.perf_counter)
    status: str = "running"
    error: Optional[str] = None
    finalized: bool = False

    def __post_init__(self) -> None:
        self.output_dir = (
            _root() / "storage" / "organismes" / _slug(self.organisme)
            / "projects" / _slug(self.project_name) / "years" / str(self.year)
            / "ennoscholar" / "budget_logs"
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.output_dir / f"budget_{self.run_id}.jsonl"
        self.summary_path = self.output_dir / f"budget_{self.run_id}_summary.json"
        self.csv_path = self.output_dir / f"budget_{self.run_id}.csv"
        print(f"[BUDGET][RUN_START] run_id={self.run_id} project_id={self.project_id}", flush=True)

    def total_cost(self) -> float:
        return round(sum(_safe_float(e.get("cost_usd")) for e in self.events), 8)

    def record(self, meta: Optional[Mapping[str, Any]] = None, *, request_name: Any = None,
               provider: Any = None, model: Any = None, status: str = "ok", error: Any = None) -> Dict[str, Any]:
        data = dict(meta or {})
        request = str(request_name or data.get("request_name") or data.get("request") or "-")
        provider_name = str(provider or data.get("provider") or "unknown")
        model_name = str(model or data.get("model") or data.get("model_name") or "unknown")
        usage = normalize_usage(data)
        event = {
            "timestamp": _now(), "run_id": self.run_id, "project_id": self.project_id,
            "phase": infer_phase(request), "request_name": request,
            "provider": provider_name, "model": model_name, "status": status,
            "error": str(error)[:1000] if error else None,
            "elapsed_seconds": data.get("elapsed_seconds"),
            **usage, **compute_cost(model_name, provider_name, usage),
        }
        self.events.append(event)
        with _LOCK:
            with self.jsonl_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        print(
            f"[BUDGET][CALL] phase={event['phase']} request={request} model={model_name} "
            f"input={usage['input_tokens']} cached={usage['cached_input_tokens']} "
            f"output={usage['output_tokens']} total={usage['total_tokens']} "
            f"cost=${event['cost_usd']:.6f} run_cost=${self.total_cost():.6f}" +
            (" estimated_split=1" if usage["usage_split_estimated"] else ""),
            flush=True,
        )
        warn = _safe_float(os.getenv("ENNOSMART_BUDGET_WARN_USD", "0.75"))
        if warn > 0 and self.total_cost() >= warn:
            print(f"[BUDGET][WARNING] coût de la relance >= ${warn:.2f}", flush=True)
        return event

    def _aggregate(self, key: str) -> Dict[str, Dict[str, Any]]:
        rows: Dict[str, Dict[str, Any]] = {}
        for event in self.events:
            name = str(event.get(key) or "unknown")
            row = rows.setdefault(name, {key: name, "calls": 0, "failed_calls": 0,
                                         "input_tokens": 0, "cached_input_tokens": 0,
                                         "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0})
            row["calls"] += 1
            row["failed_calls"] += int(event.get("status") != "ok")
            for token_key in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"):
                row[token_key] += _safe_int(event.get(token_key))
            row["cost_usd"] += _safe_float(event.get("cost_usd"))
        for row in rows.values():
            row["cost_usd"] = round(row["cost_usd"], 8)
        return rows

    def summary(self) -> Dict[str, Any]:
        phase_rows = {phase: {"phase": phase, "calls": 0, "failed_calls": 0,
                              "input_tokens": 0, "cached_input_tokens": 0,
                              "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
                      for phase in PHASES}
        phase_rows.update(self._aggregate("phase"))
        return {
            "ok": self.status == "completed", "status": self.status, "error": self.error,
            "run_id": self.run_id, "run_type": self.run_type,
            "project": {"id": self.project_id, "organisme": self.organisme,
                        "project_name": self.project_name, "year": self.year},
            "started_at": self.started_at, "finished_at": _now() if self.finalized else None,
            "elapsed_seconds": round(time.perf_counter() - self.started_perf, 3),
            "pricing_version": "official_openai_2026-07-31",
            "totals": {
                "calls": len(self.events),
                "failed_calls": sum(e.get("status") != "ok" for e in self.events),
                "input_tokens": sum(_safe_int(e.get("input_tokens")) for e in self.events),
                "cached_input_tokens": sum(_safe_int(e.get("cached_input_tokens")) for e in self.events),
                "output_tokens": sum(_safe_int(e.get("output_tokens")) for e in self.events),
                "total_tokens": sum(_safe_int(e.get("total_tokens")) for e in self.events),
                "cost_usd": self.total_cost(),
            },
            "by_phase": list(phase_rows.values()),
            "by_model": list(self._aggregate("model").values()),
            "by_request": list(self._aggregate("request_name").values()),
            "files": {"jsonl": str(self.jsonl_path), "summary_json": str(self.summary_path), "csv": str(self.csv_path)},
        }

    def finalize(self, status: str, error: Any = None) -> Dict[str, Any]:
        if self.finalized:
            return self.summary()
        self.status, self.error, self.finalized = status, (str(error)[:2000] if error else None), True
        report = self.summary()
        with _LOCK:
            self.summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            fields = ["timestamp", "phase", "request_name", "provider", "model", "status",
                      "input_tokens", "cached_input_tokens", "output_tokens", "total_tokens",
                      "cost_usd", "usage_split_estimated", "elapsed_seconds", "error"]
            with self.csv_path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                for event in self.events:
                    writer.writerow({name: event.get(name) for name in fields})
        total = report["totals"]
        print("=" * 100, flush=True)
        print(f"[BUDGET][RUN_END] status={status} run_id={self.run_id} calls={total['calls']} "
              f"tokens={total['total_tokens']} cost=${total['cost_usd']:.6f}", flush=True)
        for row in report["by_phase"]:
            print(f"[BUDGET][PHASE] {row['phase']} calls={row['calls']} "
                  f"tokens={row['total_tokens']} cost=${row['cost_usd']:.6f}", flush=True)
        print(f"[BUDGET][REPORT] {self.summary_path}", flush=True)
        print(f"[BUDGET][CSV] {self.csv_path}", flush=True)
        print("=" * 100, flush=True)
        return report


def budgeted_pipeline(func=None, *, run_type: str = "ennoscholar_state_of_art"):
    def decorator(target):
        if getattr(target, "_ennosmart_budgeted", False):
            return target
        @functools.wraps(target)
        def wrapped(*args, **kwargs):
            if not _enabled() or _CURRENT_RUN.get() is not None:
                return target(*args, **kwargs)
            project = kwargs.get("project")
            if project is None:
                project = next((x for x in args if hasattr(x, "project_name") and hasattr(x, "id")), None)
            run = BudgetRun(
                project_id=getattr(project, "id", "unknown"),
                organisme=str(getattr(project, "organisme", "unknown")),
                project_name=str(getattr(project, "project_name", "unknown")),
                year=getattr(project, "year", "unknown"),
                run_type=run_type,
            )
            token = _CURRENT_RUN.set(run)
            try:
                result = target(*args, **kwargs)
                logical_status = (
                    "completed"
                    if not isinstance(result, dict)
                    or result.get("ok", True)
                    else "stopped"
                )
                report = run.finalize(logical_status)
                if isinstance(result, dict):
                    result = dict(result)
                    result["budget"] = report
                return result
            except Exception as exc:
                run.finalize("failed", exc)
                raise
            finally:
                _CURRENT_RUN.reset(token)
        wrapped._ennosmart_budgeted = True
        return wrapped
    return decorator(func) if func is not None else decorator


# BEGIN ENNOSMART_OPENAI_429_RETRY_V1
_OPENAI_CALL_LOCKS: Dict[str, threading.RLock] = {}
_OPENAI_CALL_LOCKS_GUARD = threading.RLock()
_OPENAI_LAST_FINISH: Dict[str, float] = {}


def _retry_env_int(name: str, default: int) -> int:
    try:
        return max(0, int(str(os.getenv(name, default)).strip()))
    except Exception:
        return default


def _retry_env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(str(os.getenv(name, default)).strip()))
    except Exception:
        return default


def _openai_model_from_call(args: tuple, kwargs: Mapping[str, Any]) -> str:
    for key in ("model", "model_name", "current_model"):
        value = kwargs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().split("/", 1)[-1]

    for value in args:
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.startswith("gpt-") or candidate.startswith("openai/gpt-"):
                return candidate.split("/", 1)[-1]
    return "openai_unknown_model"


def _openai_lock_for(model: str) -> threading.RLock:
    key = str(model or "openai_unknown_model").lower()
    with _OPENAI_CALL_LOCKS_GUARD:
        lock = _OPENAI_CALL_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _OPENAI_CALL_LOCKS[key] = lock
        return lock


def _parse_retry_after_seconds(error: BaseException) -> Optional[float]:
    text = str(error or "")
    patterns = (
        r"please\s+try\s+again\s+in\s+([0-9]+(?:\.[0-9]+)?)\s*s",
        r"try\s+again\s+in\s+([0-9]+(?:\.[0-9]+)?)\s*seconds?",
        r"retry[-_\s]*after[^0-9]*([0-9]+(?:\.[0-9]+)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                return max(0.0, float(match.group(1)))
            except Exception:
                pass
    return None


def _is_openai_rate_limit(error: BaseException) -> bool:
    text = str(error or "").lower()
    return (
        "http 429" in text
        or "status code: 429" in text
        or "rate_limit_exceeded" in text
        or "rate limit reached" in text
    )


def _minimum_gap_for_model(model: str) -> float:
    normalized = str(model or "").lower().split("/", 1)[-1]
    if normalized == "gpt-4.1":
        return _retry_env_float("ENNOSMART_GPT41_MIN_GAP_SECONDS", 20.0)
    return _retry_env_float("ENNOSMART_OPENAI_MIN_GAP_SECONDS", 0.0)


def _install_openai_429_retry(llm_class: Any) -> bool:
    if getattr(llm_class, "_ennosmart_openai_429_retry_installed", False):
        return True

    method_name = None
    original = None
    for candidate in (
        "_generate_openai",
        "_call_openai",
        "_openai_generate",
        "generate_openai",
        "_request_openai",
    ):
        method = getattr(llm_class, candidate, None)
        if callable(method):
            method_name = candidate
            original = method
            break

    if method_name is None or original is None:
        print(
            "[LLM-RATE][WARN] Methode OpenAI interne introuvable. "
            "Le retry 429 n'a pas ete installe.",
            flush=True,
        )
        return False

    @functools.wraps(original)
    def wrapped_openai(self: Any, *args: Any, **kwargs: Any) -> Any:
        model = _openai_model_from_call(args, kwargs)
        model_key = str(model or "openai_unknown_model").lower()
        lock = _openai_lock_for(model_key)
        max_retries = _retry_env_int("ENNOSMART_OPENAI_429_MAX_RETRIES", 4)
        buffer_seconds = _retry_env_float(
            "ENNOSMART_OPENAI_429_RETRY_BUFFER_SECONDS",
            1.5,
        )

        # Une seule requete par modele a la fois dans le processus backend.
        with lock:
            min_gap = _minimum_gap_for_model(model_key)
            last_finish = _OPENAI_LAST_FINISH.get(model_key, 0.0)
            remaining_gap = min_gap - (time.monotonic() - last_finish)
            if remaining_gap > 0:
                print(
                    f"[LLM-RATE][QUEUE] model={model} "
                    f"wait={remaining_gap:.2f}s reason=min_gap",
                    flush=True,
                )
                time.sleep(remaining_gap)

            for attempt in range(max_retries + 1):
                try:
                    result = original(self, *args, **kwargs)
                    _OPENAI_LAST_FINISH[model_key] = time.monotonic()
                    if attempt > 0:
                        print(
                            f"[LLM-RATE][RECOVERED] model={model} "
                            f"attempt={attempt + 1}/{max_retries + 1}",
                            flush=True,
                        )
                    return result
                except Exception as exc:
                    if not _is_openai_rate_limit(exc) or attempt >= max_retries:
                        _OPENAI_LAST_FINISH[model_key] = time.monotonic()
                        raise

                    provider_delay = _parse_retry_after_seconds(exc)
                    exponential_delay = min(60.0, 2.0 ** attempt)
                    delay = max(
                        provider_delay if provider_delay is not None else 0.0,
                        exponential_delay,
                    ) + buffer_seconds

                    print(
                        f"[LLM-RATE][429] model={model} "
                        f"attempt={attempt + 1}/{max_retries + 1} "
                        f"retry_after={provider_delay if provider_delay is not None else 'unknown'} "
                        f"sleep={delay:.2f}s",
                        flush=True,
                    )
                    time.sleep(delay)

            raise RuntimeError("Boucle retry OpenAI terminee sans resultat.")

    setattr(llm_class, method_name, wrapped_openai)
    llm_class._ennosmart_openai_429_retry_installed = True
    print(
        f"[LLM-RATE] Retry 429 installe sur LLMClient.{method_name}",
        flush=True,
    )
    return True
# END ENNOSMART_OPENAI_429_RETRY_V1

def install_llm_budget_hooks(llm_class: Any) -> None:
    if not _enabled():
        return

    # Le retry réseau est centralisé dans LLMClient._post_with_retry afin de
    # couvrir aussi la Responses API utilisée par la recherche Web. Ne pas
    # ajouter ici une seconde boucle, qui multiplierait les appels après un 429.

    if getattr(llm_class, "_budget_hook_installed", False):
        return
    original = getattr(llm_class, "generate", None)
    if not callable(original):
        print("[BUDGET][WARN] LLMClient.generate introuvable", flush=True)
        return

    @functools.wraps(original)
    def wrapped(self, *args, **kwargs):
        depth = _HOOK_DEPTH.get()
        token = _HOOK_DEPTH.set(depth + 1)
        started = time.perf_counter()
        try:
            current_run = _CURRENT_RUN.get()
            if depth == 0:
                _dev_wallet_preflight_call(
                    self,
                    prompt=(
                        args[0]
                        if args
                        else kwargs.get("prompt", "")
                    ),
                    request_name=kwargs.get("request_name"),
                    max_output_tokens=kwargs.get(
                        "max_output_tokens",
                        1400,
                    ),
                )
            if depth == 0 and current_run is not None:
                max_calls = _budget_max_calls()
                if (
                    max_calls > 0
                    and len(current_run.events) >= max_calls
                ):
                    raise BudgetLimitExceeded(
                        "Plafond d'appels LLM atteint avant "
                        "la prochaine requête : "
                        f"{len(current_run.events)}/{max_calls}."
                    )

                prompt = (
                    args[0]
                    if args
                    else kwargs.get("prompt", "")
                )
                request_name = kwargs.get("request_name")
                max_output_tokens = kwargs.get(
                    "max_output_tokens", 1400
                )
                estimate = _estimate_next_call_cost(
                    llm_client=self,
                    prompt=prompt,
                    request_name=request_name,
                    max_output_tokens=max_output_tokens,
                )
                # ENNOSMART_DYNAMIC_COST_UNKNOWN_PRICE_V3
                if (
                    not estimate.get("price_known")
                    and str(
                        os.getenv(
                            "ENNOSMART_BUDGET_BLOCK_UNKNOWN_PRICE",
                            "1",
                        )
                    ).strip().casefold()
                    in ('1', 'true', 'yes', 'oui', 'on')
                ):
                    raise BudgetLimitExceeded(
                        "Coût du modèle inconnu : appel bloqué "
                        "avant dépense. Configurez son prix dans "
                        "usage_budget.py avant de l'autoriser."
                    )

                hard_limit = _budget_hard_limit_usd()
                projected = (
                    current_run.total_cost()
                    + _safe_float(
                        estimate.get("estimated_cost_usd")
                    )
                )
                if (
                    hard_limit > 0
                    and estimate.get("price_known")
                    and projected > hard_limit
                ):
                    print(
                        "[BUDGET][BLOCK] "
                        f"request={request_name or '-'} "
                        f"model={estimate.get('model')} "
                        f"spent=${current_run.total_cost():.6f} "
                        f"estimated_next="
                        f"${estimate.get('estimated_cost_usd'):.6f} "
                        f"hard_limit=${hard_limit:.2f}",
                        flush=True,
                    )
                    raise BudgetLimitExceeded(
                        "Plafond financier EnnoScholar atteint "
                        "avant un nouvel appel payant : "
                        f"dépensé=${current_run.total_cost():.4f}, "
                        f"prochain appel estimé="
                        f"${estimate.get('estimated_cost_usd'):.4f}, "
                        f"plafond=${hard_limit:.2f}."
                    )

            output = original(self, *args, **kwargs)
            if depth == 0:
                wallet_meta: Dict[str, Any] = {}
                wallet_getter = getattr(
                    self,
                    "get_last_generation_meta",
                    None,
                )
                if callable(wallet_getter):
                    candidate = wallet_getter()
                    if isinstance(candidate, Mapping):
                        wallet_meta = dict(candidate)
                _dev_wallet_record_success(
                    self,
                    request_name=(
                        kwargs.get("request_name")
                        or wallet_meta.get("request_name")
                    ),
                    meta=wallet_meta,
                    source="generate",
                )
            if depth == 0 and _CURRENT_RUN.get() is not None:
                meta: Dict[str, Any] = {}
                getter = getattr(self, "get_last_generation_meta", None)
                if callable(getter):
                    candidate = getter()
                    if isinstance(candidate, Mapping):
                        meta = dict(candidate)
                elif isinstance(getattr(self, "last_generation_meta", None), Mapping):
                    meta = dict(self.last_generation_meta)
                elif isinstance(getattr(self, "_last_generation_meta", None), Mapping):
                    meta = dict(self._last_generation_meta)
                meta.setdefault("elapsed_seconds", round(time.perf_counter() - started, 3))
                _CURRENT_RUN.get().record(
                    meta,
                    request_name=kwargs.get("request_name") or meta.get("request_name"),
                    provider=meta.get("provider") or getattr(self, "provider", None),
                    model=meta.get("model") or getattr(self, "model_name", None),
                )
            return output
        except Exception as exc:
            if (
                depth == 0
                and _CURRENT_RUN.get() is not None
                and not isinstance(exc, BudgetLimitExceeded)
            ):
                _CURRENT_RUN.get().record(
                    {
                        "elapsed_seconds": round(
                            time.perf_counter() - started, 3
                        )
                    },
                    request_name=kwargs.get("request_name"),
                    provider=getattr(self, "provider", None),
                    model=getattr(self, "model_name", None),
                    status="failed",
                    error=exc,
                )
            raise
        finally:
            _HOOK_DEPTH.reset(token)

    llm_class.generate = wrapped
    llm_class._budget_hook_installed = True
    print("[BUDGET] Hook LLMClient.generate installé", flush=True)

    # DEV Wallet : web_search utilise Responses API directement.
    web_original = getattr(llm_class, "web_search", None)
    if (
        callable(web_original)
        and not getattr(
            llm_class,
            "_dev_wallet_web_hook_installed",
            False,
        )
    ):
        @functools.wraps(web_original)
        def wrapped_web_search(self, *args, **kwargs):
            depth = _HOOK_DEPTH.get()
            token = _HOOK_DEPTH.set(depth + 1)
            try:
                if depth == 0:
                    query = (
                        args[0]
                        if args
                        else kwargs.get("query", "")
                    )
                    _dev_wallet_preflight_call(
                        self,
                        prompt=query,
                        request_name=kwargs.get(
                            "request_name",
                            "ennoscholar:guided_research:web_search",
                        ),
                        max_output_tokens=kwargs.get(
                            "max_output_tokens",
                            1400,
                        ),
                    )

                result = web_original(self, *args, **kwargs)

                if depth == 0:
                    getter = getattr(
                        self,
                        "get_last_generation_meta",
                        None,
                    )
                    meta: Dict[str, Any] = {}
                    if callable(getter):
                        candidate = getter()
                        if isinstance(candidate, Mapping):
                            meta = dict(candidate)

                    _dev_wallet_record_success(
                        self,
                        request_name=(
                            kwargs.get("request_name")
                            or meta.get("request_name")
                            or "ennoscholar:guided_research:web_search"
                        ),
                        meta=meta,
                        source="web_search",
                    )
                return result
            finally:
                _HOOK_DEPTH.reset(token)

        llm_class.web_search = wrapped_web_search
        llm_class._dev_wallet_web_hook_installed = True
        print(
            "[DEV-WALLET] Hook LLMClient.web_search installé",
            flush=True,
        )

