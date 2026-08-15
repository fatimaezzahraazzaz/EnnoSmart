# -*- coding: utf-8 -*-
from __future__ import annotations

"""Vue et préflight du portefeuille DEV EnnoSmart.

Le solde retourné ici est une estimation locale EnnoSmart :
solde de départ configuré - appels enregistrés par EnnoSmart depuis la baseline.

Ce n'est pas le solde officiel du compte OpenAI.
"""

from typing import Any, Dict

from modules.LLM.usage_budget import (
    dev_wallet_preflight_run,
    get_dev_wallet_snapshot,
)
from services.ennoscholar_cost_service import estimate_state_of_art_cost


def get_dev_budget_status(*, db: Any, project: Any) -> Dict[str, Any]:
    estimate = estimate_state_of_art_cost(db=db, project=project)
    wallet = get_dev_wallet_snapshot()
    expected = float(
        (estimate.get("estimated_cost_usd") or {}).get("expected") or 0.0
    )
    return {
        "ok": True,
        "wallet": wallet,
        "state_of_art_estimate": estimate,
        "safe_to_start_expected_run": bool(
            not wallet.get("enabled")
            or (
                expected <= float(wallet.get("spendable_remaining_usd") or 0.0)
                and expected <= float(wallet.get("daily_remaining_usd") or 0.0)
            )
        ),
        "note": (
            "Le solde EnnoSmart est une estimation locale. Toute consommation "
            "OpenAI faite hors de ce backend n'est pas automatiquement déduite."
        ),
    }


def assert_dev_budget_for_state_of_art(*, db: Any, project: Any) -> Dict[str, Any]:
    estimate = estimate_state_of_art_cost(db=db, project=project)
    expected = float(
        (estimate.get("estimated_cost_usd") or {}).get("expected") or 0.0
    )
    high = float(
        (estimate.get("estimated_cost_usd") or {}).get("high") or expected
    )
    wallet = dev_wallet_preflight_run(
        estimated_run_cost_usd=expected,
        reason="ennoscholar_state_of_art_expected_cost",
    )
    return {
        "ok": True,
        "wallet": wallet,
        "estimate": estimate,
        "expected_cost_usd": expected,
        "high_cost_usd": high,
    }
