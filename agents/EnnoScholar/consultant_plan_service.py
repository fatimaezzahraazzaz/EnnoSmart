# -*- coding: utf-8 -*-
from __future__ import annotations

"""Service autonome du plan consultant.

Le backend du chat peut appeler ces fonctions sans dépendre du writer :

1. proposer un plan depuis la Phase 4.7 ;
2. enregistrer une version modifiée ;
3. approuver cette version ;
4. enregistrer l'ordre explicite de rédaction.
"""

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .contracts import (
    ContractError,
    build_plan_contract,
    clean_text,
    normalize_plan_sections,
    plan_hash,
)


def read_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        return {}
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, dict) else {}


def write_json(path: str | Path, data: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_markdown_plan(text: str) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        heading = re.match(r"^(#{1,6})\s+(?:\d+(?:\.\d+)*[.)-]?\s*)?(.+?)\s*$", line)
        numbered = re.match(r"^\s*(\d+(?:\.\d+)*)[.)-]\s+(.+?)\s*$", line)
        if heading or numbered:
            title = clean_text(heading.group(2) if heading else numbered.group(2))
            level = len(heading.group(1)) if heading else numbered.group(1).count(".") + 1
            parent_id = next(
                (
                    section["section_id"]
                    for section in reversed(sections)
                    if int(section.get("level") or 1) < level
                ),
                None,
            )
            current = {
                "section_id": f"section_{len(sections) + 1}",
                "title": title,
                "objective": "",
                "verrou_ids": [],
                "parent_id": parent_id,
                "level": level,
                "instructions": [],
                "required_dimensions": [],
                "visual_requirements": [],
                "source_preferences": [],
            }
            sections.append(current)
            continue
        if current and line:
            objective = clean_text(f"{current['objective']} {line}")
            current["objective"] = objective
    return normalize_plan_sections(sections)


def propose_from_phase47(phase47_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    axes = phase47_payload.get("project_specific_story_axes")
    sections: List[Dict[str, Any]] = []
    for index, axis in enumerate(axes or [], 1):
        if not isinstance(axis, dict):
            continue
        title = clean_text(
            axis.get("visible_title")
            or axis.get("title")
            or axis.get("axis_title")
            or axis.get("label")
        )
        if not title:
            continue
        sections.append(
            {
                "section_id": clean_text(axis.get("axis_id") or axis.get("id")) or f"section_{index}",
                "title": title,
                "objective": clean_text(
                    axis.get("goal")
                    or axis.get("objective")
                    or axis.get("narrative_goal")
                    or axis.get("description")
                ),
                "verrou_ids": [],
            }
        )
    return normalize_plan_sections(sections)


def create_contract(
    proposed_plan: Any,
    *,
    edited_plan: Any = None,
    version: int = 1,
) -> Dict[str, Any]:
    return build_plan_contract(
        proposed_plan=proposed_plan,
        consultant_edited_plan=edited_plan,
        approve=False,
        writing_authorized=False,
        plan_version=version,
    )


def update_edited_plan(contract: Dict[str, Any], edited_plan: Any) -> Dict[str, Any]:
    version = int(contract.get("plan_version") or 0) + 1
    return build_plan_contract(
        proposed_plan=contract.get("proposed_plan") or [],
        consultant_edited_plan=edited_plan,
        approve=False,
        writing_authorized=False,
        plan_version=version,
    )


def approve_plan(contract: Dict[str, Any], approved_by: str = "") -> Dict[str, Any]:
    return build_plan_contract(
        proposed_plan=contract.get("proposed_plan") or [],
        consultant_edited_plan=contract.get("consultant_edited_plan") or None,
        approve=True,
        approved_by=approved_by,
        writing_authorized=False,
        plan_version=int(contract.get("plan_version") or 1),
    )


def authorize_writing(contract: Dict[str, Any]) -> Dict[str, Any]:
    approved = normalize_plan_sections(contract.get("approved_plan"))
    if not approved:
        raise ContractError(
            "consultant_plan_not_approved",
            "Le plan doit être approuvé avant l'ordre de rédaction.",
        )
    if clean_text(contract.get("approval_hash")) != plan_hash(approved):
        raise ContractError(
            "consultant_plan_hash_mismatch",
            "Le plan a changé après son approbation.",
        )
    output = dict(contract)
    output["writing_authorized"] = True
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Contrat du plan consultant EnnoScholar")
    sub = parser.add_subparsers(dest="command", required=True)

    propose = sub.add_parser("propose")
    propose.add_argument("--phase47", required=True)
    propose.add_argument("--output", required=True)

    edit = sub.add_parser("edit")
    edit.add_argument("--contract", required=True)
    edit.add_argument("--plan-markdown", required=True)

    approve = sub.add_parser("approve")
    approve.add_argument("--contract", required=True)
    approve.add_argument("--approved-by", default="")

    authorize = sub.add_parser("authorize")
    authorize.add_argument("--contract", required=True)

    args = parser.parse_args()
    if args.command == "propose":
        plan = propose_from_phase47(read_json(args.phase47))
        result = create_contract(plan)
        target = Path(args.output)
    elif args.command == "edit":
        target = Path(args.contract)
        result = update_edited_plan(
            read_json(target),
            parse_markdown_plan(Path(args.plan_markdown).read_text(encoding="utf-8-sig")),
        )
    elif args.command == "approve":
        target = Path(args.contract)
        result = approve_plan(read_json(target), args.approved_by)
    else:
        target = Path(args.contract)
        result = authorize_writing(read_json(target))
    write_json(target, result)
    print(json.dumps({"ok": True, "path": str(target), "plan_version": result.get("plan_version")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
