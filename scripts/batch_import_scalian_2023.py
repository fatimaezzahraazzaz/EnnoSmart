# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import sys
from pathlib import Path
from difflib import get_close_matches

ROOT = Path(r"C:\EnnoSmart")
SCRIPTS = ROOT / "scripts"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from experience_memory_v2_engine import build_cir_final_v2, rebuild_global_graph_and_catalog


ORGANISME = "Scalian"
YEAR = "2023"

# Mets ici le dossier où sont tes CIR 2023
SOURCE_DIR = Path(r"C:\Users\dell\Downloads\CIR_2023")

SUPPORTED_EXTS = {".docx", ".pdf", ".txt", ".md"}


def norm_name(s: str) -> str:
    s = str(s or "").lower()
    s = s.replace("-", "_").replace(" ", "_")
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def clean_project_name(s: str) -> str:
    s = str(s or "").strip()
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s.title()


def extract_project_from_filename(file_name: str, year: str = YEAR) -> str:
    stem = Path(file_name).stem

    # Exemples :
    # SCALIAN-DS_DT-2023_OCEANO
    # SCALIAN-DS_DT-2023_IA RADAR
    pattern = rf"{year}[_\-\s]+(.+)$"
    m = re.search(pattern, stem, flags=re.IGNORECASE)

    if m:
        project = m.group(1)
    else:
        project = stem

    project = re.sub(r"^SCALIAN[-_ ]*DS[-_ ]*DT[-_ ]*", "", project, flags=re.I)
    project = re.sub(rf"^{year}[-_ ]*", "", project, flags=re.I)

    return clean_project_name(project)


def load_existing_2024_projects() -> dict[str, str]:
    base = ROOT / "storage" / "organismes" / ORGANISME / "projects"
    mapping = {}

    if not base.exists():
        return mapping

    for p in base.iterdir():
        if not p.is_dir():
            continue

        year_dir = p / "years" / "2024"
        if year_dir.exists():
            mapping[norm_name(p.name)] = p.name

    return mapping


def align_project_name(project_2023: str, existing_2024: dict[str, str]) -> str:
    if not existing_2024:
        return project_2023

    n = norm_name(project_2023)

    # match exact
    if n in existing_2024:
        return existing_2024[n]

    # match sans préfixes fréquents
    n2 = n.replace("ia_", "").replace("ai_", "")
    for k, original in existing_2024.items():
        k2 = k.replace("ia_", "").replace("ai_", "")
        if n2 == k2 or n2 in k2 or k2 in n2:
            return original

    # match flou
    matches = get_close_matches(n, list(existing_2024.keys()), n=1, cutoff=0.78)
    if matches:
        return existing_2024[matches[0]]

    return project_2023


def main():
    if not SOURCE_DIR.exists():
        raise FileNotFoundError(f"Dossier introuvable : {SOURCE_DIR}")

    existing_2024 = load_existing_2024_projects()

    files = [
        p for p in SOURCE_DIR.iterdir()
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTS
        and not p.name.startswith("~$")
    ]

    print(f"Fichiers trouvés : {len(files)}")
    print(f"Projets 2024 connus : {list(existing_2024.values())}")

    for i, file_path in enumerate(files, start=1):
        raw_project = extract_project_from_filename(file_path.name)
        project = align_project_name(raw_project, existing_2024)

        print("\n" + "=" * 80)
        print(f"[{i}/{len(files)}] {file_path.name}")
        print(f"Projet détecté : {raw_project}")
        print(f"Projet final   : {project}")

        try:
            report = build_cir_final_v2(
                file_path=file_path,
                organisme=ORGANISME,
                project=project,
                year=YEAR,
                copy_to_library=True,
                reset_chroma=False,
                vision_mode="text_only",
                formula_mode="off",
            )

            print(
                f"OK | chunks={report.get('chunks_count')} "
                f"| cards={report.get('cards_count')} "
                f"| secondes={report.get('elapsed_seconds')}"
            )

        except Exception as e:
            print(f"ERREUR sur {file_path.name} : {e}")

    print("\nReconstruction globale Chroma/catalog...")
    final_report = rebuild_global_graph_and_catalog(reset_chroma=True)

    print("Terminé.")
    print(f"Chunks : {final_report.get('chunks_count')}")
    print(f"Cards : {final_report.get('cards_count')}")
    print(f"Relations : {final_report.get('relations_count')}")


if __name__ == "__main__":
    main()