"""Offline financial replay of runs 128698bb and 91590987 (audit receipts, no provider call).

For each received call: former estimate (input/output only), new amount by category,
former and new reservation bounds, settled amount in a temporary ledger, and the
pricing error of the former formula. Prints a Markdown report.

    python tests/ennoscholar_v4/finance_replay.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend_api")]

from sqlalchemy import create_engine  # noqa: E402

from agents.EnnoScholarV4.config import ScholarV4Config  # noqa: E402
from agents.EnnoScholarV4.finance import BudgetPolicy, Ledger, SpendScope, migrate, task_key  # noqa: E402
from agents.EnnoScholarV4.pricing import cost, legacy_cost, reserve_bound, resolve, usage_categories  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "audit_128698bb_receipts.json").read_text(encoding="utf-8"))
OUTPUT_TOKENS = 7000


def replay():
    price = resolve(ScholarV4Config(api_key="offline", model="gpt-5.6-sol"))
    rows = []
    with tempfile.TemporaryDirectory() as folder:
        engine = create_engine(f"sqlite:///{Path(folder) / 'ledger.db'}")
        migrate(engine)
        ledger = Ledger(engine, BudgetPolicy())
        for index, receipt in enumerate(FIXTURE["receipts"], 1):
            tokens = usage_categories(receipt["usage"])
            new = cost(receipt["usage"], price)
            old = legacy_cost(receipt["usage"], 4.0, 20.0)
            # The request size estimate is not archived: the received input count stands for it.
            old_reserve = (tokens["input_tokens"]*4.0 + OUTPUT_TOKENS*20.0)/1e6
            new_reserve = reserve_bound(tokens["input_tokens"], OUTPUT_TOKENS, price)
            scope = SpendScope("org", 1, "project:replay", receipt["execution"])
            ticket = ledger.reserve(scope, task_key(scope, receipt["kind"], [receipt["target"]]), new_reserve,
                kind=receipt["kind"], label=receipt["target"], model="gpt-5.6-sol")
            ledger.settle(ticket, new["usd"], usage=receipt["usage"])
            rows.append({"n": index, "run": receipt["run"].split()[0], "kind": receipt["kind"], **tokens,
                "old_usd": round(old, 6), "new_usd": round(new["usd"], 6), "pricing_error_usd": round(new["usd"]-old, 6),
                "old_reserve_usd": round(old_reserve, 6), "new_reserve_usd": round(new_reserve, 6),
                "old_reserve_covers": old_reserve >= new["usd"], "new_reserve_covers": new_reserve >= new["usd"],
                "settled_usd": round(next(r for r in ledger.history() if r["call_id"] == ticket.call_id)["settled_usd"], 6),
                "audit_standard_usd": receipt["audit_standard_cost"]})
        engine.dispose()  # Windows: release the file before the temporary folder is removed
    return rows


def report(rows):
    lines = ["| # | Run | Tâche | Entrée ord. | Cache lu | Cache écrit | Sortie (dont raisonnement) | Ancien $ | Nouveau $ | Écart $ | Réservé avant $ | Réservé après $ | Réglé $ |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(f"| {r['n']} | {r['run']} | {r['kind']} | {r['ordinary_input_tokens']} | {r['cache_read_tokens']} | "
            f"{r['cache_write_tokens']} | {r['output_tokens']} ({r['reasoning_tokens']}) | {r['old_usd']:.6f} | {r['new_usd']:.6f} | "
            f"{r['pricing_error_usd']:+.6f} | {r['old_reserve_usd']:.6f} | {r['new_reserve_usd']:.6f} | {r['settled_usd']:.6f} |")
    for run in dict.fromkeys(r["run"] for r in rows):
        part = [r for r in rows if r["run"] == run]
        lines.append(f"\n**{run}** : {len(part)} appels, ancien {sum(r['old_usd'] for r in part):.6f} $, nouveau "
            f"{sum(r['new_usd'] for r in part):.6f} $ (audit {sum(r['audit_standard_usd'] for r in part):.6f} $), "
            f"sous-estimation corrigée {sum(r['pricing_error_usd'] for r in part):+.6f} $ ; réservations nouvelles qui couvrent le "
            f"coût réel : {sum(r['new_reserve_covers'] for r in part)}/{len(part)} (anciennes : {sum(r['old_reserve_covers'] for r in part)}/{len(part)}).")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(replay()))
