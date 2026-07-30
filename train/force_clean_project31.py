from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_31_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_31_\annotations\ner_candidates_clean_balanced.json")

REMOVE_EXACT = {
    # faux positifs causés par recherche sans word boundary
    "pid",

    # trop générique / administratif
    "Démarche expérimentale",
    "démarche expérimentale",
    "Validation expérimentale",
    "validation expérimentale",
    "Campagnes de simulation",
    "campagnes de simulation",

    # expressions trop génériques
    "résultats",
    "resultats",
    "résultats obtenus",
    "resultats obtenus",
    "domaine de recherche",
    "objectif principal",
    "objectif clé",
    "objectif cle",
    "deuxième verrou",
    "deuxieme verrou",
    "verrous identifiés",
    "verrous identifies",

    # redondance courte si expression longue existe
    "compensation active",
}

FIX_LABELS = {
    # objectifs
    "atténuation efficace des oscillations": "OBJECTIF_RD",
    "attenuation efficace des oscillations": "OBJECTIF_RD",
    "limiter les oscillations": "OBJECTIF_RD",
    "réduire les oscillations": "OBJECTIF_RD",
    "reduire les oscillations": "OBJECTIF_RD",
    "concilier réactivité et limitation des oscillations": "OBJECTIF_RD",
    "concilier reactivite et limitation des oscillations": "OBJECTIF_RD",
    "assurer une forte réactivité": "OBJECTIF_RD",
    "assurer une forte reactivite": "OBJECTIF_RD",
    "assurer une continuité de commande": "OBJECTIF_RD",
    "assurer une continuite de commande": "OBJECTIF_RD",
    "compenser les dynamiques existantes": "OBJECTIF_RD",
    "optimiser les performances dynamiques": "OBJECTIF_RD",
    "maintenir un comportement stable": "OBJECTIF_RD",
    "développer une approche novatrice": "OBJECTIF_RD",
    "developper une approche novatrice": "OBJECTIF_RD",
    "développer une loi de commande hybride": "OBJECTIF_RD",
    "developper une loi de commande hybride": "OBJECTIF_RD",
    "maîtriser le comportement dynamique": "OBJECTIF_RD",
    "maitriser le comportement dynamique": "OBJECTIF_RD",

    # résultats
    "résultats expérimentaux": "RESULTAT_RD",
    "resultats experimentaux": "RESULTAT_RD",
    "réduction significative des oscillations": "RESULTAT_RD",
    "reduction significative des oscillations": "RESULTAT_RD",
    "stabilité globale": "RESULTAT_RD",
    "stabilite globale": "RESULTAT_RD",
    "oscillations sont amorties": "RESULTAT_RD",
    "oscillations restent contenues": "RESULTAT_RD",
    "régime permanent stabilisé": "RESULTAT_RD",
    "regime permanent stabilise": "RESULTAT_RD",
    "bonne atténuation": "RESULTAT_RD",
    "bonne attenuation": "RESULTAT_RD",
    "vitesse globalement stable": "RESULTAT_RD",
    "efficacité du couplage": "RESULTAT_RD",
    "efficacite du couplage": "RESULTAT_RD",
    "niveau de représentativité estimé à environ 90 %": "RESULTAT_RD",
    "niveau de representativite estime a environ 90 %": "RESULTAT_RD",
    "90 %": "RESULTAT_RD",

    # technologies
    "commande hybride": "TECHNOLOGIE_RD",
    "commande hybride couple/vitesse": "TECHNOLOGIE_RD",
    "stratégie de commande hybride": "TECHNOLOGIE_RD",
    "strategie de commande hybride": "TECHNOLOGIE_RD",
    "fonction de commande hybride": "TECHNOLOGIE_RD",
    "loi de commande hybride": "TECHNOLOGIE_RD",
    "Time Delay Control": "TECHNOLOGIE_RD",
    "TDC": "TECHNOLOGIE_RD",
    "contrôle en couple": "TECHNOLOGIE_RD",
    "controle en couple": "TECHNOLOGIE_RD",
    "contrôle en vitesse": "TECHNOLOGIE_RD",
    "controle en vitesse": "TECHNOLOGIE_RD",
    "PID": "TECHNOLOGIE_RD",
    "LQR": "TECHNOLOGIE_RD",
    "ADRC": "TECHNOLOGIE_RD",
    "SMC": "TECHNOLOGIE_RD",
    "MFAC": "TECHNOLOGIE_RD",
    "Model-Free Adaptive Control": "TECHNOLOGIE_RD",
    "Zero Vibration Derivative": "TECHNOLOGIE_RD",
    "ZVD": "TECHNOLOGIE_RD",

    # méthodes
    "input shaping": "METHODE_RD",
    "feedforward": "METHODE_RD",
    "backstepping": "METHODE_RD",
    "fonction de Lyapunov": "METHODE_RD",
    "fonctions de Lyapunov": "METHODE_RD",
    "observateur d’état étendu": "METHODE_RD",
    "observateur d'etat etendu": "METHODE_RD",
    "Slime Mould Algorithm": "METHODE_RD",
    "simulations": "METHODE_RD",
    "simulation": "METHODE_RD",
    "essais": "METHODE_RD",
    "essais réels": "METHODE_RD",
    "essais reels": "METHODE_RD",
    "essais expérimentaux": "METHODE_RD",
    "essais experimentaux": "METHODE_RD",
    "essais en conditions réelles": "METHODE_RD",
    "essais en conditions reelles": "METHODE_RD",
    "modélisation simplifiée": "METHODE_RD",
    "modelisation simplifiee": "METHODE_RD",
    "représentation simplifiée": "METHODE_RD",
    "representation simplifiee": "METHODE_RD",
    "analyse du comportement oscillant": "METHODE_RD",
    "variations paramétriques": "METHODE_RD",
    "variations parametriques": "METHODE_RD",
    "mécanismes de compensation": "METHODE_RD",
    "mecanismes de compensation": "METHODE_RD",
    "gestion des transitions": "METHODE_RD",

    # verrous
    "oscillations": "VERROU_TECH",
    "phénomènes oscillatoires": "VERROU_TECH",
    "phenomenes oscillatoires": "VERROU_TECH",
    "oscillations résiduelles": "VERROU_TECH",
    "oscillations residuelles": "VERROU_TECH",
    "phases transitoires": "VERROU_TECH",
    "transitions dynamiques": "VERROU_TECH",
    "discontinuités dans la commande": "VERROU_TECH",
    "discontinuites dans la commande": "VERROU_TECH",
    "flexibilité de leur structure": "VERROU_TECH",
    "flexibilite de leur structure": "VERROU_TECH",
    "flexibilité des câbles": "VERROU_TECH",
    "flexibilite des cables": "VERROU_TECH",
    "non-linéarités": "VERROU_TECH",
    "non-linearites": "VERROU_TECH",
    "couplages dynamiques": "VERROU_TECH",
    "nature sous-actionnée": "VERROU_TECH",
    "nature sous-actionnee": "VERROU_TECH",
    "non-collocation": "VERROU_TECH",
    "observabilité partielle": "VERROU_TECH",
    "observabilite partielle": "VERROU_TECH",
    "absence de mesures directes": "VERROU_TECH",
    "information partielle": "VERROU_TECH",
    "incertitudes paramétriques": "VERROU_TECH",
    "incertitudes parametriques": "VERROU_TECH",
    "variabilité des conditions d’exploitation": "VERROU_TECH",
    "variabilite des conditions d'exploitation": "VERROU_TECH",
    "variations de charge": "VERROU_TECH",
    "perturbations externes": "VERROU_TECH",
    "vent": "VERROU_TECH",
    "limitation en termes de réactivité": "VERROU_TECH",
    "limitation en termes de reactivite": "VERROU_TECH",
    "complexité computationnelle élevée": "VERROU_TECH",
    "complexite computationnelle elevee": "VERROU_TECH",
    "complexité dynamique": "VERROU_TECH",
    "complexite dynamique": "VERROU_TECH",
    "stabilité oscillatoire": "VERROU_TECH",
    "stabilite oscillatoire": "VERROU_TECH",

    # domaines
    "Mécatronique": "DOMAINE_RD",
    "mécatronique": "DOMAINE_RD",
    "systèmes mécaniques": "DOMAINE_RD",
    "systemes mecaniques": "DOMAINE_RD",
    "systèmes mécaniques flexibles": "DOMAINE_RD",
    "systemes mecaniques flexibles": "DOMAINE_RD",
    "systèmes mécatroniques": "DOMAINE_RD",
    "systemes mecatroniques": "DOMAINE_RD",
    "systèmes dynamiques sous-actionnés": "DOMAINE_RD",
    "systemes dynamiques sous-actionnes": "DOMAINE_RD",
    "systèmes oscillants": "DOMAINE_RD",
    "systemes oscillants": "DOMAINE_RD",
    "systèmes flexibles": "DOMAINE_RD",
    "systemes flexibles": "DOMAINE_RD",
    "systèmes non linéaires": "DOMAINE_RD",
    "systemes non lineaires": "DOMAINE_RD",
    "systèmes industriels de levage": "DOMAINE_RD",
    "systemes industriels de levage": "DOMAINE_RD",
    "commande des systèmes oscillants": "DOMAINE_RD",
    "commande des systemes oscillants": "DOMAINE_RD",

    # composants
    "flèche": "COMPOSANT_TECHNIQUE",
    "fleche": "COMPOSANT_TECHNIQUE",
    "charge": "COMPOSANT_TECHNIQUE",
    "chariot": "COMPOSANT_TECHNIQUE",
    "câbles": "COMPOSANT_TECHNIQUE",
    "cables": "COMPOSANT_TECHNIQUE",
    "variateurs de vitesse": "COMPOSANT_TECHNIQUE",
    "boucle de vitesse": "COMPOSANT_TECHNIQUE",
    "boucle de contrôle": "COMPOSANT_TECHNIQUE",
    "boucle de controle": "COMPOSANT_TECHNIQUE",
    "consigne": "COMPOSANT_TECHNIQUE",
    "correcteur": "COMPOSANT_TECHNIQUE",
    "couple moteur": "COMPOSANT_TECHNIQUE",
    "double intégrateur perturbé": "COMPOSANT_TECHNIQUE",
    "double integrateur perturbe": "COMPOSANT_TECHNIQUE",

    # équipements
    "grues de levage à longue portée": "EQUIPEMENT_RD",
    "grues de levage a longue portee": "EQUIPEMENT_RD",
    "grues de grande dimension": "EQUIPEMENT_RD",
    "grues oscillantes": "EQUIPEMENT_RD",
    "grues à tour": "EQUIPEMENT_RD",
    "grues a tour": "EQUIPEMENT_RD",
    "grue portique": "EQUIPEMENT_RD",
    "grue réelle": "EQUIPEMENT_RD",
    "grue reelle": "EQUIPEMENT_RD",
    "grue de laboratoire": "EQUIPEMENT_RD",
    "système de grue": "EQUIPEMENT_RD",
    "systeme de grue": "EQUIPEMENT_RD",
    "banc de test": "EQUIPEMENT_RD",

    # data / mesures
    "données d’entrée/sortie": "MATERIAU_SPECIFIQUE",
    "donnees d'entree/sortie": "MATERIAU_SPECIFIQUE",
    "enregistrements": "MATERIAU_SPECIFIQUE",
    "mesures directes": "MATERIAU_SPECIFIQUE",
    "paramètres du système": "MATERIAU_SPECIFIQUE",
    "parametres du systeme": "MATERIAU_SPECIFIQUE",
    "Wj": "MATERIAU_SPECIFIQUE",
    "Wmeas": "MATERIAU_SPECIFIQUE",
    "Tm": "MATERIAU_SPECIFIQUE",

    # organismes
    "Schneider": "ORGANISME",
    "ENERGEO": "ORGANISME",
}

TEXT_CAPS = {
    "mecatronique": 5,
    "systemes mecaniques": 5,
    "systemes mecaniques flexibles": 5,
    "systemes oscillants": 6,
    "commande hybride": 6,
    "commande hybride couple/vitesse": 5,
    "strategie de commande hybride": 5,
    "time delay control": 6,
    "tdc": 5,
    "controle en couple": 6,
    "controle en vitesse": 6,
    "pid": 0,
    "oscillations": 6,
    "phenomenes oscillatoires": 5,
    "oscillations residuelles": 5,
    "phases transitoires": 5,
    "transitions dynamiques": 5,
    "fleche": 5,
    "charge": 4,
    "consigne": 4,
    "vent": 4,
    "simulations": 5,
    "essais": 5,
    "input shaping": 4,
    "adrc": 4,
    "smc": 4,
    "mfac": 3,
    "schneider": 3,
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

        # supprimer faux pid minuscule uniquement
        if text == "pid":
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

print("✅ Patch projet 31 terminé")
print("✅ Projet 31 = CIR, utilisable pour dataset CIR après validation")
print(f"Avant          : {before}")
print(f"Après          : {after}")
print(f"Removed clean  : {removed}")
print(f"Removed caps   : {removed_caps}")
print(f"Removed nested : {removed_nested}")
print(f"Fixed labels   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")