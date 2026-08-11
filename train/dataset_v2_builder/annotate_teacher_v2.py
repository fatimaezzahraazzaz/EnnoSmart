from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from openai import OpenAI

FAST_LABELS = [
    "objectif","verrou","methode","parametre",
    "resultat","limite","contribution","bruit"
]

FAST_SYSTEM = """Tu es annotateur expert de passages issus de projets R&D/CIR.
Tu dois choisir UNE seule classe parmi :
objectif = but, finalité ou résultat attendu
verrou = incertitude scientifique/technologique non résolue, obstacle de connaissance
methode = démarche, protocole, algorithme, expérimentation, procédé ou moyen utilisé
parametre = variable, réglage, configuration, seuil ou caractéristique mesurée
resultat = résultat obtenu, observation, mesure ou performance constatée
limite = faiblesse, restriction ou insuffisance d'une méthode/résultat, sans être forcément le verrou du projet
contribution = apport, nouveauté, innovation ou solution proposée
bruit = administratif, contexte non pertinent, passage trop vague ou hors catégories

Important :
- une simple difficulté n'est pas automatiquement un verrou ;
- une limite connue n'est pas automatiquement un verrou ;
- classe selon le sens principal du passage.
Réponds uniquement en JSON valide.
"""

VERROU_SYSTEM = """Tu es annotateur expert et strict pour la détection de verrous R&D/CIR.

verrou_evidence = le passage contient une vraie incertitude scientifique ou technologique :
phénomène, causalité, représentativité, validité, prédiction, comportement ou connaissance
qui n'est pas encore maîtrisé, expliqué, déterminé ou transposable avec l'état des connaissances disponibles.

non_verrou = objectif, méthode, résultat, contrainte client, bug, intégration,
maintenance, réglage, optimisation classique, simple difficulté d'implémentation,
problème déjà résolu, ou limite connue sans incertitude scientifique réelle.

Ne transforme jamais une simple difficulté technique en verrou.
Réponds uniquement en JSON valide.
"""

def read_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def done_ids(path: Path):
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

def batches(rows, n):
    for i in range(0, len(rows), n):
        yield rows[i:i+n]

def annotate_batch(client, model, task, rows):
    if task == "fastjudge":
        system = FAST_SYSTEM
        payload = {
            "task": "fastjudge_classification",
            "allowed_labels": FAST_LABELS,
            "items": [{"id": r["id"], "text": r["text"][:1800]} for r in rows],
            "required_output": {
                "items": [
                    {"id": "same id", "label": "one allowed label",
                     "confidence": "number 0..1", "reason": "short reason"}
                ]
            }
        }
    else:
        system = VERROU_SYSTEM
        payload = {
            "task": "verrou_detection",
            "allowed_labels": ["verrou_evidence", "non_verrou"],
            "items": [{"id": r["id"], "text": r["text"][:1800]} for r in rows],
            "required_output": {
                "items": [
                    {"id": "same id", "label": "verrou_evidence or non_verrou",
                     "confidence": "number 0..1", "reason": "short reason"}
                ]
            }
        }

    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        ]
    )
    data = json.loads(response.choices[0].message.content)
    return data.get("items", [])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\EnnoSmart")
    ap.add_argument("--task", choices=["fastjudge", "verrou"], required=True)
    ap.add_argument("--model", default="gpt-4.1-mini")
    ap.add_argument("--batch-size", type=int, default=10)
    ap.add_argument("--max-items", type=int, default=100)
    args = ap.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY absent de l'environnement.")

    root = Path(args.root)
    qdir = root / "train" / "data_v2" / "teacher_queues"
    odir = root / "train" / "data_v2" / "teacher_labels_v2"
    odir.mkdir(parents=True, exist_ok=True)

    if args.task == "fastjudge":
        src = qdir / "fastjudge_teacher_queue_v2.csv"
        out = odir / "fastjudge_teacher_labels_v2.jsonl"
    else:
        src = qdir / "verrou_teacher_queue_v2.csv"
        out = odir / "verrou_teacher_labels_v2.jsonl"

    if not src.exists():
        raise SystemExit(f"File introuvable : {src}")

    rows = read_csv(src)
    if args.max_items > 0:
        rows = rows[:args.max_items]

    already = done_ids(out)
    rows = [r for r in rows if str(r.get("id")) not in already]

    print(f"[Teacher V2] task={args.task}")
    print(f"[Teacher V2] model={args.model}")
    print(f"[Teacher V2] restant={len(rows)}")
    print(f"[Teacher V2] source={src}")

    client = OpenAI()

    with out.open("a", encoding="utf-8") as f:
        for bi, batch in enumerate(batches(rows, args.batch_size), start=1):
            success = False
            for attempt in range(4):
                try:
                    preds = annotate_batch(client, args.model, args.task, batch)
                    by_id = {str(x.get("id")): x for x in preds if x.get("id")}

                    for row in batch:
                        p = by_id.get(str(row["id"]))
                        if not p:
                            continue
                        rec = {
                            "id": row["id"],
                            "task": args.task,
                            "teacher_model": args.model,
                            "candidate_label": row.get("candidate_label", ""),
                            "candidate_bucket": row.get("candidate_bucket", ""),
                            "teacher_label": str(p.get("label") or ""),
                            "teacher_confidence": float(p.get("confidence") or 0),
                            "teacher_reason": str(p.get("reason") or "")[:300],
                        }
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        f.flush()

                    print(f"[OK] batch {bi} ({len(batch)} items)")
                    success = True
                    break
                except Exception as exc:
                    print(f"[Retry {attempt+1}/4] {repr(exc)}")
                    time.sleep(2 ** attempt)

            if not success:
                print(f"[ERREUR] batch {bi} ignoré après 4 tentatives")

    print("[TERMINE]", out)

if __name__ == "__main__":
    main()
