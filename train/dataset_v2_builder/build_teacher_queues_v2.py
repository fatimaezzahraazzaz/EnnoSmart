from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

FAST_LABELS = [
    "objectif", "verrou", "methode", "parametre",
    "resultat", "limite", "contribution", "bruit"
]
NON_LOCK_ROLES = {"objectif","methode","parametre","resultat","contribution","bruit"}

def iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if not line: continue
            try:
                x=json.loads(line)
                if isinstance(x,dict): yield x
            except Exception:
                pass

def sf(v):
    try: return float(v or 0)
    except Exception: return 0.0

def key(r):
    return str(r.get("text_hash") or f"{r.get('source')}::{r.get('project_id')}::{r.get('text')}")

def diverse(rows: List[Dict[str,Any]], n: int, score="queue_score", exclude=None):
    exclude=set(exclude or [])
    rows=[r for r in rows if key(r) not in exclude]
    rows=sorted(rows,key=lambda r:sf(r.get(score)),reverse=True)
    first=[]; rest=[]; seen=set()
    for r in rows:
        pid=str(r.get("project_id") or "")
        if pid and pid not in seen:
            seen.add(pid); first.append(r)
        else:
            rest.append(r)
    out=first[:n]
    if len(out)<n:
        used={key(x) for x in out}
        out += [x for x in rest if key(x) not in used][:n-len(out)]
    return out[:n]

def lock_feat(r):
    f=r.get("lock_rule_features") or {}
    if not isinstance(f,dict): f={}
    return {
        "positive_hits":int(f.get("positive_hits") or 0),
        "routine_negative_hits":int(f.get("routine_negative_hits") or 0),
        "technical_difficulty_hits":int(f.get("technical_difficulty_hits") or 0),
    }

def build_fast(rows, per_class):
    pools=defaultdict(list)
    noise_fallback=[]

    for r in rows:
        x=dict(r)
        lab=str(x.get("suggested_label") or "")
        ms=sf(x.get("model_score"))
        rs=sf(x.get("rule_score"))
        agree=1.0 if str(x.get("model_label"))==str(x.get("rule_label")) else 0.0
        bonus={"silver_strong":.20,"silver_model_high":.15,"review_priority":.08}.get(str(x.get("annotation_status")),0)
        x["queue_score"]=round(.45*ms+.20*rs+.20*agree+bonus,6)
        if lab in FAST_LABELS:
            x["candidate_label"]=lab
            pools[lab].append(x)

        # Candidats bruit supplémentaires : aucune règle sémantique claire,
        # modèle peu confiant / weak_only. Le teacher décidera réellement du label.
        if (
            str(x.get("annotation_status"))=="weak_only"
            and (str(x.get("rule_label") or "") in {"","unknown"})
            and rs <= 0.05
            and ms <= 0.70
        ):
            y=dict(x)
            y["candidate_label"]="bruit"
            y["queue_score"]=round((1.0-ms)*0.7 + 0.3,6)
            noise_fallback.append(y)

    selected=[]
    used=set()

    # 7 classes non-bruit.
    for lab in [x for x in FAST_LABELS if x!="bruit"]:
        sel=diverse(pools[lab],per_class,exclude=used)
        selected += sel
        used.update(key(x) for x in sel)

    # Bruit : d'abord vrais candidats bruit existants, puis fallback.
    bruit=diverse(pools["bruit"],per_class,exclude=used)
    used.update(key(x) for x in bruit)
    if len(bruit)<per_class:
        extra=diverse(noise_fallback,per_class-len(bruit),exclude=used)
        bruit += extra
        used.update(key(x) for x in extra)
    selected += bruit
    return selected

def build_verrou(verrou_rows, fast_rows, pos_n, easy_n, hard_n):
    fast_by={key(r):r for r in fast_rows}
    pos_pool=[]; easy_pool=[]; hard_pool=[]

    for r in verrou_rows:
        x=dict(r); f=lock_feat(x); fr=fast_by.get(key(x),{})
        vm=sf(x.get("model_score"))
        fj=str(fr.get("suggested_label") or fr.get("model_label") or "")
        fjs=sf(fr.get("model_score"))
        x["fastjudge_label"]=fj; x["fastjudge_score"]=fjs; x.update(f)

        if f["positive_hits"]>=1 or fj in {"verrou","limite"} or vm>=0.70:
            y=dict(x); y["candidate_bucket"]="positive_like"; y["candidate_label"]="verrou_evidence"
            y["queue_score"]=round(.42*vm+.20*min(f["positive_hits"],3)/3+.18*(1 if fj=="verrou" else .6 if fj=="limite" else 0)+.10*fjs+.10*(1 if f["technical_difficulty_hits"] else 0),6)
            pos_pool.append(y)

        if f["positive_hits"]==0 and fj in NON_LOCK_ROLES and not bool(x.get("is_hard_negative")):
            y=dict(x); y["candidate_bucket"]="easy_negative"; y["candidate_label"]="non_verrou"
            y["queue_score"]=round(.55*(1-vm)+.30*fjs+.15*(1 if f["technical_difficulty_hits"]==0 else .4),6)
            easy_pool.append(y)

        if bool(x.get("is_hard_negative")) or (
            f["positive_hits"]==0 and f["technical_difficulty_hits"]>=1
            and (f["routine_negative_hits"]>=1 or fj in {"objectif","methode","resultat","parametre"})
        ):
            y=dict(x); y["candidate_bucket"]="hard_negative"; y["candidate_label"]="non_verrou"
            y["queue_score"]=round(.35*(1-abs(vm-.55))+.30*min(f["technical_difficulty_hits"],2)/2+.25*min(f["routine_negative_hits"],2)/2+.10*fjs,6)
            hard_pool.append(y)

    # IMPORTANT : on réserve d'abord les hard negatives, puis les easy,
    # puis les positive-like. Ainsi chaque bucket garde réellement sa taille.
    hard=diverse(hard_pool,hard_n)
    used={key(x) for x in hard}
    easy=diverse(easy_pool,easy_n,exclude=used)
    used.update(key(x) for x in easy)
    pos=diverse(pos_pool,pos_n,exclude=used)

    return pos+easy+hard, {
        "positive_like_pool":len(pos_pool),
        "easy_negative_pool":len(easy_pool),
        "hard_negative_pool":len(hard_pool),
    }

def write_csv(path,rows):
    fields=[
        "id","task","source","project_id","title","section_title","text",
        "candidate_bucket","candidate_label","model_label","model_score",
        "fastjudge_label","fastjudge_score","positive_hits",
        "routine_negative_hits","technical_difficulty_hits",
        "annotation_status","is_hard_negative","queue_score",
        "teacher_label","teacher_confidence","teacher_reason","human_label","human_comment"
    ]
    with Path(path).open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader()
        for r in rows:
            x=dict(r); x.setdefault("teacher_label",""); x.setdefault("teacher_confidence","")
            x.setdefault("teacher_reason",""); x.setdefault("human_label",""); x.setdefault("human_comment","")
            w.writerow(x)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default=r"C:\EnnoSmart")
    ap.add_argument("--fast-per-class",type=int,default=2500)
    ap.add_argument("--verrou-positive-like",type=int,default=8000)
    ap.add_argument("--verrou-easy-negatives",type=int,default=4500)
    ap.add_argument("--verrou-hard-negatives",type=int,default=4500)
    args=ap.parse_args()

    root=Path(args.root)
    cand=root/"train"/"data_v2"/"candidates"
    out=root/"train"/"data_v2"/"teacher_queues"
    out.mkdir(parents=True,exist_ok=True)

    fast=list(iter_jsonl(cand/"fastjudge_candidates.jsonl"))
    verrou=list(iter_jsonl(cand/"verrou_candidates.jsonl"))

    fq=build_fast(fast,args.fast_per_class)
    vq,pools=build_verrou(verrou,fast,args.verrou_positive_like,args.verrou_easy_negatives,args.verrou_hard_negatives)

    for x in fq: x["task"]="fastjudge"
    for x in vq: x["task"]="verrou_detector"

    write_csv(out/"fastjudge_teacher_queue_v2.csv",fq)
    write_csv(out/"verrou_teacher_queue_v2.csv",vq)

    report={
        "fastjudge":{
            "target_final":16000,
            "queue_total":len(fq),
            "queue_by_candidate_label":dict(Counter(str(x.get("candidate_label")) for x in fq)),
            "projects":len(set(str(x.get("project_id")) for x in fq)),
        },
        "verrou_detector":{
            "target_final":12000,
            "queue_total":len(vq),
            "queue_by_bucket":dict(Counter(str(x.get("candidate_bucket")) for x in vq)),
            "projects":len(set(str(x.get("project_id")) for x in vq)),
            "pools":pools,
        },
        "outputs":{
            "fastjudge":str(out/"fastjudge_teacher_queue_v2.csv"),
            "verrou_detector":str(out/"verrou_teacher_queue_v2.csv"),
        }
    }
    (out/"teacher_queue_report_v2.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
