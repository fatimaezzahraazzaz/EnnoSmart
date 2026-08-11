# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import py_compile
import re
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
    usage_path = root / "modules" / "LLM" / "usage_budget.py"
    env_path = root / ".env"

    for path in (writer, usage_path):
        if not path.exists():
            raise SystemExit(f"MISSING: {path}")
        py_compile.compile(str(path), doraise=True)

    writer_text = writer.read_text(encoding="utf-8")
    usage_text = usage_path.read_text(encoding="utf-8")
    env_text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""

    checks = {
        "quality_marker":
            "BEGIN ENNOSMART_PHASE5_QUALITY_RECOVERY_V1" in writer_text,
        "compact_prompt":
            "CONTRAT DE PREUVES COMPACT" in writer_text,
        "gap_validator":
            "hard_evidence_guards_plus_soft_style_warnings" in writer_text,
        "cross_section_dedupe":
            "cross_section_semantic_duplicate" in writer_text,
        "polish_fallback":
            "v19_optional_polish_failed_deterministic_preserved" in writer_text,
        "taxonomy_rule":
            "N'assimile jamais deux familles techniques différentes" in writer_text,
        "calibrated_claim_rule":
            "dans le corpus sélectionné" in writer_text,
        "retry_installed":
            "BEGIN ENNOSMART_OPENAI_429_RETRY_V1" in usage_text,
        "safe_verifier_hotfix":
            "BEGIN ENNOSMART_PHASE5_SEMANTIC_VERIFIER_HOTFIX_V1_1" in writer_text,
        "unsafe_verifier_wrapper_absent":
            "BEGIN ENNOSMART_PHASE5_SEMANTIC_VERIFIER_WRAPPERS_V1" not in writer_text,
        "gpt41_gap_45":
            re.search(
                r"(?m)^ENNOSMART_GPT41_MIN_GAP_SECONDS\s*=\s*45\s*$",
                env_text,
            ) is not None,
    }

    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("PHASE5_QUALITY_RECOVERY_VERIFY_FAILED")
        for name in failed:
            print(f"- {name}")
        return 2

    print("PHASE5_QUALITY_RECOVERY_VERIFY_OK")
    print(
        "Contrôles : prompt compact, anti-répétition, validation du gap, "
        "prudence scientifique, taxonomie, fallback déterministe, "
        "hotfix du vérificateur et retry 429."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
