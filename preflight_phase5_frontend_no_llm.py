# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    parser.add_argument("--organisme", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "backend_api"))

    os.environ["ENNOSMART_PHASE5_REUSE_VALIDATED_DRAFT"] = "1"
    os.environ["ENNOSMART_PHASE5_ALLOW_PAID_REGEN"] = "0"
    os.environ["ENNOSMART_PHASE5_V11_ENABLE_LLM"] = "0"
    os.environ["ENNOSMART_PHASE5_V11_VERIFY_WITH_LLM"] = "0"
    os.environ["ENNOSMART_PHASE5_USE_LLM"] = "0"

    try:
        from agents.EnnoScholar.state_of_art import (
            phase_5_state_of_art_writer_service as writer,
        )

        phase47_path = writer.default_phase47_payload_path(
            args.organisme,
            args.project,
            args.year,
        )
        contract = writer._v15_attach_contract(
            args.organisme,
            args.project,
            args.year,
            phase47_path,
        )
        result = writer._v15_try_reuse(
            args.organisme,
            args.project,
            args.year,
            contract,
            promote=args.promote,
        )

        print("=" * 108)
        print("PHASE 5 — STABILITY PREFLIGHT, SANS LLM")
        print(f"Snapshot sections : {len(contract.get('sections') or [])}")
        print(f"Réutilisable      : {result.get('reusable')}")
        print(f"Raison            : {result.get('reason')}")
        print(f"Sections prêtes   : {result.get('sections_ready')}")
        print(f"Evidence units    : {result.get('evidence_units_count')}")
        print(f"Promu au frontend : {bool(args.promote and result.get('reusable'))}")
        print(f"Erreurs garde     : {result.get('errors')}")
        print("LLM calls         : 0")
        print("=" * 108)

        if not result.get("reusable"):
            print(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            print("PHASE5_STABILITY_PREFLIGHT_FAILED")
            return 3

        print("PHASE5_STABILITY_PREFLIGHT_OK")
        return 0

    except Exception as exc:
        print(
            f"PHASE5_STABILITY_PREFLIGHT_EXCEPTION: "
            f"{type(exc).__name__}: {exc}"
        )
        print(traceback.format_exc())
        print("LLM calls: 0")
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
