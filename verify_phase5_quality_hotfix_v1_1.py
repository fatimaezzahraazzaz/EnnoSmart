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
        "unsafe_wrapper_removed":
            "BEGIN ENNOSMART_PHASE5_SEMANTIC_VERIFIER_WRAPPERS_V1" not in text,
        "safe_hotfix_present":
            "BEGIN ENNOSMART_PHASE5_SEMANTIC_VERIFIER_HOTFIX_V1_1" in text,
        "exact_alias_present":
            "_V19_SAFE_ORIGINAL_INDEPENDENT_SEMANTIC_VERIFIER" in text,
        "writer_not_aliased":
            "_V19_ORIGINAL_SEMANTIC_run_phase_5_state_of_art_writer" not in text,
        "sectional_writer_not_aliased":
            "_V19_ORIGINAL_SEMANTIC_call_sectional_writer_llm" not in text,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise SystemExit(
            "PHASE5_QUALITY_HOTFIX_VERIFY_FAILED: " + ", ".join(failed)
        )

    hotfix = text.split(
        "# BEGIN ENNOSMART_PHASE5_SEMANTIC_VERIFIER_HOTFIX_V1_1",
        1,
    )[1].split(
        "# END ENNOSMART_PHASE5_SEMANTIC_VERIFIER_HOTFIX_V1_1",
        1,
    )[0]
    if hotfix.count("def _call_independent_semantic_verifier(") != 1:
        raise SystemExit("Nombre incorrect de wrappers indépendants.")

    print("PHASE5_QUALITY_HOTFIX_VERIFY_OK")
    print(
        "Le pipeline et le sectional writer ne sont plus enveloppés ; "
        "seul le vérificateur indépendant est ajusté."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
