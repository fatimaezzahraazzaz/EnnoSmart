from pathlib import Path
import json
import re

INPUT = Path(r"C:\EnnoSmart\projects\projet_4_\annotations\ner_candidates.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_4_\annotations\ner_candidates_clean.json")

KEEP_SECTION_TITLES = True

REMOVE_EXACT = {
    "société", "ressources humaines", "professionnels volontaires", "560 adhérents",
    "conseillers techniques", "conseillers\ntechniques", "expérimentateurs",
    "agriculteurs", "producteurs", "producteurs de plantes ornementales",
    "filières horticoles et maraichères", "associations de producteurs",
    "gouvernement", "etat", "site d’essai", "région",
    "période donnée", "plusieurs années",
    "post-semi", "post-semis", "printemps", "automne",
    "début de saison", "mi-aout", "fin aout",
    "professionnels", "consommateurs", "entreprises",
    "objectifs", "résultats", "résultat",
    "nom", "chef de projet", "chercheur", "heures", "fonction",
    "ministère de la défense",
    "biological control", "phytoma", "cabi publishing",
    "et al", "europe", "wallingford", "new-york", "oxon", "afrique de l’ouest",
    "apiaceae",
    "asteraceae",
    "fabaceae",
    "sphaerophoria scripta",
    "episyrphus balteatus",
    "eupeodes corollae",
    "petites exploitations",
    "foyers résiduaires",
    "parcelle",
}

# Auteurs/références bibliographiques
REFERENCE_NAMES = {
    "fritsch n", "friesen", "rabinowitch h.d", "currah l", "ricroch a",
    "lamichhane j.r", "ferre a", "fiedler a.k", "suty l",
    "frank. s.d", "hogg b.n", "baggen l.r", "calatayud p.-a",
    "arnó j", "gordon t.l",
}

AUTO_FIX = {
    # ORGANISMES
    "planete légumes fleurs et plantes": "ORGANISME",
    "planete légumes": "ORGANISME",
    "planete légume": "ORGANISME",
    "gpdla": "ORGANISME",
    "groupement de développement des producteurs de légumes d’alsace": "ORGANISME",
    "green’puls": "ORGANISME",
    "anses": "ORGANISME",
    "ocde": "ORGANISME",
    "union européenne": "ORGANISME",
    "union européenne": "ORGANISME",
    "ministère de l’agriculture": "ORGANISME",
    "dga": "ORGANISME",
    "ecophyto": "ORGANISME",
    "fam/casdar": "ORGANISME",
    "agence de l’eau": "ORGANISME",
    "agende de l’eau": "ORGANISME",
    "ensaiA".lower(): "ORGANISME",
    "iut colmar": "ORGANISME",
    "ensh": "ORGANISME",
    "université de lorraine": "ORGANISME",

    # PERSONNES valides
    "laura freudenreich": "PERSONNE",
    "maylis ohrel": "PERSONNE",
    "marie-anne joussemet": "PERSONNE",
    "alex ludovic": "PERSONNE",
    "bedel léa": "PERSONNE",
    "jung denis": "PERSONNE",
    "litzler marine": "PERSONNE",
    "mougenot jonas": "PERSONNE",
    "paolucci maxime": "PERSONNE",
    "varaillas carla": "PERSONNE",

    # METHODES
    "anova": "METHODE_RD",
    "test de dunett": "METHODE_RD",
    "test de newman-keuls": "METHODE_RD",
    "newman-keuls": "METHODE_RD",
    "test de tukey": "METHODE_RD",
    "tukey": "METHODE_RD",
    "protocole expérimental": "METHODE_RD",
    "dispositif fisher randomisé": "METHODE_RD",
    "blocs randomisés": "METHODE_RD",
    "analyse de la variance": "METHODE_RD",
    "démarche expérimentale": "METHODE_RD",

    # TECHNOLOGIES / PRODUITS / COMPOSANTS
    "m500": "COMPOSANT_TECHNIQUE",
    "m204": "COMPOSANT_TECHNIQUE",
    "m465": "COMPOSANT_TECHNIQUE",
    "ortiva": "COMPOSANT_TECHNIQUE",
    "cantor": "COMPOSANT_TECHNIQUE",
    "orondis plus": "COMPOSANT_TECHNIQUE",
    "pygmalion": "COMPOSANT_TECHNIQUE",
    "phosphonate": "COMPOSANT_TECHNIQUE",
    "phosphonates": "COMPOSANT_TECHNIQUE",
    "fosétyl-aluminium": "COMPOSANT_TECHNIQUE",
    "cymoxanil": "COMPOSANT_TECHNIQUE",
    "oxathiapiprolin": "COMPOSANT_TECHNIQUE",
    "promocarbe": "COMPOSANT_TECHNIQUE",
    "famoxadone": "COMPOSANT_TECHNIQUE",
    "chlorothalonil": "COMPOSANT_TECHNIQUE",
    "mancobèze": "COMPOSANT_TECHNIQUE",
    "benevia": "COMPOSANT_TECHNIQUE",
    "cyantraniliprole": "COMPOSANT_TECHNIQUE",
    "flonicamide": "COMPOSANT_TECHNIQUE",
    "lambda-cyhalothrine": "COMPOSANT_TECHNIQUE",
    "azadirachtine": "COMPOSANT_TECHNIQUE",
    "bacillus subtilis": "COMPOSANT_TECHNIQUE",
    "octanoate de cuivre": "COMPOSANT_TECHNIQUE",

    # MATERIAUX / FILETS
    "pla": "MATERIAU_SPECIFIQUE",
    "pehd": "MATERIAU_SPECIFIQUE",
    "polyethylène haute densité": "MATERIAU_SPECIFIQUE",
    "polyéthylène haute densité": "MATERIAU_SPECIFIQUE",
    "polypropylène": "MATERIAU_SPECIFIQUE",
    "polyamide": "MATERIAU_SPECIFIQUE",
    "bioplastique": "MATERIAU_SPECIFIQUE",
    "sable argilo-limoneux": "MATERIAU_SPECIFIQUE",
    "huile paraffinique": "MATERIAU_SPECIFIQUE",
    "terre de diatomée": "MATERIAU_SPECIFIQUE",
    "savon": "MATERIAU_SPECIFIQUE",
    "filbio839": "MATERIAU_SPECIFIQUE",
    "filbio 839": "MATERIAU_SPECIFIQUE",
    "tip 650": "MATERIAU_SPECIFIQUE",
    "tip650": "MATERIAU_SPECIFIQUE",
    "tip 651": "MATERIAU_SPECIFIQUE",
    "tip651": "MATERIAU_SPECIFIQUE",
    "biotis 450": "MATERIAU_SPECIFIQUE",
    "filclimat": "MATERIAU_SPECIFIQUE",
    "p17": "MATERIAU_SPECIFIQUE",

    # DOMAINES
    "horticulture": "DOMAINE_RD",
    "cultures maraîchères": "DOMAINE_RD",
    "cultures horticoles": "DOMAINE_RD",
    "agriculture biologique": "DOMAINE_RD",
    "protection biologique intégrée": "DOMAINE_RD",
    "pbi": "DOMAINE_RD",
    "biocontrôle": "DOMAINE_RD",

    # TAXONS / BIOAGRESSEURS -> composants techniques pour ton dataset
    "altica spp": "COMPOSANT_TECHNIQUE",
    "phyllotreta spp": "COMPOSANT_TECHNIQUE",
    "delia radicum": "COMPOSANT_TECHNIQUE",
    "athalia rosae": "COMPOSANT_TECHNIQUE",
    "aphididae": "COMPOSANT_TECHNIQUE",
    "aphis fabae": "COMPOSANT_TECHNIQUE",
    "myzus persicae": "COMPOSANT_TECHNIQUE",
    "peronospora destructor": "COMPOSANT_TECHNIQUE",
}

SECTION_TITLES = {
    "objectifs visés": "OBJECTIF_RD",
    "verrous": "VERROU_TECH",
    "démarche expérimentale": "METHODE_RD",
    "protocole expérimental": "METHODE_RD",
}

BAD_LABEL_TEXT = {
    ("société", "ORGANISME"),
    ("agriculteurs", "ORGANISME"),
    ("producteurs", "ORGANISME"),
    ("ressources humaines", "PERSONNE"),
    ("professionnels volontaires", "PERSONNE"),
    ("560 adhérents", "PERSONNE"),
    ("conseillers techniques", "PERSONNE"),
    ("expérimentateurs", "PERSONNE"),
    ("pépinières hors-sol", "LIEU"),
    ("site d’essai", "LIEU"),
    ("région", "LIEU"),
}

DATE_KEEP_PATTERNS = [
    r"^\d{4}$",
    r"^\d{2}/\d{2}/\d{4}$",
    r"^(début|fin|mi)[ -]?(mai|juin|juillet|août|aout|septembre|octobre)$",
    r"^mai-juin$",
]

REFERENCE_PATTERNS = [
    r".+\bet al\.?$",
    r".+\bet al,?\s*\d{4}$",
    r".*\bvol\.\s*\d+.*",
    r".*\bp\.\s*\d+.*",
    r".*\bbiological control\b.*",
    r".*\bcrop protection\b.*",
    r".*\bphytoma\b.*",
    r".*\bcabi publishing\b.*",
]

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def is_reference_text(text: str) -> bool:
    t = norm(text).lower()
    if t in REFERENCE_NAMES:
        return True
    return any(re.match(p, t, flags=re.IGNORECASE) for p in REFERENCE_PATTERNS)

def is_valid_date(text: str) -> bool:
    t = norm(text).lower()
    return any(re.match(p, t, flags=re.IGNORECASE) for p in DATE_KEEP_PATTERNS)

def clean_entity(ent):
    text = norm(ent.get("text", ""))
    label = ent.get("label", "")
    lower = text.lower()

    if not text:
        return None

    if lower in SECTION_TITLES:
        if KEEP_SECTION_TITLES:
            ent["text"] = text
            ent["label"] = SECTION_TITLES[lower]
            ent["status"] = "force_cleaned"
            return ent
        return None

    if lower in REMOVE_EXACT:
        return None

    if (lower, label) in BAD_LABEL_TEXT:
        return None

    if is_reference_text(text):
        return None

    if "@" in text or lower.startswith("http") or lower.startswith("www.") or lower.endswith(".com") or lower.endswith(".fr"):
        return None

    if label == "PERSONNE":
        if len(text) <= 3:
            return None
        if lower in {"nom", "chercheur", "technicien", "chef de projet", "ingénieur", "agronome"}:
            return None

    if label == "ORGANISME":
        if lower in {"société", "association", "organismes", "collectivités", "institutions"}:
            return None

    if label == "DATE_PERIODE":
        # on garde les vraies années et dates, on supprime les durées vagues
        if not is_valid_date(text):
            return None

    if label == "ETP" and lower in {"tip 650", "tip 651", "ceB".lower()}:
        if lower == "ceb":
            ent["label"] = "ORGANISME"
        else:
            ent["label"] = "MATERIAU_SPECIFIQUE"
        ent["text"] = text
        ent["status"] = "force_cleaned"
        return ent

    if lower in AUTO_FIX:
        ent["text"] = text
        ent["label"] = AUTO_FIX[lower]
        ent["status"] = "force_cleaned"
        return ent

    ent["text"] = text
    return ent

def deduplicate(entities):
    seen = set()
    clean = []
    for ent in entities:
        key = (ent.get("text", "").lower(), ent.get("label"), ent.get("start"), ent.get("end"))
        if key in seen:
            continue
        seen.add(key)
        clean.append(ent)
    return clean

with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

before = 0
after = 0
removed = 0
fixed = 0

for item in data:
    # إذا chunk كامل ديال references/biblio نحيدو entities ديالو
    text_block = item.get("text", "")
    if any(x in text_block.lower() for x in ["bibliographie", "références bibliographiques"]):
        removed += len(item.get("entities", []))
        before += len(item.get("entities", []))
        item["entities"] = []
        item["annotation_status"] = "force_cleaned"
        continue

    new_entities = []

    for ent in item.get("entities", []):
        before += 1
        old_label = ent.get("label")
        old_text = ent.get("text")

        cleaned = clean_entity(dict(ent))

        if cleaned is None:
            removed += 1
            continue

        if cleaned.get("label") != old_label or cleaned.get("text") != old_text or cleaned.get("status") == "force_cleaned":
            fixed += 1

        new_entities.append(cleaned)

    item["entities"] = deduplicate(new_entities)
    item["annotation_status"] = "force_cleaned"
    after += len(item["entities"])

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("✅ Force clean projet 4 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")
print(f"Output  : {OUTPUT}")