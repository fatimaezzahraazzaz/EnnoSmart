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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


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
            found = find_draft(node.get(key))
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


def extract_evidence_units(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "evidence_units", "normalized_evidence_units"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def collect_citations(obj: Any) -> list[str]:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    labels = re.findall(r"\[\s*A\s*(\d+)\s*\]", text, flags=re.I)
    return sorted(
        {f"A{int(number)}" for number in labels},
        key=lambda label: int(label[1:]),
    )


def find_first_existing(base: Path, names: tuple[str, ...]) -> Path | None:
    direct_dir = base / "phase_5_state_of_art_writer"
    for name in names:
        direct = direct_dir / name
        if direct.exists():
            return direct

    matches = []
    for name in names:
        matches.extend(base.rglob(name))
    matches = [path for path in matches if "_backups" not in str(path).lower()]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def build_real_context(
    root: Path,
    organisme: str,
    project: str,
    year: str,
):
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "backend_api"))

    os.environ["ENNOSMART_PHASE5_ENABLE_POLISH"] = "0"
    os.environ["ENNOSMART_PHASE5_VERIFY_WITH_LLM"] = "0"
    os.environ["ENNOSMART_PHASE5_USE_LLM"] = "0"

    base = (
        root / "storage" / "organismes" / slug(organisme)
        / "projects" / slug(project) / "years" / str(year)
        / "ennoscholar" / "state_of_art_payload"
    )

    snapshot_path = base / "consultant_plan_snapshot.json"
    draft_path = find_first_existing(
        base,
        ("state_of_art_draft_payload.json",),
    )
    blueprint_path = find_first_existing(
        base,
        (
            "unified_writer_blueprint_used.json",
            "scientific_writing_blueprint_used.json",
            "writer_blueprint_used.json",
        ),
    )
    evidence_path = find_first_existing(
        base,
        (
            "normalized_evidence_units.json",
            "normalized_evidence_units_payload.json",
        ),
    )

    missing = []
    for label, path in (
        ("snapshot", snapshot_path if snapshot_path.exists() else None),
        ("draft", draft_path),
        ("blueprint", blueprint_path),
        ("evidence", evidence_path),
    ):
        if path is None:
            missing.append(label)
    if missing:
        raise RuntimeError(
            "Contexte Phase 5 incomplet, fichiers absents: "
            + ", ".join(missing)
        )

    snapshot = read_json(snapshot_path)
    draft_payload = read_json(draft_path)
    blueprint = read_json(blueprint_path)
    evidence_payload = read_json(evidence_path)

    draft = find_draft(draft_payload)
    if not draft:
        raise RuntimeError(f"draft_json introuvable dans {draft_path}")
    if not isinstance(blueprint, dict):
        raise RuntimeError(f"Blueprint invalide: {blueprint_path}")

    evidence_units = extract_evidence_units(evidence_payload)
    blueprint = copy.deepcopy(blueprint)
    blueprint["consultant_plan_contract"] = snapshot

    from agents.EnnoScholar.state_of_art import (
        phase_5_state_of_art_writer_service as writer,
    )

    return {
        "base": base,
        "snapshot_path": snapshot_path,
        "draft_path": draft_path,
        "blueprint_path": blueprint_path,
        "evidence_path": evidence_path,
        "snapshot": snapshot,
        "draft_payload": draft_payload,
        "draft": draft,
        "blueprint": blueprint,
        "evidence_units": evidence_units,
        "writer": writer,
    }


def guard_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(report.get("ok") or report.get("passed")),
        "errors": report.get("errors") or [],
        "unknown_citations": report.get("unknown_citations") or [],
        "detected_citations": (
            report.get("detected_citations")
            or report.get("citations_detected")
            or []
        ),
        "missing_required_citations":
            report.get("missing_required_citations") or [],
        "expected_verrous": report.get("expected_verrous") or [],
        "actual_verrous": report.get("actual_verrous") or [],
        "per_verrou_coverage": report.get("per_verrou_coverage") or [],
        "verrou_coverage_ok": report.get("verrou_coverage_ok"),
    }


import argparse


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    parser.add_argument("--organisme", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--year", required=True)
    args = parser.parse_args()

    ctx = build_real_context(
        Path(args.root).resolve(),
        args.organisme,
        args.project,
        args.year,
    )

    candidate = copy.deepcopy(ctx["draft"])
    report = ctx["writer"].validate_draft(
        candidate,
        copy.deepcopy(ctx["blueprint"]),
        source_draft=ctx["draft"],
        evidence_units=ctx["evidence_units"],
    )
    summary = guard_summary(report)

    expected_titles = [
        str(section.get("title") or "")
        for section in ctx["snapshot"].get("sections") or []
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
        "titles_match": titles_match,
        "snapshot_path": str(ctx["snapshot_path"]),
        "draft_path": str(ctx["draft_path"]),
        "blueprint_path": str(ctx["blueprint_path"]),
        "evidence_path": str(ctx["evidence_path"]),
        "evidence_units_count": len(ctx["evidence_units"]),
        "citations_in_draft": collect_citations(candidate),
        "guard_summary": summary,
        "guard": report,
    }

    output = ctx["base"] / "phase5_real_context_no_llm_report_v1_4_3.json"
    write_json(output, result)

    print("=" * 116)
    print("PHASE 5 — REVALIDATION AVEC LE CONTEXTE RÉEL, SANS LLM")
    print(f"Snapshot       : {ctx['snapshot_path']}")
    print(f"Brouillon      : {ctx['draft_path']}")
    print(f"Blueprint réel : {ctx['blueprint_path']}")
    print(f"Preuves réelles: {ctx['evidence_path']}")
    print(f"Evidence units : {len(ctx['evidence_units'])}")
    print(f"Rapport        : {output}")
    print("LLM calls      : 0")
    print(f"Titres consultant conformes : {titles_match}")
    print(f"Guard ok/passed            : {summary['ok']}")
    print(f"Erreurs                    : {summary['errors']}")
    print(f"Citations inconnues        : {summary['unknown_citations']}")
    print(f"Citations détectées        : {summary['detected_citations']}")
    print(f"Citations requises absentes: {summary['missing_required_citations']}")
    print(f"Verrous attendus           : {summary['expected_verrous']}")
    print(f"Verrous réels              : {summary['actual_verrous']}")
    print(f"Couverture par verrou      : {summary['per_verrou_coverage']}")
    print("=" * 116)

    if not titles_match:
        print("PHASE5_REAL_CONTEXT_NO_LLM_FAILED: consultant_titles")
        return 3
    if not summary["ok"]:
        print("PHASE5_REAL_CONTEXT_NO_LLM_FAILED: guard")
        return 4

    print("PHASE5_REAL_CONTEXT_NO_LLM_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
