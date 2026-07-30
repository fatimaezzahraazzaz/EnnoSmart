from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


LEGACY_NUMERIC_IDS = {
    "492", "493", "494", "495", "496", "497", "498",
    "521", "522", "523", "524",
}

LEGACY_IDS = LEGACY_NUMERIC_IDS | {
    f"verrou_{value}" for value in LEGACY_NUMERIC_IDS
}

CANONICAL_IDS = {
    "676", "677", "678",
    "verrou_676", "verrou_677", "verrou_678",
}

# Champs pouvant contenir l'identité ou le rattachement d'un verrou.
VERROU_SCALAR_KEYS = {
    "verrou_id",
    "lock_id",
    "scientific_lock_id",
    "canonical_verrou_id",
    "target_verrou",
    "source_verrou_id",
    "parent_verrou_id",
}

VERROU_LIST_KEYS = {
    "verrou_ids",
    "target_verrous",
    "canonical_verrou_ids",
    "related_verrous",
    "source_verrou_ids",
    "scientific_verrous",
    "selected_verrous",
    "verrous",
}

# Objets scientifiques qui doivent être supprimés lorsqu'ils appartiennent
# exclusivement à un ancien verrou.
SCIENTIFIC_OBJECT_HINTS = {
    "evidence",
    "evidence_item",
    "evidence_bank",
    "claim",
    "claims",
    "gap",
    "scientific_gap",
    "reasoning",
    "reasoning_item",
    "argument",
    "limitation",
    "method_analysis",
    "concept_limit",
    "transposability",
    "impact_on_verrou",
    "narrative",
    "narrative_item",
    "verrou",
    "lock",
}


class Removed:
    pass


REMOVED = Removed()


def normalize(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("-", "_").replace(" ", "_")
    if text.startswith("verrou_"):
        suffix = text.removeprefix("verrou_")
        return f"verrou_{suffix}"
    if text.isdigit():
        return text
    return text


def is_legacy(value: Any) -> bool:
    normalized = normalize(value)
    return normalized in LEGACY_IDS


def is_canonical(value: Any) -> bool:
    normalized = normalize(value)
    return normalized in CANONICAL_IDS


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def object_kind(data: dict[str, Any]) -> str:
    candidates = (
        data.get("payload_type"),
        data.get("type"),
        data.get("kind"),
        data.get("item_type"),
        data.get("object_type"),
    )
    return " ".join(str(value or "").lower() for value in candidates)


def looks_scientific(data: dict[str, Any]) -> bool:
    kind = object_kind(data)

    if any(hint in kind for hint in SCIENTIFIC_OBJECT_HINTS):
        return True

    scientific_keys = {
        "claim_id",
        "gap_id",
        "evidence_id",
        "evidence_refs",
        "evidence_snippets",
        "scientific_claim",
        "scientific_gap",
        "limitation",
        "limitations",
        "method",
        "protocol",
        "reasoning",
        "citation_label",
        "article_id",
    }

    return bool(scientific_keys.intersection(data.keys()))


def extract_direct_verrou_values(
    data: dict[str, Any],
) -> tuple[list[Any], bool]:
    values: list[Any] = []
    had_verrou_field = False

    for key, value in data.items():
        lowered = str(key).lower()

        if lowered in VERROU_SCALAR_KEYS:
            had_verrou_field = True
            values.append(value)

        elif lowered in VERROU_LIST_KEYS:
            had_verrou_field = True
            if isinstance(value, list):
                values.extend(value)
            elif value is not None:
                values.append(value)

    return values, had_verrou_field


def clean_node(
    node: Any,
    stats: dict[str, int],
    path: str = "$",
) -> Any:
    if isinstance(node, list):
        cleaned_items = []

        for index, item in enumerate(node):
            cleaned = clean_node(
                item,
                stats,
                f"{path}[{index}]",
            )

            if cleaned is REMOVED:
                stats["objects_removed"] += 1
                continue

            cleaned_items.append(cleaned)

        return cleaned_items

    if not isinstance(node, dict):
        return node

    original_verrous, had_verrou_field = extract_direct_verrou_values(node)

    had_legacy = any(is_legacy(value) for value in original_verrous)
    had_canonical = any(is_canonical(value) for value in original_verrous)

    # Objet explicitement rattaché à un seul ancien verrou :
    # suppression complète avant nettoyage des sous-objets.
    if (
        had_verrou_field
        and had_legacy
        and not had_canonical
        and looks_scientific(node)
    ):
        stats["legacy_scientific_objects_removed"] += 1
        return REMOVED

    cleaned_dict: dict[str, Any] = {}

    for key, value in node.items():
        lowered = str(key).lower()

        if lowered in VERROU_SCALAR_KEYS:
            if is_legacy(value):
                stats["legacy_scalar_refs_removed"] += 1
                continue

            cleaned_dict[key] = value
            continue

        if lowered in VERROU_LIST_KEYS:
            values = value if isinstance(value, list) else [value]
            cleaned_values = []

            for verrou_value in values:
                if is_legacy(verrou_value):
                    stats["legacy_list_refs_removed"] += 1
                    continue

                cleaned_values.append(verrou_value)

            if isinstance(value, list):
                cleaned_dict[key] = cleaned_values
            elif cleaned_values:
                cleaned_dict[key] = cleaned_values[0]

            continue

        cleaned = clean_node(
            value,
            stats,
            f"{path}.{key}",
        )

        if cleaned is REMOVED:
            stats["objects_removed"] += 1
            continue

        cleaned_dict[key] = cleaned

    # Après filtrage, un objet scientifique qui possédait uniquement
    # des anciens verrous ne doit pas survivre vide.
    cleaned_verrous, cleaned_had_verrou_field = extract_direct_verrou_values(
        cleaned_dict
    )

    if (
        had_verrou_field
        and had_legacy
        and not any(is_canonical(value) for value in cleaned_verrous)
        and looks_scientific(node)
    ):
        stats["empty_legacy_objects_removed"] += 1
        return REMOVED

    return cleaned_dict


def count_occurrences(node: Any) -> dict[str, int]:
    serialized = json.dumps(
        node,
        ensure_ascii=False,
    ).lower()

    return {
        legacy_id: (
            serialized.count(f'"verrou_{legacy_id}"')
            + serialized.count(f'"{legacy_id}"')
        )
        for legacy_id in sorted(LEGACY_NUMERIC_IDS)
    }


def load_json(path: Path) -> Any:
    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as stream:
        return json.load(stream)


def save_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")

    with temporary.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as stream:
        json.dump(
            payload,
            stream,
            ensure_ascii=False,
            indent=2,
        )
        stream.write("\n")

    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default=r"C:\EnnoSmart",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
    )
    args = parser.parse_args()

    root = Path(args.root)

    payload_root = (
        root
        / "storage"
        / "organismes"
        / "scalian"
        / "projects"
        / "ai_radar"
        / "years"
        / "2025"
        / "ennoscholar"
        / "state_of_art_payload"
    )

    article_cards = (
        payload_root
        / "article_cards"
        / "article_cards_payload.json"
    )

    selection_payload = (
        payload_root
        / "selection_payload.json"
    )

    target_files = [
        payload_root
        / "phase_4_scientific_gap"
        / "gap_scientific_payload.json",

        payload_root
        / "phase_4_5_scientific_reasoning"
        / "scientific_reasoning_payload.json",

        payload_root
        / "phase_4_6_project_rd_argumentation"
        / "project_rd_argumentation_payload.json",

        payload_root
        / "phase_4_7_scientific_narrative"
        / "scientific_narrative_payload.json",
    ]

    print("=" * 78)
    print("Nettoyage ciblé des anciens verrous de test")
    print("=" * 78)
    print(f"Mode : {'APPLICATION' if args.apply else 'ANALYSE SEULEMENT'}")
    print(f"Racine payload : {payload_root}")
    print()

    if not article_cards.exists():
        raise FileNotFoundError(
            f"Article Cards introuvable : {article_cards}"
        )

    article_hash_before = sha256(article_cards)
    article_data = load_json(article_cards)

    article_items = (
        article_data.get("items")
        or article_data.get("article_cards")
        or article_data.get("cards")
        or []
    )

    print("[PROTÉGÉ] Article Cards")
    print(f"  chemin : {article_cards}")
    print(f"  SHA-256 avant : {article_hash_before}")
    print(f"  nombre détecté : {len(article_items)}")
    print()

    if selection_payload.exists():
        selection_hash_before = sha256(selection_payload)
        print("[PROTÉGÉ] Sélection")
        print(f"  chemin : {selection_payload}")
        print(f"  SHA-256 avant : {selection_hash_before}")
        print()
    else:
        selection_hash_before = None

    existing_targets = [
        path for path in target_files if path.exists()
    ]

    if not existing_targets:
        raise FileNotFoundError(
            "Aucun payload Phase 4 à 4.7 n'a été trouvé."
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = (
        root
        / "_backups"
        / f"legacy_test_verrous_cleanup_{timestamp}"
    )

    cleaned_payloads: dict[Path, Any] = {}
    global_stats = {
        "legacy_scientific_objects_removed": 0,
        "empty_legacy_objects_removed": 0,
        "legacy_scalar_refs_removed": 0,
        "legacy_list_refs_removed": 0,
        "objects_removed": 0,
    }

    for path in existing_targets:
        payload = load_json(path)
        before_counts = count_occurrences(payload)

        local_stats = {
            key: 0 for key in global_stats
        }

        cleaned = clean_node(
            copy.deepcopy(payload),
            local_stats,
        )

        after_counts = count_occurrences(cleaned)

        cleaned_payloads[path] = cleaned

        for key, value in local_stats.items():
            global_stats[key] += value

        print(f"[FICHIER] {path}")
        print(
            "  références anciennes avant : "
            f"{sum(before_counts.values())}"
        )
        print(
            "  références anciennes après : "
            f"{sum(after_counts.values())}"
        )
        print(
            "  objets scientifiques supprimés : "
            f"{local_stats['legacy_scientific_objects_removed']}"
        )
        print(
            "  références de verrou supprimées : "
            f"{local_stats['legacy_scalar_refs_removed'] + local_stats['legacy_list_refs_removed']}"
        )
        print()

    if not args.apply:
        print("=" * 78)
        print("Aucune modification effectuée.")
        print("Pour appliquer après vérification :")
        print(
            'python "C:\\EnnoSmart\\clean_legacy_test_verrous.py" '
            '--root "C:\\EnnoSmart" --apply'
        )
        print("=" * 78)
        return 0

    backup_root.mkdir(
        parents=True,
        exist_ok=False,
    )

    for path in existing_targets:
        relative = path.relative_to(payload_root)
        backup_path = backup_root / relative
        backup_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        shutil.copy2(
            path,
            backup_path,
        )

    manifest = {
        "created_at": datetime.now().isoformat(),
        "protected_article_cards": str(article_cards),
        "protected_article_cards_sha256": article_hash_before,
        "protected_selection_payload": str(selection_payload),
        "protected_selection_sha256": selection_hash_before,
        "legacy_verrous_removed": sorted(LEGACY_NUMERIC_IDS),
        "canonical_verrous_preserved": ["676", "677", "678"],
        "modified_files": [
            str(path) for path in existing_targets
        ],
    }

    with (
        backup_root / "cleanup_manifest.json"
    ).open(
        "w",
        encoding="utf-8",
    ) as stream:
        json.dump(
            manifest,
            stream,
            ensure_ascii=False,
            indent=2,
        )

    for path, cleaned_payload in cleaned_payloads.items():
        save_json(
            path,
            cleaned_payload,
        )

    article_hash_after = sha256(article_cards)

    if article_hash_after != article_hash_before:
        raise RuntimeError(
            "Protection échouée : le fichier Article Cards a changé."
        )

    if (
        selection_hash_before is not None
        and sha256(selection_payload) != selection_hash_before
    ):
        raise RuntimeError(
            "Protection échouée : selection_payload.json a changé."
        )

    print("=" * 78)
    print("[OK] Nettoyage appliqué.")
    print(f"[BACKUP] {backup_root}")
    print()
    print("[PROTÉGÉ] Article Cards inchangé")
    print(f"  SHA-256 après : {article_hash_after}")
    print()
    print("[STATISTIQUES]")
    for key, value in global_stats.items():
        print(f"  {key} = {value}")
    print("=" * 78)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[ERREUR] {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
