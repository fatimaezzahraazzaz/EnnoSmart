from pathlib import Path
import json
import random
import re
from collections import Counter, defaultdict

# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(r"C:\EnnoSmart")
PROJECTS_DIR = BASE_DIR / "projects"

OUTPUT_DIR = BASE_DIR / "train" / "final_gliner_cir_dataset"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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

# Labels vraiment utiles pour fine-tuning CIR/R&D
CORE_LABELS = {
    "VERROU_TECH",
    "METHODE_RD",
    "TECHNOLOGIE_RD",
    "EQUIPEMENT_RD",
    "COMPOSANT_TECHNIQUE",
    "MATERIAU_SPECIFIQUE",
    "DOMAINE_RD",
    "RESULTAT_RD",
    "OBJECTIF_RD",
}

# Labels secondaires : utiles mais à limiter fortement
SECONDARY_LABELS = {
    "ORGANISME",
    "DATE_PERIODE",
    "PERSONNE",
    "LIEU",
    "MONTANT_CIR",
    "ETP",
    "JALON",
}

ALLOWED_LABELS = CORE_LABELS | SECONDARY_LABELS

# Maximum global pour éviter que DATE/PERSONNE/LIEU dominent
MAX_GLOBAL_PER_SECONDARY_LABEL = {
    "ORGANISME": 700,
    "DATE_PERIODE": 500,
    "PERSONNE": 250,
    "LIEU": 200,
    "MONTANT_CIR": 120,
    "ETP": 100,
    "JALON": 80,
}

# Maximum par projet pour les labels secondaires
MAX_PER_PROJECT_SECONDARY_LABEL = {
    "ORGANISME": 80,
    "DATE_PERIODE": 60,
    "PERSONNE": 35,
    "LIEU": 25,
    "MONTANT_CIR": 20,
    "ETP": 15,
    "JALON": 12,
}

# Limiter les gros projets pour éviter domination projet_3/projet_4/projet_12
MAX_ITEMS_PER_PROJECT = {
    "projet_1_": 500,
    "projet_2_": 500,
    "projet_3_": 650,
    "projet_4_": 650,
    "projet_5_": 500,
    "projet_6_": 500,
    "projet_7_": 500,
    "projet_8_": 500,
    "projet_9_": 550,
    "projet_10_": 500,
    "projet_11_": 500,
    "projet_12_": 650,
    "projet_13_": 500,
    "projet_14_": 500,
}

# Minimum : si un item contient un core label, on le garde plus facilement
MIN_CORE_ENTITIES_TO_KEEP_ITEM = 1

# Split
TRAIN_RATIO = 0.80
VAL_RATIO = 0.10
TEST_RATIO = 0.10

RANDOM_SEED = 42
random.seed(RANDOM_SEED)

# Fichiers de sortie
OUT_ALL = OUTPUT_DIR / "gliner_cir_all.json"
OUT_TRAIN = OUTPUT_DIR / "gliner_cir_train.json"
OUT_VAL = OUTPUT_DIR / "gliner_cir_val.json"
OUT_TEST = OUTPUT_DIR / "gliner_cir_test.json"
OUT_REPORT = OUTPUT_DIR / "prepare_report.json"

# Bruits à supprimer encore si présents
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
    "date",
    "site",
    "ville",
    "centre",
    "architectes",
    "architecte",
    "concepteurs",
    "habitants",
    "acteurs",
}

NOISE_REGEX = [
    r"^www\.",
    r"^http",
    r".+@.+",
    r"^\d{2}\s\d{2}\s\d{2}\s\d{2}\s\d{2}$",
    r"^0+\s*0*\s*0*\s*€$",
]


# ============================================================
# UTILS
# ============================================================

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def lower_norm(text: str) -> str:
    return norm(text).lower()


def is_noise_text(text: str) -> bool:
    low = lower_norm(text)

    if not low:
        return True

    if low in NOISE_TERMS:
        return True

    for pattern in NOISE_REGEX:
        if re.match(pattern, low):
            return True

    return False


def load_project_file(project_id: str):
    ann_dir = PROJECTS_DIR / project_id / "annotations"
    clean_file = ann_dir / "ner_candidates_clean.json"
    raw_file = ann_dir / "ner_candidates.json"

    if clean_file.exists():
        return clean_file

    if raw_file.exists():
        return raw_file

    return None


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def has_overlap(e1, e2):
    return not (e1["end"] <= e2["start"] or e2["end"] <= e1["start"])


def deduplicate_and_fix_overlaps(entities):
    """
    Supprime doublons exacts et résout les overlaps.
    Priorité :
    1. CORE_LABELS
    2. entité plus longue
    3. score plus élevé
    """
    cleaned = []
    seen = set()

    for ent in entities:
        key = (
            lower_norm(ent.get("text", "")),
            ent.get("label", ""),
            ent.get("start"),
            ent.get("end"),
        )
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(ent)

    def priority(ent):
        label = ent.get("label")
        length = ent.get("end", 0) - ent.get("start", 0)
        score = ent.get("score") or 0.0
        core_priority = 1 if label in CORE_LABELS else 0
        return (core_priority, length, score)

    cleaned.sort(key=lambda e: (e.get("start", 0), -(e.get("end", 0) - e.get("start", 0))))

    final_entities = []

    for ent in cleaned:
        conflict_index = None

        for i, existing in enumerate(final_entities):
            if has_overlap(ent, existing):
                conflict_index = i
                break

        if conflict_index is None:
            final_entities.append(ent)
        else:
            existing = final_entities[conflict_index]

            if priority(ent) > priority(existing):
                final_entities[conflict_index] = ent

    final_entities.sort(key=lambda e: e.get("start", 0))
    return final_entities


def convert_item_to_gliner(item, project_id):
    """
    Format GLiNER simple :
    {
      "text": "...",
      "entities": [
        {"start": 0, "end": 10, "label": "VERROU_TECH"}
      ],
      "metadata": {...}
    }
    """
    text = item.get("text", "")
    entities = []

    for ent in item.get("entities", []):
        ent_text = norm(ent.get("text", ""))
        label = ent.get("label", "")

        if label not in ALLOWED_LABELS:
            continue

        if is_noise_text(ent_text):
            continue

        start = ent.get("start")
        end = ent.get("end")

        if not isinstance(start, int) or not isinstance(end, int):
            continue

        if start < 0 or end <= start or end > len(text):
            continue

        # Vérifier que le span correspond à peu près au texte
        span_text = norm(text[start:end])
        if ent_text and span_text and lower_norm(ent_text) != lower_norm(span_text):
            # On ne supprime pas automatiquement, mais on corrige le text interne
            ent_text = span_text

        entities.append({
            "start": start,
            "end": end,
            "label": label,
            "text": ent_text,
            "score": ent.get("score"),
            "status": ent.get("status", ""),
        })

    entities = deduplicate_and_fix_overlaps(entities)

    return {
        "text": text,
        "entities": entities,
        "metadata": {
            "project_id": project_id,
            "annotation_id": item.get("annotation_id", ""),
            "source_file": item.get("source_file", ""),
            "source_chunk_id": item.get("source_chunk_id", ""),
        }
    }


def count_core_entities(item):
    return sum(1 for e in item["entities"] if e["label"] in CORE_LABELS)


def count_labels(items):
    c = Counter()
    for item in items:
        for ent in item["entities"]:
            c[ent["label"]] += 1
    return c


def count_projects(items):
    c = Counter()
    for item in items:
        c[item["metadata"]["project_id"]] += 1
    return c


# ============================================================
# LOAD + FILTER
# ============================================================

all_candidates = []
raw_stats = {
    "loaded_projects": [],
    "missing_projects": [],
    "input_items": 0,
    "input_entities": 0,
}

for project_id in PROJECT_IDS:
    path = load_project_file(project_id)

    if path is None:
        raw_stats["missing_projects"].append(project_id)
        continue

    raw_stats["loaded_projects"].append({
        "project_id": project_id,
        "path": str(path),
    })

    data = load_json(path)
    raw_stats["input_items"] += len(data)

    project_items = []

    for item in data:
        raw_stats["input_entities"] += len(item.get("entities", []))

        gliner_item = convert_item_to_gliner(item, project_id)

        if not gliner_item["entities"]:
            continue

        # Garder surtout les items qui contiennent au moins un label CIR/R&D
        core_count = count_core_entities(gliner_item)

        if core_count >= MIN_CORE_ENTITIES_TO_KEEP_ITEM:
            project_items.append(gliner_item)
        else:
            # On garde parfois des items secondaires pour ORGANISME/DATE/etc.,
            # mais pas trop.
            if random.random() < 0.15:
                project_items.append(gliner_item)

    # Limiter les gros projets
    max_items = MAX_ITEMS_PER_PROJECT.get(project_id, 500)

    # Priorité aux items qui contiennent beaucoup de core labels
    project_items.sort(key=lambda x: count_core_entities(x), reverse=True)

    if len(project_items) > max_items:
        project_items = project_items[:max_items]

    all_candidates.extend(project_items)


# ============================================================
# LIMIT SECONDARY LABELS
# ============================================================

global_secondary_counts = Counter()
project_secondary_counts = defaultdict(Counter)

final_items = []
removed_entities_by_secondary_limit = 0
removed_empty_items = 0

random.shuffle(all_candidates)

for item in all_candidates:
    project_id = item["metadata"]["project_id"]
    kept_entities = []

    for ent in item["entities"]:
        label = ent["label"]

        if label in CORE_LABELS:
            kept_entities.append(ent)
            continue

        # Secondary labels : limiter globalement et par projet
        if label in SECONDARY_LABELS:
            max_global = MAX_GLOBAL_PER_SECONDARY_LABEL.get(label, 999999)
            max_project = MAX_PER_PROJECT_SECONDARY_LABEL.get(label, 999999)

            if global_secondary_counts[label] >= max_global:
                removed_entities_by_secondary_limit += 1
                continue

            if project_secondary_counts[project_id][label] >= max_project:
                removed_entities_by_secondary_limit += 1
                continue

            kept_entities.append(ent)
            global_secondary_counts[label] += 1
            project_secondary_counts[project_id][label] += 1

    item["entities"] = deduplicate_and_fix_overlaps(kept_entities)

    if item["entities"]:
        final_items.append(item)
    else:
        removed_empty_items += 1


# ============================================================
# FINAL SHUFFLE + SPLIT
# ============================================================

random.shuffle(final_items)

n = len(final_items)
n_train = int(n * TRAIN_RATIO)
n_val = int(n * VAL_RATIO)

train_items = final_items[:n_train]
val_items = final_items[n_train:n_train + n_val]
test_items = final_items[n_train + n_val:]


# ============================================================
# EXPORT FORMAT GLINER
# ============================================================

def strip_internal_fields(items):
    """
    Garde un format propre.
    Selon ton script d'entraînement, tu peux garder ou retirer metadata.
    Ici on garde metadata car ça aide à tracer les erreurs.
    """
    output = []

    for item in items:
        output.append({
            "text": item["text"],
            "entities": [
                {
                    "start": ent["start"],
                    "end": ent["end"],
                    "label": ent["label"],
                    "text": ent["text"],
                }
                for ent in item["entities"]
            ],
            "metadata": item["metadata"],
        })

    return output


all_out = strip_internal_fields(final_items)
train_out = strip_internal_fields(train_items)
val_out = strip_internal_fields(val_items)
test_out = strip_internal_fields(test_items)

save_json(OUT_ALL, all_out)
save_json(OUT_TRAIN, train_out)
save_json(OUT_VAL, val_out)
save_json(OUT_TEST, test_out)


# ============================================================
# REPORT
# ============================================================

report = {
    "raw_stats": raw_stats,
    "final_stats": {
        "final_items": len(final_items),
        "train_items": len(train_items),
        "val_items": len(val_items),
        "test_items": len(test_items),
        "final_entities": sum(len(x["entities"]) for x in final_items),
        "train_entities": sum(len(x["entities"]) for x in train_items),
        "val_entities": sum(len(x["entities"]) for x in val_items),
        "test_entities": sum(len(x["entities"]) for x in test_items),
    },
    "label_counts_all": dict(count_labels(final_items)),
    "label_counts_train": dict(count_labels(train_items)),
    "label_counts_val": dict(count_labels(val_items)),
    "label_counts_test": dict(count_labels(test_items)),
    "project_item_counts_all": dict(count_projects(final_items)),
    "project_item_counts_train": dict(count_projects(train_items)),
    "project_item_counts_val": dict(count_projects(val_items)),
    "project_item_counts_test": dict(count_projects(test_items)),
    "removed_entities_by_secondary_limit": removed_entities_by_secondary_limit,
    "removed_empty_items": removed_empty_items,
    "config": {
        "core_labels": sorted(CORE_LABELS),
        "secondary_labels": sorted(SECONDARY_LABELS),
        "max_global_per_secondary_label": MAX_GLOBAL_PER_SECONDARY_LABEL,
        "max_per_project_secondary_label": MAX_PER_PROJECT_SECONDARY_LABEL,
        "max_items_per_project": MAX_ITEMS_PER_PROJECT,
        "train_ratio": TRAIN_RATIO,
        "val_ratio": VAL_RATIO,
        "test_ratio": TEST_RATIO,
        "random_seed": RANDOM_SEED,
    },
    "outputs": {
        "all": str(OUT_ALL),
        "train": str(OUT_TRAIN),
        "val": str(OUT_VAL),
        "test": str(OUT_TEST),
        "report": str(OUT_REPORT),
    }
}

save_json(OUT_REPORT, report)


# ============================================================
# PRINT SUMMARY
# ============================================================

print("\n✅ Préparation dataset GLiNER CIR terminée")
print("=" * 80)

print(f"Input items       : {raw_stats['input_items']}")
print(f"Input entities    : {raw_stats['input_entities']}")
print(f"Final items       : {report['final_stats']['final_items']}")
print(f"Final entities    : {report['final_stats']['final_entities']}")

print("\n📌 Split :")
print(f"Train items       : {report['final_stats']['train_items']}")
print(f"Val items         : {report['final_stats']['val_items']}")
print(f"Test items        : {report['final_stats']['test_items']}")

print("\n📌 Labels finaux :")
for label, count in Counter(report["label_counts_all"]).most_common():
    total = report["final_stats"]["final_entities"]
    ratio = count / total if total else 0
    print(f"  {label:25s} {count:6d} ({ratio:.1%})")

print("\n📌 Items par projet :")
for project_id, count in Counter(report["project_item_counts_all"]).most_common():
    print(f"  {project_id:12s} {count:6d}")

print("\n📌 Nettoyage :")
print(f"Removed secondary entities : {removed_entities_by_secondary_limit}")
print(f"Removed empty items        : {removed_empty_items}")

print("\n📁 Fichiers générés :")
print(f"All    : {OUT_ALL}")
print(f"Train  : {OUT_TRAIN}")
print(f"Val    : {OUT_VAL}")
print(f"Test   : {OUT_TEST}")
print(f"Report : {OUT_REPORT}")
print("=" * 80)