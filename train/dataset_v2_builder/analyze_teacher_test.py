from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

def read_jsonl(path):
    rows=[]
    p=Path(path)
    if not p.exists():
        return rows
    with p.open("r",encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default=r"C:\EnnoSmart")
    args=ap.parse_args()

    root=Path(args.root)
    d=root/"train"/"data_v2"/"teacher_labels_v2"

    for task,file in [
        ("fastjudge","fastjudge_teacher_labels_v2.jsonl"),
        ("verrou","verrou_teacher_labels_v2.jsonl")
    ]:
        rows=read_jsonl(d/file)
        if not rows:
            print(f"\n{task}: aucun résultat")
            continue

        labels=Counter(str(r.get("teacher_label")) for r in rows)
        candidate=Counter(str(r.get("candidate_label")) for r in rows)
        agreements=sum(
            str(r.get("teacher_label"))==str(r.get("candidate_label"))
            for r in rows
        )
        high=sum(float(r.get("teacher_confidence") or 0)>=0.80 for r in rows)

        print("\n"+"="*70)
        print(task.upper())
        print("="*70)
        print("Total :",len(rows))
        print("Teacher labels :",dict(labels))
        print("Candidate labels :",dict(candidate))
        print("Accord candidate/teacher :",round(agreements/len(rows)*100,2),"%")
        print("Confiance teacher >= 0.80 :",round(high/len(rows)*100,2),"%")

if __name__=="__main__":
    main()
