from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

NON_LOCK_ROLES = {
    "objectif", "methode", "parametre", "resultat", "contribution", "bruit"
}

def iter_jsonl(path: Path):
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

def sf(v) -> float:
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0

def quantile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac

def key_of(r: Dict[str, Any]) -> str:
    h = str(r.get("text_hash") or "")
    if h:
        return h
    return f"{r.get('source')}::{r.get('project_id')}::{r.get('text')}"

def lock_feat(r: Dict[str, Any]) -> Dict[str, int]:
    f = r.get("lock_rule_features") or {}
    if not isinstance(f, dict):
        f = {}
    return {
        "positive_hits": int(f.get("positive_hits") or 0),
        "routine_negative_hits": int(f.get("routine_negative_hits") or 0),
        "technical_difficulty_hits": int(f.get("technical_difficulty_hits") or 0),
    }

def diverse_take(rows: List[Dict[str, Any]], n: int, reverse=True) -> List[Dict[str, Any]]:
    rows = sorted(rows, key=lambda r: sf(r.get("selection_score")), reverse=reverse)
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
        used = {str(x.get("id")) for x in out}
        out += [x for x in rest if str(x.get("id")) not in used][: n-len(out)]
    return out[:n]

def write_csv(path: Path, rows: List[Dict[str, Any]]):
    fields = [
        "id", "source", "project_id", "title", "section_title", "text",
        "candidate_bucket", "candidate_label",
        "verrou_model_score", "fastjudge_label", "fastjudge_score",
        "positive_hits", "routine_negative_hits", "technical_difficulty_hits",
        "is_hard_negative", "selection_score",
        "human_label", "human_comment"
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\EnnoSmart")
    ap.add_argument("--positives", type=int, default=2000)
    ap.add_argument("--easy-negatives", type=int, default=2000)
    ap.add_argument("--hard-negatives", type=int, default=2000)
    args = ap.parse_args()

    root = Path(args.root)
    cand = root / "train" / "data_v2" / "candidates"
    out_dir = root / "train" / "data_v2" / "annotation_batches"
    out_dir.mkdir(parents=True, exist_ok=True)

    fast = list(iter_jsonl(cand / "fastjudge_candidates.jsonl"))
    verrou = list(iter_jsonl(cand / "verrou_candidates.jsonl"))

    fast_by_key = {key_of(r): r for r in fast}

    scores = [sf(r.get("model_score")) for r in verrou]
    qs = {
        "q01": quantile(scores, .01),
        "q05": quantile(scores, .05),
        "q10": quantile(scores, .10),
        "q20": quantile(scores, .20),
        "q25": quantile(scores, .25),
        "q50": quantile(scores, .50),
        "q75": quantile(scores, .75),
        "q90": quantile(scores, .90),
        "q95": quantile(scores, .95),
        "q99": quantile(scores, .99),
    }

    enriched = []
    for r in verrou:
        item = dict(r)
        f = lock_feat(item)
        fr = fast_by_key.get(key_of(item), {})

        item["verrou_model_score"] = sf(item.get("model_score"))
        item["fastjudge_label"] = str(fr.get("suggested_label") or fr.get("model_label") or "")
        item["fastjudge_score"] = sf(fr.get("model_score"))
        item.update(f)
        enriched.append(item)

    # -------------------------
    # POSITIFS CANDIDATS
    # -------------------------
    positive_pool = []
    for r in enriched:
        vm = sf(r["verrou_model_score"])
        fj = str(r["fastjudge_label"])
        hits = int(r["positive_hits"])
        routine = int(r["routine_negative_hits"])

        # Plusieurs signaux indépendants.
        if hits >= 1 and routine == 0 and (
            vm >= qs["q75"] or fj in {"verrou", "limite"}
        ):
            x = dict(r)
            x["candidate_bucket"] = "positive_candidate"
            x["candidate_label"] = "verrou_evidence"
            x["selection_score"] = round(
                0.50 * vm
                + 0.25 * min(hits, 3) / 3
                + 0.15 * (1.0 if fj == "verrou" else 0.5 if fj == "limite" else 0.0)
                + 0.10 * sf(r["fastjudge_score"]),
                6,
            )
            positive_pool.append(x)

    # -------------------------
    # NEGATIFS FACILES
    # -------------------------
    # Important : on utilise les BAS scores relatifs, pas un seuil fixe 0.15.
    easy_pool = []
    easy_score_limit = qs["q25"]

    for r in enriched:
        vm = sf(r["verrou_model_score"])
        fj = str(r["fastjudge_label"])
        hits = int(r["positive_hits"])
        routine = int(r["routine_negative_hits"])
        tech = int(r["technical_difficulty_hits"])

        if (
            hits == 0
            and vm <= easy_score_limit
            and fj in NON_LOCK_ROLES
            and not bool(r.get("is_hard_negative"))
            and routine == 0
        ):
            x = dict(r)
            x["candidate_bucket"] = "easy_negative"
            x["candidate_label"] = "non_verrou"
            x["selection_score"] = round(
                0.55 * (1.0 - vm)
                + 0.25 * sf(r["fastjudge_score"])
                + 0.20 * (1.0 if tech == 0 else 0.5),
                6,
            )
            easy_pool.append(x)

    # Si Q25 ne suffit pas, on élargit automatiquement à Q50, toujours avec
    # absence de signal positif + rôle FastJudge non-verrou.
    if len(easy_pool) < args.easy_negatives:
        existing = {str(x.get("id")) for x in easy_pool}
        for r in enriched:
            if str(r.get("id")) in existing:
                continue
            vm = sf(r["verrou_model_score"])
            fj = str(r["fastjudge_label"])
            if (
                int(r["positive_hits"]) == 0
                and vm <= qs["q50"]
                and fj in NON_LOCK_ROLES
                and not bool(r.get("is_hard_negative"))
                and int(r["routine_negative_hits"]) == 0
            ):
                x = dict(r)
                x["candidate_bucket"] = "easy_negative"
                x["candidate_label"] = "non_verrou"
                x["selection_score"] = round(
                    0.55 * (1.0 - vm) + 0.25 * sf(r["fastjudge_score"]) + 0.20,
                    6,
                )
                easy_pool.append(x)

    # -------------------------
    # HARD NEGATIVES
    # -------------------------
    hard_pool = []
    for r in enriched:
        vm = sf(r["verrou_model_score"])
        hits = int(r["positive_hits"])
        routine = int(r["routine_negative_hits"])
        tech = int(r["technical_difficulty_hits"])

        if bool(r.get("is_hard_negative")) or (
            hits == 0 and tech >= 1 and (routine >= 1 or qs["q25"] < vm < qs["q75"])
        ):
            x = dict(r)
            x["candidate_bucket"] = "hard_negative"
            x["candidate_label"] = "non_verrou"
            # Priorité aux cas les plus ambigus autour de la médiane.
            distance_mid = abs(vm - qs["q50"])
            x["selection_score"] = round(
                0.45 * (1.0 / (1.0 + distance_mid))
                + 0.30 * min(tech, 2) / 2
                + 0.25 * min(routine, 2) / 2,
                6,
            )
            hard_pool.append(x)

    pos = diverse_take(positive_pool, args.positives)
    easy = diverse_take(easy_pool, args.easy_negatives)
    hard = diverse_take(hard_pool, args.hard_negatives)

    # Évite les doublons entre buckets.
    used = set()
    final = []
    for bucket in (pos, easy, hard):
        for x in bucket:
            k = key_of(x)
            if k in used:
                continue
            used.add(k)
            y = dict(x)
            y["human_label"] = ""
            y["human_comment"] = ""
            final.append(y)

    out_csv = out_dir / "verrou_annotation_batch_v2.csv"
    write_csv(out_csv, final)

    report = {
        "input_candidates": len(verrou),
        "score_quantiles": {k: round(v, 6) for k, v in qs.items()},
        "easy_negative_score_limit_initial_q25": round(easy_score_limit, 6),
        "pools": {
            "positive_pool": len(positive_pool),
            "easy_negative_pool": len(easy_pool),
            "hard_negative_pool": len(hard_pool),
        },
        "selected": {
            "positive_candidate": sum(x["candidate_bucket"] == "positive_candidate" for x in final),
            "easy_negative": sum(x["candidate_bucket"] == "easy_negative" for x in final),
            "hard_negative": sum(x["candidate_bucket"] == "hard_negative" for x in final),
            "total": len(final),
        },
        "sources": dict(Counter(str(x.get("source")) for x in final)),
        "projects": len(set(str(x.get("project_id")) for x in final)),
        "output": str(out_csv),
    }

    out_report = out_dir / "verrou_curation_report_v2.json"
    out_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
