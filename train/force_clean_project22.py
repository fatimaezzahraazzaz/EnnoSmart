from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_22_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_22_\annotations\ner_candidates_clean_balanced.json")

REMOVE_EXACT = {
    # administratif / trop générique
    "Objectifs visés",
    "objectif 3.5 m",
    "objectifs de hauteur",
    "objectifs agronomiques",
    "verrous scientifiques et techniques",
    "dispositif expérimentait",
    "approche expérimentale rigoureuse",
    "démarche expérimentale rigoureuse",
    "rendement",
    "diversification",
    "gestion de l’enherbement",
    "gestion de l'enherbement",
    "évaluation variétale",
    "evaluation varietale",

    # faux organismes / hors intérêt NER CIR R&D
    "ministère de la défense",
    "ministere de la defense",
    "DGA",
    "organisme public",
    "Union européenne",
    "Union europeenne",
    "Université Mulhouse",
    "Universite Mulhouse",
    "IUT Colmar",
    "INESAAE",

    # faux positifs / trop courts
    "sol",
    "filtration",  # souvent capturé dans "infiltration"
    "irfel",       # doublon de IRFEL
    "vergers",     # trop générique seul
    "pommier",     # trop générique seul si isolé
    "pommiers",    # on garde plutôt "vergers de pommiers"
    "capteurs",    # trop générique seul
    "racines",     # trop générique seul
    "axe",         # trop générique seul

    # unités / valeurs
    "5 cm",
    "15 cm",
    "25 cm",
    "35 cm",
    "45 cm",
    "55 cm",
    "60 cm",
    "20 mm",
    "25 mm",
    "40 mm",
}

FIX_LABELS = {
    # objectifs mal classés
    "maximiser l’efficacité de l’irrigation": "OBJECTIF_RD",
    "maximiser l'efficacite de l'irrigation": "OBJECTIF_RD",
    "limiter les effets du stress hydrique": "OBJECTIF_RD",
    "réduire le stress thermique": "OBJECTIF_RD",
    "reduire le stress thermique": "OBJECTIF_RD",
    "assurer une bonne irrigation": "OBJECTIF_RD",
    "sécuriser la production": "OBJECTIF_RD",
    "securiser la production": "OBJECTIF_RD",
    "renforcer la qualité des sols": "OBJECTIF_RD",
    "renforcer la qualite des sols": "OBJECTIF_RD",
    "redéfinir les systèmes de conduite": "OBJECTIF_RD",
    "redefinir les systemes de conduite": "OBJECTIF_RD",
    "maintenir la vigueur des vergers": "OBJECTIF_RD",
    "identifier des variétés et porte-greffes plus résistants": "OBJECTIF_RD",
    "identifier des varietes et porte-greffes plus resistants": "OBJECTIF_RD",
    "optimiser l’usage de la ressource": "OBJECTIF_RD",
    "optimiser l'usage de la ressource": "OBJECTIF_RD",

    # domaines
    "vergers de pommiers": "DOMAINE_RD",
    "interactions sol-plante-climat": "DOMAINE_RD",
    "gestion de l’eau": "DOMAINE_RD",
    "gestion de l'eau": "DOMAINE_RD",
    "fonctionnement hydrique": "DOMAINE_RD",
    "arboriculture": "DOMAINE_RD",
    "production fruitière": "DOMAINE_RD",
    "production fruitiere": "DOMAINE_RD",
    "fertilité des sols": "DOMAINE_RD",
    "fertilite des sols": "DOMAINE_RD",
    "agronomie": "DOMAINE_RD",
    "agroécologie": "DOMAINE_RD",
    "agroecologie": "DOMAINE_RD",
    "physiologie végétale": "DOMAINE_RD",
    "physiologie vegetale": "DOMAINE_RD",
    "systèmes arboricoles": "DOMAINE_RD",
    "systemes arboricoles": "DOMAINE_RD",

    # méthodes
    "démarche expérimentale": "METHODE_RD",
    "demarche experimentale": "METHODE_RD",
    "essais expérimentaux multi-facteurs": "METHODE_RD",
    "essais experimentaux multi-facteurs": "METHODE_RD",
    "essais comparatifs": "METHODE_RD",
    "expérimentations en conditions réelles de production": "METHODE_RD",
    "experimentations en conditions reelles de production": "METHODE_RD",
    "instrumentation et acquisition de données": "METHODE_RD",
    "instrumentation et acquisition de donnees": "METHODE_RD",
    "suivi agronomique": "METHODE_RD",
    "analyses foliaires": "METHODE_RD",
    "analyses de sol": "METHODE_RD",
    "analyse multicritères": "METHODE_RD",
    "analyse multicriteres": "METHODE_RD",
    "traitements statistiques": "METHODE_RD",
    "essais variétaux": "METHODE_RD",
    "essais varietaux": "METHODE_RD",
    "essais variétaux et porte-greffes": "METHODE_RD",
    "essais varietaux et porte-greffes": "METHODE_RD",
    "analyse de la matière organique": "METHODE_RD",
    "analyse de la matiere organique": "METHODE_RD",
    "pilotage de l’irrigation": "METHODE_RD",
    "pilotage de l'irrigation": "METHODE_RD",
    "irrigation localisée": "METHODE_RD",
    "irrigation localisee": "METHODE_RD",
    "goutte à goutte": "METHODE_RD",
    "goutte a goutte": "METHODE_RD",
    "aspersion": "METHODE_RD",
    "micro-aspersion": "METHODE_RD",
    "surgreffage": "METHODE_RD",
    "replantation": "METHODE_RD",
    "jachère": "METHODE_RD",
    "jachere": "METHODE_RD",
    "jachère nématicide": "METHODE_RD",
    "jachere nematicide": "METHODE_RD",
    "couverts végétaux": "METHODE_RD",
    "couverts vegetaux": "METHODE_RD",
    "éclaircissage mécanique": "METHODE_RD",
    "eclaircissage mecanique": "METHODE_RD",

    # verrous
    "fatigue des sols": "VERROU_TECH",
    "stress hydrique": "VERROU_TECH",
    "stress thermique": "VERROU_TECH",
    "sécheresse": "VERROU_TECH",
    "secheresse": "VERROU_TECH",
    "canicule": "VERROU_TECH",
    "gel tardifs": "VERROU_TECH",
    "gel tardif": "VERROU_TECH",
    "aléas climatiques": "VERROU_TECH",
    "aleas climatiques": "VERROU_TECH",
    "blocage du calcium": "VERROU_TECH",
    "minéralisation": "VERROU_TECH",
    "mineralisation": "VERROU_TECH",
    "antagonismes potassium/calcium": "VERROU_TECH",
    "excès de phosphore": "VERROU_TECH",
    "exces de phosphore": "VERROU_TECH",
    "déséquilibres nutritionnels": "VERROU_TECH",
    "desequilibres nutritionnels": "VERROU_TECH",
    "pathogènes telluriques": "VERROU_TECH",
    "pathogenes telluriques": "VERROU_TECH",
    "déséquilibres microbiens": "VERROU_TECH",
    "desequilibres microbiens": "VERROU_TECH",
    "phénomènes d’allélopathie": "VERROU_TECH",
    "phenomenes d'allelopathie": "VERROU_TECH",
    "absence de corrélation directe": "VERROU_TECH",
    "absence de correlation directe": "VERROU_TECH",
    "absence de modèles prédictifs": "VERROU_TECH",
    "absence de modeles predictifs": "VERROU_TECH",
    "absence de référentiels robustes": "VERROU_TECH",
    "absence de referentiels robustes": "VERROU_TECH",
    "seuils hydriques": "VERROU_TECH",
    "seuils opérationnels": "VERROU_TECH",
    "seuils operationnels": "VERROU_TECH",
    "fonctionnement racinaire": "VERROU_TECH",
    "efficacité de l’irrigation": "VERROU_TECH",
    "efficacite de l'irrigation": "VERROU_TECH",
    "profondeur d’infiltration": "VERROU_TECH",
    "profondeur d'infiltration": "VERROU_TECH",
    "temporalité longue": "VERROU_TECH",
    "temporalite longue": "VERROU_TECH",

    # résultats
    "absence d’effet significatif": "RESULTAT_RD",
    "absence d'effet significatif": "RESULTAT_RD",
    "aucune amélioration significative": "RESULTAT_RD",
    "aucune amelioration significative": "RESULTAT_RD",
    "évolution positive": "RESULTAT_RD",
    "evolution positive": "RESULTAT_RD",
    "différences significatives": "RESULTAT_RD",
    "differences significatives": "RESULTAT_RD",
    "pas de différence significative": "RESULTAT_RD",
    "pas de difference significative": "RESULTAT_RD",
    "bonne vigueur": "RESULTAT_RD",
    "faible vigueur": "RESULTAT_RD",
    "bonne fructification": "RESULTAT_RD",
    "faible fructification": "RESULTAT_RD",
    "bonne absorption": "RESULTAT_RD",
    "infiltration efficace": "RESULTAT_RD",
    "chute homogène": "RESULTAT_RD",
    "chute homogene": "RESULTAT_RD",
    "bon effet de chute": "RESULTAT_RD",
    "bonne coloration": "RESULTAT_RD",
    "rendements stables": "RESULTAT_RD",

    # composants / équipements / matières
    "système racinaire": "COMPOSANT_TECHNIQUE",
    "systeme racinaire": "COMPOSANT_TECHNIQUE",
    "porte-greffe": "COMPOSANT_TECHNIQUE",
    "porte-greffes": "COMPOSANT_TECHNIQUE",
    "M9": "COMPOSANT_TECHNIQUE",
    "G11": "COMPOSANT_TECHNIQUE",
    "G41": "COMPOSANT_TECHNIQUE",
    "Geneva 11": "COMPOSANT_TECHNIQUE",
    "Geneva 41": "COMPOSANT_TECHNIQUE",
    "Pajam 2": "COMPOSANT_TECHNIQUE",
    "M200": "COMPOSANT_TECHNIQUE",
    "MM 111": "COMPOSANT_TECHNIQUE",
    "MM 116": "COMPOSANT_TECHNIQUE",
    "Ba29": "COMPOSANT_TECHNIQUE",
    "BA 29": "COMPOSANT_TECHNIQUE",
    "OHF69": "COMPOSANT_TECHNIQUE",
    "OHF 87": "COMPOSANT_TECHNIQUE",
    "Pyrodwarf": "COMPOSANT_TECHNIQUE",
    "Pyriam": "COMPOSANT_TECHNIQUE",
    "greffon": "COMPOSANT_TECHNIQUE",
    "biaxe": "COMPOSANT_TECHNIQUE",
    "axe haut": "COMPOSANT_TECHNIQUE",
    "axe bas": "COMPOSANT_TECHNIQUE",
    "double axe": "COMPOSANT_TECHNIQUE",
    "canopée": "COMPOSANT_TECHNIQUE",
    "canopee": "COMPOSANT_TECHNIQUE",

    "sondes capacitives": "EQUIPEMENT_RD",
    "capteurs d’humidité": "EQUIPEMENT_RD",
    "capteurs d'humidite": "EQUIPEMENT_RD",
    "pied à coulisse": "EQUIPEMENT_RD",
    "pied a coulisse": "EQUIPEMENT_RD",
    "Mitutoyo CD-15DC": "EQUIPEMENT_RD",
    "pompes": "EQUIPEMENT_RD",
    "conduites de distribution": "EQUIPEMENT_RD",
    "puits": "EQUIPEMENT_RD",
    "tracteur": "EQUIPEMENT_RD",
    "rotor": "EQUIPEMENT_RD",
    "tiges semi-rigides": "EQUIPEMENT_RD",
    "machine Éclairvale": "EQUIPEMENT_RD",
    "machine Eclairvale": "EQUIPEMENT_RD",

    "matière organique": "MATERIAU_SPECIFIQUE",
    "matiere organique": "MATERIAU_SPECIFIQUE",
    "compost": "MATERIAU_SPECIFIQUE",
    "azote": "MATERIAU_SPECIFIQUE",
    "phosphore": "MATERIAU_SPECIFIQUE",
    "potassium": "MATERIAU_SPECIFIQUE",
    "potasse": "MATERIAU_SPECIFIQUE",
    "calcium": "MATERIAU_SPECIFIQUE",
    "magnésium": "MATERIAU_SPECIFIQUE",
    "magnesium": "MATERIAU_SPECIFIQUE",
    "engrais verts": "MATERIAU_SPECIFIQUE",
    "Tagetes patula": "MATERIAU_SPECIFIQUE",
    "radis fourrager": "MATERIAU_SPECIFIQUE",
    "Inogo": "MATERIAU_SPECIFIQUE",
    "INOGO": "MATERIAU_SPECIFIQUE",
    "Early Crunch": "MATERIAU_SPECIFIQUE",
    "Valstar": "MATERIAU_SPECIFIQUE",
    "Wellant": "MATERIAU_SPECIFIQUE",
    "Natti": "MATERIAU_SPECIFIQUE",
    "Fuji Kiku 8": "MATERIAU_SPECIFIQUE",
    "Gala": "MATERIAU_SPECIFIQUE",
    "Gala Brookfield": "MATERIAU_SPECIFIQUE",
    "Indigo": "MATERIAU_SPECIFIQUE",
    "Topaz": "MATERIAU_SPECIFIQUE",
    "Story": "MATERIAU_SPECIFIQUE",
    "Choupette": "MATERIAU_SPECIFIQUE",
    "Tentation": "MATERIAU_SPECIFIQUE",
    "Dalirene": "MATERIAU_SPECIFIQUE",
    "Novembra": "MATERIAU_SPECIFIQUE",

    "VEREXAL": "ORGANISME",
    "Verexal": "ORGANISME",
    "IRFEL": "ORGANISME",
    "OCDE": "ORGANISME",
    "INRAe": "ORGANISME",
    "INRAE": "ORGANISME",
    "CTIFL": "ORGANISME",
    "Fédération des producteurs de fruits du Bas-Rhin": "ORGANISME",
}

TEXT_CAPS = {
    "vergers de pommiers": 8,
    "interactions sol-plante-climat": 8,
    "gestion de l’eau": 7,
    "gestion de l'eau": 7,
    "arboriculture": 6,
    "production fruitière": 5,
    "production fruitiere": 5,
    "fonctionnement hydrique": 5,

    "fatigue des sols": 8,
    "stress hydrique": 7,
    "stress thermique": 5,
    "minéralisation": 7,
    "mineralisation": 7,
    "aléas climatiques": 5,
    "aleas climatiques": 5,
    "absence de référentiels robustes": 4,
    "absence de referentiels robustes": 4,
    "seuils hydriques": 4,
    "seuils opérationnels": 4,
    "efficacité de l’irrigation": 5,
    "profondeur d’infiltration": 5,

    "démarche expérimentale": 5,
    "essais variétaux et porte-greffes": 5,
    "pilotage de l’irrigation": 6,
    "surgreffage": 5,
    "replantation": 6,
    "jachère": 5,
    "couverts végétaux": 5,
    "goutte à goutte": 5,
    "aspersion": 5,

    "porte-greffe": 7,
    "porte-greffes": 7,
    "système racinaire": 7,
    "geneva 11": 5,
    "geneva 41": 5,
    "m9": 5,
    "g11": 5,
    "g41": 5,
    "biaxe": 4,
    "axe haut": 4,
    "axe bas": 4,

    "matière organique": 8,
    "matiere organique": 8,
    "compost": 8,
    "azote": 5,
    "phosphore": 5,
    "potassium": 5,
    "potasse": 5,
    "calcium": 5,

    "verexal": 5,
    "irfel": 2,
    "ocde": 2,
    "inrae": 3,
    "ctifl": 3,
    "éclairvale": 6,
    "eclairvale": 6,
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
    })
    return s.translate(table)

def key(s):
    return strip_accents(norm(s).lower())

remove_keys = {key(x) for x in REMOVE_EXACT}
fix_keys = {key(k): v for k, v in FIX_LABELS.items()}
cap_keys = {key(k): v for k, v in TEXT_CAPS.items()}

def is_bad_context(ent, original_text):
    text = norm(ent.get("text", ""))
    k = key(text)
    s = ent.get("start", 0)
    e = ent.get("end", 0)

    # Supprimer "filtration" si c'est dans "infiltration"
    if k == "filtration":
        before = original_text[max(0, s - 3):s].lower()
        if "in" in before:
            return True

    # Supprimer capteurs seul, sauf dans une entité complète capteurs d'humidité
    if k == "capteurs":
        window = original_text[s:e + 20].lower()
        if "humidité" not in window and "humidite" not in strip_accents(window):
            return True

    return False

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
        s = ent.get("start")
        e = ent.get("end")
        label = ent.get("label")
        nested = False

        for kept in final:
            ks = kept.get("start")
            ke = kept.get("end")
            klabel = kept.get("label")

            if ks <= s and e <= ke:
                if label == klabel:
                    nested = True
                    break

                if klabel in {"OBJECTIF_RD", "RESULTAT_RD", "VERROU_TECH", "METHODE_RD", "DOMAINE_RD"}:
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
    original_text = item.get("text", "")
    new_entities = []

    for ent in item.get("entities", []):
        before += 1

        text = norm(ent.get("text", ""))
        k = key(text)
        label = ent.get("label", "")

        if not text:
            removed += 1
            continue

        if k in remove_keys:
            removed += 1
            continue

        if is_bad_context(ent, original_text):
            removed += 1
            continue

        if k in fix_keys:
            new_label = fix_keys[k]
            if label != new_label:
                ent["label"] = new_label
                ent["status"] = "patch_balanced"
                fixed += 1

        # sécurité : petits termes trop génériques
        if len(text) <= 3 and key(text) not in {"m9", "g11", "g41"}:
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

print("✅ Patch projet 22 terminé")
print(f"Avant          : {before}")
print(f"Après          : {after}")
print(f"Removed clean  : {removed}")
print(f"Removed caps   : {removed_caps}")
print(f"Removed nested : {removed_nested}")
print(f"Fixed labels   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")