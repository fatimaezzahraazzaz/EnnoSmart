# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


def slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    for source, target in {
        "à":"a","á":"a","â":"a","ä":"a","ã":"a","ç":"c",
        "é":"e","è":"e","ê":"e","ë":"e","î":"i","ï":"i",
        "ô":"o","ö":"o","ù":"u","û":"u","ü":"u","’":"_",
        "'":"_","-":"_"," ":"_",
    }.items():
        text = text.replace(source, target)
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_") or "unknown"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def find_draft(node: Any) -> dict[str, Any] | None:
    if isinstance(node, dict):
        sections = node.get("sections")
        if (
            isinstance(sections, list)
            and sections
            and all(isinstance(item, dict) for item in sections)
        ):
            return node
        for key in (
            "draft_json", "draft", "final_draft", "candidate_draft",
            "state_of_art", "payload",
        ):
            value = node.get(key)
            found = find_draft(value)
            if found:
                return found
        for value in node.values():
            found = find_draft(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_draft(value)
            if found:
                return found
    return None


def resolve_blueprint(module: Any, phase47: dict[str, Any]) -> dict[str, Any]:
    if hasattr(module, "phase47_blueprint"):
        try:
            result = module.phase47_blueprint(phase47)
            if isinstance(result, dict) and result:
                return result
        except Exception:
            pass

    for key in (
        "phase5_consultant_blueprint",
        "scientific_writing_blueprint",
        "phase5_writer_blueprint",
        "writing_blueprint",
        "writer_blueprint",
    ):
        value = phase47.get(key)
        if isinstance(value, dict):
            return value
    return phase47


def collect_citations_from_sections(draft: dict[str, Any]) -> list[str]:
    text = json.dumps(draft.get("sections") or [], ensure_ascii=False)
    labels = re.findall(r"\[\s*A\s*(\d+)\s*\]", text, flags=re.I)
    return sorted({f"A{int(number)}" for number in labels}, key=lambda x: int(x[1:]))


def build_context(
    root: Path,
    organisme: str,
    project: str,
    year: str,
):
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "backend_api"))

    # Le script d'audit ne doit jamais appeler un LLM.
    os.environ["ENNOSMART_PHASE5_ENABLE_POLISH"] = "0"
    os.environ["ENNOSMART_PHASE5_VERIFY_WITH_LLM"] = "0"
    os.environ["ENNOSMART_PHASE5_USE_LLM"] = "0"

    base = (
        root / "storage" / "organismes" / slug(organisme)
        / "projects" / slug(project) / "years" / str(year)
        / "ennoscholar" / "state_of_art_payload"
    )
    snapshot_path = base / "consultant_plan_snapshot.json"
    phase47_path = (
        base / "phase_4_7_scientific_narrative"
        / "scientific_narrative_payload.json"
    )

    if not snapshot_path.exists():
        raise RuntimeError(f"Snapshot consultant absent: {snapshot_path}")
    if not phase47_path.exists():
        raise RuntimeError(f"Phase 4.7 absente: {phase47_path}")

    snapshot = read_json(snapshot_path)
    phase47 = read_json(phase47_path)

    from agents.EnnoScholar.state_of_art import (
        phase_5_state_of_art_writer_service as writer,
    )

    blueprint = resolve_blueprint(writer, phase47)
    blueprint = copy.deepcopy(blueprint)
    blueprint["consultant_plan_contract"] = snapshot
    return base, snapshot_path, phase47_path, snapshot, writer, blueprint


def summarize_guard(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(report.get("ok") or report.get("passed")),
        "errors": report.get("errors") or [],
        "unknown_citations": report.get("unknown_citations") or [],
        "allowed_citations": (
            report.get("allowed_citations")
            or report.get("authorized_citations")
            or []
        ),
        "detected_citations": (
            report.get("detected_citations")
            or report.get("citations_detected")
            or []
        ),
        "missing_required_citations": (
            report.get("missing_required_citations") or []
        ),
        "expected_verrous": report.get("expected_verrous") or [],
        "actual_verrous": report.get("actual_verrous") or [],
        "per_verrou_coverage": report.get("per_verrou_coverage") or [],
        "verrou_coverage_ok": report.get("verrou_coverage_ok"),
        "section_title_lock": (
            report.get("consultant_title_lock")
            or report.get("section_title_lock")
            or {}
        ),
    }


import argparse


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    parser.add_argument("--organisme", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--year", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    (
        base,
        snapshot_path,
        phase47_path,
        snapshot,
        writer,
        blueprint,
    ) = build_context(
        root,
        args.organisme,
        args.project,
        args.year,
    )

    preferred = (
        base / "phase_5_state_of_art_writer"
        / "state_of_art_draft_payload.json"
    )
    draft_paths = [preferred] if preferred.exists() else []
    draft_paths += [
        path
        for path in sorted(
            base.rglob("state_of_art_draft_payload.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if path not in draft_paths
    ]
    if not draft_paths:
        raise SystemExit("Aucun brouillon Phase 5 existant.")

    source_payload = read_json(draft_paths[0])
    draft = find_draft(source_payload)
    if not draft:
        raise SystemExit(f"draft_json introuvable: {draft_paths[0]}")

    candidate = copy.deepcopy(draft)
    report = writer.validate_draft(candidate, copy.deepcopy(blueprint))
    summary = summarize_guard(report)

    expected_titles = [
        str(section.get("title") or "")
        for section in snapshot.get("sections") or []
    ]
    actual_titles = [
        str(section.get("title") or "")
        for section in candidate.get("sections") or []
        if isinstance(section, dict)
    ]
    titles_match = actual_titles == expected_titles

    result = {
        "ok": summary["ok"] and titles_match,
        "llm_calls": 0,
        "snapshot_path": str(snapshot_path),
        "draft_path": str(draft_paths[0]),
        "phase47_path": str(phase47_path),
        "titles_match": titles_match,
        "expected_consultant_titles": expected_titles,
        "actual_titles_after_local_lock": actual_titles,
        "citations_in_draft": collect_citations_from_sections(candidate),
        "guard_summary": summary,
        "guard": report,
    }
    output = base / "phase5_no_llm_guard_report_v1_4_2.json"
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    print("=" * 112)
    print("PHASE 5 — GARDE LOCAL V1.4.2, SANS LLM")
    print(f"Snapshot                 : {snapshot_path}")
    print(f"Brouillon                : {draft_paths[0]}")
    print(f"Rapport                   : {output}")
    print("LLM calls                 : 0")
    print(f"Titres consultant conformes: {titles_match}")
    print(f"Guard ok/passed           : {summary['ok']}")
    print(f"Erreurs                   : {summary['errors']}")
    print(f"Citations du brouillon    : {result['citations_in_draft']}")
    print(f"Citations inconnues       : {summary['unknown_citations']}")
    print(f"Citations autorisées      : {summary['allowed_citations']}")
    print(f"Citations détectées garde : {summary['detected_citations']}")
    print(f"Verrous attendus          : {summary['expected_verrous']}")
    print(f"Verrous réels             : {summary['actual_verrous']}")
    print(f"Couverture par verrou     : {summary['per_verrou_coverage']}")
    print("=" * 112)

    if not titles_match:
        print("PHASE5_NO_LLM_GUARD_TEST_FAILED: consultant_titles")
        return 3
    if not summary["ok"]:
        print("PHASE5_NO_LLM_GUARD_TEST_FAILED: guard")
        return 4

    print("PHASE5_NO_LLM_GUARD_TEST_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
