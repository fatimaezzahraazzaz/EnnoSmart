from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from tqdm import tqdm

from common import ROLE_LABELS, clean_text, dedupe_rows, ensure_dirs, jsonl_iter, jsonl_write

ROLE_PATTERNS = {
    "objectif": [
        r"\bobjectif", r"\bvise [àa]\b", r"\ba pour but\b", r"\bnous cherchons [àa]\b",
        r"\bl'ambition\b", r"\bobjectif scientifique\b",
    ],
    "methode": [
        r"\bm[ée]thod", r"\bapproche\b", r"\bnous utilis", r"\bnous proposons d'utiliser\b",
        r"\bprotocole\b", r"\bsimulation", r"\bexp[ée]riment",
    ],
    "resultat": [
        r"\br[ée]sultat", r"\ba montr[ée]\b", r"\bnous avons obtenu\b",
        r"\bperformance", r"\bmesur[ée]e?s?\b", r"\bvalidation\b",
    ],
    "limite": [
        r"\blimite", r"\binsuffisan", r"\bne permet pas\b", r"\brestriction",
        r"\bfaiblesse", r"\bcependant\b",
    ],
    "contribution": [
        r"\bcontribution", r"\bnouvelle approche\b", r"\binnovation",
        r"\bnous proposons\b", r"\boriginalit", r"\bnovateur",
    ],
    "parametre": [
        r"\bparam[èe]tre", r"\bconfiguration", r"\bseuil",
        r"(?<!\w)\d+(?:[.,]\d+)?\s*(?:%|ms|s|hz|khz|mhz|ghz|°c|k|mm|cm|m)\b",
    ],
    "verrou": [
        r"\bverrou", r"\bincertitud", r"\binconnu", r"\bnon ma[iî]tris",
        r"\bimpossib", r"\breste [àa] (?:comprendre|d[ée]terminer|valider|d[ée]montrer)",
        r"\bnon transposable\b", r"\brepr[ée]sentativit[ée].{0,45}(?:non|incertain|limit)",
    ],
}

LOCK_POSITIVE_PATTERNS = [
    r"\bverrou",
    r"\bincertitud",
    r"\bph[ée]nom[èe]ne.{0,50}(?:inconnu|non compris|non ma[iî]tris)",
    r"\b(?:cause|m[ée]canisme|origine).{0,50}(?:inconnu|non d[ée]termin|incertain)",
    r"\bnon transposable\b",
    r"\bnon g[ée]n[ée]ralisable\b",
    r"\brepr[ée]sentativit[ée].{0,60}(?:non d[ée]montr|incertain|limit)",
    r"\breste [àa] (?:comprendre|d[ée]terminer|valider|d[ée]montrer|caract[ée]riser)",
    r"\bne permet pas.{0,60}(?:pr[ée]dire|d[ée]terminer|garantir|expliquer)",
    r"\babsence de (?:mod[èe]le|m[ée]thode).{0,60}(?:fiable|adapt|satisfais)",
]

ROUTINE_NEGATIVE_PATTERNS = [
    r"\b(?:erreur|probl[èe]me).{0,60}(?:corrig|r[ée]solu|r[ée]par)",
    r"\bil suffit de\b",
    r"\bmauvais param[ée]trage\b",
    r"\binstallation\b",
    r"\bmaintenance\b",
    r"\bconfiguration standard\b",
    r"\bcontrainte client\b",
    r"\bplanning\b",
    r"\bd[ée]lai\b",
]

TECHNICAL_DIFFICULTY_PATTERNS = [
    r"\bdifficult", r"\bprobl[èe]me", r"\bcomplex", r"\bcontrainte",
    r"\bperformance", r"\brobustesse", r"\bfiabilit", r"\bpr[ée]cision",
]

def rule_role(text: str) -> Tuple[str, float, Dict[str, int]]:
    low = clean_text(text).lower()
    counts = {}
    for role, patterns in ROLE_PATTERNS.items():
        counts[role] = sum(bool(re.search(p, low, re.I)) for p in patterns)
    best_role, best_count = max(counts.items(), key=lambda x: x[1])
    if best_count == 0:
        return "unknown", 0.0, counts
    confidence = min(0.95, 0.55 + 0.12 * best_count)
    return best_role, confidence, counts

def lock_rules(text: str) -> Dict[str, Any]:
    low = clean_text(text).lower()
    positive_hits = sum(bool(re.search(p, low, re.I)) for p in LOCK_POSITIVE_PATTERNS)
    routine_hits = sum(bool(re.search(p, low, re.I)) for p in ROUTINE_NEGATIVE_PATTERNS)
    technical_hits = sum(bool(re.search(p, low, re.I)) for p in TECHNICAL_DIFFICULTY_PATTERNS)
    return {
        "positive_hits": positive_hits,
        "routine_negative_hits": routine_hits,
        "technical_difficulty_hits": technical_hits,
    }

def try_load_models(root: Path):
    sys.path.insert(0, str(root))
    try:
        from modules.NLP.models import judge_passages_batch, detect_verrous_batch
        return judge_passages_batch, detect_verrous_batch, None
    except Exception as exc:
        return None, None, str(exc)

def batched(items: List[Any], n: int):
    for i in range(0, len(items), n):
        yield i, items[i:i+n]

def model_predictions(root: Path, texts: List[str], batch_size: int = 256):
    judge, verrou, err = try_load_models(root)
    role_preds = [None] * len(texts)
    lock_preds = [None] * len(texts)
    if judge is None or verrou is None:
        return role_preds, lock_preds, err

    for start, batch in tqdm(list(batched(texts, batch_size)), desc="Modèles EnnoSmart"):
        rp = judge(batch)
        vp = verrou(batch)
        role_preds[start:start+len(batch)] = rp
        lock_preds[start:start+len(batch)] = vp
    return role_preds, lock_preds, None

def status_fastjudge(model_label, model_score, rule_label, rule_score):
    if model_label in ROLE_LABELS and model_score >= 0.80 and model_label == rule_label:
        return "silver_strong", False
    if model_label in ROLE_LABELS and model_score >= 0.92:
        return "silver_model_high", True
    if model_label in ROLE_LABELS and rule_label not in {"unknown", model_label}:
        return "review_priority", True
    if model_label in ROLE_LABELS and model_score >= 0.60:
        return "review_priority", True
    if rule_label in ROLE_LABELS and rule_score >= 0.70:
        return "weak_only", True
    return "weak_only", True

def status_verrou(model_score: float, rules: Dict[str, Any]):
    pos = int(rules["positive_hits"])
    routine = int(rules["routine_negative_hits"])
    tech = int(rules["technical_difficulty_hits"])

    # Positif SILVER seulement quand modèle + règle indépendante sont d'accord.
    if model_score >= 0.80 and pos >= 1 and routine == 0:
        return "verrou_evidence", "silver_strong", False, False

    # Négatif fort.
    if model_score <= 0.15 and pos == 0:
        hard = tech >= 1
        return "non_verrou", "silver_strong", False, hard

    # Hard negative : ressemble à une difficulté mais sans véritable signal scientifique.
    if pos == 0 and tech >= 1 and routine >= 1:
        return "non_verrou", "review_priority", True, True

    # Zone ambiguë = précisément ce qu'il faut faire annoter.
    if 0.25 <= model_score <= 0.75 or pos >= 1:
        return "", "review_priority", True, tech >= 1

    return "", "weak_only", True, False

def write_review_csv(path: Path, rows: List[Dict[str, Any]], task: str):
    columns = [
        "id", "source", "project_id", "title", "section_title", "text",
        "model_label", "model_score", "rule_label", "rule_score",
        "suggested_label", "annotation_status", "is_hard_negative",
        "human_label", "human_comment",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()

    root = Path(args.root)
    dirs = ensure_dirs(root)

    raw_files = [
        dirs["anr"] / "anr_passages.jsonl",
        dirs["hal"] / "hal_passages.jsonl",
    ]
    rows = []
    for path in raw_files:
        rows.extend(list(jsonl_iter(path)))
    rows = dedupe_rows(rows)

    if not rows:
        raise SystemExit(
            "Aucun passage trouvé. Lance d'abord collect_anr.py et collect_hal.py."
        )

    texts = [clean_text(r.get("text")) for r in rows]
    role_preds, lock_preds, model_error = model_predictions(root, texts, args.batch_size)

    fast_rows = []
    verrou_rows = []

    for i, (row, text) in enumerate(tqdm(list(zip(rows, texts)), desc="Construction candidats")):
        role_rule, role_rule_score, role_rule_counts = rule_role(text)
        lock_rule = lock_rules(text)

        rp = role_preds[i] or {}
        vp = lock_preds[i] or {}

        model_role = str(rp.get("label") or rp.get("role") or "")
        try:
            model_role_score = float(rp.get("score") or rp.get("confidence") or 0.0)
        except Exception:
            model_role_score = 0.0

        try:
            model_lock_score = float(vp.get("score") or vp.get("confidence") or 0.0)
        except Exception:
            model_lock_score = 0.0

        fast_status, fast_review = status_fastjudge(
            model_role, model_role_score, role_rule, role_rule_score
        )

        base_id = f"{row.get('source','SRC')}::{row.get('project_id','')}::{row.get('text_hash','')[:12]}"

        fast_rows.append({
            **row,
            "id": "FJ::" + base_id,
            "task": "fastjudge_role",
            "model_label": model_role,
            "model_score": round(model_role_score, 6),
            "model_scores": rp.get("scores", {}),
            "rule_label": role_rule,
            "rule_score": round(role_rule_score, 6),
            "rule_counts": role_rule_counts,
            "suggested_label": model_role if model_role in ROLE_LABELS else role_rule,
            "annotation_status": fast_status,
            "needs_human_review": fast_review,
            "is_hard_negative": False,
            "human_label": "",
            "human_comment": "",
        })

        suggested, lock_status, lock_review, hard_neg = status_verrou(
            model_lock_score, lock_rule
        )
        verrou_rows.append({
            **row,
            "id": "VD::" + base_id,
            "task": "verrou_detection",
            "model_label": str(vp.get("label") or ""),
            "model_score": round(model_lock_score, 6),
            "model_scores": vp.get("scores", {}),
            "rule_label": "verrou_evidence" if lock_rule["positive_hits"] else "non_verrou",
            "rule_score": min(1.0, 0.45 + 0.18 * lock_rule["positive_hits"]),
            "lock_rule_features": lock_rule,
            "suggested_label": suggested,
            "annotation_status": lock_status,
            "needs_human_review": lock_review,
            "is_hard_negative": hard_neg,
            "human_label": "",
            "human_comment": "",
        })

    fast_out = dirs["candidates"] / "fastjudge_candidates.jsonl"
    verrou_out = dirs["candidates"] / "verrou_candidates.jsonl"
    jsonl_write(fast_out, fast_rows)
    jsonl_write(verrou_out, verrou_rows)

    # CSV : priorité aux exemples à vérifier, puis un échantillon du SILVER.
    fast_review_rows = sorted(
        fast_rows,
        key=lambda x: (
            0 if x["annotation_status"] == "review_priority" else 1,
            -float(x.get("model_score") or 0),
        ),
    )
    verrou_review_rows = sorted(
        verrou_rows,
        key=lambda x: (
            0 if x["annotation_status"] == "review_priority" else 1,
            0 if x["is_hard_negative"] else 1,
            -float(x.get("model_score") or 0),
        ),
    )
    write_review_csv(dirs["candidates"] / "fastjudge_review.csv", fast_review_rows, "fastjudge")
    write_review_csv(dirs["candidates"] / "verrou_review.csv", verrou_review_rows, "verrou")

    report = {
        "input_passages": len(rows),
        "model_load_error": model_error,
        "fastjudge_status": dict(Counter(x["annotation_status"] for x in fast_rows)),
        "fastjudge_suggested_labels": dict(Counter(x["suggested_label"] for x in fast_rows)),
        "verrou_status": dict(Counter(x["annotation_status"] for x in verrou_rows)),
        "verrou_suggested_labels": dict(Counter(x["suggested_label"] for x in verrou_rows)),
        "hard_negatives": sum(bool(x["is_hard_negative"]) for x in verrou_rows),
        "outputs": {
            "fastjudge": str(fast_out),
            "verrou": str(verrou_out),
        },
    }
    report_path = dirs["reports"] / "candidate_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[OK] Candidats Dataset V2")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
