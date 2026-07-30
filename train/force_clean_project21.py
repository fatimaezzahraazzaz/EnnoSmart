from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_21_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_21_\annotations\ner_candidates_clean_balanced.json")

REMOVE_EXACT = {
    # génériques / bruit
    "Le verrou",
    "Le verrou scientifique",
    "Les verrous techniques",
    "verrous techniques",
    "simulations",
    "bâtiment",
    "bâtiments",
    "travaux",
    "projet",
    "objectifs",
    "objectif",

    # faux positifs courts
    "patio",   # peut venir de "occupation" / trop générique
    "bso",     # peut venir de "absorbants" si non exact BSO

    # organismes non utiles pour le fine-tuning NER CIR
    "conservatoire",
    "conservatoire de musique et de danse",
    "direction",
    "ICT",
    "Musée des Beaux-Arts de Houston",
    "Eastgate",
    "Lions Campus",
    "Arup Associates",
    "ENVIROBAT-Méditerranée",
    "CIF",
    "CIH",
    "Atlantique Habitations",

    # matériaux/composants trop faibles
    "murs",
    "tubes",
    "systèmes hybrides",
    "systemes hybrides",
    "voiles",
    "dallages",
    "briques",
    "murs poreux",
    "pierre locale",

    # entités longues trop génériques issues de titres
    "Dispositifs architecturaux spécifiques pour la ventilation naturelle",
    "La régulation thermique passive en architecture",
    "objectifs nationaux de sobriété énergétique",
    "objectifs nationaux de transition énergétique",
    "objectifs d’efficacité énergétique",
}

FIX_LABELS = {
    # objectifs
    "prévenir les surchauffes estivales": "OBJECTIF_RD",
    "prevenir les surchauffes estivales": "OBJECTIF_RD",
    "optimiser l’apport de lumière naturelle": "OBJECTIF_RD",
    "optimiser l'apport de lumiere naturelle": "OBJECTIF_RD",
    "réduire les besoins en chauffage": "OBJECTIF_RD",
    "reduire les besoins en chauffage": "OBJECTIF_RD",
    "garantir un confort d’été": "OBJECTIF_RD",
    "garantir un confort d'ete": "OBJECTIF_RD",
    "assurer le confort intérieur": "OBJECTIF_RD",
    "assurer le confort interieur": "OBJECTIF_RD",
    "évacuer les calories accumulées": "OBJECTIF_RD",
    "evacuer les calories accumulees": "OBJECTIF_RD",
    "maximiser le déphasage": "OBJECTIF_RD",
    "maximiser le dephasage": "OBJECTIF_RD",

    # domaines
    "régulation thermique passive": "DOMAINE_RD",
    "regulation thermique passive": "DOMAINE_RD",
    "stratégies bioclimatiques": "DOMAINE_RD",
    "strategies bioclimatiques": "DOMAINE_RD",
    "conception bioclimatique": "DOMAINE_RD",
    "architecture bioclimatique": "DOMAINE_RD",
    "confort thermique": "DOMAINE_RD",
    "confort d’été": "DOMAINE_RD",
    "confort d'ete": "DOMAINE_RD",
    "biomimétisme": "DOMAINE_RD",
    "biomimetisme": "DOMAINE_RD",
    "régulation hygrothermique": "DOMAINE_RD",
    "regulation hygrothermique": "DOMAINE_RD",
    "résilience climatique": "DOMAINE_RD",
    "resilience climatique": "DOMAINE_RD",

    # méthodes
    "démarche biomimétique": "METHODE_RD",
    "demarche biomimetique": "METHODE_RD",
    "méthodologie biomimétique": "METHODE_RD",
    "methodologie biomimetique": "METHODE_RD",
    "simulations thermiques dynamiques": "METHODE_RD",
    "simulation thermique dynamique": "METHODE_RD",
    "validation par simulation thermique dynamique prospective": "METHODE_RD",
    "dimensionnement": "METHODE_RD",
    "calculs de dimensionnement": "METHODE_RD",
    "études d’ensoleillement": "METHODE_RD",
    "etudes d'ensoleillement": "METHODE_RD",
    "pilotage adaptatif": "METHODE_RD",
    "ventilation nocturne conditionnelle": "METHODE_RD",
    "fichier météorologique prospectif": "METHODE_RD",
    "fichier meteorologique prospectif": "METHODE_RD",

    # verrous
    "absence de méthodologies opérationnelles": "VERROU_TECH",
    "absence de methodologies operationnelles": "VERROU_TECH",
    "incertitude scientifique": "VERROU_TECH",
    "incertitudes scientifiques": "VERROU_TECH",
    "verrous scientifiques et techniques": "VERROU_TECH",
    "phénomènes couplés": "VERROU_TECH",
    "phenomenes couples": "VERROU_TECH",
    "conditions climatiques variables": "VERROU_TECH",
    "contraintes architecturales": "VERROU_TECH",
    "lois de similitude": "VERROU_TECH",
    "inertie hétérogène": "VERROU_TECH",
    "inertie heterogene": "VERROU_TECH",
    "accumulation thermique progressive": "VERROU_TECH",
    "couplage masse thermique–ventilation nocturne": "VERROU_TECH",
    "couplage masse thermique-ventilation nocturne": "VERROU_TECH",
    "robustesse des stratégies passives": "VERROU_TECH",
    "robustesse des strategies passives": "VERROU_TECH",
    "vagues de chaleur": "VERROU_TECH",
    "amplitudes thermiques nocturnes": "VERROU_TECH",
    "humidité relative": "VERROU_TECH",
    "humidite relative": "VERROU_TECH",
    "surchauffes estivales": "VERROU_TECH",
    "manque d’inertie thermique": "VERROU_TECH",
    "manque d'inertie thermique": "VERROU_TECH",
    "dimensionnement optimal des cheminées thermiques": "VERROU_TECH",
    "dimensionnement optimal des cheminees thermiques": "VERROU_TECH",
    "gradient thermique requis": "VERROU_TECH",
    "optimisation du couplage": "VERROU_TECH",
    "calibrage de la répartition des débits": "VERROU_TECH",
    "calibrage de la repartition des debits": "VERROU_TECH",

    # résultats
    "très bon comportement thermique estival": "RESULTAT_RD",
    "tres bon comportement thermique estival": "RESULTAT_RD",
    "besoins en chauffage globalement très faibles": "RESULTAT_RD",
    "besoins en chauffage globalement tres faibles": "RESULTAT_RD",
    "diminution significative du besoin global en chauffage": "RESULTAT_RD",
    "bonne gestion des apports solaires": "RESULTAT_RD",
    "performance satisfaisante": "RESULTAT_RD",
    "réductions significatives de la consommation énergétique": "RESULTAT_RD",
    "reductions significatives de la consommation energetique": "RESULTAT_RD",

    # composants / équipements / matériaux
    "inertie thermique": "COMPOSANT_TECHNIQUE",
    "masse thermique": "COMPOSANT_TECHNIQUE",
    "masses thermiques": "COMPOSANT_TECHNIQUE",
    "ventilation naturelle": "COMPOSANT_TECHNIQUE",
    "ventilation traversante": "COMPOSANT_TECHNIQUE",
    "ventilation naturelle nocturne": "COMPOSANT_TECHNIQUE",
    "cheminée solaire": "COMPOSANT_TECHNIQUE",
    "cheminee solaire": "COMPOSANT_TECHNIQUE",
    "cheminées solaires": "COMPOSANT_TECHNIQUE",
    "cheminees solaires": "COMPOSANT_TECHNIQUE",
    "cheminées thermiques": "COMPOSANT_TECHNIQUE",
    "cheminees thermiques": "COMPOSANT_TECHNIQUE",
    "puits climatique": "COMPOSANT_TECHNIQUE",
    "protections solaires": "COMPOSANT_TECHNIQUE",
    "masques solaires": "COMPOSANT_TECHNIQUE",
    "brise-soleil orientables": "COMPOSANT_TECHNIQUE",
    "ouvrants": "COMPOSANT_TECHNIQUE",
    "ouvrants en façade": "COMPOSANT_TECHNIQUE",
    "ouvrants en facade": "COMPOSANT_TECHNIQUE",
    "grilles de transfert": "COMPOSANT_TECHNIQUE",
    "terriers de chiens de prairie": "COMPOSANT_TECHNIQUE",
    "termitières": "COMPOSANT_TECHNIQUE",
    "termitieres": "COMPOSANT_TECHNIQUE",

    "CTA": "EQUIPEMENT_RD",
    "CTA double-flux": "EQUIPEMENT_RD",
    "centrale de traitement d’air": "EQUIPEMENT_RD",
    "centrale de traitement d'air": "EQUIPEMENT_RD",
    "échangeur adiabatique": "EQUIPEMENT_RD",
    "echangeur adiabatique": "EQUIPEMENT_RD",
    "cuve de récupération des eaux pluviales": "EQUIPEMENT_RD",
    "cuve de recuperation des eaux pluviales": "EQUIPEMENT_RD",
    "ventilateurs": "EQUIPEMENT_RD",
    "ventilateurs d’appoint": "EQUIPEMENT_RD",
    "brasseurs d’air": "EQUIPEMENT_RD",

    "PCM": "MATERIAU_SPECIFIQUE",
    "béton": "MATERIAU_SPECIFIQUE",
    "beton": "MATERIAU_SPECIFIQUE",
    "béton lourd": "MATERIAU_SPECIFIQUE",
    "beton lourd": "MATERIAU_SPECIFIQUE",
    "pierre": "MATERIAU_SPECIFIQUE",
    "murs en pierre": "MATERIAU_SPECIFIQUE",
    "murs en pierres fermes": "MATERIAU_SPECIFIQUE",
    "terre cuite": "MATERIAU_SPECIFIQUE",
    "briques de terre compressée": "MATERIAU_SPECIFIQUE",
    "briques de terre compressee": "MATERIAU_SPECIFIQUE",
    "laine de bois": "MATERIAU_SPECIFIQUE",
    "polystyrène expansé": "MATERIAU_SPECIFIQUE",
    "polystyrene expanse": "MATERIAU_SPECIFIQUE",
    "sable": "MATERIAU_SPECIFIQUE",
}

TEXT_CAPS = {
    # réduire les répétitions trop fortes
    "régulation thermique passive": 10,
    "regulation thermique passive": 10,
    "stratégies bioclimatiques": 7,
    "strategies bioclimatiques": 7,
    "conception bioclimatique": 6,
    "confort thermique": 6,
    "confort d’été": 6,
    "confort d'ete": 6,
    "biomimétisme": 6,
    "biomimetisme": 6,

    "inertie thermique": 8,
    "masse thermique": 6,
    "ventilation naturelle": 8,
    "ventilation naturelle nocturne": 5,
    "cheminée solaire": 5,
    "cheminées solaires": 6,
    "puits climatique": 5,
    "protections solaires": 5,
    "ouvrants": 4,
    "termitières": 5,
    "termitieres": 5,
    "terriers de chiens de prairie": 5,

    "simulations thermiques dynamiques": 5,
    "simulation thermique dynamique": 5,
    "dimensionnement": 5,
    "pilotage adaptatif": 4,
    "héliodons": 4,
    "heliodons": 4,
    "std": 4,
    "re2020": 4,
    "pléiades": 4,
    "pleiades": 4,

    "incertitude scientifique": 5,
    "incertitudes scientifiques": 5,
    "surchauffes estivales": 5,
    "optimisation du couplage": 5,

    "béton": 4,
    "beton": 4,
    "béton lourd": 4,
    "pcm": 3,
    "briques de terre compressée": 4,
    "terre cuite": 3,
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

def is_false_short(text, original_text):
    k = key(text)

    # garder BSO seulement si le texte est exactement "BSO", sinon faux positif depuis "absorbants"
    if k == "bso" and text != "BSO":
        return True

    # patio a été ajouté trop largement ; on le garde uniquement s'il existe exactement comme mot isolé
    if k == "patio":
        pattern = r"(?<![A-Za-zÀ-ÿ])patio(?![A-Za-zÀ-ÿ])"
        if not re.search(pattern, original_text, flags=re.IGNORECASE):
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

                if klabel in {"OBJECTIF_RD", "RESULTAT_RD", "VERROU_TECH", "METHODE_RD"}:
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
        label = ent.get("label", "")
        k = key(text)

        if not text:
            removed += 1
            continue

        if k in remove_keys:
            removed += 1
            continue

        if is_false_short(text, original_text):
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

print("✅ Patch projet 21 terminé")
print(f"Avant          : {before}")
print(f"Après          : {after}")
print(f"Removed clean  : {removed}")
print(f"Removed caps   : {removed_caps}")
print(f"Removed nested : {removed_nested}")
print(f"Fixed labels   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")