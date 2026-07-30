from pathlib import Path
import json
import sys
from datetime import datetime

PROJECT_ROOT = Path(r"C:\EnnoSmart")
PROJECTS_DIR = PROJECT_ROOT / "projects"

sys.path.append(str(PROJECT_ROOT))

from gliner import GLiNER


MODEL_NAME = "urchade/gliner_multi-v2.1"

LABELS = [
    "domaine de recherche appliquée",
    "technologie R&D",
    "verrou technologique",
    "méthode expérimentale",
    "matériau spécifique",
    "équipement R&D",
    "composant technique",
    "objectif de recherche",
    "résultat de recherche",
    "personne",
    "organisation",
    "lieu",
    "date",
    "période",
    "montant",
    "ETP",
    "jalon",
]

LABEL_MAP = {
    "domaine de recherche appliquée": "DOMAINE_RD",
    "technologie R&D": "TECHNOLOGIE_RD",
    "verrou technologique": "VERROU_TECH",
    "méthode expérimentale": "METHODE_RD",
    "matériau spécifique": "MATERIAU_SPECIFIQUE",
    "équipement R&D": "EQUIPEMENT_RD",
    "composant technique": "COMPOSANT_TECHNIQUE",
    "objectif de recherche": "OBJECTIF_RD",
    "résultat de recherche": "RESULTAT_RD",
    "personne": "PERSONNE",
    "organisation": "ORGANISME",
    "lieu": "LIEU",
    "date": "DATE_PERIODE",
    "période": "DATE_PERIODE",
    "montant": "MONTANT_CIR",
    "ETP": "ETP",
    "jalon": "JALON",
}

THRESHOLD = 0.35
MAX_TEXT_CHARS = 1800


def clean_entity_text(text: str) -> str:
    return (text or "").strip(" \n\t.,;:()[]{}\"'")


def is_bad_entity(text: str) -> bool:
    t = clean_entity_text(text)
    upper = t.upper()

    if len(t) < 2:
        return True

    noise = {
        "SECTION", "TABLEAU", "FIGURE", "ANNEXE",
        "R&D", "CIR", "PAGE", "DOCX", "PDF",
        "OUI", "NON", "-", "_",
    }

    if upper in noise:
        return True

    if upper.startswith("[SECTION"):
        return True

    if upper.startswith("[TABLEAU"):
        return True

    return False


def predict_entities(model, text: str):
    text = text[:MAX_TEXT_CHARS]

    predictions = model.predict_entities(
        text,
        LABELS,
        threshold=THRESHOLD,
    )

    entities = []

    for p in predictions:
        raw_text = clean_entity_text(p.get("text", ""))
        raw_label = p.get("label", "")
        score = float(p.get("score", 0.0))

        if is_bad_entity(raw_text):
            continue

        label = LABEL_MAP.get(raw_label, raw_label.upper())

        entities.append({
            "text": raw_text,
            "label": label,
            "start": p.get("start"),
            "end": p.get("end"),
            "score": round(score, 4),
            "status": "candidate",
        })

    return entities


def process_project(project_dir: Path, model):
    annotations_dir = project_dir / "annotations"
    input_path = annotations_dir / "chunks_for_annotation.json"
    output_path = annotations_dir / "ner_candidates.json"
    summary_path = annotations_dir / "ner_candidates_summary.json"

    if not input_path.exists():
        print(f"⚠️ Pas de chunks_for_annotation.json : {project_dir.name}")
        return

    with open(input_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    results = []
    total_entities = 0

    print(f"\n📁 Projet : {project_dir.name}")
    print(f"   chunks annotation : {len(chunks)}")

    for i, chunk in enumerate(chunks, start=1):
        text = chunk.get("text", "")

        try:
            entities = predict_entities(model, text)
        except Exception as e:
            entities = []
            chunk["ner_error"] = str(e)

        item = dict(chunk)
        item["entities"] = entities
        item["annotation_status"] = "candidate_generated"

        results.append(item)
        total_entities += len(entities)

        if i % 50 == 0:
            print(f"   ... {i}/{len(chunks)} chunks traités")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    summary = {
        "project_id": project_dir.name,
        "date": datetime.now().isoformat(timespec="seconds"),
        "model": MODEL_NAME,
        "threshold": THRESHOLD,
        "chunks_count": len(chunks),
        "entities_candidates_count": total_entities,
        "output_file": str(output_path),
    }

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(
        f"   ✅ fini | chunks={len(chunks)} | "
        f"entities candidates={total_entities}"
    )

def project_number_from_name(name: str):
    """
    Accepte :
    projet_15_
    projet_15
    Projet 15
    """
    import re

    match = re.search(r"projet[\s_ -]*(\d+)", name.lower())
    if not match:
        return None

    return int(match.group(1))


def main():
    START_PROJECT = 15
    END_PROJECT = 32

    print("Chargement GLiNER...")
    model = GLiNER.from_pretrained(MODEL_NAME)
    print("✅ Modèle chargé")

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
        f"de projet_{START_PROJECT}_ à projet_{END_PROJECT}_ : {len(projects)}"
    )

    if not projects:
        print("⚠️ Aucun projet trouvé.")
        print(f"Vérifie le dossier : {PROJECTS_DIR}")
        return

    for project_dir in projects:
        process_project(project_dir, model)

    print(
        f"\n✅ Génération ner_candidates terminée "
        f"pour projet_{START_PROJECT}_ → projet_{END_PROJECT}_."
    )


if __name__ == "__main__":
    main()