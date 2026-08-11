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

def iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                x = json.loads(line)
                if isinstance(x, dict):
                    yield x
            except Exception:
                pass

def sf(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0

def text_key(r: Dict[str, Any]) -> str:
    return str(r.get("text_hash") or f"{r.get('source')}::{r.get('project_id')}::{r.get('text')}")

def diverse_take(rows: List[Dict[str, Any]], n: int, score_key="queue_score"):
    rows = sorted(rows, key=lambda r: sf(r.get(score_key)), reverse=True)
    first, rest, seen = [], [], set()
    for r in rows:
        pid = str(r.get("project_id") or "")
        if pid and pid not in seen:
            seen.add(pid)
            first.append(r)
        else:
            rest.append(r)
    out = first[:n]
    if len(out) < n:
        used = {text_key(x) for x in out}
        out += [x for x in rest if text_key(x) not in used][:n-len(out)]
    return out[:n]

def lock_feat(r):
    f = r.get("lock_rule_features") or {}
    if not isinstance(f, dict):
        f = {}
    return {
        "positive_hits": int(f.get("positive_hits") or 0),
        "routine_negative_hits": int(f.get("routine_negative_hits") or 0),
        "technical_difficulty_hits": int(f.get("technical_difficulty_hits") or 0),
    }

def build_fast_queue(rows, per_class):
    by = defaultdict(list)
    for r in rows:
        lab = str(r.get("suggested_label") or "")
        if lab not in FAST_LABELS:
            continue
        x = dict(r)
        ms = sf(x.get("model_score"))
        rs = sf(x.get("rule_score"))
        agree = 1.0 if str(x.get("model_label")) == str(x.get("rule_label")) else 0.0
        status_bonus = {
            "silver_strong": 0.20,
            "silver_model_high": 0.15,
            "review_priority": 0.08,
            "weak_only": 0.0,
        }.get(str(x.get("annotation_status")), 0.0)
        x["candidate_label"] = lab
        x["queue_score"] = round(0.45*ms + 0.20*rs + 0.20*agree + status_bonus, 6)
        by[lab].append(x)

    selected = []
    for lab in FAST_LABELS:
        # On demande plus que 2k pour laisser le teacher rejeter/corriger.
        sel = diverse_take(by[lab], per_class)
        for x in sel:
            x = dict(x)
            x["task"] = "fastjudge"
            selected.append(x)
    return selected

def build_verrou_queue(verrou_rows, fast_rows, positive_like, easy_neg, hard_neg):
    fast_by_key = {text_key(r): r for r in fast_rows}
    positives, easy, hard = [], [], []

    for r in verrou_rows:
        x = dict(r)
        f = lock_feat(x)
        fr = fast_by_key.get(text_key(x), {})
        vm = sf(x.get("model_score"))
        fj = str(fr.get("suggested_label") or fr.get("model_label") or "")
        fjs = sf(fr.get("model_score"))
        x["fastjudge_label"] = fj
        x["fastjudge_score"] = fjs
        x.update(f)

        # Positifs à faire juger par un teacher indépendant :
        # on élargit volontairement pour atteindre 5k vrais positifs après filtrage.
        if (
            f["positive_hits"] >= 1
            or fj in {"verrou", "limite"}
            or vm >= 0.70
        ):
            x1 = dict(x)
            x1["candidate_bucket"] = "positive_like"
            x1["candidate_label"] = "verrou_evidence"
            x1["queue_score"] = round(
                0.42*vm
                + 0.20*min(f["positive_hits"], 3)/3
                + 0.18*(1.0 if fj=="verrou" else 0.6 if fj=="limite" else 0.0)
                + 0.10*fjs
                + 0.10*(1.0 if f["technical_difficulty_hits"] else 0.0),
                6
            )
            positives.append(x1)

        # Négatif facile : aucun signal de verrou et rôle sémantique non-verrou.
        if (
            f["positive_hits"] == 0
            and fj in {"objectif","methode","parametre","resultat","contribution","bruit"}
            and not bool(x.get("is_hard_negative"))
        ):
            x2 = dict(x)
            x2["candidate_bucket"] = "easy_negative"
            x2["candidate_label"] = "non_verrou"
            x2["queue_score"] = round(
                0.55*(1.0-vm) + 0.30*fjs + 0.15*(1.0 if f["technical_difficulty_hits"]==0 else 0.4),
                6
            )
            easy.append(x2)

        # Hard negative : ressemble à une difficulté mais probablement pas un verrou.
        if (
            bool(x.get("is_hard_negative"))
            or (
                f["positive_hits"] == 0
                and f["technical_difficulty_hits"] >= 1
                and (f["routine_negative_hits"] >= 1 or fj in {"objectif","methode","resultat","parametre"})
            )
        ):
            x3 = dict(x)
            x3["candidate_bucket"] = "hard_negative"
            x3["candidate_label"] = "non_verrou"
            x3["queue_score"] = round(
                0.35*(1.0-abs(vm-0.55))
                + 0.30*min(f["technical_difficulty_hits"],2)/2
                + 0.25*min(f["routine_negative_hits"],2)/2
                + 0.10*fjs,
                6
            )
            hard.append(x3)

    pos_sel = diverse_take(positives, positive_like)
    easy_sel = diverse_take(easy, easy_neg)
    hard_sel = diverse_take(hard, hard_neg)

    used = set()
    final = []
    for bucket in (pos_sel, easy_sel, hard_sel):
        for x in bucket:
            k = text_key(x)
            if k in used:
                continue
            used.add(k)
            y = dict(x)
            y["task"] = "verrou_detector"
            final.append(y)
    return final, {
        "positive_like_pool": len(positives),
        "easy_negative_pool": len(easy),
        "hard_negative_pool": len(hard),
    }

def write_csv(path: Path, rows: List[Dict[str, Any]]):
    fields = [
        "id","task","source","project_id","title","section_title","text",
        "candidate_bucket","candidate_label","model_label","model_score",
        "fastjudge_label","fastjudge_score",
        "positive_hits","routine_negative_hits","technical_difficulty_hits",
        "annotation_status","is_hard_negative","queue_score",
        "teacher_label","teacher_confidence","teacher_reason","human_label","human_comment"
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            x = dict(r)
            x.setdefault("teacher_label","")
            x.setdefault("teacher_confidence","")
            x.setdefault("teacher_reason","")
            x.setdefault("human_label","")
            x.setdefault("human_comment","")
            w.writerow(x)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\EnnoSmart")
    ap.add_argument("--fast-per-class", type=int, default=2500)
    ap.add_argument("--verrou-positive-like", type=int, default=8000)
    ap.add_argument("--verrou-easy-negatives", type=int, default=4500)
    ap.add_argument("--verrou-hard-negatives", type=int, default=4500)
    args = ap.parse_args()

    root = Path(args.root)
    cand = root / "train" / "data_v2" / "candidates"
    out = root / "train" / "data_v2" / "teacher_queues"
    out.mkdir(parents=True, exist_ok=True)

    fast = list(iter_jsonl(cand / "fastjudge_candidates.jsonl"))
    verrou = list(iter_jsonl(cand / "verrou_candidates.jsonl"))

    fq = build_fast_queue(fast, args.fast_per_class)
    vq, pools = build_verrou_queue(
        verrou, fast,
        args.verrou_positive_like,
        args.verrou_easy_negatives,
        args.verrou_hard_negatives
    )

    write_csv(out / "fastjudge_teacher_queue.csv", fq)
    write_csv(out / "verrou_teacher_queue.csv", vq)

    report = {
        "fastjudge": {
            "target_final": 16000,
            "queue_total": len(fq),
            "queue_by_candidate_label": dict(Counter(str(x.get("candidate_label")) for x in fq)),
            "projects": len(set(str(x.get("project_id")) for x in fq)),
        },
        "verrou_detector": {
            "target_final": 12000,
            "desired_final": {
                "verrou_evidence": 5000,
                "non_verrou_total": 7000,
                "of_which_hard_negative_approx": 3500
            },
            "queue_total": len(vq),
            "queue_by_bucket": dict(Counter(str(x.get("candidate_bucket")) for x in vq)),
            "projects": len(set(str(x.get("project_id")) for x in vq)),
            "pools": pools,
        },
        "outputs": {
            "fastjudge": str(out / "fastjudge_teacher_queue.csv"),
            "verrou_detector": str(out / "verrou_teacher_queue.csv"),
        }
    }
    (out / "teacher_queue_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
