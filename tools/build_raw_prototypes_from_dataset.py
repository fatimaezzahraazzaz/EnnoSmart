# -*- coding: utf-8 -*-


from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from collections import Counter, defaultdict
from typing import Any


ALLOWED_LABELS = {
    "objectif",
    "vrai_verrou_rd",
    "contrainte_technique",
    "critere_validation",
    "contrainte_normative",
    "methode",
    "resultat",
    "parametre",
    "variable",
    "bruit",
}

# Ces labels sont souvent moins nombreux.
# On les garde tous pour ne pas les noyer dans les grosses classes.
KEEP_ALL_LABELS = {
    "critere_validation",
    "contrainte_normative",
    "contrainte_technique",
}


def clean_text(value: Any) -> str:
    return " ".join(
        str(value or "")
        .replace("\ufeff", " ")
        .replace("\xa0", " ")
        .split()
    ).strip()


def read_dataset(path: Path) -> list[dict]:
    rows: list[dict] = []

    if not path.exists():
        raise FileNotFoundError(f"Dataset introuvable : {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        required = {"text", "label"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Colonnes manquantes dans le CSV : {missing}. "
                f"Colonnes trouvées : {reader.fieldnames}"
            )

        for row in reader:
            text = clean_text(row.get("text"))
            label = clean_text(row.get("label")).lower()
            quality = clean_text(row.get("quality") or "good").lower()

            if not text:
                continue

            if label not in ALLOWED_LABELS:
                continue

            # On garde seulement les exemples utiles.
            if quality not in {"good", "medium"}:
                continue

            # Évite les fragments trop courts.
            if len(text) < 35:
                continue

            rows.append(
                {
                    "label": label,
                    "text": text,
                    "document_type": clean_text(row.get("document_type")),
                    "domain": clean_text(row.get("domain")),
                    "source_document": clean_text(row.get("source_document")),
                    "source_section": clean_text(row.get("source_section")),
                    "quality": quality,
                    "comment": clean_text(row.get("comment")),
                }
            )

    return rows


def deduplicate(rows: list[dict]) -> list[dict]:
    seen = set()
    output: list[dict] = []

    for row in rows:
        signature = (
            row["label"].lower(),
            row["text"].lower()[:600],
        )

        if signature in seen:
            continue

        seen.add(signature)
        output.append(row)

    return output


def balance_by_label(rows: list[dict], max_per_label: int = 180) -> list[dict]:
    """
    Équilibre les prototypes pour éviter qu'une grosse classe domine la similarité.

    Exemple :
    - vrai_verrou_rd = 400 exemples
    - critere_validation = 10 exemples

    Sans équilibrage, critere_validation sera presque invisible.
    """

    by_label: dict[str, list[dict]] = defaultdict(list)

    for row in rows:
        by_label[row["label"]].append(row)

    output: list[dict] = []

    for label, items in by_label.items():
        # On garde les petites classes critiques complètes.
        if label in KEEP_ALL_LABELS:
            output.extend(items)
            continue

        # Pour les grosses classes, on limite.
        output.extend(items[:max_per_label])

    return output


def write_prototypes(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    prototypes = []

    for row in rows:
        item = {
            "label": row["label"],
            "text": row["text"],
        }

        # Métadonnées utiles, mais non obligatoires pour le collector.
        for key in [
            "document_type",
            "domain",
            "source_document",
            "source_section",
            "quality",
            "comment",
        ]:
            if row.get(key):
                item[key] = row[key]

        prototypes.append(item)

    output_path.write_text(
        json.dumps(prototypes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def print_distribution(title: str, rows: list[dict]) -> None:
    counts = Counter(row["label"] for row in rows)

    print(f"\n{title}")
    print("-" * len(title))
    print(f"Total : {len(rows)}")

    for label, count in sorted(counts.items(), key=lambda x: x[0]):
        print(f"  - {label}: {count}")


def print_report(
    input_rows: list[dict],
    dedup_rows: list[dict],
    balanced_rows: list[dict],
    output_path: Path,
) -> None:
    print_distribution("Distribution initiale", input_rows)
    print_distribution("Après déduplication", dedup_rows)
    print_distribution("Après équilibrage", balanced_rows)

    print("\nPrototypes générés avec succès.")
    print(f"Fichier : {output_path}")
    print(f"Total final : {len(balanced_rows)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument(
        "--max-per-label",
        type=int,
        default=180,
        help="Nombre maximum d'exemples gardés par grosse classe.",
    )

    args = parser.parse_args()

    input_path = Path(args.input_csv)
    output_path = Path(args.output_json)

    rows = read_dataset(input_path)
    dedup_rows = deduplicate(rows)
    balanced_rows = balance_by_label(
        dedup_rows,
        max_per_label=args.max_per_label,
    )

    write_prototypes(balanced_rows, output_path)

    print_report(
        input_rows=rows,
        dedup_rows=dedup_rows,
        balanced_rows=balanced_rows,
        output_path=output_path,
    )


if __name__ == "__main__":
    main()