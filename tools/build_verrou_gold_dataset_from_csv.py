# -*- coding: utf-8 -*-
"""
build_verrou_gold_dataset_from_csv.py
------------------------------------------------------------
Convertit le CSV annoté en dataset GOLD pour entraîner VerrouDetector.

Entrée :
    C:\EnnoSmart\data\training\verrou_gold_annotation_sample.csv

Sorties :
    C:\EnnoSmart\data\training\verrou_detection_dataset_gold.jsonl
    C:\EnnoSmart\data\training\verrou_detection_dataset_gold_summary.json

Usage :
    cd C:\EnnoSmart
    python tools\build_verrou_gold_dataset_from_csv.py
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


VALID_TYPES = {
    "",
    "limite_etat_art",
    "difficulte_mesure",
    "difficulte_modelisation",
    "manque_donnees",
    "passage_echelle",
    "temps_reel_latency",
    "incertitude_resultat",
    "variabilite_conditions",
    "non_reproductibilite",
    "complexite_systeme",
    "absence_referentiel",
    "performance_insuffisante",
    "protocole_insuffisant",
    "verrou_explicit",
    "autre_verrou",
}


def norm(x: Any) -> str:
    return str(x or "").strip().lower()


def to_bool(x: Any):
    s = norm(x)
    if s in {"true", "1", "yes", "oui", "vrai"}:
        return True
    if s in {"false", "0", "no", "non", "faux"}:
        return False
    return None


def read_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    return len(rows)


def write_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def build_dataset(rows: List[Dict[str, Any]], accept_prefilled: bool = False):
    dataset = []
    rejected = []

    for r in rows:
        status = norm(r.get("gold_status"))
        if status not in {"done", "valid", "validated", "ok"}:
            if not accept_prefilled:
                rejected.append({"sample_id": r.get("sample_id"), "reason": "gold_status_not_done", "status": status})
                continue

        is_gold = to_bool(r.get("is_verrou_evidence_gold"))
        if is_gold is None:
            rejected.append({"sample_id": r.get("sample_id"), "reason": "is_verrou_evidence_gold_invalid"})
            continue

        vtype = norm(r.get("verrou_type_gold"))
        if is_gold and not vtype:
            rejected.append({"sample_id": r.get("sample_id"), "reason": "positive_without_verrou_type"})
            continue

        if vtype not in VALID_TYPES:
            rejected.append({"sample_id": r.get("sample_id"), "reason": "invalid_verrou_type", "verrou_type_gold": vtype})
            continue

        if not is_gold:
            vtype = ""

        quality = norm(r.get("quality_gold"))
        if quality not in {"good", "medium", "bad", ""}:
            quality = ""

        item = {
            "sample_id": r.get("sample_id"),
            "candidate_id": r.get("candidate_id"),
            "source_hash": r.get("source_hash"),
            "project_id": r.get("project_id"),
            "project_type": r.get("project_type"),
            "source_type": r.get("source_type"),
            "source_doc": r.get("source_doc"),
            "text": r.get("text"),
            "context_before": r.get("context_before"),
            "context_after": r.get("context_after"),
            "role_gold": r.get("role_gold"),
            "candidate_role": r.get("candidate_role"),

            "is_verrou_evidence": bool(is_gold),
            "verrou_type": vtype,
            "quality_gold": quality,
            "comment_gold": r.get("comment_gold", ""),

            "is_verrou_evidence_auto": to_bool(r.get("is_verrou_evidence_auto")),
            "verrou_type_auto": r.get("verrou_type_auto", ""),
            "verrou_rule": r.get("verrou_rule", ""),
            "hard_negative": to_bool(r.get("hard_negative")),

            "dataset_version": "verrou_detection_dataset_gold_v1",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        dataset.append(item)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_version": "verrou_detection_dataset_gold_v1",
        "input_rows": len(rows),
        "accepted_rows": len(dataset),
        "rejected_rows": len(rejected),
        "positive_count": sum(1 for r in dataset if r["is_verrou_evidence"]),
        "negative_count": sum(1 for r in dataset if not r["is_verrou_evidence"]),
        "by_verrou_type": dict(Counter(r["verrou_type"] for r in dataset if r["is_verrou_evidence"])),
        "by_role_gold": dict(Counter(r["role_gold"] for r in dataset)),
        "by_quality": dict(Counter(r["quality_gold"] for r in dataset)),
        "rejected_examples": rejected[:80],
    }
    return dataset, summary


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input-csv", default=r"C:\EnnoSmart\data\training\verrou_gold_annotation_sample.csv")
    p.add_argument("--output-jsonl", default=r"C:\EnnoSmart\data\training\verrou_detection_dataset_gold.jsonl")
    p.add_argument("--summary-json", default=r"C:\EnnoSmart\data\training\verrou_detection_dataset_gold_summary.json")
    p.add_argument("--accept-prefilled", action="store_true", help="Utilise aussi les lignes non done. À éviter pour un vrai GOLD.")
    return p.parse_args()


def main():
    args = parse_args()
    rows = read_csv(Path(args.input_csv))
    dataset, summary = build_dataset(rows, accept_prefilled=args.accept_prefilled)

    write_jsonl(Path(args.output_jsonl), dataset)
    write_json(Path(args.summary_json), summary)

    print("DATASET GOLD VERROU CRÉÉ")
    print(f"JSONL  : {args.output_jsonl}")
    print(f"Résumé : {args.summary_json}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
