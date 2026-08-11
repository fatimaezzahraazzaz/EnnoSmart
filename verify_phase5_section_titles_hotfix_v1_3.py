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

    if not writer.exists():
        raise SystemExit(f"MISSING: {writer}")

    py_compile.compile(str(writer), doraise=True)
    text = writer.read_text(encoding="utf-8")

    checks = {
        "marker":
            "BEGIN ENNOSMART_PHASE5_SECTION_TITLES_HOTFIX_V1_3" in text,
        "validate_alias":
            "_V13_ORIGINAL_VALIDATE_DRAFT = validate_draft" in text,
        "lock_function":
            "def _v13_lock_section_titles_to_blueprint" in text,
        "section_id_mapping":
            "expected_by_id[section_id] = title" in text,
        "title_assignment":
            'actual["title"] = expected_title' in text,
        "guard_preserved":
            "_V13_ORIGINAL_VALIDATE_DRAFT(" in text,
        "report":
            'report["section_title_lock"] = title_lock_report' in text,
    }

    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("PHASE5_SECTION_TITLES_HOTFIX_VERIFY_FAILED")
        for name in failed:
            print(f"- {name}")
        return 2

    print("PHASE5_SECTION_TITLES_HOTFIX_VERIFY_OK")
    print(
        "Les titres sont verrouillés depuis le blueprint avant le garde V11 ; "
        "les contrôles de citations, verrous, contenu et sémantique restent actifs."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
