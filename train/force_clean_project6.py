from pathlib import Path
import json
import re

INPUT = Path(r"C:\EnnoSmart\projects\projet_6_\annotations\ner_candidates.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_6_\annotations\ner_candidates_clean.json")

KEEP_SECTION_TITLES = True

REMOVE_EXACT = {
    # Bruit générique / tableau
    "nom",
    "prénom",
    "fonction",
    "heures",
    "nous",
    "objectifs",
    "objectif",
    "résultats",
    "résultat",
    "résultats obtenus",
    "résultats des essais",
    "les auteurs",
    "auteurs",
    "composites",
    "composite",
    "résultats des essais menés sur l’échantillon 5x3 en pla",
    "module actif30",
    
    # Faux organismes / lieux / dates
    "ministère de la défense",
    "b3",
    "constructeurs",
    "industrie",
    "bâtiments",
    "actuellement",
    "dernières décennies",
    "deux décennies",
    "une dizaine d’années",
    "quelques années",
    "phase préliminaire",

    # Références scientifiques
    "onur",
    "ranjbar10",
    "ranjbar36",
    "ranjbar et al",
    "mostafa ranjbar",
    "k. essassi",
    "coja et kari",
    "somanath et al",

    # Termes trop génériques ou mal classés
    "raideurs dynamiques",
    "raideur dynamique",
    "module de young",
    "phase r&d spécifique",
}

AUTO_FIX = {
    # ORGANISMES
    "module actif30": "COMPOSANT_TECHNIQUE",
    "cevaa": "ORGANISME",
    "gemtex": "ORGANISME",
    "lem3": "ORGANISME",
    "lem 3": "ORGANISME",
    "dga": "ORGANISME",
    "rapid": "ORGANISME",

    # PERSONNES utiles dossier
    "t. powolny": "PERSONNE",
    "powolny tom": "PERSONNE",
    "ait younes tarik": "PERSONNE",
    "catteau antonin": "PERSONNE",
    "chevallier nicolas": "PERSONNE",
    "guilhem romain": "PERSONNE",
    "havard alexandre": "PERSONNE",
    "presotto clement": "PERSONNE",
    "beaur benoit": "PERSONNE",
    "bouvry francois": "PERSONNE",
    "pelloie quentin": "PERSONNE",

    # METHODES / TECHNOLOGIES
    "démarche expérimentale": "METHODE_RD",
    "protocole expérimental": "METHODE_RD",
    "validation expérimentale": "METHODE_RD",
    "études expérimentales et numériques": "METHODE_RD",
    "essais dma": "METHODE_RD",
    "analyse mécanique dynamique": "METHODE_RD",
    "dma": "METHODE_RD",
    "loi wlf": "METHODE_RD",
    "wlf": "METHODE_RD",
    "williams-landel-ferry": "METHODE_RD",
    "méthode 3": "METHODE_RD",
    "fabrication additive": "METHODE_RD",
    "impression 3d": "METHODE_RD",
    "fea": "METHODE_RD",
    "analyse par éléments finis": "METHODE_RD",
    "optimisation topologique continue": "METHODE_RD",
    "optimisation topologique discrète": "METHODE_RD",
    "optimisation topologique": "METHODE_RD",
    "homogénéisation discrète": "METHODE_RD",
    "homogénéisation micromorphique": "METHODE_RD",
    "transformée de laplace-carson": "METHODE_RD",
    "méthodes d’optimisation topologique multi-échelles": "METHODE_RD",
    "méthodes d'optimisation topologique multi-échelles": "METHODE_RD",

    # EQUIPEMENTS
    "banc dma": "EQUIPEMENT_RD",
    "banc rapid": "EQUIPEMENT_RD",
    "banc expérimental": "EQUIPEMENT_RD",
    "banc d’essais": "EQUIPEMENT_RD",
    "banc d'essais": "EQUIPEMENT_RD",
    "nouveau banc d’essai rapid": "EQUIPEMENT_RD",
    "pot vibrant": "EQUIPEMENT_RD",
    "marteau d’impact": "EQUIPEMENT_RD",
    "marteau d'impact": "EQUIPEMENT_RD",
    "marteaux d'impact": "EQUIPEMENT_RD",
    "accéléromètre monoaxe": "EQUIPEMENT_RD",
    "capteurs triaxes": "EQUIPEMENT_RD",
    "viscoanalyseur": "EQUIPEMENT_RD",
    "moule imprimé 3d": "EQUIPEMENT_RD",
    "étuve": "EQUIPEMENT_RD",

    # COMPOSANTS TECHNIQUES
    "plots de découplage": "COMPOSANT_TECHNIQUE",
    "plots de découplage vibratoire": "COMPOSANT_TECHNIQUE",
    "plots de découplage vibratoires": "COMPOSANT_TECHNIQUE",
    "plots élastiques": "COMPOSANT_TECHNIQUE",
    "plots de suspension": "COMPOSANT_TECHNIQUE",
    "systèmes passifs": "COMPOSANT_TECHNIQUE",
    "système passif": "COMPOSANT_TECHNIQUE",
    "systèmes actifs": "COMPOSANT_TECHNIQUE",
    "système actif": "COMPOSANT_TECHNIQUE",
    "systèmes semi-actifs": "COMPOSANT_TECHNIQUE",
    "système semi-actif": "COMPOSANT_TECHNIQUE",
    "systèmes adaptatifs-passifs": "COMPOSANT_TECHNIQUE",
    "actionneurs": "COMPOSANT_TECHNIQUE",
    "actionneur": "COMPOSANT_TECHNIQUE",
    "actionneurs piézoélectriques": "COMPOSANT_TECHNIQUE",
    "moteurs linéaires": "COMPOSANT_TECHNIQUE",
    "dispositifs magnétiques": "COMPOSANT_TECHNIQUE",
    "ressorts mécaniques": "COMPOSANT_TECHNIQUE",
    "ressorts": "COMPOSANT_TECHNIQUE",
    "amortisseurs": "COMPOSANT_TECHNIQUE",
    "capteur": "COMPOSANT_TECHNIQUE",
    "contrôleur": "COMPOSANT_TECHNIQUE",
    "module actif léger": "COMPOSANT_TECHNIQUE",
    "liaison glissière": "COMPOSANT_TECHNIQUE",
    "châssis": "COMPOSANT_TECHNIQUE",
    "berceaux": "COMPOSANT_TECHNIQUE",
    "blocs": "COMPOSANT_TECHNIQUE",
    "carlingages": "COMPOSANT_TECHNIQUE",
    "câbles électriques": "COMPOSANT_TECHNIQUE",
    "renforts auxétiques": "COMPOSANT_TECHNIQUE",
    "fil composite": "COMPOSANT_TECHNIQUE",
    "fils composites hybrides": "COMPOSANT_TECHNIQUE",
    "guipe1": "COMPOSANT_TECHNIQUE",
    "guipe2": "COMPOSANT_TECHNIQUE",

    # MATERIAUX
    "tpu": "MATERIAU_SPECIFIQUE",
    "pla": "MATERIAU_SPECIFIQUE",
    "epdm": "MATERIAU_SPECIFIQUE",
    "abs": "MATERIAU_SPECIFIQUE",
    "pu": "MATERIAU_SPECIFIQUE",
    "polyuréthane": "MATERIAU_SPECIFIQUE",
    "polymère": "MATERIAU_SPECIFIQUE",
    "composites auxétiques": "MATERIAU_SPECIFIQUE",
    "composite à renfort fibreux": "MATERIAU_SPECIFIQUE",
    "composites fibreux": "MATERIAU_SPECIFIQUE",
    "composites fibreux auxétiques": "MATERIAU_SPECIFIQUE",
    "coupons composites auxétiques": "MATERIAU_SPECIFIQUE",
    "structures tridimensionnelles auxétiques": "MATERIAU_SPECIFIQUE",
    "structures auxétiques": "MATERIAU_SPECIFIQUE",
    "matériaux auxétiques": "MATERIAU_SPECIFIQUE",
    "matériaux architecturés de nature auxétique": "MATERIAU_SPECIFIQUE",
    "matériaux cellulaires auxétiques": "MATERIAU_SPECIFIQUE",
    "matériaux auxétiques poreux": "MATERIAU_SPECIFIQUE",
    "matériaux poreux conventionnels et auxétiques": "MATERIAU_SPECIFIQUE",
    "métamatériaux mécaniques": "MATERIAU_SPECIFIQUE",
    "métamatériaux auxétiques": "MATERIAU_SPECIFIQUE",
    "stratifié composite": "MATERIAU_SPECIFIQUE",
    "matrice époxy": "MATERIAU_SPECIFIQUE",
    "matériaux sandwichs": "MATERIAU_SPECIFIQUE",
    "panneaux sandwich": "MATERIAU_SPECIFIQUE",
    "noyaux auxétiques anti-tétrachiraux et hexagonaux": "MATERIAU_SPECIFIQUE",
    "matériau isotrope": "MATERIAU_SPECIFIQUE",
    "matériaux viscoélastiques": "MATERIAU_SPECIFIQUE",
    "plots en caoutchouc": "MATERIAU_SPECIFIQUE",
    "coussins en caoutchouc": "MATERIAU_SPECIFIQUE",
    "caoutchouc naturel": "MATERIAU_SPECIFIQUE",
    "caoutchouc": "MATERIAU_SPECIFIQUE",
    "mousses techniques": "MATERIAU_SPECIFIQUE",
    "câbles métalliques": "MATERIAU_SPECIFIQUE",
    "alliages à mémoire de forme": "MATERIAU_SPECIFIQUE",
    "fluides magnétorhéologiques - mr": "MATERIAU_SPECIFIQUE",
    "élastomère": "MATERIAU_SPECIFIQUE",
    "élastomères": "MATERIAU_SPECIFIQUE",
    "âme élastomère": "MATERIAU_SPECIFIQUE",
    "aramide": "MATERIAU_SPECIFIQUE",
    "para-aramide": "MATERIAU_SPECIFIQUE",
    "twaron®": "MATERIAU_SPECIFIQUE",
    "twaron": "MATERIAU_SPECIFIQUE",
    "vistamaxx 6202": "MATERIAU_SPECIFIQUE",
    "polypropylène": "MATERIAU_SPECIFIQUE",
    "pp": "MATERIAU_SPECIFIQUE",
    "pla biosourcé renforcé par fibres de lin": "MATERIAU_SPECIFIQUE",
    "fibres de lin": "MATERIAU_SPECIFIQUE",
    "profilés en acier": "MATERIAU_SPECIFIQUE",
    "nylon m": "MATERIAU_SPECIFIQUE",
    "nylon blanc": "MATERIAU_SPECIFIQUE",
    "caoutchouc m": "MATERIAU_SPECIFIQUE",
    "caoutchouc noir": "MATERIAU_SPECIFIQUE",
    "métal s": "MATERIAU_SPECIFIQUE",
    "tpu aux 5x3": "MATERIAU_SPECIFIQUE",
    "orthotrope": "MATERIAU_SPECIFIQUE",
    "tétragonal": "MATERIAU_SPECIFIQUE",
    "isotrope": "MATERIAU_SPECIFIQUE",

    # DOMAINES
    "secteurs de motorisation électrique": "DOMAINE_RD",
    "motorisation électrique": "DOMAINE_RD",
    "naval de défense": "DOMAINE_RD",
    "isolation vibratoire": "DOMAINE_RD",
    "vibroacoustique": "DOMAINE_RD",
    "matériaux, métallurgie": "DOMAINE_RD",

    # VERROUS
    "verrou scientifique": "VERROU_TECH",
    "verrous techniques": "VERROU_TECH",
    "verrous et difficultés technologiques": "VERROU_TECH",
    "résonances parasites": "VERROU_TECH",
    "manque d’études": "VERROU_TECH",
    "manque d'etudes": "VERROU_TECH",
    "difficultés techniques": "VERROU_TECH",
}

SECTION_TITLES = {
    "démarche expérimentale": "METHODE_RD",
    "objectifs visés et performances à atteindre": "OBJECTIF_RD",
    "verrou scientifique lié au manque d’études sur le comportement vibratoire des composites auxétiques": "VERROU_TECH",
    "verrous et difficultés liés à la fabrication et mise en forme": "VERROU_TECH",
    "verrous et difficultés techniques liés à la caractérisation expérimentale des structures auxétiques à de hautes fréquences": "VERROU_TECH",
    "incertitudes liées au développement de plots de découplage vibratoires en exploitant les composites auxétiques": "VERROU_TECH",
    "résultats obtenus avec les configurations métal s et caoutchouc m": "RESULTAT_RD",
    "résultats des essais nylon m": "RESULTAT_RD",
}

REFERENCE_PATTERNS = [
    r".+\bet al\.?\d*$",
    r"^[A-Z]\.\s?[A-Z][A-Za-zÀ-ÿ\-]+$",
    r"^[A-Z][a-zà-ÿ\-]+ et [A-Z][a-zà-ÿ\-]+$",
    r"^Mostafa Ranjbar$",
    r"^K\.\s?Essassi$",
    r"^Onur$",
    r"^Ranjbar\d*$",
]

DATE_KEEP_PATTERNS = [
    r"^\d{4}$",
    r"^(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+\d{4}$",
]

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def lower_norm(text: str) -> str:
    return norm(text).lower()

def is_reference_author(text: str) -> bool:
    t = norm(text)
    low = lower_norm(t)

    if low in REMOVE_EXACT:
        return True

    return any(re.match(p, t, flags=re.IGNORECASE) for p in REFERENCE_PATTERNS)

def is_valid_date(text: str) -> bool:
    t = lower_norm(text)
    return any(re.match(p, t, flags=re.IGNORECASE) for p in DATE_KEEP_PATTERNS)

def is_reference_chunk(text: str) -> bool:
    low = lower_norm(text)
    return any(x in low for x in [
        "et al",
        "travail de recherche",
        "thèse de",
        "articles scientifiques",
        "publications académiques",
        "littérature",
    ])

def clean_entity(ent):
    text = norm(ent.get("text", ""))
    label = ent.get("label", "")
    low = lower_norm(text)

    if not text:
        return None

    # Sections/titles utiles
    if low in SECTION_TITLES:
        if KEEP_SECTION_TITLES:
            ent["text"] = text
            ent["label"] = SECTION_TITLES[low]
            ent["status"] = "force_cleaned"
            return ent
        return None

    # Suppression exacte
    if low in REMOVE_EXACT:
        return None

    # URLs / mails
    if "@" in text or low.startswith("http") or low.startswith("www.") or low.endswith(".com") or low.endswith(".fr"):
        return None

    # Faux PERSONNE
    if label == "PERSONNE":
        if is_reference_author(text):
            return None
        if low in {"nom", "prénom", "auteurs", "les auteurs"}:
            return None

    # Faux ORGANISME
    if label == "ORGANISME":
        if low in {"nous", "ministère de la défense", "constructeurs", "b3"}:
            return None

    # Faux LIEU
    if label == "LIEU":
        if low in {"industrie", "bâtiments", "batiments"}:
            return None

    # Faux DATE_PERIODE
    if label == "DATE_PERIODE":
        if not is_valid_date(text):
            return None

    # Generic trop faible
    if label in {"OBJECTIF_RD", "RESULTAT_RD"} and low in {"objectif", "objectifs", "résultat", "résultats", "résultats obtenus"}:
        return None

    # Correction directe
    if low in AUTO_FIX:
        ent["text"] = text
        ent["label"] = AUTO_FIX[low]
        ent["status"] = "force_cleaned"
        return ent

    # Patterns utiles
    if re.search(r"\bTPU\b", text):
        ent["label"] = "MATERIAU_SPECIFIQUE"
        ent["status"] = "force_cleaned"

    if re.search(r"\bPLA\b", text):
        ent["label"] = "MATERIAU_SPECIFIQUE"
        ent["status"] = "force_cleaned"

    if "composite" in low or "auxétique" in low or "auxetique" in low:
        if label in {"MATERIAU_SPECIFIQUE", "COMPOSANT_TECHNIQUE", "TECHNOLOGIE_RD"}:
            ent["label"] = "MATERIAU_SPECIFIQUE"
            ent["status"] = "force_cleaned"

    if "banc" in low and label in {"TECHNOLOGIE_RD", "COMPOSANT_TECHNIQUE"}:
        ent["label"] = "EQUIPEMENT_RD"
        ent["status"] = "force_cleaned"

    if "plots de découplage" in low:
        ent["label"] = "COMPOSANT_TECHNIQUE"
        ent["status"] = "force_cleaned"

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

        ent_text = norm(ent.get("text", ""))
        ent_label = ent.get("label", "")

        # Dans les chunks bibliographiques / état de l’art, supprimer auteurs et années isolées
        if ref_chunk:
            if ent_label == "PERSONNE":
                removed += 1
                continue

            if ent_label == "DATE_PERIODE" and re.fullmatch(r"\d{4}", ent_text):
                removed += 1
                continue

        cleaned = clean_entity(dict(ent))

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

print("✅ Force clean projet 6 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")
print(f"Output  : {OUTPUT}")