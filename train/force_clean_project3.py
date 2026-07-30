from pathlib import Path
import json
import re

INPUT = Path(r"C:\EnnoSmart\projects\projet_3_\annotations\ner_candidates.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_3_\annotations\ner_candidates_clean.json")

KEEP_SECTION_TITLES = True

REMOVE = {
    "group i", "group ii", "group iii", "groups i to iii",
    "control groups", "control groups i to iii",
    "treatment groups", "experimental groups",
    "shR rats".lower(), "wky rats", "shr rats",
    "wky\u000bvehicle",
    "sponsor",
    "fournisseur initial",
    "nom",
    "chef de projet",
    "chercheur",
    "heures",
    "pharmaco-économiste",
    "vétérinaire",
    "pharmaciens",
    "ns",
    "results",
    "résultats",
    "ministère de la défense",
    "derniers décennies",
    "dernières décennies",
    "acclimation period",
    "treatment period",
    "experimental period",
    "equilibration period",
    "evaluation period",
    "stabilization period",
    "pelvipharm facilities",
    "csp",
    "patients",
    "patient",
    "homme",
    "centres",
    "différents pays occidentaux",
    "turkish journal of medical sciences",
    "de pasquale r",
    "çömelekoğlu",
    "yalin",
    "balli",
    "ebru",
}

SECTION_TITLES = {
    "objectifs visés": "OBJECTIF_RD",
    "résultats obtenus": "RESULTAT_RD",
    "démarche expérimentale": "METHODE_RD",
    "démarche expérimentale, travaux r&d réalisés": "METHODE_RD",
}

AUTO_FIX = {
    # ORGANISMES
    "pelvipharm": "ORGANISME",
    "sigma aldrich": "ORGANISME",
    "leica biosystems": "ORGANISME",
    "université de versailles": "ORGANISME",
    "université de versailles saint-quentin-en-yvelines": "ORGANISME",
    "ufr simone veil-santé": "ORGANISME",
    "elevage janvier": "ORGANISME",
    "janvier labs": "ORGANISME",
    "charles river": "ORGANISME",
    "french ministry of agriculture": "ORGANISME",
    "french ministry of higher education and research": "ORGANISME",
    "nih": "ORGANISME",
    "ocde": "ORGANISME",
    "dga": "ORGANISME",
    "fda": "ORGANISME",
    "avicenna biosciences": "ORGANISME",
    "eau & aua": "ORGANISME",
    "cro": "ORGANISME",
    "contract research organization": "ORGANISME",

    # PERSONNES
    "madame behr-roussel": "PERSONNE",
    "marouane kheloufi": "PERSONNE",
    "kheloufi marouane": "PERSONNE",

    # METHODES
    "one-way anova": "METHODE_RD",
    "two-way anova": "METHODE_RD",
    "unpaired t-test": "METHODE_RD",
    "student’s t-test": "METHODE_RD",
    "student's t-test": "METHODE_RD",
    "bonferroni’s multiple comparisons test": "METHODE_RD",
    "tukey’s multiple comparison test": "METHODE_RD",
    "grubbs’ test": "METHODE_RD",
    "cystometry": "METHODE_RD",
    "cystometry experiment": "METHODE_RD",
    "samples processing": "METHODE_RD",
    "immunohistochemistry": "METHODE_RD",
    "hematoxylin-eosin": "METHODE_RD",
    "he": "METHODE_RD",
    "colorimetric dosage": "METHODE_RD",
    "hydroxyproline quantification": "METHODE_RD",

    # EQUIPEMENTS
    "aperio at2 – scanner for on-screen diagnosis": "EQUIPEMENT_RD",
    "aperio at2": "EQUIPEMENT_RD",
    "nis elements software": "EQUIPEMENT_RD",
    "graphpad prism® 6.07 software": "EQUIPEMENT_RD",
    "graphpad prism": "EQUIPEMENT_RD",
    "micropipette": "EQUIPEMENT_RD",
    "binocular microscope": "EQUIPEMENT_RD",
    "cages métaboliques": "EQUIPEMENT_RD",
    "pompe osmotique": "EQUIPEMENT_RD",

    # COMPOSANTS / TECHNO
    "avi-016": "COMPOSANT_TECHNIQUE",
    "rock": "COMPOSANT_TECHNIQUE",
    "rho-kinase": "COMPOSANT_TECHNIQUE",
    "rhoA".lower(): "COMPOSANT_TECHNIQUE",
    "rock1": "COMPOSANT_TECHNIQUE",
    "rock2": "COMPOSANT_TECHNIQUE",
    "mypT1".lower(): "COMPOSANT_TECHNIQUE",
    "oxybutynin": "COMPOSANT_TECHNIQUE",
    "estradiol": "COMPOSANT_TECHNIQUE",
    "β-estradiol 3-benzoate": "COMPOSANT_TECHNIQUE",
    "pentobarbital": "COMPOSANT_TECHNIQUE",
    "euthasol®": "COMPOSANT_TECHNIQUE",
    "euthasol": "COMPOSANT_TECHNIQUE",
    "isoflurane": "COMPOSANT_TECHNIQUE",
    "sesame oil": "MATERIAU_SPECIFIQUE",
    "hydroxyproline": "MATERIAU_SPECIFIQUE",
    "collagen": "MATERIAU_SPECIFIQUE",
    "keratin": "MATERIAU_SPECIFIQUE",
    "3/0 polyester suture": "MATERIAU_SPECIFIQUE",
    "vetsure© bond": "MATERIAU_SPECIFIQUE",
    "flammazine®": "COMPOSANT_TECHNIQUE",
    "flammazine": "COMPOSANT_TECHNIQUE",
    "paraffin oil": "MATERIAU_SPECIFIQUE",
    "paraffine": "MATERIAU_SPECIFIQUE",

    # DOMAINES
    "urologie": "DOMAINE_RD",
    "pharmacologie": "DOMAINE_RD",
    "physiopathologie": "DOMAINE_RD",
    "biomédecine": "DOMAINE_RD",
    "santé féminine": "DOMAINE_RD",
    "fonction cardiovasculaire": "DOMAINE_RD",
    "fonction métabolique": "DOMAINE_RD",
    "ménopause": "DOMAINE_RD",
    "atrophie vaginale": "DOMAINE_RD",
    "vessie hyperactive": "DOMAINE_RD",
    "syndrome ovarien polykystique": "DOMAINE_RD",
    "sopk": "DOMAINE_RD",
}

# Références bibliographiques à supprimer
REFERENCE_PATTERNS = [
    r".+\bet al\.?$",
    r".+\bet al,?\s*\d{4}$",
    r"^[A-ZÉÈÇÖÜ][A-Za-zÀ-ÿ\-]+ [A-Z]\.?$",
    r"^[A-ZÉÈÇÖÜ][A-ZÉÈÇÖÜ\-]+,?\s+[A-ZÉÈÇÖÜ][A-ZÉÈÇÖÜ\-]+$",
]

BAD_PERSON_WORDS = {
    "nom", "chef de projet", "chercheur", "technicien", "technicienne",
    "zootechnicien", "zootechnicienne", "vétérinaire",
    "pharmaciens", "pharmaco-économiste",
}

BAD_ORG_WORDS = {
    "group i", "group ii", "group iii",
    "groups i to iii", "control groups",
    "treatment groups", "experimental groups",
    "shr rats", "wky rats", "sci",
}

ANATOMY_WORDS = {
    "skin", "vagina", "uterus", "vaginal", "vessie",
    "tronc cérébral", "moelle épinière",
    "epidermal layer", "dermal layer",
    "subcutaneous fat layer",
}

def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()

def is_reference(text):
    t = text.strip()
    for p in REFERENCE_PATTERNS:
        if re.match(p, t, flags=re.IGNORECASE):
            return True
        if "[SECTION : References]" in item.get("text", ""):
            item["entities"] = []
            continue
    return False

def clean_entity(ent):
    text = clean_text(ent.get("text", ""))
    label = ent.get("label", "")
    lower = text.lower()

    if not text:
        return None

    # sections R&D à garder ou supprimer selon option
    if lower in SECTION_TITLES:
        if KEEP_SECTION_TITLES:
            ent["text"] = text
            ent["label"] = SECTION_TITLES[lower]
            ent["status"] = "force_cleaned"
            return ent
        return None

    if lower in REMOVE:
        return None

    if label == "PERSONNE" and lower in BAD_PERSON_WORDS:
        return None

    if label == "ORGANISME" and lower in BAD_ORG_WORDS:
        return None

    # anatomie : on supprime pour éviter faux MATERIAU/LIEU
    if lower in ANATOMY_WORDS:
        return None

    # références scientifiques
    if label in {"PERSONNE", "ORGANISME"} and is_reference(text):
        return None

    # emails / urls
    if "@" in text or lower.startswith("www.") or lower.endswith(".com") or lower.endswith(".fr"):
        return None

    # petits faux jalons
    if label == "JALON" and lower in {"ns", "nd"}:
        return None

    # faux DATE_PERIODE trop génériques
    if label == "DATE_PERIODE" and lower in {
        "treatment period", "experimental period", "evaluation period",
        "equilibration period", "stabilization period", "acclimation period",
        "dernières décennies", "derniers décennies",
    }:
        return None

    if lower in AUTO_FIX:
        ent["text"] = text
        ent["label"] = AUTO_FIX[lower]
        ent["status"] = "force_cleaned"
        return ent

    ent["text"] = text
    return ent
    

with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

before = 0
after = 0
removed = 0
fixed = 0

for item in data:
    new_entities = []

    for ent in item.get("entities", []):
        before += 1
        old_label = ent.get("label")

        cleaned = clean_entity(dict(ent))

        if cleaned is None:
            removed += 1
            continue

        if cleaned.get("label") != old_label or cleaned.get("status") == "force_cleaned":
            fixed += 1

        new_entities.append(cleaned)

    item["entities"] = new_entities
    item["annotation_status"] = "force_cleaned"
    after += len(new_entities)

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("✅ Force clean projet 3 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")
print(f"Output  : {OUTPUT}")
print(f"KEEP_SECTION_TITLES = {KEEP_SECTION_TITLES}")