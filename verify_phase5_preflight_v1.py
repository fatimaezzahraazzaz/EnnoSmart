# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set


def slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    repl = {
        "à":"a","á":"a","â":"a","ä":"a","ã":"a","ç":"c",
        "é":"e","è":"e","ê":"e","ë":"e","î":"i","ï":"i",
        "ô":"o","ö":"o","ù":"u","û":"u","ü":"u","’":"_",
        "'":"_","-":"_"," ":"_",
    }
    for a, b in repl.items():
        text = text.replace(a, b)
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_") or "unknown"


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def walk(obj: Any) -> Iterable[Any]:
    stack = [obj]
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def citation(value: Any) -> str:
    match = re.search(r"\bA\s*(\d+)\b", str(value or ""), flags=re.I)
    return f"A{match.group(1)}" if match else ""


def collect_selection_citations(payload: Any) -> Set[str]:
    out: Set[str] = set()
    for item in walk(payload):
        if not isinstance(item, dict):
            continue
        status = str(
            item.get("consultant_status")
            or item.get("status")
            or item.get("decision")
            or ""
        ).lower()
        accepted = status in {"garde", "gardé", "garder", "accepted", "accept", "selected"}
        if accepted or not status:
            for key in (
                "citation_label", "citation", "article_ref", "reference_id", "ref",
            ):
                label = citation(item.get(key))
                if label:
                    out.add(label)
    return out


def collect_card_citations(payload: Any) -> Set[str]:
    out: Set[str] = set()
    for item in walk(payload):
        if not isinstance(item, dict):
            continue
        for key in (
            "citation_label", "citation", "article_ref", "reference_id", "ref",
        ):
            label = citation(item.get(key))
            if label:
                out.add(label)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    parser.add_argument("--organisme", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--year", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    base = (
        root / "storage" / "organismes" / slug(args.organisme)
        / "projects" / slug(args.project) / "years" / str(args.year)
        / "ennoscholar" / "state_of_art_payload"
    )
    selection_path = base / "selection_payload.json"
    cards_path = base / "article_cards" / "article_cards_payload.json"
    phase46_path = (
        base / "phase_4_6_project_rd_argumentation"
        / "project_rd_argumentation_payload.json"
    )
    phase47_path = (
        base / "phase_4_7_scientific_narrative"
        / "scientific_narrative_payload.json"
    )

    missing_files = [
        str(path)
        for path in (selection_path, cards_path, phase46_path, phase47_path)
        if not path.exists()
    ]
    if missing_files:
        print("PHASE5_PREFLIGHT_FAILED")
        print("Fichiers absents :")
        for path in missing_files:
            print(f"- {path}")
        return 2

    selection = read_json(selection_path)
    cards = read_json(cards_path)
    selected = collect_selection_citations(selection)
    card_labels = collect_card_citations(cards)
    missing_cards = sorted(selected - card_labels, key=lambda x: int(x[1:]))

    serialized_selection = json.dumps(selection, ensure_ascii=False).lower()
    consultant_origin_present = (
        "consultant_chat" in serialized_selection
        or "ennoscholar_guided_research" in serialized_selection
        or "supplementary_verrou" in serialized_selection
    )

    print("=" * 94)
    print("PHASE5 PREFLIGHT")
    print(f"Base                  : {base}")
    print(f"Citations sélectionnées: {len(selected)} {sorted(selected)}")
    print(f"Article Cards          : {len(card_labels)} {sorted(card_labels)}")
    print(f"Cards manquantes       : {len(missing_cards)} {missing_cards}")
    print(f"Origine consultant vue : {consultant_origin_present}")
    print(f"Phase 4.6 disponible   : {phase46_path.exists()}")
    print(f"Phase 4.7 disponible   : {phase47_path.exists()}")
    print("=" * 94)

    if missing_cards:
        print("PHASE5_PREFLIGHT_BLOCKED")
        print(
            "Des articles sélectionnés n'ont pas d'Article Card. "
            "Relance une fois avec force_article_cards=true avant la rédaction."
        )
        return 3

    print("PHASE5_PREFLIGHT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
