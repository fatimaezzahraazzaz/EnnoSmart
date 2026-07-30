from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_32_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_32_\annotations\ner_candidates_clean_balanced.json")

REMOVE_EXACT = {
    # faux positifs dus aux recherches sans word boundary
    "co",
    "Co",
    "fid",

    # administratif / trop générique
    "résultats",
    "resultats",
    "résultats obtenus",
    "resultats obtenus",
    "verrous scientifiques",
    "verrous scientifiques et techniques",
    "verrous techniques et analytiques",
    "le second verrou",
    "recherche et développement",
    "recherche et developpement",
    "méthode développée",
    "methode developpee",
    "analyse critique des résultats",
    "analyse critique des resultats",

    # bruit RH
    "Biologie des Organisme et Ecologie",
}

FIX_LABELS = {
    # domaines
    "Filière hydrogène": "DOMAINE_RD",
    "filière hydrogène": "DOMAINE_RD",
    "filiere hydrogene": "DOMAINE_RD",
    "métrologie des gaz": "DOMAINE_RD",
    "metrologie des gaz": "DOMAINE_RD",
    "analyse des gaz": "DOMAINE_RD",
    "chimie analytique": "DOMAINE_RD",
    "chromatographie": "DOMAINE_RD",
    "détection de l’hydrogène": "DOMAINE_RD",
    "détection de l'hydrogène": "DOMAINE_RD",
    "detection de l'hydrogene": "DOMAINE_RD",
    "quantification de l’hydrogène": "DOMAINE_RD",
    "quantification de l'hydrogène": "DOMAINE_RD",
    "quantification de l'hydrogene": "DOMAINE_RD",
    "séparation isotopique de l’hydrogène": "DOMAINE_RD",
    "separation isotopique de l'hydrogene": "DOMAINE_RD",
    "piles à combustible": "DOMAINE_RD",
    "piles a combustible": "DOMAINE_RD",
    "électrolyseurs": "DOMAINE_RD",
    "electrolyseurs": "DOMAINE_RD",

    # technologies
    "GC": "TECHNOLOGIE_RD",
    "GC-TCD": "TECHNOLOGIE_RD",
    "GC-FID": "TECHNOLOGIE_RD",
    "GC-TCD/FID": "TECHNOLOGIE_RD",
    "TCD": "TECHNOLOGIE_RD",
    "FID": "TECHNOLOGIE_RD",
    "chromatographie en phase gazeuse": "TECHNOLOGIE_RD",
    "Chromatographie en phase gazeuse": "TECHNOLOGIE_RD",
    "séparation chromatographique": "TECHNOLOGIE_RD",
    "Séparation chromatographique": "TECHNOLOGIE_RD",
    "separation chromatographique": "TECHNOLOGIE_RD",
    "chromatographie cryogénique": "TECHNOLOGIE_RD",
    "chromatographie cryogenique": "TECHNOLOGIE_RD",
    "séparation isotopique": "TECHNOLOGIE_RD",
    "separation isotopique": "TECHNOLOGIE_RD",
    "détecteur à conductivité thermique": "TECHNOLOGIE_RD",
    "Détecteur à conductivité thermique": "TECHNOLOGIE_RD",
    "detecteur a conductivite thermique": "TECHNOLOGIE_RD",
    "détecteur à ionisation de flamme": "TECHNOLOGIE_RD",
    "Détecteur à ionisation de flamme": "TECHNOLOGIE_RD",
    "detecteur a ionisation de flamme": "TECHNOLOGIE_RD",

    # équipements
    "détecteur TCD": "EQUIPEMENT_RD",
    "detecteur TCD": "EQUIPEMENT_RD",
    "détecteur FID": "EQUIPEMENT_RD",
    "detecteur FID": "EQUIPEMENT_RD",
    "micro-chromatographe": "EQUIPEMENT_RD",

    # méthodes
    "méthodologie analytique robuste": "METHODE_RD",
    "methodologie analytique robuste": "METHODE_RD",
    "méthodologie expérimentale rigoureuse": "METHODE_RD",
    "methodologie experimentale rigoureuse": "METHODE_RD",
    "méthodologie expérimentale structurée": "METHODE_RD",
    "methodologie experimentale structuree": "METHODE_RD",
    "méthode analytique unifiée": "METHODE_RD",
    "methode analytique unifiee": "METHODE_RD",
    "méthodes chromatographiques universelles et robustes": "METHODE_RD",
    "methodes chromatographiques universelles et robustes": "METHODE_RD",
    "protocole expérimental": "METHODE_RD",
    "protocole experimental": "METHODE_RD",
    "protocole expérimental itératif": "METHODE_RD",
    "protocole experimental iteratif": "METHODE_RD",
    "protocoles de calibration": "METHODE_RD",
    "calibration robuste": "METHODE_RD",
    "calibration rigoureuse": "METHODE_RD",
    "validation expérimentale": "METHODE_RD",
    "validation experimentale": "METHODE_RD",
    "tests de répétabilité": "METHODE_RD",
    "tests de repetabilite": "METHODE_RD",
    "optimisation expérimentale": "METHODE_RD",
    "optimisation experimentale": "METHODE_RD",
    "optimisation de la séparation chromatographique": "METHODE_RD",
    "optimisation de la separation chromatographique": "METHODE_RD",
    "maîtrise des paramètres chromatographiques": "METHODE_RD",
    "maitrise des paramètres chromatographiques": "METHODE_RD",
    "maitrise des parametres chromatographiques": "METHODE_RD",
    "Analyse et traitement des signaux chromatographiques": "METHODE_RD",
    "analyse et traitement des signaux chromatographiques": "METHODE_RD",
    "traitement du signal": "METHODE_RD",
    "déconvolution de pics chromatographiques": "METHODE_RD",
    "Déconvolution de pics chromatographiques": "METHODE_RD",
    "deconvolution de pics chromatographiques": "METHODE_RD",
    "préparation d’échantillon": "METHODE_RD",
    "preparation d'echantillon": "METHODE_RD",
    "stabilisation instrumentale": "METHODE_RD",
    "maintenance préventive": "METHODE_RD",
    "maintenance preventive": "METHODE_RD",

    # verrous
    "matrices gazeuses complexes": "VERROU_TECH",
    "faibles concentrations": "VERROU_TECH",
    "faible sensibilité du TCD": "VERROU_TECH",
    "faible sensibilite du TCD": "VERROU_TECH",
    "sensibilité limitée": "VERROU_TECH",
    "sensibilite limitee": "VERROU_TECH",
    "co-élution": "VERROU_TECH",
    "co-elution": "VERROU_TECH",
    "co-élution des gaz légers": "VERROU_TECH",
    "co-elution des gaz legers": "VERROU_TECH",
    "dérive du signal": "VERROU_TECH",
    "derive du signal": "VERROU_TECH",
    "non-linéarité": "VERROU_TECH",
    "non-linearite": "VERROU_TECH",
    "inversion de signal": "VERROU_TECH",
    "pics atypiques": "VERROU_TECH",
    "phénomènes de diffusion": "VERROU_TECH",
    "phenomenes de diffusion": "VERROU_TECH",
    "diffusion rapide": "VERROU_TECH",
    "diffusivité élevée": "VERROU_TECH",
    "diffusivite elevee": "VERROU_TECH",
    "adsorption/désorption": "VERROU_TECH",
    "adsorption/desorption": "VERROU_TECH",
    "conductivité thermique élevée": "VERROU_TECH",
    "conductivite thermique elevee": "VERROU_TECH",
    "phénomènes physico-chimiques": "VERROU_TECH",
    "phenomenes physico-chimiques": "VERROU_TECH",
    "phénomènes thermo physiques complexes": "VERROU_TECH",
    "phenomenes thermo physiques complexes": "VERROU_TECH",
    "conditions opératoires": "VERROU_TECH",
    "conditions operatoires": "VERROU_TECH",
    "contraintes expérimentales": "VERROU_TECH",
    "contraintes experimentales": "VERROU_TECH",
    "matrices multi-composants": "VERROU_TECH",
    "dépendance aux conditions opératoires": "VERROU_TECH",
    "dependance aux conditions operatoires": "VERROU_TECH",
    "transposabilité": "VERROU_TECH",
    "transposabilite": "VERROU_TECH",
    "cohérence métrologique": "VERROU_TECH",
    "coherence metrologique": "VERROU_TECH",
    "synchronisation": "VERROU_TECH",
    "résolution chromatographique": "VERROU_TECH",
    "resolution chromatographique": "VERROU_TECH",
    "temps de rétention très court": "VERROU_TECH",
    "temps de retention tres court": "VERROU_TECH",
    "étanchéité du système": "VERROU_TECH",
    "etancheite du systeme": "VERROU_TECH",
    "pertes d’échantillon": "VERROU_TECH",
    "pertes d'echantillon": "VERROU_TECH",
    "instabilité des mesures": "VERROU_TECH",
    "instabilite des mesures": "VERROU_TECH",
    "interférences": "VERROU_TECH",
    "interferences": "VERROU_TECH",

    # objectifs
    "développer une méthode fiable et reproductible": "OBJECTIF_RD",
    "developper une methode fiable et reproductible": "OBJECTIF_RD",
    "développer une méthodologie analytique fiable et reproductible": "OBJECTIF_RD",
    "developper une methodologie analytique fiable et reproductible": "OBJECTIF_RD",
    "garantir une quantification fiable et reproductible": "OBJECTIF_RD",
    "Définir des conditions chromatographiques optimales": "OBJECTIF_RD",
    "définir des conditions chromatographiques optimales": "OBJECTIF_RD",
    "definir des conditions chromatographiques optimales": "OBJECTIF_RD",
    "Caractériser la sensibilité": "OBJECTIF_RD",
    "caractériser la sensibilité": "OBJECTIF_RD",
    "caracteriser la sensibilite": "OBJECTIF_RD",
    "Déterminer la plage de quantification fiable": "OBJECTIF_RD",
    "déterminer la plage de quantification fiable": "OBJECTIF_RD",
    "determiner la plage de quantification fiable": "OBJECTIF_RD",
    "Valider la reproductibilité des mesures": "OBJECTIF_RD",
    "valider la reproductibilité des mesures": "OBJECTIF_RD",
    "valider la reproductibilite des mesures": "OBJECTIF_RD",
    "maîtriser les phénomènes d’interaction": "OBJECTIF_RD",
    "maitriser les phenomenes d'interaction": "OBJECTIF_RD",
    "renforcer la robustesse": "OBJECTIF_RD",
    "améliorer la résolution chromatographique": "OBJECTIF_RD",
    "ameliorer la resolution chromatographique": "OBJECTIF_RD",
    "réduire le bruit de fond": "OBJECTIF_RD",
    "reduire le bruit de fond": "OBJECTIF_RD",
    "renforcer le rapport signal/bruit": "OBJECTIF_RD",
    "établir un protocole standardisé": "OBJECTIF_RD",
    "etablir un protocole standardise": "OBJECTIF_RD",

    # résultats
    "stabilité significativement améliorée": "RESULTAT_RD",
    "stabilite significativement amelioree": "RESULTAT_RD",
    "séparation renforcée des gaz légers": "RESULTAT_RD",
    "separation renforcee des gaz legers": "RESULTAT_RD",
    "quantification reproductible": "RESULTAT_RD",
    "faisabilité technique": "RESULTAT_RD",
    "faisabilite technique": "RESULTAT_RD",
    "reproductibilité des résultats": "RESULTAT_RD",
    "reproductibilite des resultats": "RESULTAT_RD",
    "bonne reproductibilité": "RESULTAT_RD",
    "bonne reproductibilite": "RESULTAT_RD",
    "récupération proche de 100 %": "RESULTAT_RD",
    "recuperation proche de 100 %": "RESULTAT_RD",
    "réduction du temps d’analyse": "RESULTAT_RD",
    "reduction du temps d'analyse": "RESULTAT_RD",
    "45 %": "RESULTAT_RD",
    "RSD": "RESULTAT_RD",
    "LOD": "RESULTAT_RD",
    "LOQ": "RESULTAT_RD",
    "linéarité": "RESULTAT_RD",
    "linearite": "RESULTAT_RD",
    "répétabilité": "RESULTAT_RD",
    "repetabilite": "RESULTAT_RD",
    "stabilité du signal": "RESULTAT_RD",
    "stabilite du signal": "RESULTAT_RD",
    "rapport signal/bruit": "RESULTAT_RD",

    # composants
    "système analytique": "COMPOSANT_TECHNIQUE",
    "systeme analytique": "COMPOSANT_TECHNIQUE",
    "chaîne analytique": "COMPOSANT_TECHNIQUE",
    "chaine analytique": "COMPOSANT_TECHNIQUE",
    "colonnes": "COMPOSANT_TECHNIQUE",
    "colonne Molecular Sieve 5A": "COMPOSANT_TECHNIQUE",
    "Molecular Sieve 5A": "COMPOSANT_TECHNIQUE",
    "colonne adsorbante": "COMPOSANT_TECHNIQUE",
    "Hayesep Q": "COMPOSANT_TECHNIQUE",
    "Carboxen": "COMPOSANT_TECHNIQUE",
    "filament": "COMPOSANT_TECHNIQUE",
    "raccords": "COMPOSANT_TECHNIQUE",
    "joints": "COMPOSANT_TECHNIQUE",
    "boucles calibrées": "COMPOSANT_TECHNIQUE",
    "boucles calibrees": "COMPOSANT_TECHNIQUE",
    "signaux chromatographiques": "COMPOSANT_TECHNIQUE",
    "signal chromatographique": "COMPOSANT_TECHNIQUE",
    "pics chromatographiques": "COMPOSANT_TECHNIQUE",
    "pic d’hydrogène": "COMPOSANT_TECHNIQUE",
    "pic d'hydrogene": "COMPOSANT_TECHNIQUE",

    # matériaux / gaz
    "hydrogène": "MATERIAU_SPECIFIQUE",
    "hydrogene": "MATERIAU_SPECIFIQUE",
    "L’hydrogène": "MATERIAU_SPECIFIQUE",
    "l’hydrogène": "MATERIAU_SPECIFIQUE",
    "H₂": "MATERIAU_SPECIFIQUE",
    "H2": "MATERIAU_SPECIFIQUE",
    "HD": "MATERIAU_SPECIFIQUE",
    "D₂": "MATERIAU_SPECIFIQUE",
    "D2": "MATERIAU_SPECIFIQUE",
    "O₂": "MATERIAU_SPECIFIQUE",
    "O2": "MATERIAU_SPECIFIQUE",
    "N₂": "MATERIAU_SPECIFIQUE",
    "N2": "MATERIAU_SPECIFIQUE",
    "CO": "MATERIAU_SPECIFIQUE",
    "CO₂": "MATERIAU_SPECIFIQUE",
    "CO2": "MATERIAU_SPECIFIQUE",
    "argon": "MATERIAU_SPECIFIQUE",
    "hélium": "MATERIAU_SPECIFIQUE",
    "helium": "MATERIAU_SPECIFIQUE",
    "gaz vecteur": "MATERIAU_SPECIFIQUE",
    "Gaz vecteur": "MATERIAU_SPECIFIQUE",
    "gaz légers": "MATERIAU_SPECIFIQUE",
    "gaz legers": "MATERIAU_SPECIFIQUE",
    "gaz permanents": "MATERIAU_SPECIFIQUE",
    "matrices complexes": "MATERIAU_SPECIFIQUE",
    "mélanges gazeux": "MATERIAU_SPECIFIQUE",
    "melanges gazeux": "MATERIAU_SPECIFIQUE",
    "échantillon": "MATERIAU_SPECIFIQUE",
    "echantillon": "MATERIAU_SPECIFIQUE",
    "gaz étalons certifiés": "MATERIAU_SPECIFIQUE",
    "gaz etalons certifies": "MATERIAU_SPECIFIQUE",
    "MOFs": "MATERIAU_SPECIFIQUE",
    "pillared-layer": "MATERIAU_SPECIFIQUE",
    "phase stationnaire": "MATERIAU_SPECIFIQUE",

    # organismes
    "NATRAN": "ORGANISME",
    "ENERGEO": "ORGANISME",
}

TEXT_CAPS = {
    "hydrogene": 7,
    "l'hydrogene": 6,
    "h2": 6,
    "hd": 5,
    "d2": 5,
    "o2": 5,
    "n2": 5,
    "co": 4,
    "co2": 5,
    "gc": 5,
    "gc-tcd": 6,
    "gc-fid": 6,
    "gc-tcd/fid": 5,
    "tcd": 6,
    "fid": 5,
    "detecteur tcd": 6,
    "detecteur fid": 5,
    "matrices gazeuses complexes": 6,
    "matrices complexes": 5,
    "gaz vecteur": 6,
    "gaz legers": 6,
    "conditions operatoires": 6,
    "co-elution": 6,
    "co-elution des gaz legers": 6,
    "chromatographie en phase gazeuse": 6,
    "separation chromatographique": 6,
    "chromatographie cryogenique": 5,
    "molecular sieve 5a": 4,
    "hayesep q": 4,
    "carboxen": 4,
    "mofs": 4,
    "lineairite": 5,
    "linearite": 5,
    "repetabilite": 5,
    "lod": 4,
    "loq": 4,
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
        "\u00a0": " ",
        "₂": "2",
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
                    "DOMAINE_RD",
                    "COMPOSANT_TECHNIQUE",
                    "EQUIPEMENT_RD",
                    "MATERIAU_SPECIFIQUE",
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

        # Supprimer uniquement les faux positifs minuscules
        if text in {"co", "Co", "fid"}:
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
    item["project_tax_type"] = "CIR"
    item["use_for_cir_training"] = True

    after += len(new_entities)

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

counts = defaultdict(int)
for item in data:
    for ent in item.get("entities", []):
        counts[ent["label"]] += 1

print("✅ Patch projet 32 terminé")
print("✅ Projet 32 = CIR, utilisable pour dataset CIR après validation")
print(f"Avant          : {before}")
print(f"Après          : {after}")
print(f"Removed clean  : {removed}")
print(f"Removed caps   : {removed_caps}")
print(f"Removed nested : {removed_nested}")
print(f"Fixed labels   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")