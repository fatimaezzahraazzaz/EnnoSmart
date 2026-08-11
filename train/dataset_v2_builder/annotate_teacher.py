from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Any

from openai import OpenAI

FAST_LABELS = [
    "objectif","verrou","methode","parametre",
    "resultat","limite","contribution","bruit"
]

FAST_SYSTEM = """Tu es annotateur de données NLP pour des dossiers R&D/CIR.
Tu dois classer chaque passage dans UNE seule classe :
objectif = but visé ou résultat attendu
verrou = incertitude scientifique/technologique non résolue, obstacle de connaissance
methode = démarche, protocole, algorithme, expérimentation ou procédé utilisé
parametre = variable, réglage, configuration, seuil, caractéristique mesurée
resultat = résultat obtenu, observation, mesure, performance constatée
limite = faiblesse, restriction ou insuffisance d'une méthode/résultat, sans être forcément le verrou du projet
contribution = nouveauté, apport, solution ou amélioration proposée
bruit = administratif, contexte non pertinent, phrase trop vague ou hors catégories
Ne déduis pas un verrou simplement parce que le texte mentionne une difficulté.
Retourne uniquement du JSON valide.
"""

VERROU_SYSTEM = """Tu es annotateur strict de verrous R&D/CIR.
Un 'verrou_evidence' exige une incertitude scientifique ou technologique réelle :
un phénomène, comportement, causalité, représentativité, validité, prédiction ou connaissance
qui n'est pas maîtrisé/déterminé par l'état des connaissances disponibles.
Un simple bug, problème d'intégration, contrainte client, optimisation, réglage,
maintenance, difficulté d'implémentation ou objectif de performance est 'non_verrou'
s'il ne contient pas cette incertitude de connaissance.
Retourne uniquement du JSON valide.
"""

def chunks(rows, n):
    for i in range(0, len(rows), n):
        yield rows[i:i+n]

def read_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def existing_ids(path: Path):
    ids = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                x = json.loads(line)
                if x.get("id"):
                    ids.add(str(x["id"]))
            except Exception:
                pass
    return ids

def call_batch(client: OpenAI, model: str, task: str, batch: List[Dict[str,Any]]):
    if task == "fastjudge":
        system = FAST_SYSTEM
        user = {
            "task": "classify_fastjudge",
            "allowed_labels": FAST_LABELS,
            "items": [{"id": r["id"], "text": r["text"][:1800]} for r in batch],
            "output_schema": {
                "items":[
                    {"id":"string","label":"one_allowed_label","confidence":"0_to_1","reason":"short"}
                ]
            }
        }
    else:
        system = VERROU_SYSTEM
        user = {
            "task": "classify_verrou",
            "allowed_labels": ["verrou_evidence","non_verrou"],
            "items": [{"id": r["id"], "text": r["text"][:1800]} for r in batch],
            "output_schema": {
                "items":[
                    {"id":"string","label":"verrou_evidence_or_non_verrou","confidence":"0_to_1","reason":"short"}
                ]
            }
        }

    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type":"json_object"},
        messages=[
            {"role":"system","content":system},
            {"role":"user","content":json.dumps(user, ensure_ascii=False)}
        ]
    )
    content = resp.choices[0].message.content
    data = json.loads(content)
    return data.get("items", [])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\EnnoSmart")
    ap.add_argument("--task", choices=["fastjudge","verrou"], required=True)
    ap.add_argument("--model", default="gpt-4.1-mini")
    ap.add_argument("--batch-size", type=int, default=12)
    ap.add_argument("--max-items", type=int, default=0)
    args = ap.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY absent de l'environnement.")

    root = Path(args.root)
    qdir = root / "train" / "data_v2" / "teacher_queues"
    odir = root / "train" / "data_v2" / "teacher_labels"
    odir.mkdir(parents=True, exist_ok=True)

    if args.task == "fastjudge":
        src = qdir / "fastjudge_teacher_queue.csv"
        out = odir / "fastjudge_teacher_labels.jsonl"
    else:
        src = qdir / "verrou_teacher_queue.csv"
        out = odir / "verrou_teacher_labels.jsonl"

    rows = read_csv(src)
    if args.max_items > 0:
        rows = rows[:args.max_items]

    done = existing_ids(out)
    rows = [r for r in rows if str(r.get("id")) not in done]

    client = OpenAI()
    print(f"[Teacher] task={args.task} model={args.model} restant={len(rows)}")

    with out.open("a", encoding="utf-8") as f:
        for batch in chunks(rows, args.batch_size):
            for attempt in range(4):
                try:
                    preds = call_batch(client, args.model, args.task, batch)
                    byid = {str(x.get("id")): x for x in preds if x.get("id")}
                    for row in batch:
                        pred = byid.get(str(row["id"]))
                        if not pred:
                            continue
                        rec = {
                            "id": row["id"],
                            "teacher_label": str(pred.get("label") or ""),
                            "teacher_confidence": float(pred.get("confidence") or 0),
                            "teacher_reason": str(pred.get("reason") or "")[:300],
                            "task": args.task,
                            "model": args.model,
                        }
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        f.flush()
                    break
                except Exception as exc:
                    if attempt == 3:
                        print("[ERREUR batch]", repr(exc))
                    else:
                        time.sleep(2 ** attempt)

    print("[OK]", out)

if __name__ == "__main__":
    main()
