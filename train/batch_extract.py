from pathlib import Path
import json
import sys
import re
from datetime import datetime

PROJECT_ROOT = Path(r"C:\EnnoSmart")
PROJECTS_DIR = PROJECT_ROOT / "projects"

sys.path.append(str(PROJECT_ROOT))

SUPPORTED_EXT = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".xls",
    ".csv",
    ".eml",
    ".msg",
    ".txt",
    ".html",
    ".htm",
}

# =========================================================
# CONFIG SPLIT NER
# =========================================================

MIN_CHARS = 300
MAX_CHARS = 1500

FORMULA_NOISE = [
    "[FORMULE",
    "LATEX",
    "OMML",
    "FORMULADOMAIN",
    "DOMAINE :",
    "CONFIANCE :",
    "EXPLICATION :",
]

# =========================================================
# CLEAN
# =========================================================

def is_formula_noise(text: str) -> bool:
    upper = (text or "").upper()
    return any(x.upper() in upper for x in FORMULA_NOISE)


def normalize_text(text: str) -> str:
    text = text or ""

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    return text.strip()


def chunks_from_result(result):
    chunks = list(getattr(result, "text_chunks", []) or [])

    clean_chunks = []

    for c in chunks:

        if not c:
            continue

        if is_formula_noise(c):
            continue

        c = normalize_text(c)

        if len(c) < 30:
            continue

        clean_chunks.append(c)

    return clean_chunks

# =========================================================
# SPLIT CHUNKS FOR NER
# =========================================================

def split_by_paragraphs(text: str) -> list[str]:

    text = normalize_text(text)

    if len(text) <= MAX_CHARS:
        return [text] if len(text) >= MIN_CHARS else []

    paragraphs = [
        p.strip()
        for p in re.split(r"\n\s*\n", text)
        if p.strip()
    ]

    parts = []

    current = ""

    for para in paragraphs:

        if len(para) > MAX_CHARS:

            if current:
                parts.append(current.strip())
                current = ""

            sentences = re.split(
                r"(?<=[.!?])\s+",
                para
            )

            temp = ""

            for sent in sentences:

                if len(temp) + len(sent) + 1 <= MAX_CHARS:
                    temp = f"{temp} {sent}".strip()

                else:
                    if len(temp) >= MIN_CHARS:
                        parts.append(temp.strip())

                    temp = sent

            if len(temp) >= MIN_CHARS:
                parts.append(temp.strip())

        else:

            if len(current) + len(para) + 2 <= MAX_CHARS:
                current = f"{current}\n\n{para}".strip()

            else:
                if len(current) >= MIN_CHARS:
                    parts.append(current.strip())

                current = para

    if len(current) >= MIN_CHARS:
        parts.append(current.strip())

    return parts

# =========================================================
# DOCX / PPTX
# =========================================================

def extract_docx_pptx(path: Path):

    from modules.extraction.text.office import extract_office

    return extract_office(path)

# =========================================================
# EXCEL
# =========================================================

def extract_excel(path: Path):

    from modules.extraction.structured.excel_struct import extract_excel

    return extract_excel(path)

# =========================================================
# EMAIL / TEXT
# =========================================================

def extract_email_or_text(path: Path):

    try:
        from modules.extraction.text.email_parser import extract_email

        return extract_email(path)

    except Exception:

        from modules.extraction.router import extract

        return extract(path, vision_mode=False)

# =========================================================
# PDF
# =========================================================

def is_pdf_native(path: Path) -> bool:

    try:
        import fitz

        doc = fitz.open(str(path))

        text = ""

        max_pages = min(3, len(doc))

        for i in range(max_pages):
            text += doc[i].get_text("text") or ""

        doc.close()

        return len(text.strip()) > 100

    except Exception:
        return False


def extract_pdf(path: Path):

    if is_pdf_native(path):

        from modules.extraction.text.pdf_native import (
            extract_pdf_native
        )

        return extract_pdf_native(path)

    raise RuntimeError(
        "PDF scanné ignoré en mode NER rapide"
    )

# =========================================================
# ROUTER
# =========================================================

def safe_extract(path: Path):

    ext = path.suffix.lower()

    if ext in {".docx", ".pptx"}:
        return extract_docx_pptx(path)

    if ext in {".xlsx", ".xls", ".csv"}:
        return extract_excel(path)

    if ext in {
        ".eml",
        ".msg",
        ".txt",
        ".html",
        ".htm",
    }:
        return extract_email_or_text(path)

    if ext == ".pdf":
        return extract_pdf(path)

    raise ValueError(
        f"Extension non supportée : {ext}"
    )

# =========================================================
# FILES
# =========================================================

def get_project_files(project_dir: Path):

    files = []

    for folder_name in ["raw", "cir_final"]:

        folder = project_dir / folder_name

        if not folder.exists():
            continue

        for f in folder.rglob("*"):

            if (
                f.is_file()
                and f.suffix.lower() in SUPPORTED_EXT
            ):
                files.append(f)

    return sorted(files)

# =========================================================
# FLATTEN
# =========================================================

def flatten(project_id, extracted_files):

    chunks = []

    idx = 0

    for item in extracted_files:

        for text in item["text_chunks"]:

            chunks.append({
                "chunk_id":
                    f"{project_id}_chunk_{idx:06d}",

                "project_id": project_id,

                "source_file":
                    item["source_file"],

                "file_name":
                    item["file_name"],

                "file_category":
                    item["file_category"],

                "source_type": "text",

                "text": text,
            })

            idx += 1

    return chunks

# =========================================================
# SPLIT FOR ANNOTATION
# =========================================================

def build_annotation_chunks(project_id, chunks):

    output = []

    total_subchunks = 0

    for chunk in chunks:

        text = normalize_text(
            chunk.get("text", "")
        )

        subtexts = split_by_paragraphs(text)

        for i, subtext in enumerate(subtexts):

            output.append({

                "annotation_id":
                    f"{chunk['chunk_id']}_part_{i:03d}",

                "project_id":
                    chunk.get("project_id"),

                "source_chunk_id":
                    chunk.get("chunk_id"),

                "source_file":
                    chunk.get("source_file"),

                "file_name":
                    chunk.get("file_name"),

                "file_category":
                    chunk.get("file_category"),

                "source_type":
                    chunk.get("source_type"),

                "text":
                    subtext,

                "entities": [],

                "annotation_status":
                    "not_started",
            })

            total_subchunks += 1

    return output, total_subchunks

# =========================================================
# EXTRACT PROJECT
# =========================================================

def extract_project(project_dir: Path):

    project_id = project_dir.name

    extracted_dir = (
        project_dir / "extracted"
    )

    annotations_dir = (
        project_dir / "annotations"
    )

    extracted_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    annotations_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    files = get_project_files(project_dir)

    extracted_files = []

    errors = []

    print(f"\n📁 Projet : {project_id}")

    print(
        f"   fichiers trouvés "
        f"raw + cir_final : {len(files)}"
    )

    for file_path in files:

        try:
            print(
                f"   🔎 Extraction directe : "
                f"{file_path.name}"
            )

            result = safe_extract(file_path)

            extracted_files.append({

                "source_file":
                    str(file_path),

                "file_name":
                    getattr(
                        result,
                        "file_name",
                        file_path.name,
                    ),

                "file_category":
                    str(
                        getattr(
                            result,
                            "file_type",
                            file_path.suffix.lower(),
                        )
                    ),

                "text_chunks":
                    chunks_from_result(result),
            })

        except Exception as e:

            errors.append({
                "source_file":
                    str(file_path),

                "error":
                    str(e),
            })

            print(
                f"   ⚠️ Ignoré/Erreur : "
                f"{file_path.name} | {e}"
            )

    # =====================================================
    # CHUNKS
    # =====================================================

    chunks = flatten(
        project_id,
        extracted_files
    )

    # =====================================================
    # SPLIT NER
    # =====================================================

    annotation_chunks, total_subchunks = (
        build_annotation_chunks(
            project_id,
            chunks
        )
    )

    # =====================================================
    # SAVE
    # =====================================================

    with open(
        extracted_dir / "extracted_files.json",
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            extracted_files,
            f,
            ensure_ascii=False,
            indent=2,
        )

    with open(
        extracted_dir / "chunks.json",
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            chunks,
            f,
            ensure_ascii=False,
            indent=2,
        )

    with open(
        annotations_dir /
        "chunks_for_annotation.json",
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            annotation_chunks,
            f,
            ensure_ascii=False,
            indent=2,
        )

    summary = {

        "project_id":
            project_id,

        "mode":
            "direct_text_only_for_ner",

        "date":
            datetime.now().isoformat(
                timespec="seconds"
            ),

        "files_count":
            len(files),

        "extracted_files_count":
            len(extracted_files),

        "failed_or_skipped_files_count":
            len(errors),

        "chunks_count":
            len(chunks),

        "annotation_chunks_count":
            total_subchunks,

        "errors":
            errors,
    }

    with open(
        extracted_dir /
        "extraction_summary.json",
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            summary,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(
        f"   ✅ fini | "
        f"fichiers={len(extracted_files)} | "
        f"chunks={len(chunks)} | "
        f"annotation_chunks={total_subchunks} | "
        f"ignorés/erreurs={len(errors)}"
    )

# =========================================================
# MAIN
# =========================================================
def project_number_from_name(name: str):
    """
    Accepte :
    projet_15_
    projet_15
    Projet 15
    """
    match = re.search(r"projet[\s_ -]*(\d+)", name.lower())
    if not match:
        return None
    return int(match.group(1))
def main():

    START_PROJECT = 15
    END_PROJECT = 32

    projects = []

    for p in PROJECTS_DIR.iterdir():
        if not p.is_dir():
            continue

        num = project_number_from_name(p.name)

        if num is None:
            continue

        if START_PROJECT <= num <= END_PROJECT:
            projects.append(p)

    projects = sorted(
        projects,
        key=lambda p: project_number_from_name(p.name)
    )

    print(
        f"Nombre de projets sélectionnés "
        f"de projet_{START_PROJECT}_ à projet_{END_PROJECT}_ : "
        f"{len(projects)}"
    )

    if not projects:
        print("⚠️ Aucun projet trouvé.")
        print(f"Vérifie dans : {PROJECTS_DIR}")
        return

    for project_dir in projects:
        extract_project(project_dir)

    print(
        f"\n✅ Extraction + split NER terminés "
        f"pour projet_{START_PROJECT}_ → projet_{END_PROJECT}_."
    )


if __name__ == "__main__":
    main()