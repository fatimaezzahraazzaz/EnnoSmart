# -*- coding: utf-8 -*-
"""
build_fastjudge_role_dataset_from_csv.py
------------------------------------------------------------
Convertit le CSV annoté de l'étape 3 en dataset entraînable.

Entrée :
    C:\EnnoSmart\data\training\fastjudge_annotation_sample.csv

Sorties :
    C:\EnnoSmart\data\training\role_classification_dataset.jsonl
    C:\EnnoSmart\data\training\role_classification_dataset_summary.json

Usage :
cd C:\EnnoSmart
python tools\build_fastjudge_role_dataset_from_csv.py
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


VALID_ROLES = {
    "objectif",
    "verrou",
    "methode",
    "parametre",
    "variable",
    "resultat",
    "limite",
    "contribution",
    "hypothese",
    "bruit",
}


def norm_bool(x: Any):
    s = str(x or "").strip().lower()
    if s in {"true", "1", "yes", "oui", "vrai"}:
        return True
    if s in {"false", "0", "no", "non", "faux"}:
        return False
    return None


def read_csv(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return len(rows)


def write_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def build_dataset(rows: List[Dict[str, Any]], include_unsure: bool = False) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    dataset = []
    rejected = []

    for r in rows:
        role_gold = str(r.get("role_gold") or "").strip().lower()
        status = str(r.get("annotation_status") or "").strip().lower()

        if role_gold not in VALID_ROLES:
            rejected.append({"candidate_id": r.get("candidate_id"), "reason": "role_gold_invalid_or_empty", "role_gold": role_gold})
            continue

        if status not in {"done", "valid", "validated", "ok"}:
            if not include_unsure:
                rejected.append({"candidate_id": r.get("candidate_id"), "reason": "annotation_not_done", "status": status})
                continue

        item = {
            "candidate_id": r.get("candidate_id"),
            "project_id": r.get("project_id"),
            "project_type": r.get("project_type"),
            "source_type": r.get("source_type"),
            "source_doc": r.get("source_doc"),
            "text": r.get("text"),
            "context_before": r.get("context_before"),
            "context_after": r.get("context_after"),
            "candidate_role": r.get("candidate_role"),
            "role_gold": role_gold,
            "sub_role_gold": r.get("sub_role_gold") or "",
            "keep_gold": norm_bool(r.get("keep_gold")),
            "useful_for_cir_gold": norm_bool(r.get("useful_for_cir_gold")),
            "linked_final_section": r.get("linked_final_section") or "",
            "domain": r.get("domain") or "",
            "sub_domain": r.get("sub_domain") or "",
            "quality_score": r.get("quality_score"),
            "priority": r.get("priority"),
            "comment": r.get("comment") or "",
            "dataset_version": "fastjudge_role_classification_dataset_v1",
        }
        dataset.append(item)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_version": "fastjudge_role_classification_dataset_v1",
        "total_csv_rows": len(rows),
        "accepted_rows": len(dataset),
        "rejected_rows": len(rejected),
        "by_role_gold": dict(Counter(x["role_gold"] for x in dataset)),
        "by_project_type": dict(Counter(x["project_type"] for x in dataset)),
        "by_source_type": dict(Counter(x["source_type"] for x in dataset)),
        "rejected_examples": rejected[:50],
    }
    return dataset, summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", default=r"C:\EnnoSmart\data\training\fastjudge_annotation_sample.csv")
    parser.add_argument("--output-jsonl", default=r"C:\EnnoSmart\data\training\role_classification_dataset.jsonl")
    parser.add_argument("--include-unsure", action="store_true")
    args = parser.parse_args()

    input_csv = Path(args.input_csv)
    output_jsonl = Path(args.output_jsonl)
    summary_path = output_jsonl.with_name("role_classification_dataset_summary.json")

    if not input_csv.exists():
        raise FileNotFoundError(f"CSV introuvable : {input_csv}")

    rows = read_csv(input_csv)
    dataset, summary = build_dataset(rows, include_unsure=bool(args.include_unsure))

    write_jsonl(output_jsonl, dataset)
    write_json(summary_path, summary)

    print("DATASET CRÉÉ")
    print(f"JSONL  : {output_jsonl}")
    print(f"Résumé : {summary_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
