from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_15_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_15_\annotations\ner_candidates_clean_balanced.json")

REMOVE_TEXTS = {
    "composants statiques",
    "Composants statiques",
}

FIX_TO_OBJECTIF = {
    "contrôle précis de la montée en puissance du bus DC",
    "controle precis de la montee en puissance du bus DC",
}

# réduire un peu les répétitions des objectifs très génériques
OBJECTIF_REPEAT_CAP = {
    "décharge sécurisée": 10,
    "decharge securisee": 10,
    "gestion de la montée en tension": 8,
    "gestion de la montee en tension": 8,
    "limitation des transitoires": 8,
    "précharge et décharge des condensateurs": 10,
    "precharge et decharge des condensateurs": 10,
}

def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()

def key(s):
    s = norm(s).lower()
    s = s.replace("é", "e").replace("è", "e").replace("ê", "e")
    s = s.replace("à", "a").replace("â", "a")
    s = s.replace("î", "i").replace("ï", "i")
    s = s.replace("ô", "o")
    s = s.replace("ù", "u").replace("û", "u")
    s = s.replace("ç", "c")
    s = s.replace("’", "'")
    return s

remove_keys = {key(x) for x in REMOVE_TEXTS}
fix_objectif_keys = {key(x) for x in FIX_TO_OBJECTIF}
objectif_caps = {key(k): v for k, v in OBJECTIF_REPEAT_CAP.items()}

with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

before = 0
after = 0
removed = 0
fixed = 0
objectif_seen = defaultdict(int)

for item in data:
    new_entities = []

    for ent in item.get("entities", []):
        before += 1

        text = norm(ent.get("text", ""))
        label = ent.get("label", "")
        k = key(text)

        if k in remove_keys:
            removed += 1
            continue

        if k in fix_objectif_keys and label != "OBJECTIF_RD":
            ent["label"] = "OBJECTIF_RD"
            ent["status"] = "patch_balanced"
            fixed += 1
            label = "OBJECTIF_RD"

        if label == "OBJECTIF_RD" and k in objectif_caps:
            if objectif_seen[k] >= objectif_caps[k]:
                removed += 1
                continue
            objectif_seen[k] += 1

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

print("✅ Patch projet 15 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")