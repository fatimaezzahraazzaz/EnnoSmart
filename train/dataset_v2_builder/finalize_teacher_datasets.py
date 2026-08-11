from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Any, List

SEED=42
FAST_LABELS=["objectif","verrou","methode","parametre","resultat","limite","contribution","bruit"]

def read_csv(path):
    with Path(path).open("r",encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f))

def read_jsonl(path):
    out={}
    p=Path(path)
    if not p.exists(): return out
    with p.open("r",encoding="utf-8") as f:
        for line in f:
            try:
                x=json.loads(line)
                if x.get("id"): out[str(x["id"])]=x
            except Exception: pass
    return out

def write_jsonl(path, rows):
    with Path(path).open("w",encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r,ensure_ascii=False)+"\n")

def diverse_select(rows, n):
    rows=sorted(rows,key=lambda r: float(r.get("teacher_confidence") or 0),reverse=True)
    first=[]; rest=[]; seen=set()
    for r in rows:
        pid=str(r.get("project_id") or "")
        if pid and pid not in seen:
            seen.add(pid); first.append(r)
        else: rest.append(r)
    out=first[:n]
    if len(out)<n:
        ids={r["id"] for r in out}
        out += [r for r in rest if r["id"] not in ids][:n-len(out)]
    return out[:n]

def merge(queue, labels):
    out=[]
    for r in queue:
        t=labels.get(str(r.get("id")))
        if not t: continue
        x=dict(r)
        x.update(t)
        out.append(x)
    return out

def project_split(rows):
    rng=random.Random(SEED)
    by=defaultdict(list)
    for r in rows:
        by[str(r.get("project_id") or "unknown")].append(r)
    pids=list(by); rng.shuffle(pids)
    n=len(pids); a=int(n*.70); b=int(n*.85)
    sets={"train":set(pids[:a]),"validation":set(pids[a:b]),"test":set(pids[b:])}
    out={"train":[],"validation":[],"test":[]}
    for pid,items in by.items():
        target="train" if pid in sets["train"] else "validation" if pid in sets["validation"] else "test"
        for r in items:
            x=dict(r); x["split"]=target; out[target].append(x)
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default=r"C:\EnnoSmart")
    ap.add_argument("--teacher-min-confidence",type=float,default=0.80)
    args=ap.parse_args()
    root=Path(args.root)
    q=root/"train"/"data_v2"/"teacher_queues"
    l=root/"train"/"data_v2"/"teacher_labels"
    out=root/"train"/"data_v2"/"final_teacher"
    out.mkdir(parents=True,exist_ok=True)

    # FASTJUDGE 2k per class
    fq=read_csv(q/"fastjudge_teacher_queue.csv")
    fl=read_jsonl(l/"fastjudge_teacher_labels.jsonl")
    fm=merge(fq,fl)
    selected_f=[]
    fast_counts={}
    for lab in FAST_LABELS:
        pool=[r for r in fm if r.get("teacher_label")==lab and float(r.get("teacher_confidence") or 0)>=args.teacher_min_confidence]
        sel=diverse_select(pool,2000)
        fast_counts[lab]={"pool":len(pool),"selected":len(sel)}
        selected_f += sel

    # VERROU 5k positive + 7k negative, including approx 3.5k hard negatives
    vq=read_csv(q/"verrou_teacher_queue.csv")
    vl=read_jsonl(l/"verrou_teacher_labels.jsonl")
    vm=merge(vq,vl)
    pos_pool=[r for r in vm if r.get("teacher_label")=="verrou_evidence" and float(r.get("teacher_confidence") or 0)>=args.teacher_min_confidence]
    neg_pool=[r for r in vm if r.get("teacher_label")=="non_verrou" and float(r.get("teacher_confidence") or 0)>=args.teacher_min_confidence]
    hard_pool=[r for r in neg_pool if r.get("candidate_bucket")=="hard_negative"]
    easy_pool=[r for r in neg_pool if r.get("candidate_bucket")!="hard_negative"]

    pos=diverse_select(pos_pool,5000)
    hard=diverse_select(hard_pool,3500)
    used={r["id"] for r in hard}
    easy=[r for r in diverse_select(easy_pool,3500) if r["id"] not in used][:3500]
    selected_v=pos+hard+easy

    report={
        "fastjudge":{"target":16000,"selected":len(selected_f),"per_class":fast_counts},
        "verrou_detector":{
            "target":12000,
            "positive_pool":len(pos_pool),"positive_selected":len(pos),
            "hard_negative_pool":len(hard_pool),"hard_negative_selected":len(hard),
            "easy_negative_pool":len(easy_pool),"easy_negative_selected":len(easy),
            "selected":len(selected_v)
        }
    }

    for name,rows in [("fastjudge",selected_f),("verrou_detector",selected_v)]:
        splits=project_split(rows)
        d=out/name; d.mkdir(parents=True,exist_ok=True)
        for split,data in splits.items():
            write_jsonl(d/f"{split}.jsonl",data)

    (out/"final_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
