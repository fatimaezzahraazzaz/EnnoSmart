from __future__ import annotations

import argparse
import csv
import json
import random
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import requests

OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"

FAST_LABELS = [
    "objectif", "verrou", "methode", "parametre",
    "resultat", "limite", "contribution", "bruit"
]

FAST_SYSTEM = """Tu es un annotateur expert de passages issus de projets R&D/CIR.
Classe chaque passage dans UNE seule catégorie.

- objectif : but, finalité ou résultat attendu.
- verrou : incertitude scientifique/technologique réellement non résolue, obstacle de connaissance.
- methode : démarche, protocole, algorithme, expérimentation, procédé ou moyen utilisé.
- parametre : variable, réglage, configuration, seuil ou caractéristique mesurée.
- resultat : résultat obtenu, observation, mesure ou performance constatée.
- limite : faiblesse, restriction ou insuffisance connue, sans être forcément un verrou.
- contribution : apport, nouveauté, innovation ou solution proposée.
- bruit : administratif, contexte non pertinent, trop vague ou hors catégories.

Règles strictes :
1. Une difficulté ou une limite n'est PAS automatiquement un verrou.
2. Pour "verrou", il faut une incertitude de connaissance réellement non maîtrisée.
3. Choisis le sens principal du passage.
4. N'utilise que les labels autorisés.
"""

VERROU_SYSTEM = """Tu es un annotateur expert et strict des verrous R&D/CIR.

- verrou_evidence : vraie incertitude scientifique/technologique non maîtrisée
  (causalité, comportement, validité, prédiction, généralisation, représentativité,
  transposabilité, phénomène non expliqué, connaissance manquante).
- non_verrou : objectif, méthode, résultat, bug, intégration, maintenance, réglage,
  optimisation classique, contrainte client, difficulté d'implémentation,
  problème résolu ou limite connue sans incertitude scientifique réelle.

Une simple difficulté ou l'expression "surmonter une limite" ne suffit jamais à
constituer un verrou. N'utilise que verrou_evidence ou non_verrou.
"""

def read_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def stratified_fast(rows: List[Dict[str, Any]], per_class: int, seed: int):
    rng = random.Random(seed)
    by = defaultdict(list)
    for r in rows:
        lab = str(r.get("candidate_label") or "")
        if lab in FAST_LABELS:
            by[lab].append(r)

    out = []
    for lab in FAST_LABELS:
        pool = by[lab][:]
        rng.shuffle(pool)
        out.extend(pool[:per_class])
    rng.shuffle(out)
    return out

def stratified_verrou(rows: List[Dict[str, Any]], positive: int, easy: int, hard: int, seed: int):
    rng = random.Random(seed)
    by = defaultdict(list)
    for r in rows:
        by[str(r.get("candidate_bucket") or "")].append(r)

    out = []
    for bucket, n in [
        ("positive_like", positive),
        ("easy_negative", easy),
        ("hard_negative", hard),
    ]:
        pool = by[bucket][:]
        rng.shuffle(pool)
        out.extend(pool[:n])
    rng.shuffle(out)
    return out

def request_batch(model: str, task: str, batch: List[Dict[str, Any]], timeout: int):
    system = FAST_SYSTEM if task == "fastjudge" else VERROU_SYSTEM
    allowed = FAST_LABELS if task == "fastjudge" else ["verrou_evidence", "non_verrou"]

    # Short numeric indices are much more reliable than asking the LLM
    # to reproduce long hashes/IDs.
    items = [
        {"idx": i + 1, "text": str(r.get("text") or "")[:1800]}
        for i, r in enumerate(batch)
    ]

    prompt = {
        "allowed_labels": allowed,
        "items": items,
        "instruction": (
            "Retourne exactement un objet pour chaque idx fourni. "
            "Ne saute aucun idx."
        ),
        "output_example": {
            "items": [
                {
                    "idx": 1,
                    "label": allowed[0],
                    "confidence": 0.95,
                    "reason": "raison courte"
                }
            ]
        }
    }

    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_ctx": 8192},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}
        ],
    }

    r = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=timeout)
    r.raise_for_status()
    content = r.json().get("message", {}).get("content", "")
    data = json.loads(content)
    preds = data.get("items", [])
    return preds if isinstance(preds, list) else []

def annotate_one_by_one(model: str, task: str, row: Dict[str, Any], timeout: int):
    preds = request_batch(model, task, [row], timeout)
    return preds[0] if preds else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\EnnoSmart")
    ap.add_argument("--task", choices=["fastjudge", "verrou"], required=True)
    ap.add_argument("--model", default="qwen3:4b-instruct")
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fast-per-class", type=int, default=12)  # 96 total
    ap.add_argument("--verrou-positive", type=int, default=40)
    ap.add_argument("--verrou-easy", type=int, default=28)
    ap.add_argument("--verrou-hard", type=int, default=28)     # 96 total
    args = ap.parse_args()

    root = Path(args.root)
    qdir = root / "train" / "data_v2" / "teacher_queues"
    outdir = root / "train" / "data_v2" / "teacher_labels_local_stratified"
    outdir.mkdir(parents=True, exist_ok=True)

    if args.task == "fastjudge":
        src = qdir / "fastjudge_teacher_queue_v2.csv"
        rows = stratified_fast(read_csv(src), args.fast_per_class, args.seed)
        out = outdir / "fastjudge_stratified_qwen3.jsonl"
    else:
        src = qdir / "verrou_teacher_queue_v2.csv"
        rows = stratified_verrou(
            read_csv(src),
            args.verrou_positive,
            args.verrou_easy,
            args.verrou_hard,
            args.seed,
        )
        out = outdir / "verrou_stratified_qwen3.jsonl"

    # Fresh deterministic test file.
    if out.exists():
        out.unlink()

    allowed = set(FAST_LABELS if args.task == "fastjudge"
                  else ["verrou_evidence", "non_verrou"])

    print(f"[STRATIFIED TEST] task={args.task}")
    print(f"[STRATIFIED TEST] model={args.model}")
    print(f"[STRATIFIED TEST] selected={len(rows)}")
    print(f"[STRATIFIED TEST] output={out}")

    written = 0
    missing_total = 0

    with out.open("w", encoding="utf-8") as f:
        for start in range(0, len(rows), args.batch_size):
            batch = rows[start:start + args.batch_size]
            batch_no = start // args.batch_size + 1

            try:
                preds = request_batch(args.model, args.task, batch, args.timeout)
            except Exception as exc:
                print(f"[BATCH ERROR] {batch_no}: {exc!r}")
                preds = []

            by_idx = {}
            for p in preds:
                try:
                    idx = int(p.get("idx"))
                except Exception:
                    continue
                if 1 <= idx <= len(batch):
                    by_idx[idx] = p

            # Automatic fallback for any missing item.
            for local_idx, row in enumerate(batch, start=1):
                pred = by_idx.get(local_idx)
                if pred is None:
                    missing_total += 1
                    try:
                        pred = annotate_one_by_one(args.model, args.task, row, args.timeout)
                    except Exception:
                        pred = None

                if pred is None:
                    continue

                label = str(pred.get("label") or "").strip()
                if label not in allowed:
                    continue

                try:
                    conf = float(pred.get("confidence") or 0)
                except Exception:
                    conf = 0.0
                conf = max(0.0, min(1.0, conf))

                rec = {
                    "id": str(row.get("id") or ""),
                    "task": args.task,
                    "teacher_model": args.model,
                    "candidate_label": str(row.get("candidate_label") or ""),
                    "candidate_bucket": str(row.get("candidate_bucket") or ""),
                    "teacher_label": label,
                    "teacher_confidence": conf,
                    "teacher_reason": str(pred.get("reason") or "")[:400],
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                written += 1

            print(f"[OK] batch={batch_no} requested={len(batch)} total_written={written}")

    print(f"[TERMINE] selected={len(rows)} written={written} fallback_missing={missing_total}")
    print(out)

if __name__ == "__main__":
    main()
