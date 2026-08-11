from __future__ import annotations

import argparse
import csv
import json
import time
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

Définitions strictes :
- objectif : but, finalité ou résultat attendu.
- verrou : incertitude scientifique ou technologique non résolue, obstacle de connaissance.
- methode : démarche, protocole, algorithme, expérimentation, procédé ou moyen utilisé.
- parametre : variable, réglage, configuration, seuil ou caractéristique mesurée.
- resultat : résultat obtenu, observation, mesure ou performance constatée.
- limite : faiblesse, restriction ou insuffisance d'une méthode/résultat, sans être forcément un verrou.
- contribution : apport, nouveauté, innovation ou solution proposée.
- bruit : administratif, contexte non pertinent, texte trop vague ou hors catégories.

Règles :
1. Une simple difficulté n'est pas automatiquement un verrou.
2. Une limite connue n'est pas automatiquement un verrou.
3. Une méthode utilisée pour résoudre un problème reste une méthode.
4. Choisis le sens principal du passage.
5. N'utilise QUE les labels autorisés.
6. Retourne uniquement l'objet JSON demandé, sans texte avant ou après.
"""

VERROU_SYSTEM = """Tu es un annotateur expert et strict pour la détection de verrous R&D/CIR.

Labels :
- verrou_evidence : le passage contient une véritable incertitude scientifique ou technologique.
  Il doit exister une connaissance manquante ou non maîtrisée concernant par exemple un phénomène,
  une causalité, une représentativité, une validité, une prédiction, un comportement,
  une généralisation ou une transposabilité.
- non_verrou : objectif, méthode, résultat, contrainte client, bug, intégration,
  maintenance, paramétrage, optimisation classique, difficulté d'implémentation,
  problème déjà résolu ou limite connue sans incertitude scientifique réelle.

Règles :
1. Ne transforme jamais une difficulté technique ordinaire en verrou.
2. Cherche explicitement l'incertitude de connaissance.
3. En cas de doute réel, baisse la confiance plutôt que d'inventer une certitude.
4. N'utilise QUE verrou_evidence ou non_verrou.
5. Retourne uniquement l'objet JSON demandé, sans texte avant ou après.
"""

def read_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def existing_ids(path: Path) -> set[str]:
    ids = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                if row.get("id"):
                    ids.add(str(row["id"]))
            except Exception:
                pass
    return ids

def batches(rows: List[Dict[str, Any]], n: int):
    for i in range(0, len(rows), n):
        yield rows[i:i+n]

def build_schema(task: str, batch_size: int) -> Dict[str, Any]:
    if task == "fastjudge":
        labels = FAST_LABELS
    else:
        labels = ["verrou_evidence", "non_verrou"]

    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": batch_size,
                "maxItems": batch_size,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "label": {"type": "string", "enum": labels},
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0
                        },
                        "reason": {"type": "string"}
                    },
                    "required": ["id", "label", "confidence", "reason"],
                    "additionalProperties": False
                }
            }
        },
        "required": ["items"],
        "additionalProperties": False
    }

def parse_json_robust(content: str) -> Dict[str, Any]:
    """
    1) JSON normal.
    2) Retire éventuelles fences markdown.
    3) Si plusieurs objets JSON sont concaténés / séparés par lignes,
       les parse un par un et fusionne leurs `items`.
    """
    content = (content or "").strip()

    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    try:
        obj = json.loads(content)
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list):
            return {"items": obj}
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    idx = 0
    objects = []

    while idx < len(content):
        while idx < len(content) and content[idx].isspace():
            idx += 1
        if idx >= len(content):
            break

        # Avance jusqu'au prochain début JSON si texte parasite.
        if content[idx] not in "{[":
            next_obj = [p for p in (content.find("{", idx), content.find("[", idx)) if p != -1]
            if not next_obj:
                break
            idx = min(next_obj)

        try:
            obj, end = decoder.raw_decode(content, idx)
            objects.append(obj)
            idx = end
        except json.JSONDecodeError:
            idx += 1

    if not objects:
        raise ValueError(f"Impossible de parser la réponse JSON : {content[:500]!r}")

    merged = []
    for obj in objects:
        if isinstance(obj, dict):
            if isinstance(obj.get("items"), list):
                merged.extend(obj["items"])
            elif {"id", "label"}.issubset(obj.keys()):
                merged.append(obj)
        elif isinstance(obj, list):
            merged.extend(x for x in obj if isinstance(x, dict))

    if not merged:
        raise ValueError(f"JSON trouvé mais aucun item exploitable : {content[:500]!r}")

    return {"items": merged}

def ask_ollama(
    model: str,
    task: str,
    batch: List[Dict[str, Any]],
    timeout: int,
    num_ctx: int,
) -> List[Dict[str, Any]]:
    if task == "fastjudge":
        system = FAST_SYSTEM
        allowed = FAST_LABELS
    else:
        system = VERROU_SYSTEM
        allowed = ["verrou_evidence", "non_verrou"]

    items = [
        {
            "id": str(row["id"]),
            "text": str(row.get("text") or "")[:1800],
        }
        for row in batch
    ]

    schema = build_schema(task, len(batch))

    prompt = {
        "instruction": (
            "Classe TOUS les items. Retourne exactement un résultat par id. "
            "Ne retourne aucun texte hors de l'objet JSON."
        ),
        "allowed_labels": allowed,
        "items": items,
        "json_schema": schema
    }

    payload = {
        "model": model,
        "stream": False,
        # Structured Outputs : vrai JSON Schema
        "format": schema,
        "keep_alive": "30m",
        "options": {
            "temperature": 0,
            "num_ctx": num_ctx,
        },
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(prompt, ensure_ascii=False),
            },
        ],
    }

    response = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()

    content = (
        data.get("message", {}).get("content")
        or ""
    ).strip()

    parsed = parse_json_robust(content)
    predictions = parsed.get("items", [])

    if not isinstance(predictions, list):
        raise ValueError("Réponse Ollama invalide: items n'est pas une liste.")

    # Filtre sécurité + dédoublonnage par id
    requested_ids = {str(x["id"]) for x in items}
    unique = {}
    for p in predictions:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "")
        label = str(p.get("label") or "")
        if pid in requested_ids and label in allowed:
            unique[pid] = p

    return list(unique.values())

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    parser.add_argument("--task", choices=["fastjudge", "verrou"], required=True)
    parser.add_argument("--model", default="qwen3:4b-instruct")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-items", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--num-ctx", type=int, default=8192)
    args = parser.parse_args()

    root = Path(args.root)
    qdir = root / "train" / "data_v2" / "teacher_queues"
    odir = root / "train" / "data_v2" / "teacher_labels_local"
    odir.mkdir(parents=True, exist_ok=True)

    if args.task == "fastjudge":
        source = qdir / "fastjudge_teacher_queue_v2.csv"
        output = odir / "fastjudge_teacher_qwen3.jsonl"
    else:
        source = qdir / "verrou_teacher_queue_v2.csv"
        output = odir / "verrou_teacher_qwen3.jsonl"

    if not source.exists():
        raise SystemExit(f"Fichier introuvable : {source}")

    try:
        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=10)
        r.raise_for_status()
    except Exception as exc:
        raise SystemExit(
            "Ollama n'est pas accessible sur http://127.0.0.1:11434.\n"
            "Lance Ollama puis réessaie.\n"
            f"Détail: {exc!r}"
        )

    rows = read_csv(source)
    if args.max_items > 0:
        rows = rows[:args.max_items]

    done = existing_ids(output)
    rows = [r for r in rows if str(r.get("id")) not in done]

    print(f"[LOCAL TEACHER FIX] model={args.model}")
    print(f"[LOCAL TEACHER FIX] task={args.task}")
    print(f"[LOCAL TEACHER FIX] batch_size={args.batch_size}")
    print(f"[LOCAL TEACHER FIX] restant={len(rows)}")
    print(f"[LOCAL TEACHER FIX] output={output}")

    allowed = set(
        FAST_LABELS if args.task == "fastjudge"
        else ["verrou_evidence", "non_verrou"]
    )

    with output.open("a", encoding="utf-8") as f:
        for batch_index, batch in enumerate(batches(rows, args.batch_size), start=1):
            success = False

            for attempt in range(3):
                try:
                    predictions = ask_ollama(
                        args.model,
                        args.task,
                        batch,
                        args.timeout,
                        args.num_ctx,
                    )

                    by_id = {
                        str(p.get("id")): p
                        for p in predictions
                        if p.get("id")
                    }

                    written = 0
                    for row in batch:
                        pred = by_id.get(str(row["id"]))
                        if not pred:
                            continue

                        label = str(pred.get("label") or "").strip()
                        if label not in allowed:
                            continue

                        try:
                            confidence = float(pred.get("confidence") or 0)
                        except Exception:
                            confidence = 0.0
                        confidence = min(max(confidence, 0.0), 1.0)

                        record = {
                            "id": str(row["id"]),
                            "task": args.task,
                            "teacher_model": args.model,
                            "candidate_label": str(row.get("candidate_label") or ""),
                            "candidate_bucket": str(row.get("candidate_bucket") or ""),
                            "teacher_label": label,
                            "teacher_confidence": confidence,
                            "teacher_reason": str(pred.get("reason") or "")[:400],
                        }

                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
                        f.flush()
                        written += 1

                    if written == 0:
                        raise ValueError("Le batch a été parsé mais aucun id valide n'a été retourné.")

                    print(
                        f"[OK] batch={batch_index} "
                        f"requested={len(batch)} written={written}"
                    )
                    success = True
                    break

                except Exception as exc:
                    print(
                        f"[Retry {attempt + 1}/3] "
                        f"batch={batch_index} erreur={exc!r}"
                    )
                    time.sleep(2 ** attempt)

            if not success:
                print(f"[ERREUR] batch {batch_index} non traité.")

    print("[TERMINE]", output)

if __name__ == "__main__":
    main()
