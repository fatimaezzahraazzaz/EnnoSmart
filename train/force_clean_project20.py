from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_20_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_20_\annotations\ner_candidates_clean_balanced.json")

REMOVE_EXACT = {
    # bruit administratif / organismes non utiles
    "ministère de la défense",
    "DGA",
    "politiques publiques",
    "Lisière CARQUEFOU",
    "Mellinet",
    "Bagneux",

    # faux positif créé par matching dans "spécifique"
    "cif",

    # trop générique
    "durabilité",
    "matériaux d’isolation",
    "biosourcés",
    "essais",
}

FIX_LABELS = {
    # corrections de labels
    "thermique dynamique": "METHODE_RD",
    "simulations thermiques dynamiques": "METHODE_RD",
    "simulation thermique dynamique": "METHODE_RD",

    "essais": "METHODE_RD",
    "essais spécifiques": "METHODE_RD",
    "essais physiques": "METHODE_RD",
    "essais de ruine": "METHODE_RD",
    "essais AEV": "METHODE_RD",

    "BioFib": "MATERIAU_SPECIFIQUE",
    "Biofib": "MATERIAU_SPECIFIQUE",
    "Biofib’Trio": "MATERIAU_SPECIFIQUE",
    "Biofib'Trio": "MATERIAU_SPECIFIQUE",

    "BTONLIN": "ORGANISME",
    "CobBauge": "ORGANISME",
    "MABIONAT": "ORGANISME",

    "matériaux biosourcés": "DOMAINE_RD",
    "Matériaux biosourcés": "DOMAINE_RD",
    "construction bas carbone": "DOMAINE_RD",
    "systèmes constructifs biosourcés": "DOMAINE_RD",

    "acoustique": "DOMAINE_RD",
    "Acoustique": "DOMAINE_RD",
    "hygrothermie": "DOMAINE_RD",

    "confort d’été": "VERROU_TECH",
    "faible inertie": "VERROU_TECH",
    "ponts thermiques": "VERROU_TECH",
    "résistance au feu": "VERROU_TECH",
    "stabilité structurelle": "VERROU_TECH",
    "développement fongique": "VERROU_TECH",
    "tassement": "VERROU_TECH",
    "retrait": "VERROU_TECH",
    "moisissures": "VERROU_TECH",
}

TEXT_CAPS = {
    # domaines trop répétés
    "matériaux biosourcés": 18,
    "construction bas carbone": 6,
    "acoustique": 8,
    "hygrothermie": 6,
    "confort d’été": 8,

    # matériaux fréquents
    "bois/béton": 5,
    "bois-béton": 5,
    "terre/chanvre": 6,
    "terre-chanvre": 6,
    "paille": 5,
    "paille hachée": 5,
    "paille IELO": 4,
    "chènevotte": 4,
    "béton de chanvre": 6,
    "fibre de bois": 5,
    "bois": 0,

    # méthodes / technos
    "STD": 5,
    "ATEx": 5,
    "WUFI": 5,
    "RE2020": 5,
    "modélisation EF": 4,
    "modélisations WUFI": 4,

    # composants
    "ossature bois": 6,
    "structure bois": 5,
    "plancher hybride bois/béton": 4,
    "façade ossature bois": 4,
    "goujons": 4,
    "connecteurs": 4,
    "ponts thermiques": 6,
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
        "–": "-",
        "—": "-",
    })
    return s.translate(table)

def key(s):
    return strip_accents(norm(s).lower())

remove_keys = {key(x) for x in REMOVE_EXACT}
fix_keys = {key(k): v for k, v in FIX_LABELS.items()}
cap_keys = {key(k): v for k, v in TEXT_CAPS.items()}

def remove_nested_entities(entities):
    """
    Supprime les entités incluses dans une entité plus longue.
    Exemple :
    plancher hybride bois/béton + bois/béton => garde plancher hybride bois/béton
    façade ossature bois + bois => garde façade ossature bois
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
        text = key(ent.get("text", ""))

        nested = False

        for kept in final:
            ks = kept.get("start")
            ke = kept.get("end")
            klabel = kept.get("label")

            if ks <= s and e <= ke:
                # On supprime si l'entité courte est incluse dans une entité plus informative
                if label == klabel:
                    nested = True
                    break

                if klabel in {
                    "COMPOSANT_TECHNIQUE",
                    "OBJECTIF_RD",
                    "RESULTAT_RD",
                    "VERROU_TECH",
                    "METHODE_RD",
                }:
                    nested = True
                    break

        # Supprimer les matériaux trop courts inclus dans des composants
        if text in {"bois", "beton", "chanvre", "paille"}:
            for kept in final:
                ks = kept.get("start")
                ke = kept.get("end")
                klabel = kept.get("label")
                if ks <= s and e <= ke and klabel == "COMPOSANT_TECHNIQUE":
                    nested = True
                    break

        if not nested:
            final.append(ent)

    return final

def dedup_entities(entities):
    seen = set()
    out = []

    for ent in entities:
        sig = (
            ent.get("label"),
            key(ent.get("text", "")),
            ent.get("start"),
            ent.get("end"),
        )
        if sig in seen:
            continue
        seen.add(sig)
        out.append(ent)

    return out

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

        # Supprimer le faux "cif" si ce n'est pas écrit exactement CIF dans le texte
        if k == "cif" and text != "CIF":
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
    new_entities = dedup_entities(new_entities)
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

print("✅ Patch projet 20 terminé")
print(f"Avant          : {before}")
print(f"Après          : {after}")
print(f"Removed clean  : {removed}")
print(f"Removed caps   : {removed_caps}")
print(f"Removed nested : {removed_nested}")
print(f"Fixed labels   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")