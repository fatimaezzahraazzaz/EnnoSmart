from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

SEED = 42
RNG = random.Random(SEED)

FAST_LABELS = [
    "objectif", "verrou", "methode", "parametre",
    "resultat", "limite", "contribution", "bruit"
]

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

def sf(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0

def project_diverse_take(rows: List[Dict[str, Any]], n: int, score_key: str) -> List[Dict[str, Any]]:
    """
    Prend d'abord au plus un exemple par projet, puis complète.
    Cela évite qu'un gros projet ANR domine le batch.
    """
    rows = sorted(rows, key=lambda x: sf(x.get(score_key)), reverse=True)
    first = []
    rest = []
    seen_projects = set()

    for r in rows:
        pid = str(r.get("project_id") or "")
        if pid and pid not in seen_projects:
            seen_projects.add(pid)
            first.append(r)
        else:
            rest.append(r)

    selected = first[:n]
    if len(selected) < n:
        selected.extend(rest[: n - len(selected)])
    return selected[:n]

def fastjudge_priority(r: Dict[str, Any]) -> float:
    model = sf(r.get("model_score"))
    rule = sf(r.get("rule_score"))
    agree = 1.0 if str(r.get("model_label")) == str(r.get("rule_label")) else 0.0
    strong = 1.0 if r.get("annotation_status") == "silver_strong" else 0.0
    high = 1.0 if r.get("annotation_status") == "silver_model_high" else 0.0
    # On ne veut pas seulement les exemples "faciles".
    return 0.45 * model + 0.20 * rule + 0.20 * agree + 0.10 * strong + 0.05 * high

def build_fastjudge_batch(rows: List[Dict[str, Any]], per_class: int) -> List[Dict[str, Any]]:
    by_label = defaultdict(list)

    for r in rows:
        label = str(r.get("suggested_label") or "").strip()
        if label not in FAST_LABELS:
            continue

        item = dict(r)
        item["curation_score"] = round(fastjudge_priority(item), 6)
        by_label[label].append(item)

    selected = []
    for label in FAST_LABELS:
        pool = by_label[label]

        # Priorité aux silver_strong, puis silver_model_high,
        # puis aux review_priority les plus informatifs.
        strong = [x for x in pool if x.get("annotation_status") == "silver_strong"]
        high = [x for x in pool if x.get("annotation_status") == "silver_model_high"]
        review = [x for x in pool if x.get("annotation_status") == "review_priority"]
        weak = [x for x in pool if x.get("annotation_status") == "weak_only"]

        # Mélange contrôlé : 55% très fiables, 30% difficiles, 15% autres.
        n_strong = int(per_class * 0.40)
        n_high = int(per_class * 0.15)
        n_review = int(per_class * 0.30)
        n_weak = per_class - n_strong - n_high - n_review

        chosen = []
        chosen += project_diverse_take(strong, n_strong, "curation_score")
        chosen += project_diverse_take(high, n_high, "curation_score")
        chosen += project_diverse_take(review, n_review, "curation_score")
        chosen += project_diverse_take(weak, n_weak, "curation_score")

        # Complète si une catégorie manque.
        used = {str(x.get("id")) for x in chosen}
        remainder = [x for x in sorted(pool, key=lambda x: sf(x.get("curation_score")), reverse=True)
                     if str(x.get("id")) not in used]
        if len(chosen) < per_class:
            chosen.extend(remainder[: per_class - len(chosen)])

        for x in chosen[:per_class]:
            x = dict(x)
            x["target_task"] = "fastjudge"
            x["candidate_label"] = label
            x["human_label"] = ""
            x["human_comment"] = ""
            selected.append(x)

    return selected

def lock_features(r: Dict[str, Any]) -> Dict[str, Any]:
    feat = r.get("lock_rule_features") or {}
    if not isinstance(feat, dict):
        feat = {}
    return {
        "positive_hits": int(feat.get("positive_hits") or 0),
        "routine_negative_hits": int(feat.get("routine_negative_hits") or 0),
        "technical_difficulty_hits": int(feat.get("technical_difficulty_hits") or 0),
    }

def verrou_positive_score(r: Dict[str, Any]) -> float:
    f = lock_features(r)
    m = sf(r.get("model_score"))
    fast_label = str(r.get("model_label") or "")
    # model_label ici est celui du verrou detector ; on utilise surtout le score.
    return (
        0.55 * m
        + 0.20 * min(f["positive_hits"], 3) / 3
        + 0.10 * min(f["technical_difficulty_hits"], 2) / 2
        - 0.15 * min(f["routine_negative_hits"], 2) / 2
    )

def verrou_negative_score(r: Dict[str, Any]) -> float:
    f = lock_features(r)
    m = sf(r.get("model_score"))
    return (
        0.60 * (1.0 - m)
        + 0.20 * (1.0 if f["positive_hits"] == 0 else 0.0)
        + 0.20 * min(f["routine_negative_hits"], 2) / 2
    )

def build_verrou_batch(rows: List[Dict[str, Any]], positives: int, easy_negatives: int, hard_negatives: int):
    pos_pool = []
    easy_pool = []
    hard_pool = []

    for r in rows:
        f = lock_features(r)
        m = sf(r.get("model_score"))

        item = dict(r)

        # Positifs candidats : on exige au moins un signal lexical indépendant
        # ou un score modèle vraiment élevé.
        if (f["positive_hits"] >= 1 and m >= 0.35) or m >= 0.80:
            item["curation_score"] = round(verrou_positive_score(item), 6)
            item["candidate_label"] = "verrou_evidence"
            item["candidate_bucket"] = "positive_candidate"
            pos_pool.append(item)

        # Négatifs faciles : aucun signal positif et score très bas.
        if f["positive_hits"] == 0 and m <= 0.15:
            item2 = dict(r)
            item2["curation_score"] = round(verrou_negative_score(item2), 6)
            item2["candidate_label"] = "non_verrou"
            item2["candidate_bucket"] = "easy_negative"
            easy_pool.append(item2)

        # Hard negatives : textes qui ressemblent à des difficultés,
        # mais comportent un signal routine/correction ou sont marqués par le builder.
        if bool(r.get("is_hard_negative")) or (
            f["technical_difficulty_hits"] >= 1
            and f["positive_hits"] == 0
            and (f["routine_negative_hits"] >= 1 or 0.15 < m < 0.70)
        ):
            item3 = dict(r)
            item3["curation_score"] = round(
                0.45 * (1.0 - abs(m - 0.5) * 2)
                + 0.30 * min(f["technical_difficulty_hits"], 2) / 2
                + 0.25 * min(f["routine_negative_hits"], 2) / 2,
                6,
            )
            item3["candidate_label"] = "non_verrou"
            item3["candidate_bucket"] = "hard_negative"
            hard_pool.append(item3)

    pos_sel = project_diverse_take(pos_pool, positives, "curation_score")
    easy_sel = project_diverse_take(easy_pool, easy_negatives, "curation_score")
    hard_sel = project_diverse_take(hard_pool, hard_negatives, "curation_score")

    selected = []
    for row in pos_sel + easy_sel + hard_sel:
        item = dict(row)
        item["target_task"] = "verrou_detector"
        item["human_label"] = ""
        item["human_comment"] = ""
        selected.append(item)

    return selected, {
        "positive_pool": len(pos_pool),
        "easy_negative_pool": len(easy_pool),
        "hard_negative_pool": len(hard_pool),
        "positive_selected": len(pos_sel),
        "easy_negative_selected": len(easy_sel),
        "hard_negative_selected": len(hard_sel),
    }

def write_csv(path: Path, rows: List[Dict[str, Any]], task: str):
    if task == "fastjudge":
        fields = [
            "id", "source", "project_id", "title", "section_title", "text",
            "candidate_label", "model_label", "model_score",
            "rule_label", "rule_score", "annotation_status",
            "curation_score", "human_label", "human_comment",
        ]
    else:
        fields = [
            "id", "source", "project_id", "title", "section_title", "text",
            "candidate_bucket", "candidate_label",
            "model_label", "model_score", "annotation_status",
            "is_hard_negative", "curation_score",
            "human_label", "human_comment",
        ]

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\EnnoSmart")
    ap.add_argument("--fast-per-class", type=int, default=500)
    ap.add_argument("--verrou-positives", type=int, default=2000)
    ap.add_argument("--verrou-easy-negatives", type=int, default=2000)
    ap.add_argument("--verrou-hard-negatives", type=int, default=2000)
    args = ap.parse_args()

    root = Path(args.root)
    cand = root / "train" / "data_v2" / "candidates"
    out = root / "train" / "data_v2" / "annotation_batches"
    out.mkdir(parents=True, exist_ok=True)

    fast_rows = list(iter_jsonl(cand / "fastjudge_candidates.jsonl"))
    verrou_rows = list(iter_jsonl(cand / "verrou_candidates.jsonl"))

    fast_batch = build_fastjudge_batch(fast_rows, args.fast_per_class)
    verrou_batch, verrou_stats = build_verrou_batch(
        verrou_rows,
        args.verrou_positives,
        args.verrou_easy_negatives,
        args.verrou_hard_negatives,
    )

    write_csv(out / "fastjudge_annotation_batch.csv", fast_batch, "fastjudge")
    write_csv(out / "verrou_annotation_batch.csv", verrou_batch, "verrou")

    report = {
        "fastjudge": {
            "input_candidates": len(fast_rows),
            "selected_total": len(fast_batch),
            "selected_labels": dict(Counter(x["candidate_label"] for x in fast_batch)),
            "selected_sources": dict(Counter(x.get("source") for x in fast_batch)),
            "selected_projects": len(set(str(x.get("project_id")) for x in fast_batch)),
        },
        "verrou_detector": {
            "input_candidates": len(verrou_rows),
            **verrou_stats,
            "selected_total": len(verrou_batch),
            "selected_buckets": dict(Counter(x.get("candidate_bucket") for x in verrou_batch)),
            "selected_sources": dict(Counter(x.get("source") for x in verrou_batch)),
            "selected_projects": len(set(str(x.get("project_id")) for x in verrou_batch)),
        },
        "outputs": {
            "fastjudge": str(out / "fastjudge_annotation_batch.csv"),
            "verrou": str(out / "verrou_annotation_batch.csv"),
        }
    }

    (out / "curation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
