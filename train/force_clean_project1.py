from pathlib import Path
import json

INPUT = Path(r"C:\EnnoSmart\projects\projet_1_\annotations\ner_candidates_rules.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_1_\annotations\ner_candidates_clean.json")

REMOVE = {
    "btoc",
    "services généraux",
    "ressources humaines",
    "communication",
    "sara.bouhout",
    "biologique-recherche",
    "biologique-recherche.com",
    "activité biologique recherche",
    "équipe réglementaire et qualité",
    "résultats de la recherche",
    "résultats",
    "résultat",
    "crédit d’impôt recherche",
    "laboratoires de recherche privés",
    "formules",
    "formules actuelles",
    "four seasons",
    "sofitel",
    "six senses",
    "groupe barrière",
    "ambassade de la beauté",
    "bon marché",
    "rive gauche",
    "ministère de la défense",
    "münzel t. et al",
    "hoek g. et al",
    "kim h.-b. et al",
    "hieda d.s. et al",
    "curpen s. et al",
    "lefebvre m.-a. et al",
    "environmental health",
    "int j environ res public health",
    "int j cosmet sci",
}

AUTO_FIX = {
    "atp": "COMPOSANT_TECHNIQUE",
    "adp": "COMPOSANT_TECHNIQUE",
    "ero": "COMPOSANT_TECHNIQUE",
    "ros": "COMPOSANT_TECHNIQUE",
    "elisa": "METHODE_RD",
    "ecar": "METHODE_RD",
    "raman": "METHODE_RD",
    "micro-imagerie raman": "METHODE_RD",
    "chimiluminescence": "METHODE_RD",
    "mélatonine": "MATERIAU_SPECIFIQUE",
    "immunight": "MATERIAU_SPECIFIQUE",
    "immunigth": "MATERIAU_SPECIFIQUE",
    "regenight": "MATERIAU_SPECIFIQUE",
    "reginight": "MATERIAU_SPECIFIQUE",
    "perfluorodécaline": "MATERIAU_SPECIFIQUE",
    "mélatonine liposomale": "TECHNOLOGIE_RD",
    "systèmes de vectorisation": "TECHNOLOGIE_RD",
    "système de vectorisation": "TECHNOLOGIE_RD",
    "vectorisation": "TECHNOLOGIE_RD",
    "complexe iv": "COMPOSANT_TECHNIQUE",
    "complexe i": "COMPOSANT_TECHNIQUE",
    "citrate synthase": "COMPOSANT_TECHNIQUE",
    "citrate synthétase": "COMPOSANT_TECHNIQUE",
}

def clean_entity(ent):
    text = (ent.get("text") or "").strip()
    label = ent.get("label")
    lower = text.lower()

    if not text:
        return None

    if lower in REMOVE:
        return None

    if "@" in text or lower.endswith(".com") or lower.endswith(".fr"):
        return None

    if label == "DATE_PERIODE" and text.isdigit() and int(text) < 1900:
        return None

    if lower in AUTO_FIX:
        ent["label"] = AUTO_FIX[lower]
        ent["status"] = "force_cleaned"

    ent["text"] = text
    return ent

with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

before = 0
after = 0
removed = 0
fixed = 0

for item in data:
    new_entities = []
    for ent in item.get("entities", []):
        before += 1
        old_label = ent.get("label")

        cleaned = clean_entity(ent)

        if cleaned is None:
            removed += 1
            continue

        if cleaned.get("label") != old_label or cleaned.get("status") == "force_cleaned":
            fixed += 1

        new_entities.append(cleaned)

    item["entities"] = new_entities
    item["annotation_status"] = "force_cleaned"
    after += len(new_entities)

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("✅ Force clean terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")
print(f"Output  : {OUTPUT}")