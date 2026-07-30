from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_17_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_17_\annotations\ner_candidates_clean_balanced.json")

REMOVE_EXACT = {
    # organismes RH / écoles pas utiles pour fine-tuning CIR
    "ENSEA",
    "ESIEE",
    "Université Paris XII Val de Marne",
    "Mines Paris",

    # trop génériques / bruit
    "verrou",
    "mécanique",
    "matériel",

    # faux composant dans contexte DCP
    "transmission",
}

FIX_LABELS = {
    "Garantir une exécution précise et sans latence": "OBJECTIF_RD",
    "garantir une exécution précise et sans latence": "OBJECTIF_RD",
    "Garantir une execution precise et sans latence": "OBJECTIF_RD",

    "mécanisme de synchronisation adaptatif": "METHODE_RD",
    "mecanisme de synchronisation adaptatif": "METHODE_RD",

    "Absence de contrôleur universel": "VERROU_TECH",
    "Absence de controleur universel": "VERROU_TECH",

    "réduire la latence du contrôleur": "OBJECTIF_RD",
    "reduire la latence du controleur": "OBJECTIF_RD",

    "réduction de cette latence": "OBJECTIF_RD",
    "reduction de cette latence": "OBJECTIF_RD",

    "contrôle des bancs d’essai multi-machines": "DOMAINE_RD",
    "controle des bancs d'essai multi-machines": "DOMAINE_RD",

    "Latence excessive des architectures distribuées": "VERROU_TECH",
    "Latence excessive des architectures distribuees": "VERROU_TECH",

    "synchronisation de plusieurs machines de charge": "VERROU_TECH",
    "contrôle synchrone des machines de charges": "VERROU_TECH",
    "controle synchrone des machines de charges": "VERROU_TECH",
}

# limiter répétitions trop fréquentes
TEXT_CAPS = {
    "xmod": 8,
    "dcp": 8,
    "distributed co-simulation protocol": 6,
    "co-simulation distribuée": 10,
    "co-simulation distribuee": 10,
    "simulation distribuée": 0,   # on garde plutôt co-simulation distribuée
    "simulation distribuee": 0,

    "systèmes adas": 6,
    "systemes adas": 6,
    "véhicules hybrides": 4,
    "vehicules hybrides": 4,
    "véhicules électriques": 4,
    "vehicules electriques": 4,

    "matlab/simulink": 5,
    "matlab": 4,
    "simulink": 4,
    "fmu": 5,
    "banc d’essai": 5,
    "banc d'essai": 5,
    "bancs d’essai": 5,
    "bancs d'essai": 5,
    "groupe motopropulseur": 5,
    "groupes motopropulseurs": 5,
    "moteur": 3,
    "moteur électrique": 5,
    "moteur electrique": 5,
    "moteur thermique": 5,
    "dynamomètres": 5,
    "dynamometres": 5,
    "machines de charge": 5,
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

def remove_nested_entities(entities):
    """
    Supprime les entités incluses dans une entité plus longue du même label.
    Exemple :
    co-simulation distribuée / simulation distribuée
    On garde la plus longue.
    """
    entities = sorted(
        entities,
        key=lambda e: (
            e.get("start", 0),
            -(e.get("end", 0) - e.get("start", 0))
        )
    )

    final = []

    for ent in entities:
        s = ent.get("start")
        e = ent.get("end")
        label = ent.get("label")

        nested = False
        for kept in final:
            ks = kept.get("start")
            ke = kept.get("end")
            klabel = kept.get("label")

            if label == klabel and ks <= s and e <= ke:
                nested = True
                break

        if not nested:
            final.append(ent)

    return final

with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

before = 0
after = 0
removed = 0
fixed = 0
removed_caps = 0
removed_nested = 0

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
                label = new_label
                fixed += 1

        if k in cap_keys:
            if seen_text[k] >= cap_keys[k]:
                removed_caps += 1
                continue
            seen_text[k] += 1

        ent["text"] = text
        new_entities.append(ent)

    before_nested = len(new_entities)
    new_entities = remove_nested_entities(new_entities)
    removed_nested += before_nested - len(new_entities)

    item["entities"] = new_entities
    item["annotation_status"] = "clean_balanced_patched"
    after += len(new_entities)

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

counts = defaultdict(int)
for item in data:
    for ent in item.get("entities", []):
        counts[ent["label"]] += 1

print("✅ Patch projet 17 terminé")
print(f"Avant          : {before}")
print(f"Après          : {after}")
print(f"Removed clean  : {removed}")
print(f"Removed caps   : {removed_caps}")
print(f"Removed nested : {removed_nested}")
print(f"Fixed labels   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")