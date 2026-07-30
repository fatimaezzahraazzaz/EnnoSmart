# -*- coding: utf-8 -*-
"""
build_fastjudge_annotation_sample.py
------------------------------------------------------------
ÉTAPE 3 FastJudge : créer un échantillon d'annotation équilibré.

Entrée :
    C:\EnnoSmart\data\training\fastjudge_candidates_all.jsonl

Sorties :
    C:\EnnoSmart\data\training\fastjudge_annotation_sample.jsonl
    C:\EnnoSmart\data\training\fastjudge_annotation_sample.csv
    C:\EnnoSmart\data\training\fastjudge_annotation_sample_summary.json

But :
- Ne pas annoter les 36k candidats.
- Sélectionner 2k à 3k exemples utiles.
- Garder un équilibre entre rôles, projets et sources.
- Préparer un fichier CSV ouvrable dans Excel pour corriger role_gold.

Usage :
cd C:\EnnoSmart
python tools\build_fastjudge_annotation_sample.py

Usage personnalisé :
python tools\build_fastjudge_annotation_sample.py --target-size 3000 --prefer-raw-plus-cir

Puis ouvrir :
C:\EnnoSmart\data\training\fastjudge_annotation_sample.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


ROLES_TARGET = [
    "objectif",
    "verrou",
    "methode",
    "parametre",
    "variable",
    "resultat",
    "limite",
    "contribution",
    "hypothese",
    "bruit",
]

DEFAULT_ROLE_QUOTAS = {
    "objectif": 300,
    "verrou": 400,
    "methode": 400,
    "resultat": 400,
    "limite": 300,
    "variable": 250,
    "contribution": 150,
    "hypothese": 80,
    "bruit": 250,
    "unknown": 500,
}

CSV_COLUMNS = [
    "annotation_status",
    "role_gold",
    "keep_gold",
    "useful_for_cir_gold",
    "sub_role_gold",
    "linked_final_section",
    "comment",

    "candidate_id",
    "project_id",
    "project_type",
    "source_type",
    "source_doc",
    "candidate_role",
    "quality_score",
    "priority",

    "text",
    "context_before",
    "context_after",

    "domain",
    "sub_domain",
    "source_relpath",
    "read_mode",
    "paragraph_index",
    "sentence_index",
    "dataset_version",
]


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            n += 1
    return n


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            clean = {k: row.get(k, "") for k in CSV_COLUMNS}
            writer.writerow(clean)
    return len(rows)


def norm_text(s: Any) -> str:
    s = str(s or "").lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def text_hash(s: Any) -> str:
    return hashlib.md5(norm_text(s).encode("utf-8", errors="ignore")).hexdigest()


def to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def base_role(row: Dict[str, Any]) -> str:
    role = str(row.get("candidate_role") or "unknown").strip()
    return role if role else "unknown"


def is_good_candidate(row: Dict[str, Any]) -> bool:
    text = str(row.get("text") or "").strip()
    if len(text) < 35:
        return False
    if len(text) > 1600:
        return False

    # éviter les lignes trop numériques / tableaux
    digits = sum(ch.isdigit() for ch in text)
    if digits / max(1, len(text)) > 0.55 and len(text) < 350:
        return False

    # éviter doublons de sommaire / biblio
    n = norm_text(text)
    if len(text) < 180 and any(x in n for x in ["sommaire", "bibliographie", "remerciements", "copyright"]):
        return False

    return True


def compute_priority(row: Dict[str, Any]) -> str:
    role = base_role(row)
    q = to_float(row.get("quality_score"), 0.0)
    ptype = row.get("project_type")
    source = row.get("source_type")

    if role in {"objectif", "verrou", "methode", "resultat", "limite"} and ptype == "raw_plus_cir":
        return "P1"
    if role in {"variable", "parametre", "contribution", "hypothese"}:
        return "P2"
    if role == "unknown" and q >= 0.65:
        return "P2"
    if source == "cir_final" and role in {"objectif", "verrou"}:
        return "P2"
    return "P3"


def normalize_for_annotation(row: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(row)
    r["annotation_status"] = r.get("annotation_status") or "to_annotate"
    r["role_gold"] = r.get("role_gold") or ""
    r["keep_gold"] = r.get("keep_gold") if r.get("keep_gold") is not None else ""
    r["useful_for_cir_gold"] = r.get("useful_for_cir_gold") if r.get("useful_for_cir_gold") is not None else ""
    r["sub_role_gold"] = r.get("sub_role_gold") or ""
    r["linked_final_section"] = r.get("linked_final_section") or r.get("cir_final_section_candidate") or ""
    r["comment"] = r.get("comment") or ""
    r["priority"] = compute_priority(r)
    return r


def deduplicate_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Déduplication souple :
    - même texte exact normalisé -> garder le meilleur score
    - garder aussi la diversité projet/source
    """
    best_by_text: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        h = text_hash(row.get("text"))
        current = best_by_text.get(h)
        if current is None:
            best_by_text[h] = row
            continue

        score_new = score_for_sampling(row)
        score_old = score_for_sampling(current)
        if score_new > score_old:
            best_by_text[h] = row

    return list(best_by_text.values())


def score_for_sampling(row: Dict[str, Any]) -> float:
    score = to_float(row.get("quality_score"), 0.0)
    role = base_role(row)
    ptype = row.get("project_type")
    source = row.get("source_type")

    if ptype == "raw_plus_cir":
        score += 0.18
    if source == "raw":
        score += 0.08
    if role in {"objectif", "verrou", "methode", "resultat", "limite"}:
        score += 0.12
    if role == "unknown":
        score -= 0.03
    if row.get("is_obvious_noise") is True:
        score -= 0.25
    return score


def stratified_pick(
    rows: List[Dict[str, Any]],
    n: int,
    max_per_project: int = 180,
    max_per_doc: int = 80,
    seed: int = 42,
) -> List[Dict[str, Any]]:
    random.seed(seed)
    rows = [r for r in rows if is_good_candidate(r)]
    rows = deduplicate_rows(rows)

    # Tri qualité, puis léger shuffle par blocs pour diversité.
    rows = sorted(rows, key=score_for_sampling, reverse=True)

    selected = []
    project_counts = Counter()
    doc_counts = Counter()
    seen_text = set()

    for row in rows:
        if len(selected) >= n:
            break

        project_id = row.get("project_id")
        doc_key = (row.get("project_id"), row.get("source_doc"))
        h = text_hash(row.get("text"))

        if h in seen_text:
            continue
        if project_counts[project_id] >= max_per_project:
            continue
        if doc_counts[doc_key] >= max_per_doc:
            continue

        selected.append(row)
        seen_text.add(h)
        project_counts[project_id] += 1
        doc_counts[doc_key] += 1

    # Si quotas trop stricts, compléter sans limites projet/doc mais sans doublons.
    if len(selected) < n:
        for row in rows:
            if len(selected) >= n:
                break
            h = text_hash(row.get("text"))
            if h in seen_text:
                continue
            selected.append(row)
            seen_text.add(h)

    return selected[:n]


def build_role_quotas(target_size: int, role_quotas: Dict[str, int]) -> Dict[str, int]:
    default_total = sum(role_quotas.values())
    if target_size <= 0 or target_size == default_total:
        return dict(role_quotas)

    factor = target_size / default_total
    quotas = {role: max(10, int(round(q * factor))) for role, q in role_quotas.items()}

    # Ajuster pour coller au target_size.
    diff = target_size - sum(quotas.values())
    roles_order = sorted(quotas.keys(), key=lambda r: role_quotas.get(r, 0), reverse=True)

    idx = 0
    while diff != 0 and roles_order:
        r = roles_order[idx % len(roles_order)]
        if diff > 0:
            quotas[r] += 1
            diff -= 1
        else:
            if quotas[r] > 5:
                quotas[r] -= 1
                diff += 1
        idx += 1

    return quotas


def build_annotation_sample(
    candidates: List[Dict[str, Any]],
    target_size: int = 3030,
    prefer_raw_plus_cir: bool = True,
    seed: int = 42,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    candidates = [normalize_for_annotation(r) for r in candidates if is_good_candidate(r)]
    candidates = deduplicate_rows(candidates)

    quotas = build_role_quotas(target_size, DEFAULT_ROLE_QUOTAS)

    selected: List[Dict[str, Any]] = []
    selected_hashes = set()

    for role, quota in quotas.items():
        pool = [r for r in candidates if base_role(r) == role]

        if prefer_raw_plus_cir:
            pool_main = [r for r in pool if r.get("project_type") == "raw_plus_cir"]
            pool_other = [r for r in pool if r.get("project_type") != "raw_plus_cir"]

            main_quota = int(quota * 0.75) if role != "unknown" else int(quota * 0.65)
            part1 = stratified_pick(pool_main, main_quota, seed=seed)
            used = {text_hash(x.get("text")) for x in part1}

            remaining_pool = [r for r in pool_other + pool_main if text_hash(r.get("text")) not in used]
            part2 = stratified_pick(remaining_pool, quota - len(part1), seed=seed + 1)
            chosen = part1 + part2
        else:
            chosen = stratified_pick(pool, quota, seed=seed)

        for r in chosen:
            h = text_hash(r.get("text"))
            if h in selected_hashes:
                continue
            selected.append(r)
            selected_hashes.add(h)

    # Compléter si certains rôles n'ont pas assez.
    if len(selected) < target_size:
        remaining = [r for r in candidates if text_hash(r.get("text")) not in selected_hashes]
        fill = stratified_pick(remaining, target_size - len(selected), seed=seed + 2)
        for r in fill:
            h = text_hash(r.get("text"))
            if h not in selected_hashes:
                selected.append(r)
                selected_hashes.add(h)

    # Tri pratique pour annotation : priorité, rôle, projet.
    priority_rank = {"P1": 1, "P2": 2, "P3": 3}
    selected = sorted(
        selected[:target_size],
        key=lambda r: (
            priority_rank.get(r.get("priority"), 9),
            base_role(r),
            r.get("project_id", ""),
            r.get("source_type", ""),
            -to_float(r.get("quality_score"), 0.0),
        )
    )

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_version": "fastjudge_annotation_sample_v1",
        "target_size": target_size,
        "actual_size": len(selected),
        "quotas_requested": quotas,
        "by_candidate_role": dict(Counter(base_role(r) for r in selected)),
        "by_project_type": dict(Counter(r.get("project_type", "unknown") for r in selected)),
        "by_source_type": dict(Counter(r.get("source_type", "unknown") for r in selected)),
        "by_priority": dict(Counter(r.get("priority", "unknown") for r in selected)),
        "by_project_id": dict(Counter(r.get("project_id", "unknown") for r in selected)),
        "instructions": {
            "role_gold": "Remplir avec objectif/verrou/methode/parametre/variable/resultat/limite/contribution/hypothese/bruit.",
            "keep_gold": "true si la phrase doit être conservée comme preuve, false sinon.",
            "useful_for_cir_gold": "true si utile pour CIR, false sinon.",
            "annotation_status": "to_annotate / done / unsure.",
        }
    }

    return selected, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=r"C:\EnnoSmart\data\training\fastjudge_candidates_all.jsonl")
    parser.add_argument("--out-dir", default=r"C:\EnnoSmart\data\training")
    parser.add_argument("--target-size", type=int, default=3030)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--prefer-raw-plus-cir", action="store_true", default=True)
    parser.add_argument("--no-prefer-raw-plus-cir", action="store_false", dest="prefer_raw_plus_cir")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.out_dir)

    if not input_path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {input_path}")

    candidates = read_jsonl(input_path)
    print(f"Candidats chargés : {len(candidates)}")

    sample, summary = build_annotation_sample(
        candidates,
        target_size=int(args.target_size),
        prefer_raw_plus_cir=bool(args.prefer_raw_plus_cir),
        seed=int(args.seed),
    )

    jsonl_path = out_dir / "fastjudge_annotation_sample.jsonl"
    csv_path = out_dir / "fastjudge_annotation_sample.csv"
    summary_path = out_dir / "fastjudge_annotation_sample_summary.json"

    write_jsonl(jsonl_path, sample)
    write_csv(csv_path, sample)
    write_json(summary_path, summary)

    print("\nÉTAPE 3 TERMINÉE")
    print(f"Sample JSONL : {jsonl_path}")
    print(f"Sample CSV   : {csv_path}")
    print(f"Résumé       : {summary_path}")
    print(json.dumps({
        "actual_size": summary["actual_size"],
        "by_candidate_role": summary["by_candidate_role"],
        "by_project_type": summary["by_project_type"],
        "by_source_type": summary["by_source_type"],
        "by_priority": summary["by_priority"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
