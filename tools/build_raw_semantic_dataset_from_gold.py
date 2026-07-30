# -*- coding: utf-8 -*-
"""
build_raw_semantic_dataset_from_gold.py

Transforme ton ancien CSV annoté FastJudge en raw_semantic_dataset.csv
pour EnnoSmart NLP V13.3+.

Usage PowerShell :
    cd C:\EnnoSmart
    python tools\build_raw_semantic_dataset_from_gold.py ^
      --input "C:\chemin\vers\ton_ancien_dataset.csv" ^
      --output "modules\NLP\data\raw_semantic_dataset.csv"

Ensuite :
    python tools\build_raw_prototypes_from_dataset.py ^
      --input-csv modules\NLP\data\raw_semantic_dataset.csv ^
      --output-json modules\NLP\data\raw_verrou_prototypes.json
"""

from __future__ import annotations
from pathlib import Path
import argparse
import pandas as pd
import re
import json


def clean_text(x):
    if pd.isna(x):
        return ""
    return re.sub(r"\s+", " ", str(x).replace("\ufeff", " ").replace("\xa0", " ")).strip()


def to_bool(x):
    if isinstance(x, bool):
        return x
    return str(x).strip().lower() in {"true", "1", "yes", "oui"}


def map_label(row):
    role = clean_text(row.get("role_gold")).lower()
    sub = clean_text(row.get("sub_role_gold")).lower()
    linked = clean_text(row.get("linked_final_section")).lower()

    if role == "objectif":
        return "objectif"
    if role == "methode":
        return "methode"
    if role == "resultat":
        return "resultat"
    if role == "contribution":
        return "resultat"
    if role == "parametre":
        return "parametre"
    if role == "variable":
        return "variable"
    if role == "bruit":
        return "bruit"
    if role == "verrou":
        return "vrai_verrou_rd"
    if role == "limite":
        if linked == "verrou_technique" or sub in {
            "verrou_technique",
            "complexite_ou_manque_maitrise",
            "manque_referentiel_ou_protocole",
            "surveillance_temps_reel_grande_echelle",
        }:
            return "vrai_verrou_rd"
        if linked == "limite_incertitude" or "incertitude" in sub or "validation" in sub:
            return "vrai_verrou_rd"
        return "contrainte_technique"
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    df = pd.read_csv(args.input, encoding="utf-8", engine="python", on_bad_lines="skip")

    work = df.copy()
    for col in ["text", "context_before", "context_after", "source_type", "role_gold"]:
        if col in work.columns:
            work[col] = work[col].map(clean_text)
    work["label"] = work.apply(map_label, axis=1)
    work["keep_bool"] = work.get("keep_gold", "").map(to_bool)
    work["useful_bool"] = work.get("useful_for_cir_gold", "").map(to_bool)
    work["quality_score_num"] = pd.to_numeric(work.get("quality_score", 0), errors="coerce").fillna(0)

    raw = work[work["source_type"].str.lower().eq("raw")].copy()

    mask = (
        (raw["label"] != "") &
        (
            ((raw["label"] == "bruit") & (raw["quality_score_num"] >= 0.05)) |
            ((raw["label"] != "bruit") & raw["keep_bool"] & raw["useful_bool"] & (raw["quality_score_num"] >= 0.80))
        )
    )

    raw = raw[mask].copy()
    raw = raw[raw["text"].str.len() >= 35].copy()
    raw["_sig"] = raw["label"].str.lower() + "||" + raw["text"].str.lower().str[:600]
    raw = raw.drop_duplicates("_sig").copy()

    out = pd.DataFrame({
        "text": raw["text"],
        "label": raw["label"],
        "document_type": "raw",
        "domain": raw.get("domain", "").map(clean_text),
        "source_document": raw.get("source_doc", "").map(clean_text),
        "source_section": raw.get("linked_final_section", "").map(clean_text),
        "quality": raw["quality_score_num"].apply(lambda x: "good" if x >= 0.80 else "medium"),
        "comment": raw.apply(
            lambda r: f"from role_gold={r.get('role_gold')} | sub_role={clean_text(r.get('sub_role_gold'))} | status={clean_text(r.get('annotation_status'))}",
            axis=1,
        ),
        "candidate_id": raw.get("candidate_id", "").map(clean_text),
        "project_id": raw.get("project_id", "").map(clean_text),
        "original_role_gold": raw.get("role_gold", "").map(clean_text),
        "original_sub_role_gold": raw.get("sub_role_gold", "").map(clean_text),
        "context_before": raw.get("context_before", ""),
        "context_after": raw.get("context_after", ""),
        "review_priority": raw.get("review_priority", "").map(clean_text),
    })

    label_order = {
        "vrai_verrou_rd": 0,
        "objectif": 1,
        "methode": 2,
        "resultat": 3,
        "parametre": 4,
        "variable": 5,
        "contrainte_technique": 6,
        "critere_validation": 7,
        "contrainte_normative": 8,
        "bruit": 9,
    }
    out["_order"] = out["label"].map(label_order).fillna(99)
    out = out.sort_values(["_order", "quality", "source_document"]).drop(columns=["_order"])

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False, encoding="utf-8-sig")

    report = {
        "input_rows": int(len(df)),
        "raw_rows_before_filter": int(len(work[work["source_type"].str.lower().eq("raw")])),
        "output_rows": int(len(out)),
        "label_distribution": out["label"].value_counts().to_dict(),
        "note": "Dataset raw semantic initial. Ajouter manuellement critere_validation / contrainte_normative depuis les erreurs du pipeline.",
    }

    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
