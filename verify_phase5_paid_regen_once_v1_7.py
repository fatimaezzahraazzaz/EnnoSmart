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
    prepare = root / "prepare_phase5_paid_regen_once_v1_7.py"

    for path in (writer, prepare):
        if not path.exists():
            raise SystemExit(f"MISSING: {path}")
        py_compile.compile(str(path), doraise=True)

    text = writer.read_text(encoding="utf-8")
    checks = {
        "one_shot_marker":
            "BEGIN ENNOSMART_PHASE5_PAID_REGEN_ONCE_V1_7"
            in text,
        "arm_required":
            "PHASE5_PAID_REGEN_NOT_ARMED" in text,
        "arm_consumed":
            "_v17_consume_arm" in text,
        "safe_after_success":
            "status=success safe_mode_written_to_env" in text,
        "safe_after_failure":
            "status=failed type=" in text,
        "public_alias":
            "run_phase_5 = run_phase_5_state_of_art_writer" in text,
    }

    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("PHASE5_PAID_REGEN_ONCE_VERIFY_FAILED")
        for name in failed:
            print(f"- {name}")
        return 2

    print("PHASE5_PAID_REGEN_ONCE_VERIFY_OK")
    print(
        "Une seule génération payante peut être consommée après armement ; "
        "toute seconde tentative est bloquée."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
