from pathlib import Path
import shutil
import json
import re
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================

SOURCE_DIR = Path(r"C:\EnnoSmart\train\data")
OUTPUT_DIR = Path(r"C:\EnnoSmart\projects")

START_PROJECT_ID = 15
END_PROJECT_ID = 32

EXT_MAP = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "docx",
    ".pptx": "pptx",
    ".ppt": "pptx",
    ".xlsx": "excel",
    ".xls": "excel",
    ".csv": "excel",
    ".eml": "emails",
    ".msg": "emails",
    ".txt": "emails",
    ".html": "emails",
    ".htm": "emails",
}

FINAL_KEYWORDS = [
    "cir",
    "dossier final",
    "dossier technique final",
    "technique final",
    "livrable final",
    "final",
    "dossier justificatif",
    "justificatif",
]

LABELS_TARGET = [
    "DOMAINE_RD",
    "TECHNOLOGIE_RD",
    "VERROU_TECH",
    "METHODE_RD",
    "MATERIAU_SPECIFIQUE",
    "EQUIPEMENT_RD",
    "COMPOSANT_TECHNIQUE",
    "OBJECTIF_RD",
    "RESULTAT_RD",
    "PERSONNE",
    "ORGANISME",
    "LIEU",
    "DATE_PERIODE",
    "MONTANT_CIR",
    "ETP",
    "JALON",
]

SKIP_EXISTING_FILES = True


# ============================================================
# UTILS
# ============================================================

def normalize(text: str) -> str:
    return (
        text.lower()
        .replace("_", " ")
        .replace("-", " ")
        .replace(".", " ")
        .strip()
    )


def project_number_from_name(name: str):
    """
    Accepte:
    projet_15_
    projet_15
    projet 15
    Projet 15
    """
    match = re.search(r"projet[\s_ -]*(\d+)", name.lower())
    if not match:
        return None
    return int(match.group(1))


def is_supported_file(file_path: Path) -> bool:
    return file_path.suffix.lower() in EXT_MAP


def create_project_structure(project_dir: Path) -> None:
    folders = [
        "raw/pdf",
        "raw/docx",
        "raw/pptx",
        "raw/excel",
        "raw/emails",
        "cir_final",
        "extracted",
        "annotations",
    ]

    for folder in folders:
        (project_dir / folder).mkdir(parents=True, exist_ok=True)


def safe_copy(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)

    if not dst.exists():
        shutil.copy2(src, dst)
        return dst

    if SKIP_EXISTING_FILES:
        return dst

    counter = 1
    while True:
        new_dst = dst.with_name(f"{dst.stem}_{counter}{dst.suffix}")
        if not new_dst.exists():
            shutil.copy2(src, new_dst)
            return new_dst
        counter += 1


def path_text(file_path: Path) -> str:
    parts = [normalize(p.name) for p in file_path.parents]
    return " / ".join(parts) + " / " + normalize(file_path.name)


def is_cir_final(file_path: Path) -> bool:
    text = path_text(file_path)
    name = normalize(file_path.name)

    if any(keyword in text for keyword in FINAL_KEYWORDS):
        return True

    if "cir" in name and file_path.suffix.lower() in {".pdf", ".docx", ".doc"}:
        return True

    return False


def detect_project_type(raw_count: int, cir_count: int) -> str:
    if raw_count > 0 and cir_count > 0:
        return "full"
    if raw_count > 0:
        return "raw_only"
    if cir_count > 0:
        return "cir_only"
    return "empty"


def get_source_projects_15_32() -> list[Path]:
    if not SOURCE_DIR.exists():
        raise FileNotFoundError(f"Source introuvable : {SOURCE_DIR}")

    selected = []

    for p in SOURCE_DIR.iterdir():
        if not p.is_dir():
            continue

        num = project_number_from_name(p.name)

        if num is None:
            continue

        if START_PROJECT_ID <= num <= END_PROJECT_ID:
            selected.append(p)

    selected = sorted(
        selected,
        key=lambda p: project_number_from_name(p.name)
    )

    return selected


def canonical_project_id(source_project: Path) -> str:
    num = project_number_from_name(source_project.name)

    if num is None:
        raise ValueError(f"Nom projet invalide : {source_project.name}")

    return f"projet_{num}_"


# ============================================================
# ORGANIZATION
# ============================================================

def organize_projects_15_32() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    source_projects = get_source_projects_15_32()

    print("=" * 80)
    print(f"Source      : {SOURCE_DIR}")
    print(f"Destination : {OUTPUT_DIR}")
    print(f"Filtre      : projet_{START_PROJECT_ID}_ → projet_{END_PROJECT_ID}_")
    print(f"Trouvés     : {len(source_projects)} dossiers")
    print("=" * 80)

    expected = END_PROJECT_ID - START_PROJECT_ID + 1
    if len(source_projects) != expected:
        print(f"⚠️ Attention : attendu {expected} projets, trouvé {len(source_projects)}")
        print("Projets trouvés :")
        for p in source_projects:
            print(" -", p.name)

    global_summary = []

    for source_project in source_projects:
        project_id = canonical_project_id(source_project)
        target_project = OUTPUT_DIR / project_id

        create_project_structure(target_project)

        raw_count = 0
        cir_count = 0
        copied_files = []

        files = [
            f for f in source_project.rglob("*")
            if f.is_file() and is_supported_file(f)
        ]

        has_final_detected = any(is_cir_final(f) for f in files)

        for file_path in files:
            ext = file_path.suffix.lower()

            if is_cir_final(file_path):
                destination = target_project / "cir_final" / file_path.name
                cir_count += 1
            else:
                if (
                    not has_final_detected
                    and files
                    and all(f.suffix.lower() in {".pdf", ".docx", ".doc"} for f in files)
                ):
                    destination = target_project / "cir_final" / file_path.name
                    cir_count += 1
                else:
                    file_type = EXT_MAP[ext]
                    destination = target_project / "raw" / file_type / file_path.name
                    raw_count += 1

            copied_to = safe_copy(file_path, destination)

            copied_files.append({
                "source": str(file_path),
                "destination": str(copied_to),
                "category": "cir_final" if "cir_final" in str(copied_to) else "raw",
            })

        project_type = detect_project_type(raw_count, cir_count)

        metadata = {
            "project_id": project_id,
            "original_project_name": source_project.name,
            "created_at": datetime.now().isoformat(timespec="seconds"),

            "project_type": project_type,
            "has_raw_documents": raw_count > 0,
            "has_cir_final": cir_count > 0,

            "raw_files_count": raw_count,
            "cir_final_files_count": cir_count,
            "total_files_count": raw_count + cir_count,

            "domain": "to_detect_by_nlp",
            "sub_domain": "to_detect_by_nlp",

            "status": "not_annotated",
            "annotation_status": "not_started",

            "goal": "gliner_finetuning_ner_rd_cir",
            "labels_target": LABELS_TARGET,

            "source_folder": str(source_project),
            "copied_files": copied_files,
        }

        with open(target_project / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        global_summary.append({
            "project_id": project_id,
            "original_project_name": source_project.name,
            "project_type": project_type,
            "raw_files_count": raw_count,
            "cir_final_files_count": cir_count,
            "total_files_count": raw_count + cir_count,
            "target_project": str(target_project),
        })

        print(
            f"✅ {source_project.name} → {project_id} | "
            f"type={project_type} | raw={raw_count} | cir_final={cir_count}"
        )

    summary_path = OUTPUT_DIR / "organize_projects_15_32_summary.json"

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "date": datetime.now().isoformat(timespec="seconds"),
                "source_dir": str(SOURCE_DIR),
                "output_dir": str(OUTPUT_DIR),
                "start_project_id": START_PROJECT_ID,
                "end_project_id": END_PROJECT_ID,
                "projects_count": len(global_summary),
                "projects": global_summary,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("=" * 80)
    print("✅ Structuration terminée.")
    print(f"Résumé global : {summary_path}")
    print("=" * 80)


if __name__ == "__main__":
    organize_projects_15_32()