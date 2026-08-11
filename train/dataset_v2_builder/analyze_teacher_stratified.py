from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

def rows(path):
    out=[]
    if not path.exists():
        return out
    with path.open("r",encoding="utf-8") as f:
        for line in f:
            try: out.append(json.loads(line))
            except Exception: pass
    return out

def report(name, data):
    print("\n"+"="*72)
    print(name)
    print("="*72)
    if not data:
        print("Aucun résultat.")
        return

    cand=Counter(str(r.get("candidate_label")) for r in data)
    teach=Counter(str(r.get("teacher_label")) for r in data)
    agree=sum(str(r.get("candidate_label"))==str(r.get("teacher_label")) for r in data)
    avg=sum(float(r.get("teacher_confidence") or 0) for r in data)/len(data)

    print("Total :",len(data))
    print("Candidats :",dict(cand))
    print("Teacher :",dict(teach))
    print("Accord global :",round(100*agree/len(data),2),"%")
    print("Confiance moyenne :",round(avg,4))

    by=defaultdict(list)
    for r in data:
        by[str(r.get("candidate_label"))].append(r)

    print("\nAccord par classe candidate:")
    for lab, vals in sorted(by.items()):
        a=sum(str(x.get("candidate_label"))==str(x.get("teacher_label")) for x in vals)
        print(f"  {lab:18s} {a:3d}/{len(vals):3d} = {100*a/len(vals):6.2f}%")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default=r"C:\EnnoSmart")
    args=ap.parse_args()
    d=Path(args.root)/"train"/"data_v2"/"teacher_labels_local_stratified"
    report("FASTJUDGE STRATIFIED", rows(d/"fastjudge_stratified_qwen3.jsonl"))
    report("VERROU STRATIFIED", rows(d/"verrou_stratified_qwen3.jsonl"))

if __name__=="__main__":
    main()
