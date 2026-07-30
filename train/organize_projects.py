from pathlib import Path
import shutil
import json
from datetime import datetime

SOURCE_DIR = Path(r"C:\EnnoSmart\train\data")
OUTPUT_DIR = Path(r"C:\EnnoSmart\projects")

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


def normalize(text: str) -> str:
    return text.lower().replace("_", " ").replace("-", " ").strip()


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


def safe_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)

    if not dst.exists():
        shutil.copy2(src, dst)
        return

    counter = 1
    while True:
        new_dst = dst.with_name(f"{dst.stem}_{counter}{dst.suffix}")
        if not new_dst.exists():
            shutil.copy2(src, new_dst)
            return
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


def organize_projects() -> None:
    if not SOURCE_DIR.exists():
        raise FileNotFoundError(f"Source introuvable : {SOURCE_DIR}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    source_projects = [
        p for p in SOURCE_DIR.iterdir()
        if p.is_dir()
    ]

    for source_project in source_projects:
        project_name = source_project.name
        target_project = OUTPUT_DIR / project_name

        create_project_structure(target_project)

        raw_count = 0
        cir_count = 0

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

            safe_copy(file_path, destination)

        project_type = detect_project_type(raw_count, cir_count)

        metadata = {
            "project_id": project_name,
            "original_project_name": source_project.name,
            "created_at": datetime.now().isoformat(timespec="seconds"),

            "project_type": project_type,
            "has_raw_documents": raw_count > 0,
            "has_cir_final": cir_count > 0,

            "raw_files_count": raw_count,
            "cir_final_files_count": cir_count,

            "domain": "to_detect_by_nlp",
            "sub_domain": "to_detect_by_nlp",

            "status": "not_annotated",
            "annotation_status": "not_started",

            "goal": "gliner_finetuning_ner_rd_cir",
            "labels_target": LABELS_TARGET,
        }

        with open(target_project / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        print(
            f"✅ {source_project.name} → {target_project.name} | "
            f"type={project_type} | raw={raw_count} | cir_final={cir_count}"
        )


if __name__ == "__main__":
    organize_projects()