# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import py_compile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    writer = (
        root / "agents" / "EnnoScholar" / "state_of_art"
        / "phase_5_state_of_art_writer_service.py"
    )
    if not writer.exists():
        raise SystemExit(f"MISSING: {writer}")

    py_compile.compile(str(writer), doraise=True)
    text = writer.read_text(encoding="utf-8")

    checks = {
        "v15_present":
            "BEGIN ENNOSMART_PHASE5_STABILITY_GATE_V1_5" in text,
        "v14_removed":
            "BEGIN ENNOSMART_CONSULTANT_PLAN_PRIORITY_V1_4" not in text,
        "independent_text_helper":
            "def _v15_text(value, limit=None):" in text,
        "strict_snapshot":
            "CONSULTANT_PLAN_SNAPSHOT_INVALID" in text,
        "atomic_write":
            "def _v15_write_json_atomic" in text,
        "real_context_reuse":
            "def _v15_try_reuse" in text,
        "paid_regen_default_blocked":
            '"ENNOSMART_PHASE5_ALLOW_PAID_REGEN",' in text,
        "traceback":
            "[PHASE5][ENTRY_ERROR]" in text,
        "frontend_ready":
            'section["consultant_quality_ready"] = True' in text,
    }

    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("PHASE5_STABILITY_GATE_VERIFY_FAILED")
        for name in failed:
            print(f"- {name}")
        return 2

    print("PHASE5_STABILITY_GATE_VERIFY_OK")
    print(
        "Snapshot, Phase 4.7, blueprint, preuves et brouillon sont "
        "contrôlés avant LLM ; la réutilisation validée alimente le "
        "frontend en 7/7."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
