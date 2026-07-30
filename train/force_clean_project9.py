from pathlib import Path
import json
import re

INPUT = Path(r"C:\EnnoSmart\projects\projet_9_\annotations\ner_candidates.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_9_\annotations\ner_candidates_clean.json")

KEEP_SECTION_TITLES = True

REMOVE_EXACT = {
    # Pronoms / générique
    "nous",
    "notre",
    "nos",
    "client",
    "personnel soignant",
    "infirmière",
    "infirmière de bloc",
    "chirurgien",
    "personnes",
    "collaborateurs",
    "acteurs du domaine",

    # Bruit RH / tableau
    "nom",
    "prénom",
    "fonction",
    "heures",
    "chef de projet",
    "nom prénom",

    # Faux organismes / codes
    "ministère de la défense",
    "b7c12",
    "entite",
    "[x]",

    # Faux montants / labels colonnes
    "coût total",
    "000 000 €",
    "000\u00a0000 €",

    # Génériques trop faibles
    "objectif",
    "objectifs",
    "objectifs ambitieux",
    "objectifs visés",
    "objectifs de r&d",
    "résultat",
    "résultats",
    "résultats obtenus",
    "résultats de nos travaux",
    "solution technique",
    "solutions techniques",
    "système",
    "concept",
    "matériau",
    "matériaux",
    "interne",
    "ergonomie",
    "recyclabilité des matériaux",
    "sécurisation du dispositif médical",

    # Faux lieux
    "plan de travail",
    "bloc opératoire",
    "salon de xxxxxx",

    # Dates non utiles
    "année n",
    "première année d’étude",
    "première année d'etude",

    # Trop vague
    "domaine médical",
    "matériaux délicats",
    "biodégradables",
    "matériaux recyclables",
    "logement spécifique",
    "recherche de concepts",
    "domaine du médical",
}

AUTO_FIX = {
    # ORGANISMES
    "systèmes de barrière stérile préformés": "COMPOSANT_TECHNIQUE",
    "systèmes de barrière stérile": "COMPOSANT_TECHNIQUE",
    "barrière stérile": "COMPOSANT_TECHNIQUE",
    "top clean packaging": "ORGANISME",
    "tcp r&i": "ORGANISME",
    "ocde": "ORGANISME",
    "dga": "ORGANISME",
    "lne": "ORGANISME",
    "société twenans": "ORGANISME",
    "twenans": "ORGANISME",
    "velfort": "ORGANISME",

    # PERSONNES utiles
    "hervé vergne": "PERSONNE",
    "vergne hervé": "PERSONNE",
    "vergne h": "PERSONNE",
    "berry alexis": "PERSONNE",
    "jeannin céline": "PERSONNE",
    "moigner geoffrey": "PERSONNE",
    "ytournel jérôme": "PERSONNE",
    "nathalie techer": "PERSONNE",
    "bodiment vicky": "PERSONNE",

    # DOMAINES
    "emballage médical": "DOMAINE_RD",
    "industrie de l'emballage": "DOMAINE_RD",
    "dispositifs médicaux": "DOMAINE_RD",
    "device médical": "DOMAINE_RD",
    "domaine de la santé": "DOMAINE_RD",
    "domaine médical": "DOMAINE_RD",

    # METHODES / NORMES / PROCEDES
    "amdec": "METHODE_RD",
    "analyse fonctionnelle": "METHODE_RD",
    "simulation par éléments finis": "METHODE_RD",
    "impression 3d": "METHODE_RD",
    "impression 3 d": "METHODE_RD",
    "conception 3d": "METHODE_RD",
    "thermoformage": "METHODE_RD",
    "injection": "METHODE_RD",
    "soudure haute fréquence": "METHODE_RD",
    "découpe et soudure par haute fréquence": "METHODE_RD",
    "essais mécaniques": "METHODE_RD",
    "essais climatiques": "METHODE_RD",
    "méthodes d’essai": "METHODE_RD",
    "méthodes d'essai": "METHODE_RD",
    "tests de chocs": "METHODE_RD",
    "tests de chute": "METHODE_RD",
    "tests d’abrasion": "METHODE_RD",
    "tests d'abrasion": "METHODE_RD",
    "tests de transport": "METHODE_RD",
    "test en environnement": "METHODE_RD",
    "gerbage en transport": "METHODE_RD",
    "vibration en chargement libre": "METHODE_RD",
    "pression haute altitude": "METHODE_RD",
    "norme iso 11607": "METHODE_RD",
    "nf en iso 11607-1": "METHODE_RD",
    "nf en iso 11607-2": "METHODE_RD",
    "iso 11607": "METHODE_RD",
    "ista": "METHODE_RD",
    "ista 3": "METHODE_RD",
    "astm 4169": "METHODE_RD",
    "astm4169": "METHODE_RD",
    "astm4169 -23": "METHODE_RD",
    "astm 4169 -23": "METHODE_RD",
    "eto": "METHODE_RD",
    "gamma": "METHODE_RD",
    "stérilisation eto": "METHODE_RD",
    "stérilisation gamma": "METHODE_RD",
    "vapeur": "METHODE_RD",

    # VERROUS
    "verrou principal": "VERROU_TECH",
    "principal verrou": "VERROU_TECH",
    "verrou": "VERROU_TECH",
    "tenue aux chocs": "VERROU_TECH",
    "tenue aux chocs et aux chutes": "VERROU_TECH",
    "résistance à l’abrasion": "VERROU_TECH",
    "résistance à l'abrasion": "VERROU_TECH",
    "sécurisation du dispositif médical": "VERROU_TECH",
    "recyclabilité des matériaux": "VERROU_TECH",
    "amortissement des chocs": "VERROU_TECH",
    "absence d’amortissement": "VERROU_TECH",
    "absence d'amortissement": "VERROU_TECH",
    "risque de chute": "VERROU_TECH",
    "risque patient": "VERROU_TECH",

    # COMPOSANTS / CONCEPTS EMBALLAGE
    "emballage device medical en suspension": "COMPOSANT_TECHNIQUE",
    "emballage suspension": "COMPOSANT_TECHNIQUE",
    "emballage suspendu": "COMPOSANT_TECHNIQUE",
    "système d’emballage suspendu": "COMPOSANT_TECHNIQUE",
    "système d'emballage suspendu": "COMPOSANT_TECHNIQUE",
    "système d’emballage double protection": "COMPOSANT_TECHNIQUE",
    "système d'emballage double protection": "COMPOSANT_TECHNIQUE",
    "emballage médical": "COMPOSANT_TECHNIQUE",
    "emballage stérilisable": "COMPOSANT_TECHNIQUE",
    "emballage recyclable": "COMPOSANT_TECHNIQUE",
    "emballage performant": "COMPOSANT_TECHNIQUE",
    "poche tpu": "COMPOSANT_TECHNIQUE",
    "poches tpu": "COMPOSANT_TECHNIQUE",
    "poche en tpu": "COMPOSANT_TECHNIQUE",
    "fourreau": "COMPOSANT_TECHNIQUE",
    "le fourreau": "COMPOSANT_TECHNIQUE",
    "rosace": "COMPOSANT_TECHNIQUE",
    "sphère": "COMPOSANT_TECHNIQUE",
    "sphere": "COMPOSANT_TECHNIQUE",
    "cube": "COMPOSANT_TECHNIQUE",
    "compound": "COMPOSANT_TECHNIQUE",
    "le compound": "COMPOSANT_TECHNIQUE",
    "suspendu": "COMPOSANT_TECHNIQUE",
    "boitier": "COMPOSANT_TECHNIQUE",
    "boîtier": "COMPOSANT_TECHNIQUE",
    "boîtier refermable": "COMPOSANT_TECHNIQUE",
    "boitier avec film étirable": "COMPOSANT_TECHNIQUE",
    "boîtier avec film étirable": "COMPOSANT_TECHNIQUE",
    "film étirable": "COMPOSANT_TECHNIQUE",
    "couvercle de protection": "COMPOSANT_TECHNIQUE",
    "opercule": "COMPOSANT_TECHNIQUE",
    "opercule de fermeture": "COMPOSANT_TECHNIQUE",
    "opercule de suremballage": "COMPOSANT_TECHNIQUE",
    "suremballage": "COMPOSANT_TECHNIQUE",
    "enveloppe": "COMPOSANT_TECHNIQUE",
    "enveloppe amortissante": "COMPOSANT_TECHNIQUE",
    "membrane amortissante": "COMPOSANT_TECHNIQUE",
    "charnière": "COMPOSANT_TECHNIQUE",
    "cavité": "COMPOSANT_TECHNIQUE",
    "cuve inférieure": "COMPOSANT_TECHNIQUE",
    "cuve supérieure inversée": "COMPOSANT_TECHNIQUE",
    "rebord interne": "COMPOSANT_TECHNIQUE",
    "bordure externe": "COMPOSANT_TECHNIQUE",
    "plots": "COMPOSANT_TECHNIQUE",
    "canaux de distribution": "COMPOSANT_TECHNIQUE",
    "cale en mousse": "COMPOSANT_TECHNIQUE",
    "dm": "COMPOSANT_TECHNIQUE",
    "dispositif médical": "COMPOSANT_TECHNIQUE",
    "dispositifs médicaux": "COMPOSANT_TECHNIQUE",
    "prothèses de hanche": "COMPOSANT_TECHNIQUE",
    "prothèses de genoux": "COMPOSANT_TECHNIQUE",
    "stimulateur cardiaque": "COMPOSANT_TECHNIQUE",
    "pacemaker": "COMPOSANT_TECHNIQUE",
    "barrette de réparation de mâchoire": "COMPOSANT_TECHNIQUE",

    # MATERIAUX
    "tpu": "MATERIAU_SPECIFIQUE",
    "film tpu": "MATERIAU_SPECIFIQUE",
    "film t pu": "MATERIAU_SPECIFIQUE",
    "polyuréthane thermoplastique": "MATERIAU_SPECIFIQUE",
    "tpe": "MATERIAU_SPECIFIQUE",
    "élastomère thermoplastique": "MATERIAU_SPECIFIQUE",
    "lsr": "MATERIAU_SPECIFIQUE",
    "liquid silicone rubber": "MATERIAU_SPECIFIQUE",
    "silicone": "MATERIAU_SPECIFIQUE",
    "petg": "MATERIAU_SPECIFIQUE",
    "film petg/tpu": "MATERIAU_SPECIFIQUE",
    "film pu": "MATERIAU_SPECIFIQUE",
    "pu": "MATERIAU_SPECIFIQUE",
    "mousse": "MATERIAU_SPECIFIQUE",
    "mousses": "MATERIAU_SPECIFIQUE",
    "mousse découpée": "MATERIAU_SPECIFIQUE",
    "aluminium": "MATERIAU_SPECIFIQUE",
    "résine": "MATERIAU_SPECIFIQUE",
    "aluminium et résine": "MATERIAU_SPECIFIQUE",
    "gaz d’éthylène": "MATERIAU_SPECIFIQUE",
    "gaz d'ethylène": "MATERIAU_SPECIFIQUE",
    "gaz stérilisant": "MATERIAU_SPECIFIQUE",
}

SECTION_TITLES = {
    "résultats obtenus": "RESULTAT_RD",
    "démarche expérimentale": "METHODE_RD",
    "démarche expérimentale, travaux r&d réalisés": "METHODE_RD",
    "raisonnement scientifique, démarche expérimentale appliquée et description des travaux réalisés en 2024": "METHODE_RD",
    "recherche et définition de solutions techniques d’emballage pour les dispositifs médicaux fragiles et agressifs": "METHODE_RD",
    "recherche et définition de solutions techniques d'emballage pour les dispositifs médicaux fragiles et agressifs": "METHODE_RD",
    "développement d’un système d’emballage suspendu performant": "METHODE_RD",
    "développement d'un système d'emballage suspendu performant": "METHODE_RD",
    "verrous et incertitudes scientifiques, techniques, technologiques": "VERROU_TECH",
    "problèmes techniques à résoudre": "VERROU_TECH",
    "objectifs visés et performances à atteindre": "OBJECTIF_RD",
    "objectifs visés": "OBJECTIF_RD",
}

DATE_KEEP_PATTERNS = [
    r"^\d{4}$",
    r"^\d{1,2}/\d{1,2}/\d{2,4}$",
    r"^\d{4}-\d{4}$",
    r"^(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+\d{4}$",
]

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def lower_norm(text: str) -> str:
    return norm(text).lower()

def is_valid_date(text: str) -> bool:
    t = lower_norm(text)
    return any(re.match(p, t, flags=re.IGNORECASE) for p in DATE_KEEP_PATTERNS)

def is_placeholder_money(text: str) -> bool:
    t = lower_norm(text)
    return bool(re.fullmatch(r"0+[\s\u00a0]*0*[\s\u00a0]*0*\s*€", t)) or t in {"000 000 €", "000\u00a0000 €"}

def clean_entity(ent):
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

    # URLs / mails / téléphone
    if "@" in text or low.startswith("http") or low.startswith("www.") or low.endswith(".com") or low.endswith(".fr"):
        return None
    if re.search(r"\b\d{2}\s\d{2}\s\d{2}\s\d{2}\s\d{2}\b", text):
        return None

    # Montants placeholders
    if label == "MONTANT_CIR":
        if low == "coût total" or is_placeholder_money(text):
            return None

    # Faux personnes
    if label == "PERSONNE" and low in {
        "nous", "personnel soignant", "infirmière", "infirmière de bloc",
        "chirurgien", "personnes", "collaborateurs", "client", "nom", "prénom"
    }:
        return None

    # Faux organismes / techno
    if label in {"ORGANISME", "TECHNOLOGIE_RD"} and low in {"nous", "tcp r&i"}:
        ent["text"] = text
        ent["label"] = "ORGANISME"
        ent["status"] = "force_cleaned"
        return ent

    if label == "ORGANISME" and low in {"ministère de la défense", "b7c12"}:
        return None

    # Faux lieux
    if label == "LIEU" and low in {"interne", "plan de travail", "bloc opératoire", "salon de xxxxxx"}:
        return None

    # Dates
    if label == "DATE_PERIODE" and not is_valid_date(text):
        return None

    # Trop générique par label
    if label == "COMPOSANT_TECHNIQUE" and low in {"système", "ergonomie", "concept"}:
        return None

    if label == "MATERIAU_SPECIFIQUE" and low in {
        "matériaux", "matériau", "solution technique",
        "sécurisation du dispositif médical", "recyclabilité des matériaux"
    }:
        return None

    if label == "RESULTAT_RD" and low in {"résultats", "résultat", "résultats obtenus", "résultats de nos travaux"}:
        return None

    if label == "OBJECTIF_RD" and low in {"objectif", "objectifs", "objectifs ambitieux", "objectifs visés", "objectifs de r&d"}:
        return None

    # Correction directe
    if low in AUTO_FIX:
        ent["text"] = text
        ent["label"] = AUTO_FIX[low]
        ent["status"] = "force_cleaned"
        return ent

    # Patterns utiles
    if "tcp r&i" in low:
        ent["label"] = "ORGANISME"
        ent["status"] = "force_cleaned"

    if "impression 3" in low:
        ent["label"] = "METHODE_RD"
        ent["status"] = "force_cleaned"

    if "thermoformage" in low:
        ent["label"] = "METHODE_RD"
        ent["status"] = "force_cleaned"

    if "tpu" in low or "petg" in low or "mousse" in low:
        ent["label"] = "MATERIAU_SPECIFIQUE"
        ent["status"] = "force_cleaned"

    if "emballage" in low and label in {"EQUIPEMENT_RD", "TECHNOLOGIE_RD", "MATERIAU_SPECIFIQUE"}:
        ent["label"] = "COMPOSANT_TECHNIQUE"
        ent["status"] = "force_cleaned"

    if "verrou" in low:
        ent["label"] = "VERROU_TECH"
        ent["status"] = "force_cleaned"

    if low in {"eto", "gamma"}:
        ent["label"] = "METHODE_RD"
        ent["status"] = "force_cleaned"

    if len(text) <= 2 and low not in {"dm"}:
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
    new_entities = []

    for ent in item.get("entities", []):
        before += 1
        old_label = ent.get("label")
        old_text = ent.get("text")

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

print("✅ Force clean projet 9 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")
print(f"Output  : {OUTPUT}")