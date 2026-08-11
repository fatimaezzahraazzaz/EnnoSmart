# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TITLE_KEYS = (
    "title", "axis_title", "section_title", "heading",
    "label", "name",
)
ID_KEYS = ("section_id", "axis_id", "id", "key")

HIGH_PRIORITY_PATH_TOKENS = {
    "current_plan": 420,
    "accepted_plan": 400,
    "validated_plan": 400,
    "consultant_plan": 360,
    "plan_snapshot": 340,
    "consultant_writing_contract": 300,
    "consultant_raw_request": 280,
    "phase5_consultant_blueprint": 230,
    "adapted_axes": 180,
    "visible_section_order": 120,
    "global_axes": 100,
    "sections": 35,
}
PATH_PENALTIES = {
    "expected_verrous": 500,
    "actual_verrous": 500,
    "verrou_sections": 350,
    "per_verrou": 300,
    "article": 220,
    "citation": 220,
    "evidence": 150,
    "references": 220,
    "claims": 130,
    "limits": 100,
}


def slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    for source, target in {
        "à": "a", "á": "a", "â": "a", "ä": "a", "ã": "a", "ç": "c",
        "é": "e", "è": "e", "ê": "e", "ë": "e", "î": "i", "ï": "i",
        "ô": "o", "ö": "o", "ù": "u", "û": "u", "ü": "u", "’": "_",
        "'": "_", "-": "_", " ": "_",
    }.items():
        text = text.replace(source, target)
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_") or "unknown"


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def is_inside_backup(path: Path, root: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except Exception:
        return False
    return any(part.lower() in {"_backups", "backups", "backup"} for part in relative.parts)


def normalize_section_list(items: list[Any]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []

    for index, item in enumerate(items):
        if isinstance(item, str):
            title = clean(item)
            if not title:
                return []
            # Des identifiants techniques seuls ne sont pas des titres consultant.
            if "_" in title and " " not in title:
                return []
            sections.append({
                "section_id": "",
                "title": title,
                "order": index + 1,
            })
            continue

        if not isinstance(item, dict):
            return []

        title = ""
        for key in TITLE_KEYS:
            title = clean(item.get(key))
            if title:
                break
        if not title:
            return []

        section_id = ""
        for key in ID_KEYS:
            section_id = clean(item.get(key))
            if section_id:
                break

        sections.append({
            "section_id": section_id,
            "title": title,
            "objective": clean(
                item.get("objective")
                or item.get("goal")
                or item.get("description")
            ),
            "parent_id": clean(item.get("parent_id")),
            "level": item.get("level"),
            "section_mode": clean(item.get("section_mode")),
            "order": int(item.get("order") or index + 1),
        })

    titles = [section["title"] for section in sections]
    if len(set(titles)) != len(titles):
        return []
    return sections


def parse_numbered_plan(raw: str) -> list[dict[str, Any]]:
    """
    Extrait les grands titres numérotés du message consultant.
    Les sous-puces et phrases de contrainte sont ignorées.
    """
    found: list[tuple[int, str]] = []
    for line in str(raw or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stripped = re.sub(r"^#{1,6}\s*", "", stripped)

        match = re.match(
            r"^(?P<number>\d{1,2})(?:[.)]|\s*[-:])?\s+"
            r"(?P<title>[A-Za-zÀ-ÿ].{3,})$",
            stripped,
        )
        if not match:
            continue

        number = int(match.group("number"))
        title = clean(match.group("title"))
        title = re.sub(r"[\s:;,-]+$", "", title).strip()
        if not title:
            continue
        found.append((number, title))

    # Garder une séquence top-level cohérente 1,2,3...
    by_number: dict[int, str] = {}
    for number, title in found:
        by_number.setdefault(number, title)

    ordered: list[dict[str, Any]] = []
    expected = 1
    while expected in by_number:
        ordered.append({
            "section_id": "",
            "title": by_number[expected],
            "order": expected,
        })
        expected += 1
    return ordered if len(ordered) >= 2 else []


def walk_candidates(
    node: Any,
    *,
    source: Path,
    json_path: str = "$",
    source_kind: str,
    out: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if out is None:
        out = []

    if isinstance(node, list) and 2 <= len(node) <= 30:
        sections = normalize_section_list(node)
        if sections and len(sections) == len(node):
            out.append({
                "source": source,
                "source_kind": source_kind,
                "json_path": json_path,
                "sections": sections,
                "origin": "structured_list",
            })

    if isinstance(node, dict):
        for key, value in node.items():
            child_path = f"{json_path}.{key}"
            if key == "consultant_raw_request" and isinstance(value, str):
                sections = parse_numbered_plan(value)
                if sections:
                    out.append({
                        "source": source,
                        "source_kind": source_kind,
                        "json_path": child_path,
                        "sections": sections,
                        "origin": "consultant_raw_request",
                    })
            walk_candidates(
                value,
                source=source,
                json_path=child_path,
                source_kind=source_kind,
                out=out,
            )
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, (dict, list)):
                walk_candidates(
                    value,
                    source=source,
                    json_path=f"{json_path}[{index}]",
                    source_kind=source_kind,
                    out=out,
                )
    return out


def iter_existing_path_strings(node: Any) -> Iterable[str]:
    if isinstance(node, dict):
        for value in node.values():
            yield from iter_existing_path_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_existing_path_strings(value)
    elif isinstance(node, str):
        lowered = node.lower()
        if (
            (":\\" in node or node.startswith("/"))
            and lowered.endswith(".json")
        ):
            yield node


def find_latest_draft(base: Path) -> tuple[Path | None, list[dict[str, Any]]]:
    preferred = (
        base / "phase_5_state_of_art_writer"
        / "global" / "state_of_art_draft_payload.json"
    )
    paths = [preferred] if preferred.exists() else []
    paths += sorted(
        (
            path for path in base.rglob("state_of_art_draft_payload.json")
            if path not in paths
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    for path in paths:
        try:
            payload = read_json(path)
        except Exception:
            continue
        stack = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                sections = node.get("sections")
                if isinstance(sections, list):
                    normalized = normalize_section_list(sections)
                    if normalized and len(normalized) == len(sections):
                        return path, normalized
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return None, []


def candidate_score(
    candidate: dict[str, Any],
    actual_sections: list[dict[str, Any]],
    current_phase47: Path,
) -> float:
    path_text = (
        candidate["json_path"] + " " + candidate["source"].name
    ).lower()
    sections = candidate["sections"]
    score = 0.0

    for token, value in HIGH_PRIORITY_PATH_TOKENS.items():
        if token in path_text:
            score += value
    for token, value in PATH_PENALTIES.items():
        if token in path_text:
            score -= value

    if candidate["source_kind"] == "current_phase47":
        score += 260
    elif candidate["source_kind"] == "phase47_referenced":
        score += 230
    elif candidate["source_kind"] == "current_project_scan":
        score += 80

    if candidate["origin"] == "consultant_raw_request":
        score += 220

    if candidate["source"].resolve() == current_phase47.resolve():
        score += 150

    if actual_sections:
        if len(sections) == len(actual_sections):
            score += 220
        else:
            score -= 40 * abs(len(sections) - len(actual_sections))

        actual_ids = {
            item.get("section_id")
            for item in actual_sections
            if item.get("section_id")
        }
        section_ids = {
            item.get("section_id")
            for item in sections
            if item.get("section_id")
        }
        score += 25 * len(actual_ids & section_ids)

    if all(section.get("section_id") for section in sections):
        score += 55
    if 4 <= len(sections) <= 12:
        score += 45

    # Titres humains, non identifiants techniques.
    human_titles = sum(
        1 for section in sections
        if " " in section["title"] and len(section["title"]) >= 8
    )
    score += 8 * human_titles
    return score


def attach_ids_from_actual(
    sections: list[dict[str, Any]],
    actual_sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(sections) != len(actual_sections):
        return sections

    result = []
    for index, section in enumerate(sections):
        updated = dict(section)
        if not updated.get("section_id"):
            updated["section_id"] = clean(
                actual_sections[index].get("section_id")
            )
        result.append(updated)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    parser.add_argument("--organisme", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--year", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    project_root = (
        root / "storage" / "organismes" / slug(args.organisme)
        / "projects" / slug(args.project) / "years" / str(args.year)
    )
    base = project_root / "ennoscholar" / "state_of_art_payload"
    phase47_path = (
        base / "phase_4_7_scientific_narrative"
        / "scientific_narrative_payload.json"
    )

    if not phase47_path.exists():
        raise SystemExit(f"Phase 4.7 introuvable: {phase47_path}")

    phase47 = read_json(phase47_path)
    draft_path, actual_sections = find_latest_draft(base)

    sources: list[tuple[Path, str]] = [(phase47_path, "current_phase47")]
    seen = {str(phase47_path.resolve()).lower()}

    # Suivre d'abord les chemins explicitement référencés par la Phase 4.7 courante.
    for raw_path in iter_existing_path_strings(phase47):
        path = Path(raw_path)
        if not path.exists() or path.suffix.lower() != ".json":
            continue
        key = str(path.resolve()).lower()
        if key in seen or is_inside_backup(path, root):
            continue
        seen.add(key)
        sources.append((path, "phase47_referenced"))

    # Puis explorer le projet courant, sans dépendre d'un dossier fixe.
    wanted_names = {
        "consultant_writing_contract.json",
        "scientific_narrative_consultant_adapted.json",
        "guided_research_sources.json",
        "consultant_plan.json",
        "plan_snapshot.json",
    }
    for path in project_root.rglob("*.json"):
        if path.name.lower() not in wanted_names:
            continue
        key = str(path.resolve()).lower()
        if key in seen or is_inside_backup(path, root):
            continue
        seen.add(key)
        sources.append((path, "current_project_scan"))

    candidates: list[dict[str, Any]] = []
    readable_sources = []
    for source, source_kind in sources:
        try:
            payload = read_json(source)
        except Exception:
            continue
        readable_sources.append((source, source_kind))
        candidates.extend(
            walk_candidates(
                payload,
                source=source,
                source_kind=source_kind,
            )
        )

    if not candidates:
        print("PLAN_SOURCE_DIAGNOSTIC")
        print(f"Project root : {project_root}")
        print(f"Phase 4.7    : {phase47_path}")
        print("Sources JSON lisibles :")
        for source, source_kind in readable_sources:
            print(f"- [{source_kind}] {source}")
        raise SystemExit(
            "Aucun plan structuré ou plan numéroté consultant détecté. "
            "Aucun snapshot n'a été créé."
        )

    for candidate in candidates:
        candidate["score"] = candidate_score(
            candidate,
            actual_sections,
            phase47_path,
        )

    candidates.sort(
        key=lambda item: (
            item["score"],
            item["source"].stat().st_mtime,
        ),
        reverse=True,
    )

    best = candidates[0]
    best_sections = attach_ids_from_actual(
        best["sections"],
        actual_sections,
    )

    print("=" * 118)
    print("CANDIDATS DU PLAN CONSULTANT — LOCAL, SANS LLM")
    for index, candidate in enumerate(candidates[:5], 1):
        print(
            f"[{index}] score={candidate['score']:.1f} "
            f"origine={candidate['origin']} "
            f"source_kind={candidate['source_kind']}"
        )
        print(f"    fichier={candidate['source']}")
        print(f"    chemin ={candidate['json_path']}")
        for section in candidate["sections"]:
            print(
                f"    {section['order']:>2}. "
                f"{section.get('section_id') or '(id repris du draft)'} | "
                f"{section['title']}"
            )
    print("=" * 118)

    second_score = candidates[1]["score"] if len(candidates) > 1 else -9999.0
    if best["score"] < 250:
        raise SystemExit(
            f"PLAN_CANDIDATE_LOW_CONFIDENCE: score={best['score']:.1f}. "
            "Aucun snapshot n'a été créé."
        )
    if (
        len(candidates) > 1
        and second_score >= best["score"] - 10
        and candidates[1]["sections"] != best["sections"]
    ):
        raise SystemExit(
            "PLAN_CANDIDATE_AMBIGUOUS: deux plans concurrents ont des "
            "scores trop proches. Aucun snapshot n'a été créé."
        )

    canonical = json.dumps(
        best_sections,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    snapshot = {
        "payload_type": "consultant_plan_snapshot_v1_4_1",
        "validated_by_consultant": True,
        "validation_basis": (
            "current_phase47_or_its_referenced_consultant_contract_"
            "selected_by_deterministic_local_locator"
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "organisme": args.organisme,
        "project": args.project,
        "year": str(args.year),
        "source_path": str(best["source"]),
        "source_json_path": best["json_path"],
        "source_kind": best["source_kind"],
        "candidate_score": best["score"],
        "current_phase47_path": str(phase47_path),
        "last_paid_draft_path": str(draft_path) if draft_path else None,
        "sections": best_sections,
        "snapshot_sha256": digest,
    }

    output = base / "consultant_plan_snapshot.json"
    output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("CONSULTANT_PLAN_SNAPSHOT_CREATED")
    print(f"Snapshot : {output}")
    print(f"Source   : {best['source']}")
    print(f"JSON path: {best['json_path']}")
    print(f"SHA-256  : {digest}")
    print(f"Sections : {len(best_sections)}")
    print("LLM calls: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
