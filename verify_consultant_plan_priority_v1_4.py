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
    scripts = (
        root / "prepare_consultant_plan_snapshot_no_llm.py",
        root / "test_phase5_guard_no_llm.py",
    )

    for path in (writer, *scripts):
        if not path.exists():
            raise SystemExit(f"MISSING: {path}")
        py_compile.compile(str(path), doraise=True)

    text = writer.read_text(encoding="utf-8")
    checks = {
        "v14_marker":
            "BEGIN ENNOSMART_CONSULTANT_PLAN_PRIORITY_V1_4" in text,
        "v13_removed":
            "BEGIN ENNOSMART_PHASE5_SECTION_TITLES_HOTFIX_V1_3" not in text,
        "fail_closed":
            "CONSULTANT_PLAN_SNAPSHOT_REQUIRED" in text,
        "snapshot_source":
            "consultant_plan_snapshot.json" in text,
        "pre_llm_attach":
            "_v14_attach_contract_to_phase47" in text,
        "guard_uses_consultant":
            'guard_blueprint["sections"] = _v14_merge_guard_sections' in text,
        "public_alias":
            "run_phase_5 = run_phase_5_state_of_art_writer" in text,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("CONSULTANT_PLAN_PRIORITY_VERIFY_FAILED")
        for name in failed:
            print(f"- {name}")
        return 2

    print("CONSULTANT_PLAN_PRIORITY_VERIFY_OK")
    print(
        "Le plan consultant validé est prioritaire ; la phase 5 bloque "
        "avant tout LLM si le snapshot manque."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
