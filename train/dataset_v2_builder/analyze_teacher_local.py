from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

def read_jsonl(path: Path):
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                x = json.loads(line)
                if isinstance(x, dict):
                    rows.append(x)
            except Exception:
                pass
    return rows

def show(task, rows):
    print("\n" + "=" * 72)
    print(task.upper())
    print("=" * 72)

    if not rows:
        print("Aucun résultat.")
        return

    teacher = Counter(str(r.get("teacher_label")) for r in rows)
    candidate = Counter(str(r.get("candidate_label")) for r in rows)

    agree = sum(
        str(r.get("teacher_label")) == str(r.get("candidate_label"))
        for r in rows
    )
    conf80 = sum(
        float(r.get("teacher_confidence") or 0) >= 0.80
        for r in rows
    )
    conf90 = sum(
        float(r.get("teacher_confidence") or 0) >= 0.90
        for r in rows
    )
    avg = sum(float(r.get("teacher_confidence") or 0) for r in rows) / len(rows)

    print("Total :", len(rows))
    print("Teacher labels :", dict(teacher))
    print("Candidate labels :", dict(candidate))
    print("Accord candidat / teacher :", round(100 * agree / len(rows), 2), "%")
    print("Confiance moyenne :", round(avg, 4))
    print("Confiance >= 0.80 :", round(100 * conf80 / len(rows), 2), "%")
    print("Confiance >= 0.90 :", round(100 * conf90 / len(rows), 2), "%")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    args = parser.parse_args()

    root = Path(args.root)
    d = root / "train" / "data_v2" / "teacher_labels_local"

    show(
        "FastJudge / Qwen3",
        read_jsonl(d / "fastjudge_teacher_qwen3.jsonl")
    )
    show(
        "VerrouDetector / Qwen3",
        read_jsonl(d / "verrou_teacher_qwen3.jsonl")
    )

if __name__ == "__main__":
    main()
