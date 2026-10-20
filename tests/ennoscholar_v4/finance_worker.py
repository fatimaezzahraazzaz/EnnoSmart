"""Separate OS process for the multi-process reservation test (not a test module).

Usage: python finance_worker.py <sqlite_path> <consultant_id> <attempts> <amount_usd> <policy_json>
Prints one JSON line: {"admitted": n, "refused": m}.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend_api")]

from sqlalchemy import create_engine  # noqa: E402

from agents.EnnoScholarV4.finance import BudgetPolicy, BudgetRefused, Ledger, SpendScope, task_key  # noqa: E402


def main():
    path, consultant, attempts, amount, policy = sys.argv[1:6]
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 60})
    ledger = Ledger(engine, BudgetPolicy(json.loads(policy)))
    scope = SpendScope("org", int(consultant), f"conversation:{consultant}")
    admitted = refused = 0
    start = float(sys.argv[6]) if len(sys.argv) > 6 else 0
    while time.time() < start:
        time.sleep(.01)
    for _ in range(int(attempts)):
        try:
            ticket = ledger.reserve(scope, task_key(scope, "reading", ["w"]), float(amount), kind="reading", label="w",
                model="gpt-5.6-sol")
            admitted += 1
            ledger.settle(ticket, float(amount))
        except BudgetRefused:
            refused += 1
    print(json.dumps({"admitted": admitted, "refused": refused}))


if __name__ == "__main__":
    main()
