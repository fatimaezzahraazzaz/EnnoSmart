from pathlib import Path
import json
import csv
from collections import Counter, defaultdict
import re

# =========================
# CONFIG
# =========================

BASE_DIR = Path(r"C:\EnnoSmart")
PROJECTS_DIR = BASE_DIR / "projects"

PROJECT_IDS = [
    "projet_1_",
    "projet_2_",
    "projet_3_",
    "projet_4_",
    "projet_5_",
    "projet_6_",
    "projet_7_",
    "projet_8_",
    "projet_9_",
    "projet_10_",
    "projet_11_",
    "projet_12_",
    "projet_13_",
    "projet_14_",
]

OUTPUT_DIR = BASE_DIR / "train" / "audit_reports"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

REPORT_JSON = OUTPUT_DIR / "audit_global_cir_dataset.json"
REPORT_CSV_BY_PROJECT = OUTPUT_DIR / "audit_entities_by_project.csv"
REPORT_CSV_BY_LABEL = OUTPUT_DIR / "audit_entities_by_label.csv"
REPORT_CSV_NOISE = OUTPUT_DIR / "audit_noise_suspects.csv"
REPORT_CSV_LOW_SCORE = OUTPUT_DIR / "audit_low_score_entities.csv"
REPORT_CSV_DUPLICATES = OUTPUT_DIR / "audit_duplicate_entities.csv"

# Labels attendus pour ton dataset CIR
EXPECTED_LABELS = {
    "VERROU_TECH",
    "METHODE_RD",
    "TECHNOLOGIE_RD",
    "EQUIPEMENT_RD",
    "COMPOSANT_TECHNIQUE",
    "MATERIAU_SPECIFIQUE",
    "DOMAINE_RD",
    "ORGANISME",
    "PERSONNE",
    "RESULTAT_RD",
    "OBJECTIF_RD",
    "DATE_PERIODE",
    "MONTANT_CIR",
    "ETP",
    "LIEU",
    "JALON",
}

# Bruits fréquents qu’on ne veut pas garder dans le dataset final
NOISE_TERMS = {
    "nous",
    "notre",
    "nos",
    "objectif",
    "objectifs",
    "résultat",
    "résultats",
    "résultats obtenus",
    "client",
    "utilisateur",
    "utilisateurs",
    "personnes",
    "auteurs",
    "industriels",
    "industrie",
    "ministère de la défense",
    "crédit impôt recherche",
    "coût total",
    "000 000 €",
    "nom",
    "prénom",
    "fonction",
    "heures",
    "chef de projet",
    "thésaurus",
}

# Bruits regex
NOISE_REGEX = [
    r"^www\.",
    r"^http",
    r".+@.+",
    r"^\d{2}\s\d{2}\s\d{2}\s\d{2}\s\d{2}$",  # téléphone
    r"^0+\s*0*\s*0*\s*€$",
]

LOW_SCORE_THRESHOLD = 0.45


# =========================
# UTILS
# =========================

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def lower_norm(text: str) -> str:
    return norm(text).lower()


def is_noise(text: str) -> bool:
    low = lower_norm(text)

    if low in NOISE_TERMS:
        return True

    for pattern in NOISE_REGEX:
        if re.match(pattern, low):
            return True

    return False


def load_project_file(project_id: str):
    """
    Priorité :
    1. ner_candidates_clean.json
    2. ner_candidates.json
    """
    ann_dir = PROJECTS_DIR / project_id / "annotations"

    clean_file = ann_dir / "ner_candidates_clean.json"
    raw_file = ann_dir / "ner_candidates.json"

    if clean_file.exists():
        return clean_file, "clean"

    if raw_file.exists():
        return raw_file, "raw"

    return None, "missing"


def safe_load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_csv(path: Path, rows, fieldnames):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# =========================
# AUDIT
# =========================

global_label_counts = Counter()
global_project_counts = Counter()
global_status_counts = Counter()
global_unknown_labels = Counter()

project_label_counts = defaultdict(Counter)
project_status_counts = defaultdict(Counter)

noise_suspects = []
low_score_entities = []
duplicate_entities = []

project_summary = {}

total_chunks = 0
total_entities = 0
missing_projects = []
loaded_files = []

# Pour détecter les doublons globaux
seen_global = defaultdict(list)

for project_id in PROJECT_IDS:
    file_path, file_status = load_project_file(project_id)

    if file_path is None:
        missing_projects.append(project_id)
        project_summary[project_id] = {
            "file_status": "missing",
            "chunks": 0,
            "entities": 0,
            "labels": {},
        }
        continue

    loaded_files.append(str(file_path))

    try:
        data = safe_load_json(file_path)
    except Exception as e:
        project_summary[project_id] = {
            "file_status": "error",
            "error": str(e),
            "chunks": 0,
            "entities": 0,
            "labels": {},
        }
        continue

    chunks_count = len(data)
    entities_count = 0

    for item in data:
        total_chunks += 1

        annotation_id = item.get("annotation_id", "")
        source_file = item.get("source_file", "")
        text_block = item.get("text", "")

        entities = item.get("entities", [])

        for ent in entities:
            total_entities += 1
            entities_count += 1

            ent_text = norm(ent.get("text", ""))
            label = ent.get("label", "")
            score = ent.get("score", None)
            status = ent.get("status", "")

            global_label_counts[label] += 1
            global_project_counts[project_id] += 1
            global_status_counts[status] += 1

            project_label_counts[project_id][label] += 1
            project_status_counts[project_id][status] += 1

            if label not in EXPECTED_LABELS:
                global_unknown_labels[label] += 1

            # Noise suspects
            if is_noise(ent_text):
                noise_suspects.append({
                    "project_id": project_id,
                    "annotation_id": annotation_id,
                    "text": ent_text,
                    "label": label,
                    "score": score,
                    "status": status,
                    "source_file": source_file,
                })

            # Low score
            if isinstance(score, (int, float)) and score < LOW_SCORE_THRESHOLD:
                low_score_entities.append({
                    "project_id": project_id,
                    "annotation_id": annotation_id,
                    "text": ent_text,
                    "label": label,
                    "score": score,
                    "status": status,
                    "source_file": source_file,
                })

            # Duplicate key
            duplicate_key = (project_id, lower_norm(ent_text), label)
            seen_global[duplicate_key].append({
                "annotation_id": annotation_id,
                "text": ent_text,
                "label": label,
                "score": score,
                "status": status,
                "source_file": source_file,
            })

    project_summary[project_id] = {
        "file_status": file_status,
        "file_path": str(file_path),
        "chunks": chunks_count,
        "entities": entities_count,
        "labels": dict(project_label_counts[project_id]),
        "statuses": dict(project_status_counts[project_id]),
    }

# Duplicates
for (project_id, text_low, label), occurrences in seen_global.items():
    if len(occurrences) > 1:
        duplicate_entities.append({
            "project_id": project_id,
            "text": occurrences[0]["text"],
            "label": label,
            "count": len(occurrences),
            "example_annotation_id": occurrences[0]["annotation_id"],
            "example_source_file": occurrences[0]["source_file"],
        })


# =========================
# QUALITY WARNINGS
# =========================

warnings = []

# Labels faibles
important_labels = [
    "VERROU_TECH",
    "METHODE_RD",
    "TECHNOLOGIE_RD",
    "EQUIPEMENT_RD",
    "COMPOSANT_TECHNIQUE",
    "MATERIAU_SPECIFIQUE",
    "DOMAINE_RD",
    "RESULTAT_RD",
    "OBJECTIF_RD",
]

for label in important_labels:
    count = global_label_counts.get(label, 0)

    if count == 0:
        warnings.append(f"Label absent : {label}")
    elif count < 50:
        warnings.append(f"Label faible : {label} = {count} entités")

# Labels trop dominants
if total_entities > 0:
    for label, count in global_label_counts.most_common():
        ratio = count / total_entities
        if ratio > 0.35:
            warnings.append(
                f"Label très dominant : {label} = {count} entités ({ratio:.1%})"
            )

# Trop de noise
if len(noise_suspects) > 0:
    warnings.append(f"Noise suspects détectés : {len(noise_suspects)}")

# Unknown labels
if global_unknown_labels:
    warnings.append(f"Labels inconnus détectés : {dict(global_unknown_labels)}")

# Projets manquants
if missing_projects:
    warnings.append(f"Projets manquants : {missing_projects}")


# =========================
# EXPORT JSON
# =========================

report = {
    "dataset": "CIR projects 1 to 14",
    "projects_used": PROJECT_IDS,
    "loaded_files": loaded_files,
    "missing_projects": missing_projects,
    "total_chunks": total_chunks,
    "total_entities": total_entities,
    "global_label_counts": dict(global_label_counts),
    "global_project_counts": dict(global_project_counts),
    "global_status_counts": dict(global_status_counts),
    "unknown_labels": dict(global_unknown_labels),
    "noise_suspects_count": len(noise_suspects),
    "low_score_entities_count": len(low_score_entities),
    "duplicate_entities_count": len(duplicate_entities),
    "warnings": warnings,
    "project_summary": project_summary,
}

with open(REPORT_JSON, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)


# =========================
# EXPORT CSV
# =========================

# CSV 1 : entities by project
rows_by_project = []

for project_id in PROJECT_IDS:
    row = {
        "project_id": project_id,
        "file_status": project_summary.get(project_id, {}).get("file_status", ""),
        "chunks": project_summary.get(project_id, {}).get("chunks", 0),
        "entities": project_summary.get(project_id, {}).get("entities", 0),
    }

    for label in sorted(EXPECTED_LABELS):
        row[label] = project_label_counts[project_id].get(label, 0)

    rows_by_project.append(row)

write_csv(
    REPORT_CSV_BY_PROJECT,
    rows_by_project,
    ["project_id", "file_status", "chunks", "entities"] + sorted(EXPECTED_LABELS)
)

# CSV 2 : entities by label
rows_by_label = []

for label, count in global_label_counts.most_common():
    rows_by_label.append({
        "label": label,
        "count": count,
        "ratio": round(count / total_entities, 4) if total_entities else 0,
        "expected": label in EXPECTED_LABELS,
    })

write_csv(
    REPORT_CSV_BY_LABEL,
    rows_by_label,
    ["label", "count", "ratio", "expected"]
)

# CSV 3 : noise suspects
write_csv(
    REPORT_CSV_NOISE,
    noise_suspects,
    ["project_id", "annotation_id", "text", "label", "score", "status", "source_file"]
)

# CSV 4 : low score
write_csv(
    REPORT_CSV_LOW_SCORE,
    low_score_entities,
    ["project_id", "annotation_id", "text", "label", "score", "status", "source_file"]
)

# CSV 5 : duplicates
write_csv(
    REPORT_CSV_DUPLICATES,
    duplicate_entities,
    ["project_id", "text", "label", "count", "example_annotation_id", "example_source_file"]
)


# =========================
# PRINT SUMMARY
# =========================

print("\n✅ Audit global terminé")
print("=" * 70)
print(f"Projects          : {len(PROJECT_IDS)}")
print(f"Total chunks      : {total_chunks}")
print(f"Total entities    : {total_entities}")
print(f"Noise suspects    : {len(noise_suspects)}")
print(f"Low score entities: {len(low_score_entities)}")
print(f"Duplicates        : {len(duplicate_entities)}")
print("=" * 70)

print("\n📌 Entités par label :")
for label, count in global_label_counts.most_common():
    ratio = count / total_entities if total_entities else 0
    print(f"  {label:25s} {count:6d}  ({ratio:.1%})")

print("\n📌 Entités par projet :")
for project_id in PROJECT_IDS:
    count = global_project_counts.get(project_id, 0)
    print(f"  {project_id:12s} {count:6d}")

if warnings:
    print("\n⚠️ Warnings :")
    for w in warnings:
        print(f"  - {w}")
else:
    print("\n✅ Aucun warning majeur détecté")

print("\n📁 Rapports générés :")
print(f"  JSON global      : {REPORT_JSON}")
print(f"  CSV par projet   : {REPORT_CSV_BY_PROJECT}")
print(f"  CSV par label    : {REPORT_CSV_BY_LABEL}")
print(f"  CSV noise        : {REPORT_CSV_NOISE}")
print(f"  CSV low score    : {REPORT_CSV_LOW_SCORE}")
print(f"  CSV duplicates   : {REPORT_CSV_DUPLICATES}")