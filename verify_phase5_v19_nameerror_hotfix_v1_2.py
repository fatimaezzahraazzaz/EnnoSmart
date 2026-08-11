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
        "cards_alias_safe":
            '_V19_ORIGINAL_CARDS_FOR_SECTION = globals().get("_v80_cards_for_section")'
            in text,
        "validator_alias_safe":
            '_V19_ORIGINAL_VALIDATE_SECTION = globals().get("_v80_validate_section")'
            in text,
        "polish_alias_safe":
            '_V19_ORIGINAL_OPTIONAL_POLISH = globals().get("optional_polish")'
            in text,
        "validator_fallback":
            "fallback_validator_used" in text,
        "polish_fallback":
            "v19_no_previous_optional_polish_deterministic_preserved" in text,
        "unsafe_cards_alias_absent":
            "_V19_ORIGINAL_CARDS_FOR_SECTION = _v80_cards_for_section"
            not in text,
        "unsafe_validator_alias_absent":
            "_V19_ORIGINAL_VALIDATE_SECTION = _v80_validate_section"
            not in text,
        "unsafe_polish_alias_absent":
            "_V19_ORIGINAL_OPTIONAL_POLISH = optional_polish"
            not in text,
    }

    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("PHASE5_V19_NAMEERROR_HOTFIX_VERIFY_FAILED")
        for name in failed:
            print(f"- {name}")
        return 2

    print("PHASE5_V19_NAMEERROR_HOTFIX_VERIFY_OK")
    print(
        "Les dépendances V80 optionnelles sont maintenant résolues "
        "sans NameError."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
