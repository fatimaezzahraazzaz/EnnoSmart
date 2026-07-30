from pathlib import Path
import json
import re

INPUT = Path(r"C:\EnnoSmart\projects\projet_7_\annotations\ner_candidates.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_7_\annotations\ner_candidates_clean.json")

KEEP_SECTION_TITLES = True

REMOVE_EXACT = {
    # Bruit pronoms / générique
    "nous",
    "notre",
    "nos",
    "auteurs",
    "auteur",
    "industriels",
    "industrie",
    "fabricants",
    "fabricants des compresseurs",
    "entreprises fabricantes",
    "entreprises",
    "ministère de la défense",
    "crédit impôt recherche",

    # Bruit RH / tableau
    "nom",
    "prénom",
    "fonction",
    "heures",
    "chef de projet",

    # Dates vagues
    "plusieurs années",
    "dernière phase",
    "première phase",
    "deuxième étape",
    "une autre étape",

    # Résultats trop génériques
    "résultat",
    "résultats",
    "résultats obtenus",
    "résultats des essais",
    "résultats expérimentaux",
    "résultats des mesures vibratoires",
    "résultats de cette étude",
    "essais",
    "conclusion",
    "conclusions",

    # Faux lieux / domaines génériques
    "sous-marin",
    "secteur marin",
    "milieu militaire",
    "fonderie",
    "sortie 3ème étage",
    "sortie 4ème étage",
    "1er étage",
    "2ème étage",
    "3ème étage",
    "4ème étage",
    "deuxième étage",
    "troisième étage",
    "quatrième étage",
    "cylindre 1",
    "cylindre 2",
    "cylindre 3",
    "cylindre 4",
    "cavités 1 et 2",
    "cavités 2 et 1",
    "cavités 2 et 3",
    "cavités 3 et 2",
    "cavités 3 et 4",
    "cavités 4 et 3",
    "cavités 4 et 1",
    "girodin-sauer",
    "girodin-sauer - sas",

    # Trop générique
    "le compresseur",
    "compresseur",
    "piston",
    "pistons",
    "plateau",
    "arbre",
    "boîtier",
    "tubes",
    "disque",
    "premier central",
    "composants techniques",
    "matériau convenable",
    # Footer / adresses / signatures PDF
    "urban valley",
    "chemin des bas des indes",
    "cormeilles en parisis",
    "bat.c - 16",
    "95240",
    "sas",
    "500 k €",
    "a s.vanden born",
    "s.vanden born",
    "mr zaher",
    "rév",
    "rédigé",
    "written by",
    "date modification",
}

AUTO_FIX = {
    "piston à simple effet": "COMPOSANT_TECHNIQUE",
    "piston à double effet": "COMPOSANT_TECHNIQUE",
    "piston étagé": "COMPOSANT_TECHNIQUE",
    "résonateur": "COMPOSANT_TECHNIQUE",
    "psl": "COMPOSANT_TECHNIQUE",
    "mr zaher": "PERSONNE",  
    # ORGANISMES
    "girodin": "ORGANISME",
    "girodin-sauer": "ORGANISME",
    "andhéo": "ORGANISME",
    "andheo": "ORGANISME",
    "andhÉo".lower(): "ORGANISME",
    "andhéo agréé": "ORGANISME",
    "cetim": "ORGANISME",
    "dynae": "ORGANISME",
    "dassault systèmes": "ORGANISME",
    "dassault systemes": "ORGANISME",
    "haldex brake corporation": "ORGANISME",
    "onera": "ORGANISME",
    "dga": "ORGANISME",
    "essais empiriques": "METHODE_RD",

    # EQUIPEMENTS / SYSTEMES
    "tgm 100": "EQUIPEMENT_RD",
    "tgm100": "EQUIPEMENT_RD",
    "tgm 60": "EQUIPEMENT_RD",
    "tgm60": "EQUIPEMENT_RD",
    "module tgm100": "EQUIPEMENT_RD",
    "module tgm 100": "EQUIPEMENT_RD",
    "compresseur à pistons": "EQUIPEMENT_RD",
    "compresseurs à pistons": "EQUIPEMENT_RD",
    "compresseur à barillet": "EQUIPEMENT_RD",
    "compresseur à barillet multi-étages": "EQUIPEMENT_RD",
    "compresseur à pistons à barillet": "EQUIPEMENT_RD",
    "compresseur à pistons à barillet multi-étages": "EQUIPEMENT_RD",
    "compresseur à plateau oscillant": "EQUIPEMENT_RD",
    "compresseurs à plateau oscillant": "EQUIPEMENT_RD",
    "compresseurs à cylindre unique": "EQUIPEMENT_RD",
    "compresseur mécanique": "EQUIPEMENT_RD",
    "compresseur multi-étages": "EQUIPEMENT_RD",
    "moto-compresseur": "EQUIPEMENT_RD",
    "banc d’essais": "EQUIPEMENT_RD",
    "banc d'essais": "EQUIPEMENT_RD",
    "banc d’essai": "EQUIPEMENT_RD",
    "banc d'essai": "EQUIPEMENT_RD",
    "caisson": "EQUIPEMENT_RD",
    "caisson insonorisé": "EQUIPEMENT_RD",
    "chambre du vilebrequin": "EQUIPEMENT_RD",

    # METHODES
    "démarche expérimentale": "METHODE_RD",
    "méthode expérimentale": "METHODE_RD",
    "méthode des coefficients d’influence": "METHODE_RD",
    "méthode des coefficients d'influence": "METHODE_RD",
    "équilibrage statique": "METHODE_RD",
    "équilibrage dynamique": "METHODE_RD",
    "analyse vibratoire": "METHODE_RD",
    "analyse vibro-acoustique": "METHODE_RD",
    "analyse modale expérimentale": "METHODE_RD",
    "analyse spectrale": "METHODE_RD",
    "fft": "METHODE_RD",
    "calcul numérique": "METHODE_RD",
    "calculs numériques": "METHODE_RD",
    "simulation numérique": "METHODE_RD",
    "simulations numériques": "METHODE_RD",
    "flow simulation": "METHODE_RD",
    "cfd": "METHODE_RD",
    "nitruration gazeuse": "METHODE_RD",
    "nitruration au sel": "METHODE_RD",
    "tenifer": "METHODE_RD",
    "sulfinisation en bain de sel": "METHODE_RD",
    "traitement thermochimique": "METHODE_RD",
    "modélisations 3d": "METHODE_RD",
    "solidworks": "METHODE_RD",

    # VERROUS
    "verrou 1": "VERROU_TECH",
    "verrou 2": "VERROU_TECH",
    "verrou 3": "VERROU_TECH",
    "verrou technique": "VERROU_TECH",
    "verrou scientifique et technique": "VERROU_TECH",
    "maîtrise du comportement vibro-acoustique à haute vitesse de rotation": "VERROU_TECH",
    "maîtrise thermique d’un compresseur multi-étages à 300 bars": "VERROU_TECH",
    "production d’un air sec conforme aux exigences en sortie de compresseur": "VERROU_TECH",
    "manque de connaissances": "VERROU_TECH",
    "instabilité vibratoire": "VERROU_TECH",
    "problématique de chauffe": "VERROU_TECH",
    "fuites d’huile": "VERROU_TECH",
    "fuites d'huile": "VERROU_TECH",

    # COMPOSANTS TECHNIQUES
    "système de bielle-manivelle": "COMPOSANT_TECHNIQUE",
    "bielle-manivelle": "COMPOSANT_TECHNIQUE",
    "bielle": "COMPOSANT_TECHNIQUE",
    "bielles": "COMPOSANT_TECHNIQUE",
    "vilebrequin": "COMPOSANT_TECHNIQUE",
    "vilebrequins": "COMPOSANT_TECHNIQUE",
    "système à barillet": "COMPOSANT_TECHNIQUE",
    "système à barillet1": "COMPOSANT_TECHNIQUE",
    "plateau oscillant": "COMPOSANT_TECHNIQUE",
    "plateau pivotant": "COMPOSANT_TECHNIQUE",
    "transformateur de mouvement": "COMPOSANT_TECHNIQUE",
    "transformateur": "COMPOSANT_TECHNIQUE",
    "rotule": "COMPOSANT_TECHNIQUE",
    "rotule de fond": "COMPOSANT_TECHNIQUE",
    "couronne mobile": "COMPOSANT_TECHNIQUE",
    "couronne fixe": "COMPOSANT_TECHNIQUE",
    "système de graissage à huile sous pression": "COMPOSANT_TECHNIQUE",
    "masselotte": "COMPOSANT_TECHNIQUE",
    "masselottes": "COMPOSANT_TECHNIQUE",
    "poulie": "COMPOSANT_TECHNIQUE",
    "poulie à 45°": "COMPOSANT_TECHNIQUE",
    "contre-poids": "COMPOSANT_TECHNIQUE",
    "contrepoids": "COMPOSANT_TECHNIQUE",
    "nouveau contrepoids": "COMPOSANT_TECHNIQUE",
    "contre-poids sans plomb": "COMPOSANT_TECHNIQUE",
    "contrepoids sans plomb": "COMPOSANT_TECHNIQUE",
    "masse d’équilibrage": "COMPOSANT_TECHNIQUE",
    "masses d’équilibrage": "COMPOSANT_TECHNIQUE",
    "plaques d’équilibrage": "COMPOSANT_TECHNIQUE",
    "cdg": "COMPOSANT_TECHNIQUE",

    "bloc des cylindres": "COMPOSANT_TECHNIQUE",
    "cylindre": "COMPOSANT_TECHNIQUE",
    "cylindres": "COMPOSANT_TECHNIQUE",
    "alésage de cylindre": "COMPOSANT_TECHNIQUE",
    "segment de piston": "COMPOSANT_TECHNIQUE",
    "bague": "COMPOSANT_TECHNIQUE",
    "actionneur": "COMPOSANT_TECHNIQUE",
    "sabots": "COMPOSANT_TECHNIQUE",
    "arbre moteur": "COMPOSANT_TECHNIQUE",
    "moteur": "COMPOSANT_TECHNIQUE",
    "nouveau moteur": "COMPOSANT_TECHNIQUE",
    "module compresseur": "COMPOSANT_TECHNIQUE",
    "motopompe à huile": "COMPOSANT_TECHNIQUE",
    "pompe à huile": "COMPOSANT_TECHNIQUE",
    "pompe attelée sur châssis": "COMPOSANT_TECHNIQUE",
    "châssis": "COMPOSANT_TECHNIQUE",
    "châssis de fixation": "COMPOSANT_TECHNIQUE",
    "corps avant": "COMPOSANT_TECHNIQUE",
    "corps arrière": "COMPOSANT_TECHNIQUE",
    "corps v2": "COMPOSANT_TECHNIQUE",

    "circuit de refroidissement": "COMPOSANT_TECHNIQUE",
    "système de refroidissement": "COMPOSANT_TECHNIQUE",
    "tubes de refroidissement": "COMPOSANT_TECHNIQUE",
    "réfrigérant": "COMPOSANT_TECHNIQUE",
    "réfrigérant tubulaire": "COMPOSANT_TECHNIQUE",
    "nouveau réfrigérant": "COMPOSANT_TECHNIQUE",
    "ancien réfrigérant": "COMPOSANT_TECHNIQUE",
    "séparateur de condensats": "COMPOSANT_TECHNIQUE",
    "séparateur air/condensats": "COMPOSANT_TECHNIQUE",
    "sécheur à membrane": "COMPOSANT_TECHNIQUE",
    "membrane de séchage": "COMPOSANT_TECHNIQUE",
    "éclateur": "COMPOSANT_TECHNIQUE",
    "nouvel éclateur": "COMPOSANT_TECHNIQUE",
    "ancien éclateur": "COMPOSANT_TECHNIQUE",
    "reniflard": "COMPOSANT_TECHNIQUE",
    "système de chicanes": "COMPOSANT_TECHNIQUE",
    "silencieux": "COMPOSANT_TECHNIQUE",
    "silencieux d’aspiration": "COMPOSANT_TECHNIQUE",
    "silencieux d'aspiration": "COMPOSANT_TECHNIQUE",
    "cartouche filtre": "COMPOSANT_TECHNIQUE",
    "cartouche filtrante": "COMPOSANT_TECHNIQUE",
    "filtre à air": "COMPOSANT_TECHNIQUE",
    "soupapes d’aspiration": "COMPOSANT_TECHNIQUE",
    "soupapes d'aspiration": "COMPOSANT_TECHNIQUE",
    "soupapes de sécurité": "COMPOSANT_TECHNIQUE",
    "portique séparateur": "COMPOSANT_TECHNIQUE",
    "ailettes": "COMPOSANT_TECHNIQUE",
    "clapet": "COMPOSANT_TECHNIQUE",

    # MATERIAUX
    "plomb": "MATERIAU_SPECIFIQUE",
    "sans plomb": "MATERIAU_SPECIFIQUE",
    "fonte": "MATERIAU_SPECIFIQUE",
    "en gjs 400-15": "MATERIAU_SPECIFIQUE",
    "graphite": "MATERIAU_SPECIFIQUE",
    "disulfure de molybdène": "MATERIAU_SPECIFIQUE",
    "disulfure de tungstène": "MATERIAU_SPECIFIQUE",
    "phosphate de zinc": "MATERIAU_SPECIFIQUE",
    "cellulose": "MATERIAU_SPECIFIQUE",
    "résine de phénolique": "MATERIAU_SPECIFIQUE",
    "résine phénolique": "MATERIAU_SPECIFIQUE",
    "fonte sans plomb": "MATERIAU_SPECIFIQUE",
    "air": "MATERIAU_SPECIFIQUE",
    "huile": "MATERIAU_SPECIFIQUE",
    "eau": "MATERIAU_SPECIFIQUE",

    # DOMAINES
    "mécanique": "DOMAINE_RD",
    "génie mécanique": "DOMAINE_RD",
    "génie civil": "DOMAINE_RD",
    "vibro-acoustique": "DOMAINE_RD",
    "simulation numérique multi-physique": "DOMAINE_RD",
    "multi-physique": "DOMAINE_RD",
    "aérothermique": "DOMAINE_RD",
    "aéroacoustique": "DOMAINE_RD",
    "thermique": "DOMAINE_RD",
    "dynamique des fluides": "DOMAINE_RD",
}

SECTION_TITLES = {
    "objectifs visés et performances à atteindre": "OBJECTIF_RD",
    "verrous et incertitudes scientifiques, techniques, technologiques": "VERROU_TECH",
    "démarche expérimentale": "METHODE_RD",
    "démarche expérimentale, travaux r&d réalisés": "METHODE_RD",
    "développement d’un prototype du contrepoids sans plomb": "METHODE_RD",
    "equilibrage statique et dynamique du compresseur avec le contre-poids sans plomb": "METHODE_RD",
    "équilibrage statique et dynamique du compresseur avec le contre-poids sans plomb": "METHODE_RD",
    "etude de l’écoulement de l’eau dans le circuit de refroidissement du compresseur": "METHODE_RD",
    "étude de l’écoulement de l’eau dans le circuit de refroidissement du compresseur": "METHODE_RD",
    "analyse de la problématique de chauffe à la sortie du compresseur": "METHODE_RD",
    "développement d’un nouveau séparateur de condensats": "METHODE_RD",
}

DATE_KEEP_PATTERNS = [
    r"^\d{4}$",
    r"^année\s+\d{4}$",
    r"^(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+\d{4}$",
]

REFERENCE_CHUNK_MARKERS = [
    "article scientifique",
    "article de recherche",
    "brevet",
    "publié en",
    "publiée en",
    "travaux de recherche",
    "littérature",
]

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def lower_norm(text: str) -> str:
    return norm(text).lower()

def is_valid_date(text: str) -> bool:
    t = lower_norm(text)
    return any(re.match(p, t, flags=re.IGNORECASE) for p in DATE_KEEP_PATTERNS)

def is_reference_chunk(text: str) -> bool:
    low = lower_norm(text)
    return any(marker in low for marker in REFERENCE_CHUNK_MARKERS)

def clean_entity(ent, ref_chunk=False):
    text = norm(ent.get("text", ""))
    label = ent.get("label", "")
    low = lower_norm(text)

    if not text:
        return None

    # Sections utiles
    if low in SECTION_TITLES:
        if KEEP_SECTION_TITLES:
            ent["text"] = text
            ent["label"] = SECTION_TITLES[low]
            ent["status"] = "force_cleaned"
            return ent
        return None

    # Suppression directe
    if low in REMOVE_EXACT:
        return None

    # URLs / mails
    if "@" in text or low.startswith("http") or low.startswith("www.") or low.endswith(".com") or low.endswith(".fr"):
        return None

    # Références : enlever auteurs + années isolées dans chunks biblio/brevets
    if ref_chunk:
        if label == "PERSONNE":
            return None
        if label == "DATE_PERIODE" and re.fullmatch(r"\d{4}", text):
            return None

    # Faux labels génériques
    if label in {"PERSONNE", "ORGANISME"} and low in {
        "nous", "notre", "nos", "auteurs", "fabricants", "industriels",
        "industrie", "entreprises fabricantes", "fabricants des compresseurs"
    }:
        return None

    if label == "ETP":
        if low in {"crédit impôt recherche", "cylindre 1", "cylindre 2", "cylindre 3", "cylindre 4"}:
            return None

    if label == "LIEU":
        if low in {
            "sous-marin", "secteur marin", "fonderie",
            "sortie 3ème étage", "sortie 4ème étage",
            "1er étage", "2ème étage", "3ème étage", "4ème étage",
            "deuxième étage", "troisième étage", "quatrième étage",
            "cylindre 1", "cylindre 2", "cylindre 3", "cylindre 4",
            "cavités 1 et 2", "cavités 2 et 1", "cavités 2 et 3",
            "cavités 3 et 2", "cavités 3 et 4", "cavités 4 et 3",
            "cavités 4 et 1"
        }:
            return None

    if label == "DATE_PERIODE":
        if not is_valid_date(text):
            return None

    if label == "RESULTAT_RD" and low in {
        "résultat", "résultats", "résultats obtenus",
        "résultats des essais", "résultats de cette étude"
    }:
        return None
    # Dans clean_entity(), après URLs / mails
    if any(x in low for x in {
        "siret",
        "tva",
        "rcs",
        "ape 291",
        "fax",
        "girodin-sauer.com",
        "info@girodin-sauer",
    }):
        return None

    # Correction directe
    if low in AUTO_FIX:
        ent["text"] = text
        ent["label"] = AUTO_FIX[low]
        ent["status"] = "force_cleaned"
        return ent

    # Patterns utiles
    if "compresseur" in low and label in {"COMPOSANT_TECHNIQUE", "EQUIPEMENT_RD", "TECHNOLOGIE_RD"}:
        if low not in {"compresseur", "le compresseur"}:
            ent["label"] = "EQUIPEMENT_RD"
            ent["status"] = "force_cleaned"

    if re.search(r"\b(tgm\s?100|tgm100)\b", low):
        ent["label"] = "EQUIPEMENT_RD"
        ent["status"] = "force_cleaned"

    if "contrepoids" in low or "contre-poids" in low:
        ent["label"] = "COMPOSANT_TECHNIQUE"
        ent["status"] = "force_cleaned"

    if "réfrigérant" in low or "refroidissement" in low or "séparateur" in low:
        ent["label"] = "COMPOSANT_TECHNIQUE"
        ent["status"] = "force_cleaned"

    if "verrou" in low:
        ent["label"] = "VERROU_TECH"
        ent["status"] = "force_cleaned"

    # Enlever les entités trop courtes sauf acronymes utiles
    if len(text) <= 2 and low not in {"ra"}:
        return None

    ent["text"] = text
    return ent

def deduplicate(entities):
    seen = set()
    out = []

    for ent in entities:
        key = (
            lower_norm(ent.get("text", "")),
            ent.get("label"),
            ent.get("start"),
            ent.get("end"),
        )

        if key in seen:
            continue

        seen.add(key)
        out.append(ent)

    return out

with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

before = 0
after = 0
removed = 0
fixed = 0

for item in data:
    text_block = item.get("text", "")
    ref_chunk = is_reference_chunk(text_block)

    new_entities = []

    for ent in item.get("entities", []):
        before += 1

        old_label = ent.get("label")
        old_text = ent.get("text")

        cleaned = clean_entity(dict(ent), ref_chunk=ref_chunk)

        if cleaned is None:
            removed += 1
            continue

        if (
            cleaned.get("label") != old_label
            or cleaned.get("text") != old_text
            or cleaned.get("status") == "force_cleaned"
        ):
            fixed += 1

        new_entities.append(cleaned)

    item["entities"] = deduplicate(new_entities)
    item["annotation_status"] = "force_cleaned"
    after += len(item["entities"])

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("✅ Force clean projet 7 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")
print(f"Output  : {OUTPUT}")