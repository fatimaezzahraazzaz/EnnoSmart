from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_27_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_27_\annotations\ner_candidates_clean_balanced.json")

REMOVE_EXACT = {
    # trop générique / bruit
    "Le verrou principal",
    "verrou principal",
    "transports",
    "optique",
    "spatial",
    "fibre",
    "fibres",
    "gaine",
    "câbles",
    "cables",
    "interfaces",
    "types de fibres",
    "Les résultats expérimentaux",

    # table / titres trop répétitifs
    "Fibre optique",
    "Transmission de puissance",
}

FIX_LABELS = {
    # technologies
    "harnais opto-pyrotechnique": "TECHNOLOGIE_RD",
    "harnais opto-pyrotechniques": "TECHNOLOGIE_RD",
    "système opto-pyrotechnique": "TECHNOLOGIE_RD",
    "systeme opto-pyrotechnique": "TECHNOLOGIE_RD",
    "solution opto-pyrotechnique": "TECHNOLOGIE_RD",
    "transmission de puissance optique": "TECHNOLOGIE_RD",
    "transmission de puissance optique élevée": "TECHNOLOGIE_RD",
    "transmission de puissance optique elevee": "TECHNOLOGIE_RD",
    "puissance optique élevée": "TECHNOLOGIE_RD",
    "puissance optique elevee": "TECHNOLOGIE_RD",
    "puissance optique impulsionnelle élevée": "TECHNOLOGIE_RD",
    "puissance optique impulsionnelle elevee": "TECHNOLOGIE_RD",
    "transmission optique pulsée": "TECHNOLOGIE_RD",
    "transmission optique pulsee": "TECHNOLOGIE_RD",
    "harnais pyrotechnique spatial": "TECHNOLOGIE_RD",
    "injection laser": "TECHNOLOGIE_RD",
    "OTDR": "TECHNOLOGIE_RD",
    "otdr": "TECHNOLOGIE_RD",
    "capteurs à fibres optiques": "TECHNOLOGIE_RD",
    "capteurs a fibres optiques": "TECHNOLOGIE_RD",
    "Fiber Bragg Gratings": "TECHNOLOGIE_RD",
    "FBG": "TECHNOLOGIE_RD",
    "Rayleigh": "TECHNOLOGIE_RD",
    "Brillouin": "TECHNOLOGIE_RD",
    "Raman": "TECHNOLOGIE_RD",

    # méthodes
    "Développement expérimental": "METHODE_RD",
    "développement expérimental": "METHODE_RD",
    "Démarche expérimentale": "METHODE_RD",
    "démarche expérimentale": "METHODE_RD",
    "Validation expérimentale": "METHODE_RD",
    "validation expérimentale": "METHODE_RD",
    "qualification environnementale": "METHODE_RD",
    "campagnes d’essais": "METHODE_RD",
    "campagnes d'essais": "METHODE_RD",
    "essais optiques": "METHODE_RD",
    "essais environnementaux": "METHODE_RD",
    "essais thermiques cyclés": "METHODE_RD",
    "tests de vibration": "METHODE_RD",
    "tests de chocs": "METHODE_RD",
    "procédés d’assemblage": "METHODE_RD",
    "procedes d'assemblage": "METHODE_RD",
    "méthodes de qualification": "METHODE_RD",
    "methodes de qualification": "METHODE_RD",
    "caractérisation des performances": "METHODE_RD",
    "caracterisation des performances": "METHODE_RD",
    "pré-étude": "METHODE_RD",
    "pre-etude": "METHODE_RD",
    "validations expérimentales": "METHODE_RD",
    "pertes de transmission par injection laser": "METHODE_RD",
    "pertes de retour par OTDR": "METHODE_RD",
    "pertes de retour par otdr": "METHODE_RD",

    # verrous
    "environnement spatial sévère": "VERROU_TECH",
    "environnement spatial severe": "VERROU_TECH",
    "environnements spatiaux sévères": "VERROU_TECH",
    "environnements spatiaux severes": "VERROU_TECH",
    "contraintes thermomécaniques": "VERROU_TECH",
    "contraintes thermomecaniques": "VERROU_TECH",
    "contraintes environnementales sévères": "VERROU_TECH",
    "contraintes environnementales severes": "VERROU_TECH",
    "cycles thermiques extrêmes": "VERROU_TECH",
    "cycles thermiques extremes": "VERROU_TECH",
    "vibrations": "VERROU_TECH",
    "chocs mécaniques": "VERROU_TECH",
    "chocs mecaniques": "VERROU_TECH",
    "radiations": "VERROU_TECH",
    "dégazage": "VERROU_TECH",
    "degazage": "VERROU_TECH",
    "outgassing": "VERROU_TECH",
    "fiber fuse": "VERROU_TECH",
    "micro-courbures": "VERROU_TECH",
    "macro-courbures": "VERROU_TECH",
    "pertes par micro-courbures": "VERROU_TECH",
    "pertes optiques": "VERROU_TECH",
    "pertes d’insertion": "VERROU_TECH",
    "pertes d'insertion": "VERROU_TECH",
    "pertes de retour": "VERROU_TECH",
    "claquage": "VERROU_TECH",
    "dégradation thermique": "VERROU_TECH",
    "degradation thermique": "VERROU_TECH",
    "vieillissement prématuré": "VERROU_TECH",
    "vieillissement premature": "VERROU_TECH",
    "sensibilité aux micro-courbures": "VERROU_TECH",
    "sensibilite aux micro-courbures": "VERROU_TECH",
    "dilatations thermiques différentielles": "VERROU_TECH",
    "dilatations thermiques differentielles": "VERROU_TECH",
    "effets couplés puissance-thermomécanique": "VERROU_TECH",
    "effets couples puissance-thermomecanique": "VERROU_TECH",
    "difficilement prédictibles": "VERROU_TECH",
    "difficilement predictibles": "VERROU_TECH",
    "insuffisamment documentées": "VERROU_TECH",
    "insuffisamment documentees": "VERROU_TECH",
    "faiblement documentée": "VERROU_TECH",
    "faiblement documentee": "VERROU_TECH",

    # objectifs
    "développer une solution opto-pyrotechnique": "OBJECTIF_RD",
    "developper une solution opto-pyrotechnique": "OBJECTIF_RD",
    "transmettre de la puissance optique élevée": "OBJECTIF_RD",
    "transmettre de la puissance optique elevee": "OBJECTIF_RD",
    "réduire la masse": "OBJECTIF_RD",
    "reduire la masse": "OBJECTIF_RD",
    "renforcer la sécurité": "OBJECTIF_RD",
    "renforcer la securite": "OBJECTIF_RD",
    "établir un référentiel de performance": "OBJECTIF_RD",
    "etablir un referentiel de performance": "OBJECTIF_RD",
    "définir une architecture de harnais": "OBJECTIF_RD",
    "definir une architecture de harnais": "OBJECTIF_RD",
    "maîtriser le comportement global": "OBJECTIF_RD",
    "maitriser le comportement global": "OBJECTIF_RD",

    # résultats
    "tenue mécanique et optique satisfaisante": "RESULTAT_RD",
    "tenue mecanique et optique satisfaisante": "RESULTAT_RD",
    "stabilité significativement augmentée": "RESULTAT_RD",
    "stabilite significativement augmentee": "RESULTAT_RD",
    "pertes d’insertion faibles": "RESULTAT_RD",
    "pertes d'insertion faibles": "RESULTAT_RD",
    "pertes de retour limitées": "RESULTAT_RD",
    "pertes de retour limitees": "RESULTAT_RD",
    "robustesse du système": "RESULTAT_RD",
    "robustesse du systeme": "RESULTAT_RD",
    "système expérimental validé": "RESULTAT_RD",
    "systeme experimental valide": "RESULTAT_RD",
    "lois de comportement": "RESULTAT_RD",
    "choix technologiques": "RESULTAT_RD",
    "méthodes de qualification inédites": "RESULTAT_RD",
    "methodes de qualification inedites": "RESULTAT_RD",

    # composants
    "harnais": "COMPOSANT_TECHNIQUE",
    "harnais optiques": "COMPOSANT_TECHNIQUE",
    "câble optique": "COMPOSANT_TECHNIQUE",
    "cable optique": "COMPOSANT_TECHNIQUE",
    "câbles optiques": "COMPOSANT_TECHNIQUE",
    "cables optiques": "COMPOSANT_TECHNIQUE",
    "fibres et câbles optiques": "COMPOSANT_TECHNIQUE",
    "fibres et cables optiques": "COMPOSANT_TECHNIQUE",
    "interfaces optiques": "COMPOSANT_TECHNIQUE",
    "interfaces opto-mécaniques": "COMPOSANT_TECHNIQUE",
    "interfaces opto-mecaniques": "COMPOSANT_TECHNIQUE",
    "gaines": "COMPOSANT_TECHNIQUE",
    "connecteurs": "COMPOSANT_TECHNIQUE",
    "contacts": "COMPOSANT_TECHNIQUE",
    "buffers": "COMPOSANT_TECHNIQUE",
    "buffer": "COMPOSANT_TECHNIQUE",
    "barrières de sécurité": "COMPOSANT_TECHNIQUE",
    "barrieres de securite": "COMPOSANT_TECHNIQUE",
    "unités de tir laser": "COMPOSANT_TECHNIQUE",
    "unites de tir laser": "COMPOSANT_TECHNIQUE",
    "initiateurs optiques": "COMPOSANT_TECHNIQUE",
    "protections mécaniques": "COMPOSANT_TECHNIQUE",
    "protections mecaniques": "COMPOSANT_TECHNIQUE",
    "modèle de vol": "COMPOSANT_TECHNIQUE",
    "modele de vol": "COMPOSANT_TECHNIQUE",

    # équipements
    "démonstrateur TRL4": "EQUIPEMENT_RD",
    "demonstrateur TRL4": "EQUIPEMENT_RD",
    "prototype TRL6": "EQUIPEMENT_RD",
    "démonstrateurs expérimentaux": "EQUIPEMENT_RD",
    "demonstrateurs experimentaux": "EQUIPEMENT_RD",
    "prototypes de harnais": "EQUIPEMENT_RD",

    # matériaux
    "fibres optiques": "MATERIAU_SPECIFIQUE",
    "fibre isolée": "MATERIAU_SPECIFIQUE",
    "fibre isolee": "MATERIAU_SPECIFIQUE",
    "fibre à cœur de grand diamètre": "MATERIAU_SPECIFIQUE",
    "fibre a coeur de grand diametre": "MATERIAU_SPECIFIQUE",
    "fibres monomodes standards": "MATERIAU_SPECIFIQUE",
    "fibres optiques à architecture spécifique": "MATERIAU_SPECIFIQUE",
    "fibres optiques a architecture specifique": "MATERIAU_SPECIFIQUE",
    "fibres à cristaux photoniques": "MATERIAU_SPECIFIQUE",
    "fibres a cristaux photoniques": "MATERIAU_SPECIFIQUE",
    "fibres à trous assistés": "MATERIAU_SPECIFIQUE",
    "fibres a trous assistes": "MATERIAU_SPECIFIQUE",
    "PCF": "MATERIAU_SPECIFIQUE",
    "HAF": "MATERIAU_SPECIFIQUE",
    "SMF": "MATERIAU_SPECIFIQUE",
    "acrylate": "MATERIAU_SPECIFIQUE",
    "polyamide": "MATERIAU_SPECIFIQUE",
    "silicone": "MATERIAU_SPECIFIQUE",
    "FEP": "MATERIAU_SPECIFIQUE",
    "PTFE": "MATERIAU_SPECIFIQUE",
    "PFA": "MATERIAU_SPECIFIQUE",
    "PEEK": "MATERIAU_SPECIFIQUE",
    "verre": "MATERIAU_SPECIFIQUE",

    # domaines
    "photonique": "DOMAINE_RD",
    "systèmes embarqués": "DOMAINE_RD",
    "systemes embarques": "DOMAINE_RD",
    "technologie des systèmes": "DOMAINE_RD",
    "technologie des systemes": "DOMAINE_RD",
    "applications spatiales": "DOMAINE_RD",
    "transmission optique": "DOMAINE_RD",

    # organismes
    "Latécoère": "ORGANISME",
    "Latecoere": "ORGANISME",
    "NASA": "ORGANISME",
    "agence spatiale européenne": "ORGANISME",
    "agence spatiale europeenne": "ORGANISME",
    "ESA": "ORGANISME",
}

TEXT_CAPS = {
    "harnais opto-pyrotechnique": 7,
    "harnais opto-pyrotechniques": 7,
    "harnais": 4,
    "transmission de puissance optique": 6,
    "transmission de puissance optique élevée": 6,
    "puissance optique élevée": 5,
    "puissance optique impulsionnelle élevée": 5,
    "environnement spatial sévère": 6,
    "environnements spatiaux sévères": 5,
    "fibres optiques": 7,
    "fibres": 0,
    "fibre": 0,
    "spatial": 0,
    "optique": 0,
    "fiber fuse": 5,
    "micro-courbures": 5,
    "vibrations": 5,
    "radiations": 4,
    "pertes d’insertion": 5,
    "pertes de retour": 5,
    "pcf": 4,
    "haf": 4,
    "smf": 3,
    "gaines": 5,
    "buffer": 4,
    "buffers": 4,
    "latécoère": 4,
    "nasa": 3,
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
                    "DOMAINE_RD",
                    "COMPOSANT_TECHNIQUE",
                    "MATERIAU_SPECIFIQUE",
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

print("✅ Patch projet 27 terminé")
print("✅ Projet 27 = CIR, utilisable pour dataset CIR après validation")
print(f"Avant          : {before}")
print(f"Après          : {after}")
print(f"Removed clean  : {removed}")
print(f"Removed caps   : {removed_caps}")
print(f"Removed nested : {removed_nested}")
print(f"Fixed labels   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")