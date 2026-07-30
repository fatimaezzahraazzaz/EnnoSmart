from pathlib import Path
import json
import re

INPUT = Path(r"C:\EnnoSmart\projects\projet_8_\annotations\ner_candidates.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_8_\annotations\ner_candidates_clean.json")

KEEP_SECTION_TITLES = True

REMOVE_EXACT = {
    # Bruit générique / faux organismes
    "nous",
    "notre",
    "nos",
    "client",
    "industriels",
    "industrie",
    "ministère de la défense",
    "nom",
    "prénom",
    "fonction",
    "heures",

    # Faux lieux / contextes génériques
    "environnement industriel ouvert",
    "site industriel de fibrage",
    "environnement industriel contraint",
    "installations existantes",

    # Trop générique
    "résultats",
    "résultat",
    "résultats de nos travaux",
    "objectifs",
    "objectif",
    "démarche",
    "travaux",
    "opération",
    "système",
    "systèmes",

    # Montant marché pas CIR réel
    "8,76 milliards de dollars",
}

AUTO_FIX = {
    # PERSONNES
    "s. de noual": "PERSONNE",
    "g. bonilla": "PERSONNE",
    "de noual sébastien": "PERSONNE",
    "bonilla geovanni": "PERSONNE",
    "sutherland anton": "PERSONNE",

    # ORGANISMES
    "dga": "ORGANISME",
    "cts": "ORGANISME",
    "praxair": "ORGANISME",
    "sysadvance": "ORGANISME",
    "girodin": "ORGANISME",
    "girodin-sauer": "ORGANISME",

    # DOMAINES
    "génie des procédés": "DOMAINE_RD",
    "génie industriel": "DOMAINE_RD",
    "fibre optique": "DOMAINE_RD",
    "fibres optiques": "DOMAINE_RD",
    "fabrication des fibres optiques": "DOMAINE_RD",
    "procédé de fibrage": "DOMAINE_RD",
    "fibrage": "DOMAINE_RD",
    "captation et séparation de gaz": "DOMAINE_RD",
    "séparation de gaz": "DOMAINE_RD",
    "gaz industriels": "DOMAINE_RD",

    # TECHNOLOGIES
    "vpsa": "TECHNOLOGIE_RD",
    "psa": "TECHNOLOGIE_RD",
    "technologie vpsa": "TECHNOLOGIE_RD",
    "technologie psa": "TECHNOLOGIE_RD",
    "vacuum pressure swing adsorption": "TECHNOLOGIE_RD",
    "vacuum\npressure swing adsorption": "TECHNOLOGIE_RD",
    "pressure swing adsorption": "TECHNOLOGIE_RD",
    "vacuum pressure swing adsorption vpsa": "TECHNOLOGIE_RD",
    "psa technology": "TECHNOLOGIE_RD",
    "vpsa technology": "TECHNOLOGIE_RD",
    "procédé vpsa": "TECHNOLOGIE_RD",
    "procédé psa": "TECHNOLOGIE_RD",
    "adsorption": "TECHNOLOGIE_RD",
    "désorption": "TECHNOLOGIE_RD",
    "régénération": "TECHNOLOGIE_RD",
    "purification membranaire": "TECHNOLOGIE_RD",
    "cryogénie": "TECHNOLOGIE_RD",

    # EQUIPEMENTS / SYSTEMES
    "helisys": "EQUIPEMENT_RD",
    "helisys 2s": "EQUIPEMENT_RD",
    "vpsa unit": "EQUIPEMENT_RD",
    "système vpsa": "EQUIPEMENT_RD",
    "système de purification vpsa": "EQUIPEMENT_RD",
    "système de purification": "EQUIPEMENT_RD",
    "module de purification": "EQUIPEMENT_RD",
    "module de récupération et recyclage de l’hélium": "EQUIPEMENT_RD",
    "module de récupération et recyclage de l'helium": "EQUIPEMENT_RD",
    "module de collecte": "EQUIPEMENT_RD",
    "module de compression basse pression": "EQUIPEMENT_RD",
    "module de stockage": "EQUIPEMENT_RD",
    "module de compression haute pression": "EQUIPEMENT_RD",
    "module de séparation": "EQUIPEMENT_RD",
    "système de captation": "EQUIPEMENT_RD",
    "systèmes de captation": "EQUIPEMENT_RD",
    "système d’aspiration du gaz": "EQUIPEMENT_RD",
    "système d'aspiration du gaz": "EQUIPEMENT_RD",
    "mâchoire d’aspiration": "EQUIPEMENT_RD",
    "mâchoire d'aspiration": "EQUIPEMENT_RD",
    "mâchoires en aluminium": "EQUIPEMENT_RD",
    "pompe à vide": "EQUIPEMENT_RD",
    "vacuum pump": "EQUIPEMENT_RD",
    "vanne de régulation": "EQUIPEMENT_RD",
    "frv001": "EQUIPEMENT_RD",
    "électrovanne d’aspiration": "EQUIPEMENT_RD",
    "électrovanne d'aspiration": "EQUIPEMENT_RD",
    "pv001": "EQUIPEMENT_RD",
    "réservoir 002": "EQUIPEMENT_RD",
    "gazomètre": "EQUIPEMENT_RD",
    "gasbag": "EQUIPEMENT_RD",
    "gazomètre basse pression": "EQUIPEMENT_RD",
    "réservoir haute pression": "EQUIPEMENT_RD",
    "réservoir de stockage": "EQUIPEMENT_RD",
    "réservoir de récupération": "EQUIPEMENT_RD",
    "analyseur de gaz": "EQUIPEMENT_RD",
    "colonnes d’adsorption": "EQUIPEMENT_RD",
    "colonnes d'adsorption": "EQUIPEMENT_RD",
    "lits d’adsorption": "EQUIPEMENT_RD",
    "lits d'adsorption": "EQUIPEMENT_RD",
    "adsorption beds": "EQUIPEMENT_RD",
    "adsorption vessels": "EQUIPEMENT_RD",
    "adsorbent bed": "EQUIPEMENT_RD",
    "un module de compression basse pression": "EQUIPEMENT_RD",
    "adsorbent bed" : "EQUIPEMENT_RD",
    # COMPOSANTS TECHNIQUES
    "systèmes de recyclage de l’hélium": "COMPOSANT_TECHNIQUE",
    "systèmes de recyclage de l'helium": "COMPOSANT_TECHNIQUE",
    "procédé de récupération et recyclage de l’hélium": "COMPOSANT_TECHNIQUE",
    "procédé de récupération et recyclage de l'helium": "COMPOSANT_TECHNIQUE",
    "procédé complet de récupération": "COMPOSANT_TECHNIQUE",
    "procédé intégré": "COMPOSANT_TECHNIQUE",
    "architecture de captation": "COMPOSANT_TECHNIQUE",
    "architecture d’aspiration": "COMPOSANT_TECHNIQUE",
    "architecture d'aspiration": "COMPOSANT_TECHNIQUE",
    "architecture de purification": "COMPOSANT_TECHNIQUE",
    "architecture à double étage": "COMPOSANT_TECHNIQUE",
    "système de recyclage": "COMPOSANT_TECHNIQUE",
    "flux de recyclage": "COMPOSANT_TECHNIQUE",
    "flux intermédiaire enrichi en hélium": "COMPOSANT_TECHNIQUE",
    "flux capté": "COMPOSANT_TECHNIQUE",
    "mélange gazeux": "COMPOSANT_TECHNIQUE",
    "ligne de récupération des gaz usés": "COMPOSANT_TECHNIQUE",
    "capteur": "COMPOSANT_TECHNIQUE",
    "vanne automatique": "COMPOSANT_TECHNIQUE",
    "compresseur": "COMPOSANT_TECHNIQUE",
    "purificateur": "COMPOSANT_TECHNIQUE",
    "filtre": "COMPOSANT_TECHNIQUE",
    "membrane": "COMPOSANT_TECHNIQUE",
    "séparateur de liquide": "COMPOSANT_TECHNIQUE",
    "amortisseur": "COMPOSANT_TECHNIQUE",

    # MATERIAUX / GAZ / ADSORBANTS
    "hélium": "MATERIAU_SPECIFIQUE",
    "l’hélium": "MATERIAU_SPECIFIQUE",
    "helium": "MATERIAU_SPECIFIQUE",
    "l'helium": "MATERIAU_SPECIFIQUE",
    "gaz": "MATERIAU_SPECIFIQUE",
    "mélange gazeux comportant 50% d’hélium": "MATERIAU_SPECIFIQUE",
    "air": "MATERIAU_SPECIFIQUE",
    "air atmosphérique": "MATERIAU_SPECIFIQUE",
    "n₂": "MATERIAU_SPECIFIQUE",
    "n2": "MATERIAU_SPECIFIQUE",
    "o₂": "MATERIAU_SPECIFIQUE",
    "o2": "MATERIAU_SPECIFIQUE",
    "co₂": "MATERIAU_SPECIFIQUE",
    "co2": "MATERIAU_SPECIFIQUE",
    "h₂o": "MATERIAU_SPECIFIQUE",
    "h2o": "MATERIAU_SPECIFIQUE",
    "azote": "MATERIAU_SPECIFIQUE",
    "oxygène": "MATERIAU_SPECIFIQUE",
    "carbon dioxide": "MATERIAU_SPECIFIQUE",
    "nitrogen": "MATERIAU_SPECIFIQUE",
    "oxygen": "MATERIAU_SPECIFIQUE",
    "fibres de verre": "MATERIAU_SPECIFIQUE",
    "fibre optique": "MATERIAU_SPECIFIQUE",
    "fibres optiques": "MATERIAU_SPECIFIQUE",
    "zéolites": "MATERIAU_SPECIFIQUE",
    "zeolites": "MATERIAU_SPECIFIQUE",
    "tamis moléculaires": "MATERIAU_SPECIFIQUE",
    "molecular sieves": "MATERIAU_SPECIFIQUE",
    "charbon actif": "MATERIAU_SPECIFIQUE",
    "activated carbon": "MATERIAU_SPECIFIQUE",
    "activated\ncarbon": "MATERIAU_SPECIFIQUE",
    "adsorbant": "MATERIAU_SPECIFIQUE",
    "adsorbants": "MATERIAU_SPECIFIQUE",
    "matériaux adsorbants": "MATERIAU_SPECIFIQUE",
    "adsorbent material": "MATERIAU_SPECIFIQUE",
    "solid adsorbent material": "MATERIAU_SPECIFIQUE",

    # METHODES
    "essais expérimentaux": "METHODE_RD",
    "résultats expérimentaux": "RESULTAT_RD",
    "expérimentations": "METHODE_RD",
    "mesures de concentration": "METHODE_RD",
    "mesure en temps réel": "METHODE_RD",
    "computational simulation": "METHODE_RD",
    "simulation": "METHODE_RD",
    "process design": "METHODE_RD",
    "material balance": "METHODE_RD",
    "modélisation": "METHODE_RD",
    "régulation asservie": "METHODE_RD",
    "régulation adaptative": "METHODE_RD",

    # VERROUS
    "verrou 1": "VERROU_TECH",
    "verrou 2": "VERROU_TECH",
    "verrou 3": "VERROU_TECH",
    "verrou 4": "VERROU_TECH",
    "le second verrou technique": "VERROU_TECH",
    "verrou technique": "VERROU_TECH",
    "captation dynamique en environnement ouvert avec débits variables": "VERROU_TECH",
    "régulation adaptative pour des flux de gaz à composition fluctuante": "VERROU_TECH",
    "adaptation du système vpsa aux conditions d’alimentation variables": "VERROU_TECH",
    "intégration non-intrusive sur installations existantes": "VERROU_TECH",

    # OBJECTIFS / RESULTATS
    "taux de récupération élevé": "OBJECTIF_RD",
    "non-impact sur le procédé de fibrage": "OBJECTIF_RD",
    "pureté minimale de 99,98%": "OBJECTIF_RD",
    "pureté supérieure à 99,98 %": "RESULTAT_RD",
    "50% d’hélium": "OBJECTIF_RD",
    "récupérer environ 7 m3/h d’hélium pur": "OBJECTIF_RD",
}

SECTION_TITLES = {
    "démarche expérimentale": "METHODE_RD",
    "démarche expérimentale, travaux r&d réalisés": "METHODE_RD",
    "objectifs visés et performances à atteindre": "OBJECTIF_RD",
    "développement et étude du système d’aspiration du gaz": "METHODE_RD",
    "développement et étude du système d'aspiration du gaz": "METHODE_RD",
    "développement du système de purification de l’hélium par technologie vpsa": "METHODE_RD",
    "développement du système de purification de l'helium par technologie vpsa": "METHODE_RD",
    "verrous et incertitudes scientifiques, techniques, technologiques": "VERROU_TECH",
    "résolution du verrou lié à la variation du débit": "METHODE_RD",
    "résolution du verrou lié à la variation de la composition du mélange": "METHODE_RD",
}

DATE_KEEP_PATTERNS = [
    r"^\d{4}$",
    r"^(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+\d{4}$",
]

REFERENCE_CHUNK_MARKERS = [
    "brevet publié",
    "article scientifique",
    "publié en",
    "publiée en",
    "littérature scientifique",
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

    # Bruit RH
    if label == "PERSONNE" and low in {"nom", "prénom", "fonction", "chef de projet"}:
        return None

    # Faux organismes
    if label == "ORGANISME" and low in {
        "nous", "client", "industrie", "industriels", "ministère de la défense"
    }:
        return None

    # Faux lieux
    if label == "LIEU" and low in {
        "environnement industriel ouvert",
        "site industriel de fibrage",
        "environnement industriel contraint",
    }:
        return None

    # Dates : garder années/mois année
    if label == "DATE_PERIODE":
        if not is_valid_date(text):
            return None

    # Dans état de l'art, les années de brevets/articles peuvent être gardées.
    # Mais on supprime les personnes si elles apparaissent un jour dans ref_chunk.
    if ref_chunk and label == "PERSONNE":
        return None

    # Corrections directes
    if low in AUTO_FIX:
        ent["text"] = text
        ent["label"] = AUTO_FIX[low]
        ent["status"] = "force_cleaned"
        return ent

    # Patterns utiles
    if re.fullmatch(r"vpsa|psa", low):
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if "vpsa" in low and label in {"ORGANISME", "MATERIAU_SPECIFIQUE", "COMPOSANT_TECHNIQUE", "EQUIPEMENT_RD"}:
        # VPSA est surtout une technologie; si phrase "système VPSA", équipement
        if "système" in low or "unit" in low or "module" in low:
            ent["label"] = "EQUIPEMENT_RD"
        else:
            ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if "psa" in low and label in {"ORGANISME", "MATERIAU_SPECIFIQUE", "COMPOSANT_TECHNIQUE", "EQUIPEMENT_RD"}:
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if "captation" in low or "aspiration" in low:
        if label in {"VERROU_TECH", "LIEU", "METHODE_RD"}:
            ent["label"] = "EQUIPEMENT_RD"
            ent["status"] = "force_cleaned"

    if "hélium" in low or "helium" in low:
        if label in {"ORGANISME", "TECHNOLOGIE_RD"}:
            ent["label"] = "MATERIAU_SPECIFIQUE"
            ent["status"] = "force_cleaned"

    if "zéolite" in low or "zeolite" in low or "charbon actif" in low or "activated carbon" in low or "molecular sieve" in low:
        ent["label"] = "MATERIAU_SPECIFIQUE"
        ent["status"] = "force_cleaned"

    if "verrou" in low:
        ent["label"] = "VERROU_TECH"
        ent["status"] = "force_cleaned"

    # Trop court sauf acronymes utiles
    if len(text) <= 2 and low not in {"he", "n₂", "o₂", "n2", "o2"}:
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

print("✅ Force clean projet 8 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")
print(f"Output  : {OUTPUT}")