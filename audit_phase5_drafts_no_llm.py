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

    draft_paths = sorted(
        base.rglob("state_of_art_draft_payload.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not draft_paths:
        raise SystemExit("Aucun brouillon Phase 5 existant.")

    expected_titles = [
        str(section.get("title") or "")
        for section in snapshot.get("sections") or []
    ]
    rows = []

    print("=" * 118)
    print("AUDIT DE TOUS LES BROUILLONS PHASE 5 — SANS LLM")
    print(f"Snapshot : {snapshot_path}")
    print(f"Phase 4.7: {phase47_path}")
    print("LLM calls: 0")
    print("=" * 118)

    for index, path in enumerate(draft_paths, 1):
        try:
            payload = read_json(path)
            draft = find_draft(payload)
            if not draft:
                raise RuntimeError("draft_json introuvable")
            candidate = copy.deepcopy(draft)
            report = writer.validate_draft(
                candidate,
                copy.deepcopy(blueprint),
            )
            summary = summarize_guard(report)
            actual_titles = [
                str(section.get("title") or "")
                for section in candidate.get("sections") or []
                if isinstance(section, dict)
            ]
            titles_match = actual_titles == expected_titles
            row = {
                "path": str(path),
                "modified_at": path.stat().st_mtime,
                "titles_match": titles_match,
                "guard_ok": summary["ok"],
                "errors": summary["errors"],
                "citations_in_draft": collect_citations_from_sections(candidate),
                "unknown_citations": summary["unknown_citations"],
                "allowed_citations": summary["allowed_citations"],
                "detected_citations": summary["detected_citations"],
                "expected_verrous": summary["expected_verrous"],
                "actual_verrous": summary["actual_verrous"],
                "per_verrou_coverage": summary["per_verrou_coverage"],
                "verrou_coverage_ok": summary["verrou_coverage_ok"],
                "guard": report,
            }
        except Exception as exc:
            row = {
                "path": str(path),
                "modified_at": path.stat().st_mtime,
                "titles_match": False,
                "guard_ok": False,
                "errors": [f"audit_exception: {exc}"],
            }

        rows.append(row)
        print(f"[{index}] {path}")
        print(f"    titres_match : {row.get('titles_match')}")
        print(f"    guard_ok     : {row.get('guard_ok')}")
        print(f"    errors       : {row.get('errors')}")
        print(f"    unknown      : {row.get('unknown_citations')}")
        print(f"    allowed      : {row.get('allowed_citations')}")
        print(f"    citations    : {row.get('citations_in_draft')}")
        print(f"    exp_verrous  : {row.get('expected_verrous')}")
        print(f"    act_verrous  : {row.get('actual_verrous')}")
        print("-" * 118)

    passing = [
        row for row in rows
        if row.get("titles_match") and row.get("guard_ok")
    ]
    output = base / "phase5_all_drafts_no_llm_audit_v1_4_2.json"
    output.write_text(
        json.dumps(
            {
                "llm_calls": 0,
                "snapshot_path": str(snapshot_path),
                "phase47_path": str(phase47_path),
                "drafts_count": len(rows),
                "passing_drafts_count": len(passing),
                "passing_drafts": [row["path"] for row in passing],
                "drafts": rows,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print("=" * 118)
    print(f"Rapport complet         : {output}")
    print(f"Brouillons analysés     : {len(rows)}")
    print(f"Brouillons conformes    : {len(passing)}")
    print("LLM calls               : 0")
    print("=" * 118)

    if passing:
        print("PHASE5_NO_LLM_AUDIT_HAS_PASSING_DRAFT")
        return 0

    print("PHASE5_NO_LLM_AUDIT_NO_PASSING_DRAFT")
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
