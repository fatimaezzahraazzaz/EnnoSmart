# -*- coding: utf-8 -*-
"""
prepare_fastjudge_metadata.py
------------------------------------------------------------
Crée metadata_fastjudge.json pour chaque projet existant.

Structure attendue :
projects/
  projet_x/
    raw/
    cir_final/
    extracted/
    annotations/
    metadata.json

Usage :
cd C:\EnnoSmart
python tools\prepare_fastjudge_metadata.py --projects-dir C:\EnnoSmart\projects
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any


RAW_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".xlsx", ".xls", ".csv", ".eml", ".msg",
    ".txt", ".png", ".jpg", ".jpeg"
}

CIR_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".txt"
}


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


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def count_files(folder: Path, extensions: set[str]) -> int:
    if not folder.exists():
        return 0
    return sum(
        1 for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in extensions
    )


def detect_project_type(has_raw: bool, has_cir: bool) -> str:
    if has_raw and has_cir:
        return "raw_plus_cir"
    if has_raw and not has_cir:
        return "raw_only"
    if not has_raw and has_cir:
        return "cir_only"
    return "empty_or_invalid"


def build_fastjudge_metadata(project_dir: Path) -> Dict[str, Any]:
    old_meta_path = project_dir / "metadata.json"
    old_meta = load_json(old_meta_path)

    raw_dir = project_dir / "raw"
    cir_dir = project_dir / "cir_final"
    extracted_dir = project_dir / "extracted"
    annotations_dir = project_dir / "annotations"

    annotations_dir.mkdir(parents=True, exist_ok=True)

    raw_count = count_files(raw_dir, RAW_EXTENSIONS)
    cir_count = count_files(cir_dir, CIR_EXTENSIONS)

    has_raw = raw_count > 0
    has_cir = cir_count > 0

    project_id = old_meta.get("project_id") or project_dir.name
    original_project_name = old_meta.get("original_project_name") or project_dir.name

    project_type = detect_project_type(has_raw, has_cir)

    metadata = {
        "project_id": project_id,
        "original_project_name": original_project_name,

        "dataset_version": "fastjudge_v1",
        "created_at": old_meta.get("created_at"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),

        "project_type": project_type,
        "has_raw_documents": has_raw,
        "has_cir_final": has_cir,

        "raw_files_count": raw_count,
        "cir_final_files_count": cir_count,

        "domain": old_meta.get("domain", "to_detect_by_nlp"),
        "sub_domain": old_meta.get("sub_domain", "to_detect_by_nlp"),

        "dataset_goal": "fastjudge_role_classification_and_cir_evidence_alignment",

        "task_type": [
            "role_classification",
            "evidence_selection",
            "raw_to_cir_alignment",
            "fastjudge_training",
            "fastjudge_evaluation",
        ],

        "roles_target": ROLES_TARGET,

        "alignment_available": bool(has_raw and has_cir),

        "annotation_status": old_meta.get("annotation_status", "not_started"),
        "quality_status": "not_verified",

        "folders": {
            "raw": str(raw_dir),
            "cir_final": str(cir_dir),
            "extracted": str(extracted_dir),
            "annotations": str(annotations_dir),
        },

        "outputs_expected": {
            "candidate_sentences": "annotations/candidates.jsonl",
            "role_annotations": "annotations/role_classification.jsonl",
            "raw_cir_alignment": "annotations/raw_cir_alignment.jsonl",
            "evaluation_output": "annotations/evaluation_report.json",
        },

        "old_metadata_backup": {
            "old_goal": old_meta.get("goal"),
            "old_labels_target": old_meta.get("labels_target", []),
            "old_status": old_meta.get("status"),
        }
    }

    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--projects-dir",
        required=True,
        help="Chemin vers le dossier projects contenant projet_1_, projet_2_, etc."
    )
    args = parser.parse_args()

    projects_dir = Path(args.projects_dir)
    if not projects_dir.exists():
        raise FileNotFoundError(f"Dossier introuvable : {projects_dir}")

    summary = {
        "total_projects": 0,
        "raw_plus_cir": 0,
        "raw_only": 0,
        "cir_only": 0,
        "empty_or_invalid": 0,
        "projects": [],
    }

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue

        metadata = build_fastjudge_metadata(project_dir)
        out_path = project_dir / "metadata_fastjudge.json"
        out_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        ptype = metadata["project_type"]
        summary["total_projects"] += 1
        summary[ptype] = summary.get(ptype, 0) + 1

        summary["projects"].append({
            "project_id": metadata["project_id"],
            "project_type": ptype,
            "raw_files_count": metadata["raw_files_count"],
            "cir_final_files_count": metadata["cir_final_files_count"],
            "metadata_fastjudge": str(out_path),
        })

        print(f"OK {project_dir.name} -> {ptype}")

    summary_path = projects_dir / "fastjudge_dataset_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print("\nRésumé :")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nRésumé sauvegardé : {summary_path}")


if __name__ == "__main__":
    main()