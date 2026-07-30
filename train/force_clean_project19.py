from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_19_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_19_\annotations\ner_candidates_clean_balanced.json")

# ============================================================
# PATCH CONFIG
# ============================================================

REMOVE_EXACT = {
    # organismes non essentiels pour ce projet NER CIR
    "GIEC",
    "ADEME",

    # bruit / trop générique
    "Le verrou principal",
    "Objectif",
    "résultats",
    "Résultats",
    "ram",

    # indicateurs trop génériques en composant
    "Isolation",
    "isolation",
    "empreinte mémoire",
    "Empreinte mémoire",

    # entités de table des matières trop répétées
    "Analyse comparative",
    "Validation expérimentale",
    "Analyse expérimentale",
}

FIX_LABELS = {
    # technologies mal classées
    "VirtIO": "TECHNOLOGIE_RD",
    "VM classiques": "TECHNOLOGIE_RD",
    "machines virtuelles": "TECHNOLOGIE_RD",
    "conteneurs": "TECHNOLOGIE_RD",
    "hyperviseurs": "TECHNOLOGIE_RD",

    # domaines
    "virtualisation des systèmes": "DOMAINE_RD",
    "virtualisation des systèmes et des réseaux": "DOMAINE_RD",
    "technologies de virtualisation": "DOMAINE_RD",
    "environnements virtualisés": "DOMAINE_RD",
    "systèmes informatiques virtualisés": "DOMAINE_RD",
    "virtualisation légère": "DOMAINE_RD",

    # méthodes
    "protocole expérimental": "METHODE_RD",
    "protocole de mesure énergétique": "METHODE_RD",
    "méthodologie reproductible": "METHODE_RD",
    "mesure expérimentale": "METHODE_RD",
    "caractérisation comparative": "METHODE_RD",
    "modélisation prédictive": "METHODE_RD",
    "scénarios différentiels": "METHODE_RD",
    "scénarios progressifs": "METHODE_RD",
    "profilage CPU/RAM": "METHODE_RD",
    "pipeline d’exécution": "METHODE_RD",

    # verrous
    "absence d’un protocole rigoureux": "VERROU_TECH",
    "absence d’un protocole expérimental rigoureux": "VERROU_TECH",
    "bruit expérimental": "VERROU_TECH",
    "variabilité des mesures": "VERROU_TECH",
    "variabilité des résultats": "VERROU_TECH",
    "incertitude structurelle": "VERROU_TECH",
    "verrou méthodologique": "VERROU_TECH",
    "verrou méthodologique majeur": "VERROU_TECH",
    "granularité temporelle": "VERROU_TECH",
    "pas d’échantillonnage": "VERROU_TECH",
    "virtualisation imbriquée": "VERROU_TECH",
    "hétérogénéité des environnements": "VERROU_TECH",
    "limitations matérielles et logicielles": "VERROU_TECH",
    "overhead énergétique": "VERROU_TECH",
    "overhead expérimental": "VERROU_TECH",
    "instabilités réseau": "VERROU_TECH",

    # objectifs
    "réduire la consommation énergétique": "OBJECTIF_RD",
    "prédire la consommation énergétique": "OBJECTIF_RD",
    "comparer la consommation énergétique": "OBJECTIF_RD",
    "isoler l’impact énergétique": "OBJECTIF_RD",
    "déterminer la configuration virtualisée optimale": "OBJECTIF_RD",

    # résultats
    "réduction significative de la variabilité des mesures": "RESULTAT_RD",
    "réduction de taille": "RESULTAT_RD",
    "Réduction de taille": "RESULTAT_RD",
    "résultats expérimentaux": "RESULTAT_RD",
    "résultats préliminaires": "RESULTAT_RD",
    "premiers résultats de modélisation": "RESULTAT_RD",
    "stabilité temporelle": "RESULTAT_RD",
}

# Caps pour éviter que projet 19 domine trop le dataset global
TEXT_CAPS = {
    "caractérisation énergétique": 10,
    "consommation énergétique": 12,
    "efficience énergétique": 8,
    "environnements virtualisés": 8,
    "systèmes informatiques virtualisés": 5,
    "virtualisation légère": 6,
    "technologies de virtualisation": 5,

    "protocole expérimental": 8,
    "protocole de mesure énergétique": 5,
    "modélisation prédictive": 8,
    "mesure expérimentale": 6,
    "caractérisation comparative": 6,

    "docker": 8,
    "microvm": 8,
    "microvms": 8,
    "unikernel": 8,
    "unikernels": 8,
    "rapl": 6,
    "scaphandre": 6,
    "prometheus": 6,
    "grafana": 5,
    "fastapi": 5,
    "parsec": 5,
    "benchmark": 5,
    "runner": 4,

    "cpu": 6,
    "ram": 0,
    "mémoire": 4,
    "réseau": 4,
    "stockage": 4,
    "i/o": 5,
    "rootfs": 4,

    "bruit expérimental": 6,
    "granularité temporelle": 6,
    "pas d’échantillonnage": 6,
    "overhead énergétique": 6,
    "variabilité des mesures": 6,
}

# Si une entité courte est incluse dans une entité longue plus informative,
# on garde la longue.
PREFER_LONGER_OVER_NESTED = True


# ============================================================
# UTILS
# ============================================================

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

            # Supprime les entités incluses dans une entité plus longue,
            # surtout si elles portent le même label ou si la longue est OBJECTIF/RESULTAT/VERROU.
            if ks <= s and e <= ke:
                if label == klabel or klabel in {"OBJECTIF_RD", "RESULTAT_RD", "VERROU_TECH"}:
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


# ============================================================
# MAIN
# ============================================================

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
    new_entities = dedup_entities(new_entities)

    if PREFER_LONGER_OVER_NESTED:
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

print("✅ Patch projet 19 terminé")
print(f"Avant          : {before}")
print(f"Après          : {after}")
print(f"Removed clean  : {removed}")
print(f"Removed caps   : {removed_caps}")
print(f"Removed nested : {removed_nested}")
print(f"Fixed labels   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")