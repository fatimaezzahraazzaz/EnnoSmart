# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import py_compile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    writer = (
        root / "agents" / "EnnoScholar" / "state_of_art"
        / "phase_5_state_of_art_writer_service.py"
    )
    recovery = root / "recover_best_phase5_llm_draft_no_llm.py"

    for path in (writer, recovery):
        if not path.exists():
            raise SystemExit(f"MISSING: {path}")
        py_compile.compile(str(path), doraise=True)

    text = writer.read_text(encoding="utf-8")
    checks = {
        "marker":
            "BEGIN ENNOSMART_PHASE5_PUBLIC_MARKDOWN_QUALITY_GATE_V1_6"
            in text,
        "raw_guard": "PHASE5_PUBLIC_MARKDOWN_LOW_QUALITY" in text,
        "recovery_log": "PUBLIC_MARKDOWN_RECOVERED" in text,
        "public_alias": "run_phase_5 = run_phase_5_state_of_art_writer" in text,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("PHASE5_LLM_DRAFT_RECOVERY_VERIFY_FAILED")
        for name in failed:
            print(f"- {name}")
        return 2

    print("PHASE5_LLM_DRAFT_RECOVERY_VERIFY_OK")
    print(
        "Le Markdown public doit être une rédaction française structurée ; "
        "les extraits bruts déterministes sont refusés."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
