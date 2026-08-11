from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, List

import requests

OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"

FAST_LABELS = [
    "objectif", "verrou", "methode", "parametre",
    "resultat", "limite", "contribution", "bruit"
]
VERROU_LABELS = ["verrou_evidence", "non_verrou"]

FAST_SYSTEM = """Tu es annotateur expert de passages issus de projets R&D/CIR.
Classe chaque passage selon son sens principal dans UNE seule classe :

objectif = but, finalité ou résultat attendu
verrou = incertitude scientifique/technologique non résolue, obstacle de connaissance
methode = démarche, protocole, algorithme, expérimentation, procédé ou moyen utilisé
parametre = variable, réglage, configuration, seuil ou caractéristique mesurée
resultat = résultat obtenu, observation, mesure ou performance constatée
limite = faiblesse, restriction ou insuffisance connue, sans être forcément un verrou
contribution = apport, nouveauté, innovation ou solution proposée
bruit = administratif, contexte non pertinent, trop vague ou hors catégories

Règles strictes :
- une difficulté n'est pas automatiquement un verrou ;
- une limite connue n'est pas automatiquement un verrou ;
- pour "verrou", il faut une incertitude de connaissance réellement non maîtrisée ;
- choisis le sens principal ;
- n'utilise que les labels autorisés.
"""

VERROU_SYSTEM = """Tu es annotateur expert et strict pour les verrous R&D/CIR.

verrou_evidence = le passage contient une véritable incertitude scientifique ou technologique
non maîtrisée : phénomène, causalité, validité, représentativité, prédiction, généralisation,
transposabilité, comportement ou connaissance manquante.

non_verrou = objectif, méthode, résultat, bug, intégration, maintenance, réglage,
optimisation classique, contrainte client, difficulté d'implémentation, problème résolu,
ou limite connue sans incertitude scientifique réelle.

Une simple difficulté ou l'expression "surmonter une limite" ne suffit jamais à constituer
un verrou. N'utilise que verrou_evidence ou non_verrou.
"""

def read_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def read_done(path: Path) -> Dict[str, Dict[str, Any]]:
    out = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                rid = str(row.get("id") or "")
                if rid:
                    out[rid] = row
            except Exception:
                pass
    return out

def schema(labels: List[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "idx": {"type": "integer"},
                        "label": {"type": "string", "enum": labels},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string"},
                    },
                    "required": ["idx", "label", "confidence", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }

def ask_batch(model: str, task: str, batch: List[Dict[str, Any]], timeout: int, num_ctx: int):
    labels = FAST_LABELS if task == "fastjudge" else VERROU_LABELS
    system = FAST_SYSTEM if task == "fastjudge" else VERROU_SYSTEM

    items = [
        {"idx": i + 1, "text": str(r.get("text") or "")[:1800]}
        for i, r in enumerate(batch)
    ]

    payload = {
        "model": model,
        "stream": False,
        "keep_alive": "30m",
        "format": schema(labels),
        "options": {"temperature": 0, "num_ctx": num_ctx},
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "instruction": (
                            "Classe tous les passages. Retourne exactement un objet par idx, "
                            "sans en oublier et sans ajouter de texte."
                        ),
                        "items": items,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }

    r = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=timeout)
    r.raise_for_status()
    content = str(r.json().get("message", {}).get("content") or "").strip()
    parsed = json.loads(content)
    items = parsed.get("items", [])
    if not isinstance(items, list):
        raise ValueError("Réponse invalide: items n'est pas une liste")
    return items

def process_task(model, task, source, output, batch_size, timeout, num_ctx):
    rows = read_csv(source)
    done = read_done(output)
    remaining = [r for r in rows if str(r.get("id")) not in done]

    allowed = set(FAST_LABELS if task == "fastjudge" else VERROU_LABELS)

    print("\n" + "=" * 80)
    print(f"QWEN3 8B FULL — {task}")
    print("=" * 80)
    print("Source   :", source)
    print("Total    :", len(rows))
    print("Déjà fait:", len(done))
    print("Restant  :", len(remaining))
    print("Sortie   :", output)

    with output.open("a", encoding="utf-8") as f:
        for start in range(0, len(remaining), batch_size):
            batch = remaining[start:start + batch_size]
            batch_no = start // batch_size + 1

            preds = []
            for attempt in range(3):
                try:
                    preds = ask_batch(model, task, batch, timeout, num_ctx)
                    break
                except Exception as exc:
                    print(f"[retry {attempt+1}/3] batch={batch_no} {exc!r}")
                    time.sleep(2 ** attempt)

            by_idx = {}
            for p in preds:
                try:
                    idx = int(p.get("idx"))
                except Exception:
                    continue
                if 1 <= idx <= len(batch):
                    by_idx[idx] = p

            written = 0
            for local_idx, row in enumerate(batch, start=1):
                pred = by_idx.get(local_idx)

                if pred is None:
                    try:
                        one = ask_batch(model, task, [row], timeout, num_ctx)
                        pred = one[0] if one else None
                    except Exception as exc:
                        print(f"[ECHEC ITEM] {row.get('id')} {exc!r}")
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
                conf = min(max(conf, 0.0), 1.0)

                rec = {
                    "id": str(row.get("id") or ""),
                    "task": task,
                    "model": model,
                    "source": str(row.get("source") or ""),
                    "project_id": str(row.get("project_id") or ""),
                    "candidate_label": str(row.get("candidate_label") or ""),
                    "candidate_bucket": str(row.get("candidate_bucket") or ""),
                    "teacher_label": label,
                    "teacher_confidence": conf,
                    "teacher_reason": str(pred.get("reason") or "")[:500],
                }

                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                written += 1

            current_done = len(read_done(output))
            print(
                f"[OK] batch={batch_no} requested={len(batch)} written={written} "
                f"progress={current_done}/{len(rows)}"
            )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\EnnoSmart")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--task", choices=["fastjudge", "verrou", "both"], default="both")
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--num-ctx", type=int, default=4096)
    args = ap.parse_args()

    try:
        tags = requests.get(OLLAMA_TAGS_URL, timeout=10)
        tags.raise_for_status()
    except Exception as exc:
        raise SystemExit(f"Ollama inaccessible: {exc!r}")

    root = Path(args.root)
    qdir = root / "train" / "data_v2" / "teacher_queues"
    odir = root / "train" / "data_v2" / "full_teacher_qwen3_8b"
    odir.mkdir(parents=True, exist_ok=True)

    if args.task in {"fastjudge", "both"}:
        process_task(
            args.model,
            "fastjudge",
            qdir / "fastjudge_teacher_queue_v2.csv",
            odir / "fastjudge_qwen3_8b_full.jsonl",
            args.batch_size,
            args.timeout,
            args.num_ctx,
        )

    if args.task in {"verrou", "both"}:
        process_task(
            args.model,
            "verrou",
            qdir / "verrou_teacher_queue_v2.csv",
            odir / "verrou_qwen3_8b_full.jsonl",
            args.batch_size,
            args.timeout,
            args.num_ctx,
        )

    print("\n[TERMINE] Dossier :", odir)

if __name__ == "__main__":
    main()
