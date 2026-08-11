from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

from common import ensure_dirs, jsonl_iter, jsonl_write

SEED = 42

def load_review_csv(path: Path) -> Dict[str, Dict[str, str]]:
    if not path.exists():
        return {}
    out = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("id"):
                out[row["id"]] = row
    return out

def accepted_rows(candidate_jsonl: Path, review_csv: Path, task: str):
    reviews = load_review_csv(review_csv)
    out = []

    for row in jsonl_iter(candidate_jsonl):
        review = reviews.get(str(row.get("id")), {})
        human = str(review.get("human_label") or "").strip()

        if human:
            label = human
            quality = "gold_human"
        elif row.get("annotation_status") == "silver_strong":
            label = str(row.get("suggested_label") or "").strip()
            quality = "silver_strong"
        else:
            continue

        if not label:
            continue

        item = dict(row)
        item["label"] = label
        item["annotation_quality"] = quality
        out.append(item)

    return out

def project_split(rows: List[Dict[str, Any]], train_ratio=0.70, val_ratio=0.15):
    rng = random.Random(SEED)
    by_project = defaultdict(list)
    for row in rows:
        by_project[str(row.get("project_id") or row.get("document_id") or "unknown")].append(row)

    projects = list(by_project)
    rng.shuffle(projects)

    n = len(projects)
    n_train = max(1, int(n * train_ratio))
    n_val = max(1, int(n * val_ratio)) if n >= 3 else 0

    train_p = set(projects[:n_train])
    val_p = set(projects[n_train:n_train+n_val])
    test_p = set(projects[n_train+n_val:])

    splits = {"train": [], "validation": [], "test": []}
    for p, items in by_project.items():
        target = "train" if p in train_p else "validation" if p in val_p else "test"
        for item in items:
            item = dict(item)
            item["split"] = target
            splits[target].append(item)

    return splits

def write_task(root: Path, task: str):
    dirs = ensure_dirs(root)
    if task == "fastjudge":
        cand = dirs["candidates"] / "fastjudge_candidates.jsonl"
        review = dirs["candidates"] / "fastjudge_review.csv"
    else:
        cand = dirs["candidates"] / "verrou_candidates.jsonl"
        review = dirs["candidates"] / "verrou_review.csv"

    rows = accepted_rows(cand, review, task)
    if not rows:
        print(f"[{task}] Aucun exemple accepté.")
        return {}

    splits = project_split(rows)
    task_dir = dirs["final"] / task
    task_dir.mkdir(parents=True, exist_ok=True)

    summary = {}
    for split, data in splits.items():
        path = task_dir / f"{split}.jsonl"
        jsonl_write(path, data)
        summary[split] = {
            "rows": len(data),
            "projects": len(set(str(x.get("project_id")) for x in data)),
            "labels": dict(Counter(str(x.get("label")) for x in data)),
        }

    (task_dir / "split_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    args = parser.parse_args()

    root = Path(args.root)
    report = {
        "fastjudge": write_task(root, "fastjudge"),
        "verrou": write_task(root, "verrou"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
