# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


SELECTED_WORDS = {
    "garde", "gardé", "gardee", "gardée", "garder",
    "accept", "accepted", "accepte", "accepté",
    "selected", "selectionne", "sélectionné",
    "keep", "kept", "valid", "validated", "valide", "validé",
}
REJECTED_WORDS = {
    "rejete", "rejeté", "rejected", "reject",
    "hors_sujet", "hors sujet", "discarded",
}
SELECTED_CONTAINER_HINTS = {
    "selected_articles", "articles_selected", "final_articles",
    "strict_selection", "accepted_articles", "kept_articles",
    "articles_gardes", "articles_gardés", "selection_consultant",
    "selected", "accepted", "kept", "gardes", "gardés",
}
STATUS_KEYS = {
    "consultant_status", "consultant_decision", "selection_status",
    "status", "decision", "human_status", "review_status",
}
BOOLEAN_KEYS = {
    "selected", "is_selected", "accepted", "is_accepted",
    "kept", "is_kept", "garde", "is_kept_by_consultant",
}
ARTICLE_ID_KEYS = (
    "article_id", "source_article_id", "scholar_article_id",
    "reference_id", "external_id", "id",
)
CITATION_KEYS = (
    "citation_label", "citation", "article_ref", "reference",
    "ref", "source_ref",
)
TITLE_KEYS = ("title", "article_title", "source_title", "name")
DOI_KEYS = ("doi", "DOI")


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


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_title(value: Any) -> str:
    text = normalize_text(value)
    text = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüç ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_doi(value: Any) -> str:
    text = normalize_text(value)
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    return text.strip()


def normalize_citation(value: Any) -> str:
    match = re.search(r"\bA\s*(\d+)\b", str(value or ""), flags=re.I)
    return f"A{match.group(1)}" if match else ""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def looks_like_article(item: Dict[str, Any]) -> bool:
    keys = {str(key).lower() for key in item}
    has_identity = bool(
        keys.intersection({k.lower() for k in ARTICLE_ID_KEYS + CITATION_KEYS + TITLE_KEYS + DOI_KEYS})
    )
    has_article_fields = bool(
        keys.intersection({
            "abstract", "authors", "year", "venue", "journal",
            "consultant_status", "consultant_decision", "article_id",
            "citation_label", "article_title",
        })
    )
    return has_identity and has_article_fields


def status_selected(item: Dict[str, Any]) -> Optional[bool]:
    for key in STATUS_KEYS:
        if key not in item:
            continue
        value = normalize_text(item.get(key))
        if value in SELECTED_WORDS:
            return True
        if value in REJECTED_WORDS:
            return False

    for key in BOOLEAN_KEYS:
        if key in item and isinstance(item.get(key), bool):
            return bool(item.get(key))
    return None


def identities(item: Dict[str, Any]) -> Set[str]:
    result: Set[str] = set()

    for key in CITATION_KEYS:
        label = normalize_citation(item.get(key))
        if label:
            result.add(f"citation:{label}")

    for key in DOI_KEYS:
        doi = normalize_doi(item.get(key))
        if doi:
            result.add(f"doi:{doi}")

    for key in ARTICLE_ID_KEYS:
        value = item.get(key)
        if value not in (None, ""):
            text = normalize_text(value)
            # Ignore large nested objects serialized as strings.
            if text and len(text) <= 160:
                result.add(f"id:{text}")

    for key in TITLE_KEYS:
        title = normalize_title(item.get(key))
        if len(title) >= 12:
            result.add(f"title:{title}")

    return result


def display_name(item: Dict[str, Any]) -> str:
    for key in CITATION_KEYS:
        label = normalize_citation(item.get(key))
        if label:
            return label
    for key in TITLE_KEYS:
        title = str(item.get(key) or "").strip()
        if title:
            return title[:100]
    for key in ARTICLE_ID_KEYS:
        value = item.get(key)
        if value not in (None, ""):
            return f"id={value}"
    return "article_sans_nom"


def collect_selected(
    node: Any,
    *,
    path: str = "$",
    inherited_selected: bool = False,
    out: Optional[List[Tuple[Dict[str, Any], str]]] = None,
) -> List[Tuple[Dict[str, Any], str]]:
    if out is None:
        out = []

    if isinstance(node, dict):
        if looks_like_article(node):
            decision = status_selected(node)
            if decision is True or (decision is None and inherited_selected):
                out.append((node, path))

        for key, value in node.items():
            key_norm = normalize_text(key).replace(" ", "_")
            child_selected = inherited_selected or key_norm in SELECTED_CONTAINER_HINTS
            collect_selected(
                value,
                path=f"{path}.{key}",
                inherited_selected=child_selected,
                out=out,
            )

    elif isinstance(node, list):
        for index, value in enumerate(node):
            collect_selected(
                value,
                path=f"{path}[{index}]",
                inherited_selected=inherited_selected,
                out=out,
            )
    return out


def collect_articles(node: Any, out: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    if out is None:
        out = []
    if isinstance(node, dict):
        if looks_like_article(node):
            out.append(node)
        for value in node.values():
            collect_articles(value, out)
    elif isinstance(node, list):
        for value in node:
            collect_articles(value, out)
    return out


def dedupe_records(
    records: List[Tuple[Dict[str, Any], str]]
) -> List[Tuple[Dict[str, Any], str, Set[str]]]:
    deduped: List[Tuple[Dict[str, Any], str, Set[str]]] = []
    seen: Set[str] = set()
    for item, source in records:
        ids = identities(item)
        key = sorted(ids)[0] if ids else f"fallback:{display_name(item)}:{source}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append((item, source, ids))
    return deduped


def candidate_selection_files(base: Path) -> List[Path]:
    result = []
    for path in base.rglob("*.json"):
        rel = str(path.relative_to(base)).lower()
        if any(token in rel for token in (
            "selection", "consultant", "article", "phase_1",
            "guided", "research", "scholar",
        )):
            result.append(path)
    return sorted(set(result))


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
    cards_path = base / "article_cards" / "article_cards_payload.json"
    phase46_path = (
        base / "phase_4_6_project_rd_argumentation"
        / "project_rd_argumentation_payload.json"
    )
    phase47_path = (
        base / "phase_4_7_scientific_narrative"
        / "scientific_narrative_payload.json"
    )

    if not base.exists():
        print(f"PHASE5_PREFLIGHT_FAILED\nBase introuvable: {base}")
        return 2
    if not cards_path.exists():
        print(f"PHASE5_PREFLIGHT_FAILED\nArticle Cards introuvables: {cards_path}")
        return 2

    selected_records: List[Tuple[Dict[str, Any], str]] = []
    inspected_files: List[str] = []

    for path in candidate_selection_files(base):
        payload = read_json(path)
        if payload is None:
            continue
        rel = str(path.relative_to(base))
        found = collect_selected(payload)
        if found:
            inspected_files.append(rel)
            selected_records.extend(
                (item, f"{rel}:{json_path}") for item, json_path in found
            )

    selected = dedupe_records(selected_records)
    cards_payload = read_json(cards_path)
    card_items = collect_articles(cards_payload)
    card_identity_sets = [identities(item) for item in card_items]
    all_card_ids: Set[str] = set().union(*card_identity_sets) if card_identity_sets else set()

    missing: List[str] = []
    matched = 0
    for item, source, ids in selected:
        if ids and ids.intersection(all_card_ids):
            matched += 1
        else:
            missing.append(f"{display_name(item)} | {source}")

    serialized_selected = "\n".join(
        json.dumps(item, ensure_ascii=False).lower()
        for item, _, _ in selected
    )
    consultant_origin_present = any(token in serialized_selected for token in (
        "consultant_chat", "ennoscholar_guided_research",
        "supplementary_verrou", "consultant",
    ))

    print("=" * 100)
    print("PHASE5 PREFLIGHT V1.1")
    print(f"Base                       : {base}")
    print(f"Fichiers de sélection utiles: {len(inspected_files)}")
    for file_name in inspected_files[:20]:
        print(f"  - {file_name}")
    print(f"Articles sélectionnés      : {len(selected)}")
    print(f"Article Cards détectées    : {len(card_items)}")
    print(f"Sélection ↔ cards appariées: {matched}")
    print(f"Cards manquantes probables : {len(missing)}")
    print(f"Origine consultant vue     : {consultant_origin_present}")
    print(f"Phase 4.6 disponible       : {phase46_path.exists()}")
    print(f"Phase 4.7 disponible       : {phase47_path.exists()}")
    print("=" * 100)

    if len(selected) == 0:
        print("PHASE5_PREFLIGHT_INDETERMINATE")
        print(
            "Aucune sélection n'a été retrouvée dans les artefacts JSON, "
            "alors que le pipeline peut la lire depuis la base de données. "
            "Ce résultat ne doit jamais être considéré comme OK."
        )
        print(
            "Compare avec le log Phase 1/2. Si le log indique selected=15 "
            "et cards=11, relance une fois avec force_article_cards=true."
        )
        return 4

    if missing:
        print("PHASE5_PREFLIGHT_BLOCKED")
        print("Articles sélectionnés sans Article Card appariée :")
        for entry in missing[:30]:
            print(f"- {entry}")
        print(
            "Relance une fois avec force_article_cards=true, "
            "puis réexécute ce préflight."
        )
        return 3

    if not phase46_path.exists() or not phase47_path.exists():
        print("PHASE5_PREFLIGHT_BLOCKED")
        print("Les phases 4.6 ou 4.7 sont absentes.")
        return 3

    print("PHASE5_PREFLIGHT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
