# -*- coding: utf-8 -*-
"""
build_verrou_gold_annotation_sample.py
------------------------------------------------------------
Crée un CSV d'annotation GOLD pour améliorer VerrouDetector.

Entrée :
    C:\EnnoSmart\data\training\verrou_detection_dataset.jsonl

Sorties :
    C:\EnnoSmart\data\training\verrou_gold_annotation_sample.csv
    C:\EnnoSmart\data\training\verrou_gold_annotation_sample.jsonl
    C:\EnnoSmart\data\training\verrou_gold_annotation_sample_summary.json

Objectif :
    Ne pas entraîner VerrouDetector sur des labels automatiques.
    Créer un échantillon propre à corriger humainement.

Colonnes à remplir/corriger :
    gold_status = done / unsure / reject
    is_verrou_evidence_gold = true / false
    verrou_type_gold = type de verrou si true
    quality_gold = good / medium / bad
    comment_gold = optionnel

Usage :
    cd C:\EnnoSmart
    python tools\build_verrou_gold_annotation_sample.py

Taille par défaut :
    environ 1200 lignes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


VERROU_TYPES = [
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
]

CSV_FIELDS = [
    # Colonnes GOLD à corriger
    "gold_status",
    "is_verrou_evidence_gold",
    "verrou_type_gold",
    "quality_gold",
    "comment_gold",

    # Auto-labels pour aide
    "is_verrou_evidence_auto",
    "verrou_type_auto",
    "verrou_confidence_auto",
    "verrou_rule",
    "review_priority",
    "hard_negative",

    # Contexte annotation
    "role_gold",
    "candidate_role",
    "project_id",
    "project_type",
    "source_type",
    "source_doc",
    "text",
    "context_before",
    "context_after",

    # IDs
    "candidate_id",
    "sample_id",
    "source_hash",
]


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            n += 1
    return n


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def clean(x: Any) -> str:
    s = str(x or "").replace("\u00a0", " ").replace("\ufeff", " ")
    return re.sub(r"\s+", " ", s).strip()


def norm(x: Any) -> str:
    return clean(x).lower().replace("’", "'")


def h_text(x: Any) -> str:
    return hashlib.md5(norm(x).encode("utf-8", errors="ignore")).hexdigest()


def to_bool(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    s = str(x or "").strip().lower()
    return s in {"true", "1", "yes", "oui", "vrai"}


def as_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def row_score_for_annotation(row: Dict[str, Any]) -> float:
    """
    Priorise les lignes importantes à annoter :
    - HIGH / MEDIUM
    - hard negatives
    - positifs avec type rare
    - rôle verrou/limite/résultat
    """
    score = 0.0

    priority = row.get("review_priority", "")
    if priority == "HIGH":
        score += 4.0
    elif priority == "MEDIUM":
        score += 3.0
    else:
        score += 1.0

    if to_bool(row.get("hard_negative")):
        score += 2.0

    if to_bool(row.get("is_verrou_evidence")):
        score += 1.5

    role = norm(row.get("role_gold"))
    if role in {"verrou", "limite", "resultat"}:
        score += 1.0
    elif role in {"methode", "parametre", "contribution"}:
        score += 0.5

    vtype = row.get("verrou_type", "")
    rare_types = {
        "manque_donnees",
        "absence_referentiel",
        "performance_insuffisante",
        "protocole_insuffisant",
        "passage_echelle",
        "difficulte_mesure",
    }
    if vtype in rare_types:
        score += 2.0

    conf = as_float(row.get("verrou_confidence_auto"), 0.0)
    if 0.55 <= conf <= 0.78:
        score += 1.0  # zone douteuse utile pour apprentissage

    return score


def deduplicate(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best = {}
    for r in rows:
        h = h_text(r.get("text"))
        old = best.get(h)
        if old is None or row_score_for_annotation(r) > row_score_for_annotation(old):
            best[h] = r
    return list(best.values())


def pick_diverse(rows: List[Dict[str, Any]], n: int, max_per_project: int = 140, max_per_doc: int = 70, seed: int = 42) -> List[Dict[str, Any]]:
    random.seed(seed)
    rows = sorted(rows, key=row_score_for_annotation, reverse=True)

    picked = []
    seen = set()
    by_project = Counter()
    by_doc = Counter()

    for r in rows:
        if len(picked) >= n:
            break

        ht = h_text(r.get("text"))
        if ht in seen:
            continue

        project = r.get("project_id", "unknown")
        doc = (project, r.get("source_doc", "unknown"))

        if by_project[project] >= max_per_project:
            continue
        if by_doc[doc] >= max_per_doc:
            continue

        picked.append(r)
        seen.add(ht)
        by_project[project] += 1
        by_doc[doc] += 1

    if len(picked) < n:
        for r in rows:
            if len(picked) >= n:
                break
            ht = h_text(r.get("text"))
            if ht not in seen:
                picked.append(r)
                seen.add(ht)

    return picked[:n]


def build_sample(rows: List[Dict[str, Any]], target_size: int = 1200, seed: int = 42) -> List[Dict[str, Any]]:
    rows = deduplicate(rows)

    positives = [r for r in rows if to_bool(r.get("is_verrou_evidence"))]
    negatives = [r for r in rows if not to_bool(r.get("is_verrou_evidence"))]
    hard_negs = [r for r in negatives if to_bool(r.get("hard_negative"))]
    easy_negs = [r for r in negatives if not to_bool(r.get("hard_negative"))]

    # Quotas recommandés
    pos_quota = int(target_size * 0.50)
    hard_neg_quota = int(target_size * 0.35)
    easy_neg_quota = target_size - pos_quota - hard_neg_quota

    # Assurer diversité par type de verrou
    selected = []

    by_type = defaultdict(list)
    for r in positives:
        by_type[r.get("verrou_type", "autre_verrou")].append(r)

    # minimum par type si possible
    min_per_type = 20
    for vtype, group in sorted(by_type.items(), key=lambda x: x[0]):
        chosen = pick_diverse(group, min(min_per_type, len(group)), seed=seed)
        selected.extend(chosen)

    selected_hashes = {h_text(r.get("text")) for r in selected}

    remaining_pos = [r for r in positives if h_text(r.get("text")) not in selected_hashes]
    selected += pick_diverse(remaining_pos, max(0, pos_quota - len(selected)), seed=seed + 1)

    selected_hashes = {h_text(r.get("text")) for r in selected}

    hard_negs = [r for r in hard_negs if h_text(r.get("text")) not in selected_hashes]
    selected += pick_diverse(hard_negs, hard_neg_quota, seed=seed + 2)

    selected_hashes = {h_text(r.get("text")) for r in selected}

    easy_negs = [r for r in easy_negs if h_text(r.get("text")) not in selected_hashes]
    selected += pick_diverse(easy_negs, max(0, target_size - len(selected)), seed=seed + 3)

    # Fallback
    if len(selected) < target_size:
        selected_hashes = {h_text(r.get("text")) for r in selected}
        rest = [r for r in rows if h_text(r.get("text")) not in selected_hashes]
        selected += pick_diverse(rest, target_size - len(selected), seed=seed + 4)

    selected = selected[:target_size]

    out = []
    for i, r in enumerate(selected, start=1):
        auto_positive = to_bool(r.get("is_verrou_evidence"))
        sample_id = f"verrou_gold_{i:05d}"

        item = {
            "gold_status": "to_annotate",
            "is_verrou_evidence_gold": "true" if auto_positive else "false",
            "verrou_type_gold": r.get("verrou_type", "") if auto_positive else "",
            "quality_gold": "",
            "comment_gold": "",

            "is_verrou_evidence_auto": "true" if auto_positive else "false",
            "verrou_type_auto": r.get("verrou_type", ""),
            "verrou_confidence_auto": r.get("verrou_confidence_auto", ""),
            "verrou_rule": r.get("verrou_rule", ""),
            "review_priority": r.get("review_priority", ""),
            "hard_negative": "true" if to_bool(r.get("hard_negative")) else "false",

            "role_gold": r.get("role_gold", ""),
            "candidate_role": r.get("candidate_role", ""),
            "project_id": r.get("project_id", ""),
            "project_type": r.get("project_type", ""),
            "source_type": r.get("source_type", ""),
            "source_doc": r.get("source_doc", ""),
            "text": r.get("text", ""),
            "context_before": r.get("context_before", ""),
            "context_after": r.get("context_after", ""),

            "candidate_id": r.get("candidate_id", ""),
            "sample_id": sample_id,
            "source_hash": h_text(r.get("text", "")),
        }
        out.append(item)

    return out


def summarize(sample: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_version": "verrou_gold_annotation_sample_v1",
        "rows": len(sample),
        "auto_positive": sum(1 for r in sample if r["is_verrou_evidence_auto"] == "true"),
        "auto_negative": sum(1 for r in sample if r["is_verrou_evidence_auto"] == "false"),
        "by_review_priority": dict(Counter(r["review_priority"] for r in sample)),
        "by_verrou_type_auto": dict(Counter(r["verrou_type_auto"] for r in sample if r["is_verrou_evidence_auto"] == "true")),
        "by_role_gold": dict(Counter(r["role_gold"] for r in sample)),
        "hard_negative_count": sum(1 for r in sample if r["hard_negative"] == "true"),
        "instructions": {
            "gold_status": "done / unsure / reject",
            "is_verrou_evidence_gold": "true si la phrase aide à construire un verrou CIR, sinon false",
            "verrou_type_gold": "obligatoire si true, vide si false",
            "quality_gold": "good / medium / bad",
        }
    }


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input-jsonl", default=r"C:\EnnoSmart\data\training\verrou_detection_dataset.jsonl")
    p.add_argument("--output-csv", default=r"C:\EnnoSmart\data\training\verrou_gold_annotation_sample.csv")
    p.add_argument("--output-jsonl", default=r"C:\EnnoSmart\data\training\verrou_gold_annotation_sample.jsonl")
    p.add_argument("--summary-json", default=r"C:\EnnoSmart\data\training\verrou_gold_annotation_sample_summary.json")
    p.add_argument("--target-size", type=int, default=1200)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def main():
    args = parse_args()

    rows = read_jsonl(Path(args.input_jsonl))
    sample = build_sample(rows, target_size=args.target_size, seed=args.seed)
    summary = summarize(sample)

    write_csv(Path(args.output_csv), sample)
    write_jsonl(Path(args.output_jsonl), sample)
    write_json(Path(args.summary_json), summary)

    print("SAMPLE GOLD VERROU CRÉÉ")
    print(f"CSV     : {args.output_csv}")
    print(f"JSONL   : {args.output_jsonl}")
    print(f"Résumé  : {args.summary_json}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
