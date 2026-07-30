from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_16_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_16_\annotations\ner_candidates_clean_balanced.json")

REMOVE_EXACT = {
    # faux positifs dus aux sous-chaînes
    "cre",
    "cem",

    # génériques / faux organismes
    "fabricants de véhicules militaires",
    "leaders de l'industrie des véhicules blindés",
    "armée française",

    # trop faibles
    "objectifs techniques",
}

FIX_LABELS = {
    "minimum de câbles électriques et de connecteurs": "OBJECTIF_RD",
    "minimum de cables electriques et de connecteurs": "OBJECTIF_RD",

    "évaluer les niveaux d'atténuation": "OBJECTIF_RD",
    "evaluer les niveaux d'attenuation": "OBJECTIF_RD",

    "évaluer les performances": "OBJECTIF_RD",
    "evaluer les performances": "OBJECTIF_RD",

    "compatibilité radioélectrique": "DOMAINE_RD",
    "compatibilite radioelectrique": "DOMAINE_RD",

    "objectifs initiaux des essais": "OBJECTIF_RD",

    "banc de mesure": "METHODE_RD",
    "banc d’essai": "METHODE_RD",
    "banc d'essai": "METHODE_RD",

    "compatibilité électromagnétique": "VERROU_TECH",
    "compatibilite electromagnetique": "VERROU_TECH",
}

# réduire répétitions trop fortes
TEXT_CAPS = {
    "fibre optique": 5,
    "fibres optiques": 5,
    "réseau électrique": 5,
    "reseau electrique": 5,
    "essais climatiques": 4,
    "essais électriques": 5,
    "essais electriques": 5,
    "essais cem": 4,
    "cem": 0,
    "cre": 0,
    "fiabilité": 5,
    "fiabilite": 5,
    "non-conformités": 6,
    "non-conformites": 6,
    "problèmes techniques": 6,
    "problemes techniques": 6,
    "évaluer les performances": 5,
    "evaluer les performances": 5,
}

def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()

def strip_accents(s):
    table = str.maketrans({
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "à": "a", "â": "a", "ä": "a",
        "î": "i", "ï": "i",
        "ô": "o", "ö": "o",
        "ù": "u", "û": "u", "ü": "u",
        "ç": "c",
        "’": "'",
    })
    return s.translate(table)

def key(s):
    return strip_accents(norm(s).lower())

remove_keys = {key(x) for x in REMOVE_EXACT}
fix_keys = {key(k): v for k, v in FIX_LABELS.items()}
cap_keys = {key(k): v for k, v in TEXT_CAPS.items()}

with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

before = 0
after = 0
removed = 0
fixed = 0
seen_text = defaultdict(int)

for item in data:
    new_entities = []

    for ent in item.get("entities", []):
        before += 1

        text = norm(ent.get("text", ""))
        label = ent.get("label", "")
        k = key(text)

        if not text:
            removed += 1
            continue

        if k in remove_keys:
            removed += 1
            continue

        if k in fix_keys:
            new_label = fix_keys[k]
            if label != new_label:
                ent["label"] = new_label
                ent["status"] = "patch_balanced"
                fixed += 1
                label = new_label

        if k in cap_keys:
            if seen_text[k] >= cap_keys[k]:
                removed += 1
                continue
            seen_text[k] += 1

        ent["text"] = text
        new_entities.append(ent)

    item["entities"] = new_entities
    item["annotation_status"] = "clean_balanced_patched"
    after += len(new_entities)

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

counts = defaultdict(int)
for item in data:
    for ent in item.get("entities", []):
        counts[ent["label"]] += 1

print("✅ Patch projet 16 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")