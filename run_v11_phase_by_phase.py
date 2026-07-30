# -*- coding: utf-8 -*-
from __future__ import annotations

"""Lance et inspecte les phases de rédaction EnnoScholar V11 séparément."""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List


PHASE_ORDER = ["3", "3b", "4", "45", "46", "47", "5"]


def slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    replacements = str.maketrans(
        "àáâäãçéèêëîïìíôöòóùûüúÿñ’'- ",
        "aaaaaceeeeiiiioooouuuuyy____",
    )
    text = text.translate(replacements)
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_") or "unknown"


def read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def build_paths(root: Path, organisme: str, project: str, year: str) -> Dict[str, Path]:
    base = (
        root
        / "storage"
        / "organismes"
        / slug(organisme)
        / "projects"
        / slug(project)
        / "years"
        / str(year)
        / "ennoscholar"
        / "state_of_art_payload"
    )
    return {
        "base": base,
        "selection": base / "selection_payload.json",
        "cards": base / "article_cards" / "article_cards_payload.json",
        "fewshot": base / "phase_3_style_memory" / "fewshot_payload.json",
        "style_profile": base / "phase_3_style_memory" / "style_profile_payload.json",
        "argumentation_profile": base / "phase_3_style_memory" / "argumentation_profile_payload.json",
        "style_signature": base / "phase_3_style_memory" / "style_signature_v11.json",
        "gap": base / "phase_4_scientific_gap" / "gap_scientific_payload.json",
        "phase45": base / "phase_4_5_scientific_reasoning" / "scientific_reasoning_payload.json",
        "phase46": base / "phase_4_6_project_rd_argumentation" / "project_rd_argumentation_payload.json",
        "phase46_md": base / "phase_4_6_project_rd_argumentation" / "project_rd_argumentation_summary.md",
        "phase47": base / "phase_4_7_scientific_narrative" / "scientific_narrative_payload.json",
        "phase47_md": base / "phase_4_7_scientific_narrative" / "scientific_narrative_summary.md",
        "phase5": base / "phase_5_state_of_art_writer" / "state_of_art_draft_payload.json",
        "phase5_md": base / "phase_5_state_of_art_writer" / "state_of_art_draft.md",
        "evidence_map": base / "phase_5_state_of_art_writer" / "sentence_evidence_map.json",
        "quality_report": base / "phase_5_state_of_art_writer" / "quality_report_v11.json",
        "test_report": base / "v11_phase_by_phase_test_report.json",
    }


def require_inputs(paths: Dict[str, Path], names: List[str]) -> None:
    missing = [f"{name}: {paths[name]}" for name in names if not paths[name].exists()]
    if missing:
        raise RuntimeError("Entrées manquantes:\n- " + "\n- ".join(missing))


def summarize_payload(phase: str, result: Dict[str, Any], output_path: Path) -> Dict[str, Any]:
    guard = result.get("guard") if isinstance(result.get("guard"), dict) else {}
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    reasoning_items = result.get("reasoning_items") if isinstance(result.get("reasoning_items"), list) else []
    argumentations = result.get("argumentations") if isinstance(result.get("argumentations"), list) else []
    sections = result.get("sections") if isinstance(result.get("sections"), list) else []
    return {
        "phase": phase,
        "ok": bool(result.get("ok")),
        "payload_type": result.get("payload_type"),
        "output_path": str(output_path),
        "output_exists": output_path.exists(),
        "output_size": output_path.stat().st_size if output_path.exists() else 0,
        "guard_passed": guard.get("passed", guard.get("ok")),
        "guard_errors": guard.get("errors") or guard.get("warnings") or [],
        "verrous_count": result.get("verrous_count") or len(reasoning_items) or len(argumentations) or len(sections),
        "accepted_claims_count": guard.get("accepted_claims_count"),
        "rejected_claims_count": guard.get("rejected_claims_count"),
        "strict_mapping_errors": result.get("strict_mapping_errors") or [],
        "unsupported_numeric_values": guard.get("unsupported_numeric_values") or [],
        "consultant_quality_ready": quality.get("consultant_quality_ready"),
        "writer_mode": quality.get("writer_mode"),
        "markdown_output_path": result.get("markdown_output_path"),
    }


def run_phase_3(args: argparse.Namespace, paths: Dict[str, Path]) -> Dict[str, Any]:
    require_inputs(paths, ["selection"])
    from modules.CIR_STYLE_MEMORY.cir_style_fewshot.phase_3_style_fewshot_service import (
        build_phase_3_style_memory,
    )

    return build_phase_3_style_memory(
        organisme=args.organisme,
        project=args.project,
        year=args.year,
        phase1_payload=read_json(paths["selection"]),
        phase1_payload_path=paths["selection"],
        force=True,
    )


def run_phase_3b(args: argparse.Namespace, paths: Dict[str, Path]) -> Dict[str, Any]:
    require_inputs(paths, ["fewshot"])
    from agents.EnnoScholar.state_of_art.phase_3_style_signature_service import (
        run_phase_3_style_signature,
    )

    return run_phase_3_style_signature(
        fewshot_payload_path=paths["fewshot"],
        output_path=paths["style_signature"],
    )


def run_phase_4(args: argparse.Namespace, paths: Dict[str, Path]) -> Dict[str, Any]:
    require_inputs(paths, ["selection", "cards", "fewshot"])
    from agents.EnnoScholar.state_of_art.phase_4_scientific_gap_service import (
        build_scientific_gap_payload,
    )

    return build_scientific_gap_payload(
        organisme=args.organisme,
        project=args.project,
        year=args.year,
        selection_payload_path=paths["selection"],
        article_cards_payload_path=paths["cards"],
        fewshot_payload_path=paths["fewshot"],
        output_path=paths["gap"],
    )


def run_phase_45(args: argparse.Namespace, paths: Dict[str, Path]) -> Dict[str, Any]:
    require_inputs(paths, ["gap", "cards", "argumentation_profile"])
    from agents.EnnoScholar.state_of_art.phase_4_5_scientific_reasoning_builder_service import (
        run_phase_4_5_scientific_reasoning,
    )

    return run_phase_4_5_scientific_reasoning(
        organisme=args.organisme,
        project=args.project,
        year=args.year,
        gap_payload_path=paths["gap"],
        article_cards_payload_path=paths["cards"],
        argumentation_payload_path=paths["argumentation_profile"],
        output_path=paths["phase45"],
    )


def run_phase_46(args: argparse.Namespace, paths: Dict[str, Path]) -> Dict[str, Any]:
    require_inputs(paths, ["selection", "cards", "fewshot", "gap", "phase45"])
    from agents.EnnoScholar.state_of_art.phase_4_6_project_rd_argumentation_service import (
        run_phase_4_6_project_rd_argumentation,
    )

    return run_phase_4_6_project_rd_argumentation(
        organisme=args.organisme,
        project=args.project,
        year=args.year,
        selection_payload_path=paths["selection"],
        article_cards_payload_path=paths["cards"],
        fewshot_payload_path=paths["fewshot"],
        scientific_gap_payload_path=paths["gap"],
        scientific_reasoning_payload_path=paths["phase45"],
        output_path=paths["phase46"],
        markdown_output_path=paths["phase46_md"],
        use_llm=False,
        dry_run=False,
    )


def run_phase_47(args: argparse.Namespace, paths: Dict[str, Path]) -> Dict[str, Any]:
    require_inputs(paths, ["phase45", "phase46"])
    from agents.EnnoScholar.state_of_art.phase_4_7_scientific_narrative_builder import (
        build_scientific_narrative_payload,
    )

    return build_scientific_narrative_payload(
        organisme=args.organisme,
        project=args.project,
        year=args.year,
        phase_4_5_path=paths["phase45"],
        phase_4_6_path=paths["phase46"],
        output_path=paths["phase47"],
        markdown_output_path=paths["phase47_md"],
        dry_run=False,
    )


def run_phase_5(args: argparse.Namespace, paths: Dict[str, Path]) -> Dict[str, Any]:
    require_inputs(paths, ["selection", "cards", "fewshot", "style_signature", "argumentation_profile", "phase45", "phase46", "phase47"])
    from agents.EnnoScholar.state_of_art.phase_5_state_of_art_writer_service import (
        run_phase_5_state_of_art_writer,
    )

    return run_phase_5_state_of_art_writer(
        organisme=args.organisme,
        project=args.project,
        year=args.year,
        selection_payload_path=paths["selection"],
        article_cards_payload_path=paths["cards"],
        fewshot_payload_path=paths["fewshot"],
        style_profile_payload_path=paths["style_signature"],
        argumentation_profile_payload_path=paths["argumentation_profile"],
        scientific_reasoning_payload_path=paths["phase45"],
        phase46_project_argumentation_payload_path=paths["phase46"],
        phase47_scientific_narrative_payload_path=paths["phase47"],
        output_path=paths["phase5"],
        markdown_output_path=paths["phase5_md"],
        dry_run=False,
        use_llm=not args.no_llm,
        verify_with_llm=not args.no_llm,
    )


RUNNERS: Dict[str, Callable[[argparse.Namespace, Dict[str, Path]], Dict[str, Any]]] = {
    "3": run_phase_3,
    "3b": run_phase_3b,
    "4": run_phase_4,
    "45": run_phase_45,
    "46": run_phase_46,
    "47": run_phase_47,
    "5": run_phase_5,
}


OUTPUT_KEYS = {
    "3": "fewshot",
    "3b": "style_signature",
    "4": "gap",
    "45": "phase45",
    "46": "phase46",
    "47": "phase47",
    "5": "phase5",
}


def inspect_existing(paths: Dict[str, Path]) -> Dict[str, Any]:
    inspection = {}
    for phase in PHASE_ORDER:
        output = paths[OUTPUT_KEYS[phase]]
        payload = read_json(output)
        inspection[phase] = summarize_payload(phase, payload, output) if payload else {
            "phase": phase,
            "ok": False,
            "output_path": str(output),
            "output_exists": output.exists(),
            "error": "payload_absent_or_invalid",
        }
    return inspection


def main() -> int:
    parser = argparse.ArgumentParser(description="Test EnnoScholar V11 phase par phase")
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    parser.add_argument("--organisme", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--phase", choices=PHASE_ORDER + ["all", "inspect"], required=True)
    parser.add_argument("--no-llm", action="store_true", help="Teste la phase 5 sans modèle; le niveau consultant restera faux.")
    parser.add_argument("--keep-going", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    os.environ["ENNOSMART_ROOT"] = str(root)
    os.environ["ENNOSMART_ROOT_DIR"] = str(root)
    os.environ["ENNOSMART_STORAGE_ROOT"] = str(root / "storage")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    paths = build_paths(root, args.organisme, args.project, args.year)
    print(f"Base payload : {paths['base']}")

    if args.phase == "inspect":
        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "mode": "inspect",
            "phases": inspect_existing(paths),
        }
        write_json(paths["test_report"], report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"Rapport : {paths['test_report']}")
        return 0

    phases = PHASE_ORDER if args.phase == "all" else [args.phase]
    reports = []
    exit_code = 0
    for phase in phases:
        print("=" * 80)
        print(f"TEST PHASE {phase} START")
        try:
            result = RUNNERS[phase](args, paths)
            summary = summarize_payload(phase, result, paths[OUTPUT_KEYS[phase]])
            reports.append(summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            if not summary["ok"] or summary.get("guard_passed") is False:
                exit_code = 1
                if not args.keep_going:
                    break
        except Exception as exc:
            exit_code = 1
            failure = {"phase": phase, "ok": False, "error": str(exc)}
            reports.append(failure)
            print(json.dumps(failure, ensure_ascii=False, indent=2))
            if not args.keep_going:
                break

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "organisme": args.organisme,
        "project": args.project,
        "year": args.year,
        "requested_phase": args.phase,
        "no_llm": args.no_llm,
        "phases": reports,
        "final_ok": exit_code == 0,
    }
    write_json(paths["test_report"], report)
    print("=" * 80)
    print(f"Rapport complet : {paths['test_report']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

