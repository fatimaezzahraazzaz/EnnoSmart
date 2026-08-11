from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

FAST_LABELS = [
    "objectif", "verrou", "methode", "parametre",
    "resultat", "limite", "contribution", "bruit"
]

def read_jsonl(path):
    rows=[]
    p=Path(path)
    if not p.exists():
        return rows
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                x=json.loads(line)
                if isinstance(x,dict):
                    rows.append(x)
            except Exception:
                pass
    return rows

def summarize_fast(rows):
    print("\n" + "="*80)
    print("FASTJUDGE — QWEN3 8B FULL")
    print("="*80)
    if not rows:
        print("Aucun résultat.")
        return

    labels=Counter(str(r.get("teacher_label")) for r in rows)
    high=Counter(
        str(r.get("teacher_label"))
        for r in rows
        if float(r.get("teacher_confidence") or 0) >= 0.80
    )
    very_high=Counter(
        str(r.get("teacher_label"))
        for r in rows
        if float(r.get("teacher_confidence") or 0) >= 0.90
    )

    print("Total :",len(rows))
    print("\nLabels Qwen3 8B:")
    for lab in FAST_LABELS:
        print(f"  {lab:18s} total={labels[lab]:5d}  conf>=.80={high[lab]:5d}  conf>=.90={very_high[lab]:5d}")

    print("\nObjectif final: 2 000 exemples fiables par classe.")
    print("Manques à conf>=0.80:")
    for lab in FAST_LABELS:
        miss=max(0,2000-high[lab])
        print(f"  {lab:18s} manque={miss:5d}")

def summarize_verrou(rows):
    print("\n" + "="*80)
    print("VERROU DETECTOR — QWEN3 8B FULL")
    print("="*80)
    if not rows:
        print("Aucun résultat.")
        return

    total=Counter(str(r.get("teacher_label")) for r in rows)
    high=Counter(
        str(r.get("teacher_label"))
        for r in rows
        if float(r.get("teacher_confidence") or 0)>=0.80
    )
    very_high=Counter(
        str(r.get("teacher_label"))
        for r in rows
        if float(r.get("teacher_confidence") or 0)>=0.90
    )

    buckets=defaultdict(Counter)
    for r in rows:
        buckets[str(r.get("candidate_bucket") or "")][str(r.get("teacher_label"))]+=1

    for lab in ["verrou_evidence","non_verrou"]:
        print(f"  {lab:18s} total={total[lab]:5d}  conf>=.80={high[lab]:5d}  conf>=.90={very_high[lab]:5d}")

    print("\nPar bucket:")
    for bucket,c in buckets.items():
        print(f"  {bucket:18s} verrou={c['verrou_evidence']:5d} non_verrou={c['non_verrou']:5d}")

    print("\nObjectif final indicatif:")
    print("  verrou_evidence : 5 000")
    print("  non_verrou      : 7 000")
    print("  dont hard negatives ~3 500")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default=r"C:\EnnoSmart")
    args=ap.parse_args()

    d=Path(args.root)/"train"/"data_v2"/"full_teacher_qwen3_8b"
    summarize_fast(read_jsonl(d/"fastjudge_qwen3_8b_full.jsonl"))
    summarize_verrou(read_jsonl(d/"verrou_qwen3_8b_full.jsonl"))

if __name__=="__main__":
    main()
