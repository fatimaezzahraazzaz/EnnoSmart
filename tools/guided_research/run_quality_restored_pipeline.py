# -*- coding: utf-8 -*-
from __future__ import annotations

"""EnnoScholar V1.2 — trois verrous canoniques, méthode consultant rattachée.

Ordre:
1) 20 Article Cards actives;
2) canonicalisation Phase 4.5/4.6, sans écraser les sources;
3) récit canonique Phase 4.7 sur les seuls verrous validés;
4) adaptation du récit au plan consultant;
5) contrat de couverture;
6) rédaction globale;
7) validation finale.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def _add_root(root: Path) -> None:
    value = str(root.resolve())
    if value not in sys.path:
        sys.path.insert(0, value)




def _normalize_cli_values(values: List[str]) -> List[str]:
    """Accepte les formes répétées, CSV, PowerShell et espaces.

    Exemples acceptés :
    - ["678", "676", "677"]
    - ["678,676,677"]
    - ["678 676 677"]
    """
    out: List[str] = []
    seen = set()
    for raw in values or []:
        for token in re.split(r"[,;\s]+", str(raw or "").strip()):
            token = token.strip().strip('"').strip("'")
            if token and token not in seen:
                seen.add(token)
                out.append(token)
    return out


def _normalize_alias_values(values: List[str]) -> List[str]:
    """Normalise SOURCE=CIBLE sans transformer les IDs en faux alias."""
    out: List[str] = []
    seen = set()
    for raw in values or []:
        for token in re.split(r"[,;\s]+", str(raw or "").strip()):
            token = token.strip().strip('"').strip("'")
            if token and token not in seen:
                seen.add(token)
                out.append(token)
    return out


def _payload_verrou_ids(path: Path, collection_key: str) -> List[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    rows = payload.get(collection_key) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    return _normalize_cli_values([str(x.get("verrou_id") or "") for x in rows if isinstance(x, dict)])


def _backup(path: Path) -> str:
    if not path.exists():
        return ""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(path.name + f".backup_quality_restored_{stamp}")
    backup.write_bytes(path.read_bytes())
    return str(backup)


def run(args: argparse.Namespace) -> Dict[str, Any]:
    root = Path(args.root).resolve()
    _add_root(root)
    from agents.EnnoScholar.state_of_art.common import payload_root  # type: ignore
    from agents.EnnoScholar.state_of_art.phase_4_7_scientific_narrative_service import build_scientific_narrative_payload  # type: ignore
    from agents.EnnoScholar.state_of_art.quality_restored.active_article_manifest_service import build_active_article_manifest  # type: ignore
    from agents.EnnoScholar.state_of_art.quality_restored.canonical_verrou_merge_service import canonicalize_verrou_payloads, parse_aliases  # type: ignore
    from agents.EnnoScholar.state_of_art.quality_restored.consultant_story_adapter_service import adapt_canonical_story_to_consultant_plan  # type: ignore
    from agents.EnnoScholar.state_of_art.quality_restored.quality_contract_service import validate_consultant_story_contract  # type: ignore
    from agents.EnnoScholar.state_of_art.quality_restored.phase_5_quality_restored_writer_service import run_quality_restored_phase5  # type: ignore

    base = payload_root(args.organisme, args.project, str(args.year))
    plan_path = Path(args.plan_file)
    plan_text = plan_path.read_text(encoding="utf-8-sig")
    cards_path = base / "article_cards" / "article_cards_payload.json"
    source45 = base / "phase_4_5_scientific_reasoning" / "scientific_reasoning_payload.json"
    source46 = base / "phase_4_6_project_rd_argumentation" / "project_rd_argumentation_payload.json"
    phase47 = base / "phase_4_7_scientific_narrative" / "scientific_narrative_payload.json"
    style_path = base / "phase_3_style_memory" / "style_signature_v11.json"
    if not style_path.exists():
        style_path = base / "phase_3_style_memory" / "style_profile_payload.json"

    work = base / "guided_research" / "quality_restored"
    work.mkdir(parents=True, exist_ok=True)
    manifest_path = work / "active_article_manifest.json"
    canonical45 = work / "scientific_reasoning_payload.canonical.json"
    canonical46 = work / "project_rd_argumentation_payload.canonical.json"
    merge_report_path = work / "canonical_verrou_merge_report.json"
    adapted_path = work / "scientific_narrative_consultant_adapted.json"
    contract_path = work / "quality_contract.json"
    summary_path = work / "run_summary.json"
    out_payload = base / "phase_5_state_of_art_writer" / "state_of_art_draft_payload.json"
    out_md = base / "phase_5_state_of_art_writer" / "state_of_art_draft.md"

    for required in (cards_path, source45, source46, plan_path):
        if not required.exists():
            raise FileNotFoundError(str(required))

    canonical_ids = _normalize_cli_values(args.canonical_verrou_id or [])
    alias_values = _normalize_alias_values(args.verrou_alias or [])
    if not canonical_ids:
        raise RuntimeError("Au moins un --canonical-verrou-id est obligatoire.")
    aliases = parse_aliases(alias_values)

    source45_ids = _payload_verrou_ids(source45, "reasoning_items")
    source46_ids = _payload_verrou_ids(source46, "argumentations")
    print(json.dumps({
        "cli_canonical_verrou_ids": canonical_ids,
        "cli_verrou_aliases": aliases,
        "phase45_verrou_ids": source45_ids,
        "phase46_verrou_ids": source46_ids,
    }, ensure_ascii=False))

    print("[1/7] Contrôle des Article Cards actives.")
    manifest = build_active_article_manifest(
        cards_path, plan_text, manifest_path,
        expected_baseline_count=args.expected_baseline,
        expected_added_count=args.expected_added,
        focus_terms=args.focus_term or None,
        phase45_payload_path=source45,
        delta_verrou_id=args.delta_verrou_id or None,
        added_citations=args.added_citation or None,
        strict_origin_counts=False,
    )
    print(json.dumps(manifest.get("counts"), ensure_ascii=False))
    if not manifest.get("ok"):
        raise RuntimeError("Correction 1 bloquée: " + "; ".join((manifest.get("guard") or {}).get("errors") or []))

    print("[2/7] Fusion des verrous artificiels dans les verrous canoniques.")
    merge = canonicalize_verrou_payloads(
        source45, source46, canonical45, canonical46, merge_report_path,
        canonical_verrou_ids=canonical_ids,
        explicit_aliases=aliases,
    )
    print(json.dumps({"canonical": merge.get("final_verrou_ids"), "merged": merge.get("merged_verrous")}, ensure_ascii=False))
    if not merge.get("ok"):
        raise RuntimeError("Correction 2 bloquée: " + "; ".join((merge.get("guard") or {}).get("errors") or []))

    print("[3/7] Construction du récit scientifique canonique Phase 4.7.")
    canonical = build_scientific_narrative_payload(
        organisme=args.organisme, project=args.project, year=str(args.year),
        phase_4_5_path=canonical45, phase_4_6_path=canonical46,
        output_path=phase47, active_article_manifest_path=manifest_path, dry_run=False,
    )
    if not canonical.get("ok"):
        raise RuntimeError("Correction 3 bloquée: " + "; ".join((canonical.get("guard") or {}).get("errors") or []))

    print("[4/7] Adaptation du récit au plan consultant, sans créer de verrou.")
    adapted = adapt_canonical_story_to_consultant_plan(phase47, manifest_path, plan_text, adapted_path)
    if not adapted.get("ok"):
        raise RuntimeError("Correction 4 bloquée: " + "; ".join((adapted.get("guard") or {}).get("errors") or []))

    print("[5/7] Contrat de couverture des 20 articles et propriétaires canoniques.")
    contract = validate_consultant_story_contract(adapted_path, contract_path)
    if not contract.get("ok"):
        raise RuntimeError("Correction 5 bloquée: " + "; ".join((contract.get("guard") or {}).get("errors") or []))

    print("[6/7] Rédaction globale avec réparations locales seulement.")
    phase5 = run_quality_restored_phase5(
        organisme=args.organisme, project=args.project, year=str(args.year),
        adapted_payload_path=adapted_path, article_cards_payload_path=cards_path,
        style_payload_path=style_path if style_path.exists() else None,
        output_payload_path=out_payload, output_markdown_path=out_md, dry_run=args.dry_run,
    )

    print("[7/7] Validation finale.")
    result = {
        "ok": phase5.get("ok") is True,
        "payload_type": "quality_restored_pipeline_summary_v1_2_canonical_verrous",
        "canonical_verrou_ids": canonical_ids,
        "merged_verrous": merge.get("merged_verrous"),
        "counts": manifest.get("counts"),
        "contract_coverage": contract.get("coverage"),
        "phase5_guard": phase5.get("guard"),
        "paths": {
            "manifest": str(manifest_path), "canonical_phase45": str(canonical45),
            "canonical_phase46": str(canonical46), "merge_report": str(merge_report_path),
            "phase47": str(phase47), "adapted_phase47": str(adapted_path),
            "quality_contract": str(contract_path), "phase5_payload": str(out_payload),
            "phase5_markdown": str(out_md),
        },
        "backups": {"phase47": _backup(phase47), "phase5_payload": _backup(out_payload), "phase5_markdown": _backup(out_md)},
    }
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default="C:/EnnoSmart")
    p.add_argument("--organisme", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--year", required=True)
    p.add_argument("--plan-file", required=True)
    p.add_argument("--expected-baseline", type=int, default=15)
    p.add_argument("--expected-added", type=int, default=5)
    p.add_argument("--focus-term", action="append", default=[])
    p.add_argument("--delta-verrou-id", default="")
    p.add_argument("--added-citation", action="append", default=[])
    p.add_argument("--canonical-verrou-id", action="append", default=[])
    p.add_argument("--verrou-alias", action="append", default=[])
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    result = run(args)
    raise SystemExit(0 if result.get("ok") or args.dry_run else 2)


if __name__ == "__main__":
    main()
