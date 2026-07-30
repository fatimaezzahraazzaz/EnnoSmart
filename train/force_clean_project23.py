from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_23_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_23_\annotations\ner_candidates_clean_balanced.json")

REMOVE_EXACT = {
    # trop générique / bruit
    "fournisseur de l’équipement",
    "fournisseur de l'equipement",
    "équipements existants",
    "equipements existants",
    "verrous spécifiques",
    "verrous specifiques",
    "lignes de fabrication",
    "industrie pharmaceutique",
    "industrie du Dispositif médical",
    "industrie du Dispositif medical",

    # titres / trop génériques
    "Produit TFD 4",
    "Produit TFD 7 stelmi",
    "Produit TFD 7 Stelmi",
}

FIX_LABELS = {
    # méthodes
    "tests de mousse": "METHODE_RD",
    "test de mousse": "METHODE_RD",
    "tests microbiologiques": "METHODE_RD",
    "analyses microbiologiques": "METHODE_RD",
    "analyses physico-chimiques": "METHODE_RD",
    "essais laboratoire": "METHODE_RD",
    "essais expérimentaux": "METHODE_RD",
    "essais experimentaux": "METHODE_RD",
    "essais industriels": "METHODE_RD",
    "essais de reformulation": "METHODE_RD",
    "tests de conductivité": "METHODE_RD",
    "tests de conductivite": "METHODE_RD",
    "protocole de nettoyage": "METHODE_RD",
    "protocole de prélèvement": "METHODE_RD",
    "protocole de prelevement": "METHODE_RD",
    "protocole d’analyse": "METHODE_RD",
    "protocole d'analyse": "METHODE_RD",
    "dilutions de soude": "METHODE_RD",
    "reformulations sans QSV": "METHODE_RD",
    "recalibrage de chaque formule": "METHODE_RD",

    # objectifs
    "absence de résidus de fabrication": "OBJECTIF_RD",
    "absence de residus de fabrication": "OBJECTIF_RD",
    "définir un nouveau procédé de fabrication": "OBJECTIF_RD",
    "definir un nouveau procede de fabrication": "OBJECTIF_RD",
    "augmenter la précision de fabrication": "OBJECTIF_RD",
    "augmenter la precision de fabrication": "OBJECTIF_RD",
    "assurer la reproductibilité et la robustesse du procédé": "OBJECTIF_RD",
    "assurer la reproductibilite et la robustesse du procede": "OBJECTIF_RD",
    "élaborer une stratégie de nettoyage efficace": "OBJECTIF_RD",
    "elaborer une strategie de nettoyage efficace": "OBJECTIF_RD",
    "déterminer la quantité exacte d’eau": "OBJECTIF_RD",
    "determiner la quantite exacte d'eau": "OBJECTIF_RD",

    # verrous
    "absence de référentiel normatif": "VERROU_TECH",
    "absence de referentiel normatif": "VERROU_TECH",
    "absence de normes ou méthodes spécifiques": "VERROU_TECH",
    "absence de normes ou methodes specifiques": "VERROU_TECH",
    "absence de repère visuel": "VERROU_TECH",
    "absence de repere visuel": "VERROU_TECH",
    "impossibilité d’observer le mélange": "VERROU_TECH",
    "impossibilite d'observer le melange": "VERROU_TECH",
    "non-conformités": "VERROU_TECH",
    "non-conformites": "VERROU_TECH",
    "défaut de mélange": "VERROU_TECH",
    "defaut de melange": "VERROU_TECH",
    "dosage incorrect": "VERROU_TECH",
    "mauvaise gestion thermique": "VERROU_TECH",
    "résidus invisibles": "VERROU_TECH",
    "residus invisibles": "VERROU_TECH",
    "zones difficiles d’accès": "VERROU_TECH",
    "zones difficiles d'acces": "VERROU_TECH",
    "biofilms": "VERROU_TECH",
    "forte viscosité": "VERROU_TECH",
    "forte viscosite": "VERROU_TECH",
    "mousse abondante": "VERROU_TECH",
    "forte tendance au moussage": "VERROU_TECH",
    "adhérence des résidus": "VERROU_TECH",
    "adherence des residus": "VERROU_TECH",
    "bloquent dans la tuyauterie": "VERROU_TECH",
    "produit fluorescent n’est pas adapté": "VERROU_TECH",
    "produit fluorescent n'est pas adapte": "VERROU_TECH",
    "mode de prélèvement non adapté": "VERROU_TECH",
    "mode de prelevement non adapte": "VERROU_TECH",

    # résultats
    "résultats aléatoires": "RESULTAT_RD",
    "resultats aleatoires": "RESULTAT_RD",
    "résultats corrects": "RESULTAT_RD",
    "resultats corrects": "RESULTAT_RD",
    "non conforme": "RESULTAT_RD",
    "non conformes": "RESULTAT_RD",
    "traces visibles": "RESULTAT_RD",
    "zones restaient fluorescentes": "RESULTAT_RD",
    "résidus étaient toujours visibles": "RESULTAT_RD",
    "residus etaient toujours visibles": "RESULTAT_RD",
    "350-980 UFC/mL": "RESULTAT_RD",
    "100 UFC/mL": "RESULTAT_RD",
    "redéfinitions sont toujours nécessaires": "RESULTAT_RD",
    "redefinitions sont toujours necessaires": "RESULTAT_RD",

    # technologies
    "NEP": "TECHNOLOGIE_RD",
    "nep": "TECHNOLOGIE_RD",
    "nettoyage en place": "TECHNOLOGIE_RD",
    "systèmes NEP": "TECHNOLOGIE_RD",
    "systemes NEP": "TECHNOLOGIE_RD",
    "système NEP": "TECHNOLOGIE_RD",
    "systeme NEP": "TECHNOLOGIE_RD",
    "système d’aspiration": "TECHNOLOGIE_RD",
    "systeme d'aspiration": "TECHNOLOGIE_RD",
    "système d’aspiration automatisée": "TECHNOLOGIE_RD",
    "systeme d'aspiration automatisee": "TECHNOLOGIE_RD",
    "aspiration automatisée": "TECHNOLOGIE_RD",
    "aspiration automatisee": "TECHNOLOGIE_RD",
    "formulation sans repère visuel": "TECHNOLOGIE_RD",
    "formulation sans repere visuel": "TECHNOLOGIE_RD",
    "nouveau mode de formulation": "TECHNOLOGIE_RD",
    "QSV": "TECHNOLOGIE_RD",
    "test UV": "TECHNOLOGIE_RD",
    "lampe UV": "TECHNOLOGIE_RD",
    "traçabilité par fluorescence": "TECHNOLOGIE_RD",
    "tracabilite par fluorescence": "TECHNOLOGIE_RD",
    "détection de résidus par fluorescence": "TECHNOLOGIE_RD",
    "detection de residus par fluorescence": "TECHNOLOGIE_RD",

    # équipements
    "équipements de production de détergents": "EQUIPEMENT_RD",
    "equipements de production de detergents": "EQUIPEMENT_RD",
    "cuve": "EQUIPEMENT_RD",
    "cuves": "EQUIPEMENT_RD",
    "cuve pilote": "EQUIPEMENT_RD",
    "cuve de 200L": "EQUIPEMENT_RD",
    "cuve de 200l": "EQUIPEMENT_RD",
    "cuve de remplissage Stoppil": "EQUIPEMENT_RD",
    "cuve de remplissage stoppil": "EQUIPEMENT_RD",
    "conditionneuse": "EQUIPEMENT_RD",
    "tuyauterie": "EQUIPEMENT_RD",
    "tuyaux": "EQUIPEMENT_RD",
    "canne": "EQUIPEMENT_RD",
    "blender": "EQUIPEMENT_RD",
    "vannes": "EQUIPEMENT_RD",
    "pompes": "EQUIPEMENT_RD",
    "pompe de retour": "EQUIPEMENT_RD",
    "flexibles": "EQUIPEMENT_RD",
    "agitateurs": "EQUIPEMENT_RD",
    "têtes de remplissage": "EQUIPEMENT_RD",
    "tetes de remplissage": "EQUIPEMENT_RD",
    "têtes de lavage": "EQUIPEMENT_RD",
    "tetes de lavage": "EQUIPEMENT_RD",
    "boule de lavage": "EQUIPEMENT_RD",
    "boule de rinçage": "EQUIPEMENT_RD",
    "boule de rincage": "EQUIPEMENT_RD",
    "fonds de cuve": "EQUIPEMENT_RD",
    "cuves inox": "EQUIPEMENT_RD",
    "éprouvette": "EQUIPEMENT_RD",
    "eprouvette": "EQUIPEMENT_RD",

    # composants
    "vortex": "COMPOSANT_TECHNIQUE",
    "circuits en boucle fermée": "COMPOSANT_TECHNIQUE",
    "circuits en boucle fermee": "COMPOSANT_TECHNIQUE",
    "réseaux de distribution": "COMPOSANT_TECHNIQUE",
    "reseaux de distribution": "COMPOSANT_TECHNIQUE",
    "zones critiques": "COMPOSANT_TECHNIQUE",
    "dead zones": "COMPOSANT_TECHNIQUE",

    # matières
    "TFD 4": "MATERIAU_SPECIFIQUE",
    "TFD 7 Stelmi": "MATERIAU_SPECIFIQUE",
    "TFD 7 stelmi": "MATERIAU_SPECIFIQUE",
    "matières premières": "MATERIAU_SPECIFIQUE",
    "matieres premieres": "MATERIAU_SPECIFIQUE",
    "produits détergents": "MATERIAU_SPECIFIQUE",
    "produits detergents": "MATERIAU_SPECIFIQUE",
    "détergents": "MATERIAU_SPECIFIQUE",
    "detergents": "MATERIAU_SPECIFIQUE",
    "désinfectants": "MATERIAU_SPECIFIQUE",
    "desinfectants": "MATERIAU_SPECIFIQUE",
    "soude": "MATERIAU_SPECIFIQUE",
    "éthanol": "MATERIAU_SPECIFIQUE",
    "ethanol": "MATERIAU_SPECIFIQUE",
    "tensioactif": "MATERIAU_SPECIFIQUE",
    "agent fluorescent": "MATERIAU_SPECIFIQUE",
    "produit fluorescent": "MATERIAU_SPECIFIQUE",
    "molécules fluorescentes": "MATERIAU_SPECIFIQUE",
    "molecules fluorescentes": "MATERIAU_SPECIFIQUE",
    "eau purifiée": "MATERIAU_SPECIFIQUE",
    "eau purifiee": "MATERIAU_SPECIFIQUE",
    "eau de rinçage": "MATERIAU_SPECIFIQUE",
    "eau de rincage": "MATERIAU_SPECIFIQUE",
    "solutions alcalines": "MATERIAU_SPECIFIQUE",
    "solutions acides": "MATERIAU_SPECIFIQUE",
    "agents moussants": "MATERIAU_SPECIFIQUE",
    "produits corrosifs": "MATERIAU_SPECIFIQUE",
    "résidus": "MATERIAU_SPECIFIQUE",
    "residus": "MATERIAU_SPECIFIQUE",

    # domaines
    "détergence": "DOMAINE_RD",
    "detergence": "DOMAINE_RD",
    "formulation": "DOMAINE_RD",
    "microbiologie": "DOMAINE_RD",
    "ingénierie des procédés": "DOMAINE_RD",
    "ingenierie des procedes": "DOMAINE_RD",
    "propreté microbiologique": "DOMAINE_RD",
    "proprete microbiologique": "DOMAINE_RD",
    "dispositifs médicaux": "DOMAINE_RD",
    "dispositifs medicaux": "DOMAINE_RD",
}

TEXT_CAPS = {
    "nep": 8,
    "nettoyage en place": 6,
    "détergents": 6,
    "detergents": 6,
    "matières premières": 6,
    "matieres premieres": 6,
    "formulation": 6,
    "cuve": 5,
    "cuves": 5,
    "tuyauterie": 6,
    "qsv": 5,
    "soude": 5,
    "agent fluorescent": 5,
    "produit fluorescent": 4,
    "tfd 4": 4,
    "tfd 7 stelmi": 4,
    "test uv": 5,
    "tests microbiologiques": 5,
    "non-conformités": 6,
    "non-conformites": 6,
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
        "œ": "oe",
        "\n": " ",
    })
    return s.translate(table)

def key(s):
    return strip_accents(norm(s).lower())

remove_keys = {key(x) for x in REMOVE_EXACT}
fix_keys = {key(k): v for k, v in FIX_LABELS.items()}
cap_keys = {key(k): v for k, v in TEXT_CAPS.items()}

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
        s = ent.get("start", 0)
        e = ent.get("end", 0)
        label = ent.get("label", "")
        nested = False

        for kept in final:
            ks = kept.get("start", 0)
            ke = kept.get("end", 0)
            klabel = kept.get("label", "")

            if ks <= s and e <= ke:
                if label == klabel:
                    nested = True
                    break

                if klabel in {
                    "OBJECTIF_RD",
                    "RESULTAT_RD",
                    "VERROU_TECH",
                    "METHODE_RD",
                    "TECHNOLOGIE_RD",
                    "EQUIPEMENT_RD",
                }:
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
        k = key(text)

        if not text:
            removed += 1
            continue

        if k in remove_keys:
            removed += 1
            continue

        if k in fix_keys:
            new_label = fix_keys[k]
            if ent.get("label") != new_label:
                ent["label"] = new_label
                ent["status"] = "patch_balanced"
                fixed += 1

        # supprimer les termes trop courts sauf exceptions utiles
        if len(k) <= 2:
            removed += 1
            continue

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

print("✅ Patch projet 23 terminé")
print(f"Avant          : {before}")
print(f"Après          : {after}")
print(f"Removed clean  : {removed}")
print(f"Removed caps   : {removed_caps}")
print(f"Removed nested : {removed_nested}")
print(f"Fixed labels   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")