# -*- coding: utf-8 -*-
from __future__ import annotations

"""Exécute un test historique : Phase 4.7 seule, sans plan consultant/MOCEM."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _add_root(root: Path) -> None:
    value = str(root.resolve())
    if value not in sys.path:
        sys.path.insert(0, value)


def _normalize(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in values or []:
        for token in re.split(r"[,;\s]+", str(raw or "").strip()):
            token = token.strip().strip('"').strip("'")
            if token and token not in seen:
                seen.add(token)
                out.append(token)
    return out


def run(args: argparse.Namespace) -> Dict[str, Any]:
    root = Path(args.root).resolve()
    _add_root(root)
    from agents.EnnoScholar.state_of_art.common import payload_root  # type: ignore
    from agents.EnnoScholar.state_of_art.phase_4_7_scientific_narrative_service import build_scientific_narrative_payload  # type: ignore
    from agents.EnnoScholar.state_of_art.quality_restored.baseline_snapshot_service import build_baseline_snapshot  # type: ignore
    from agents.EnnoScholar.state_of_art.quality_restored.phase_5_canonical_story_writer_service import run_canonical_story_phase5  # type: ignore

    base = payload_root(args.organisme, args.project, str(args.year))
    source45 = base / "phase_4_5_scientific_reasoning" / "scientific_reasoning_payload.json"
    source46 = base / "phase_4_6_project_rd_argumentation" / "project_rd_argumentation_payload.json"
    cards = base / "article_cards" / "article_cards_payload.json"
    style = base / "phase_3_style_memory" / "style_signature_v11.json"
    if not style.exists():
        style = base / "phase_3_style_memory" / "style_profile_payload.json"
    for required in (source45, source46, cards):
        if not required.exists():
            raise FileNotFoundError(str(required))

    canonical_ids = _normalize(args.canonical_verrou_id or ["678,676,677"])
    excluded = _normalize(args.exclude_citation or ["A1,A2,A16,A27"])
    work = base / "guided_research" / "baseline_without_mocem"
    work.mkdir(parents=True, exist_ok=True)
    baseline45 = work / "scientific_reasoning_payload.baseline.json"
    baseline46 = work / "project_rd_argumentation_payload.baseline.json"
    manifest = work / "active_article_manifest.baseline.json"
    snapshot_report = work / "baseline_snapshot_report.json"
    phase47 = work / "scientific_narrative_payload.baseline.json"
    phase47_md = work / "scientific_narrative_summary.baseline.md"
    phase5_payload = base / "phase_5_state_of_art_writer" / "state_of_art_baseline_without_mocem_payload.json"
    phase5_md = base / "phase_5_state_of_art_writer" / "state_of_art_baseline_without_mocem.md"
    summary_path = work / "run_summary.json"

    print("[1/4] Snapshot historique sans plan consultant.")
    snapshot = build_baseline_snapshot(
        phase45_path=source45,
        phase46_path=source46,
        article_cards_path=cards,
        output_phase45_path=baseline45,
        output_phase46_path=baseline46,
        output_manifest_path=manifest,
        output_report_path=snapshot_report,
        canonical_verrou_ids=canonical_ids,
        excluded_citations=excluded,
    )
    print(json.dumps({
        "canonical_verrous": snapshot.get("canonical_verrou_ids"),
        "excluded_citations": snapshot.get("excluded_citations"),
        "counts": snapshot.get("counts"),
    }, ensure_ascii=False))
    if not snapshot.get("ok"):
        raise RuntimeError("Snapshot bloqué: " + "; ".join((snapshot.get("guard") or {}).get("errors") or []))

    print("[2/4] Phase 4.7 construit seule l'histoire scientifique.")
    story = build_scientific_narrative_payload(
        organisme=args.organisme,
        project=args.project,
        year=str(args.year),
        phase_4_5_path=baseline45,
        phase_4_6_path=baseline46,
        active_article_manifest_path=manifest,
        output_path=phase47,
        markdown_output_path=phase47_md,
        dry_run=False,
    )
    print(json.dumps({
        "verrous_count": story.get("verrous_count"),
        "active_citations": story.get("active_citations"),
        "story_order": (story.get("scientific_story") or {}).get("story_order"),
    }, ensure_ascii=False))
    if not story.get("ok"):
        raise RuntimeError("Phase 4.7 bloquée: " + "; ".join((story.get("guard") or {}).get("errors") or []))

    print("[3/4] Phase 5 rédige directement depuis la Phase 4.7, sans adaptateur consultant.")
    phase5 = run_canonical_story_phase5(
        organisme=args.organisme,
        project=args.project,
        year=str(args.year),
        phase47_payload_path=phase47,
        article_cards_payload_path=cards,
        style_payload_path=style if style.exists() else None,
        output_payload_path=phase5_payload,
        output_markdown_path=phase5_md,
        dry_run=args.dry_run,
    )

    print("[4/4] Validation finale.")
    result = {
        "ok": phase5.get("ok") is True,
        "payload_type": "baseline_without_mocem_pipeline_summary_v1",
        "dry_run": bool(args.dry_run),
        "canonical_verrou_ids": canonical_ids,
        "excluded_citations": excluded,
        "snapshot_counts": snapshot.get("counts"),
        "phase47_guard": story.get("guard"),
        "phase5_guard": phase5.get("guard"),
        "rules": {
            "consultant_plan_used": False,
            "consultant_story_adapter_used": False,
            "artificial_verrou_529_used": False,
            "source_payloads_overwritten": False,
        },
        "paths": {
            "baseline_phase45": str(baseline45),
            "baseline_phase46": str(baseline46),
            "baseline_manifest": str(manifest),
            "phase47": str(phase47),
            "phase47_summary": str(phase47_md),
            "phase5_payload": str(phase5_payload),
            "phase5_markdown": str(phase5_md),
        },
    }
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="C:/EnnoSmart")
    parser.add_argument("--organisme", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--canonical-verrou-id", action="append", default=[])
    parser.add_argument("--exclude-citation", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run(args)
    raise SystemExit(0 if result.get("ok") or args.dry_run else 2)


if __name__ == "__main__":
    main()
