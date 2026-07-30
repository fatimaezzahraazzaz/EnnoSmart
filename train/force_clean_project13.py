from pathlib import Path
import json
import re

INPUT = Path(r"C:\EnnoSmart\projects\projet_13_\annotations\ner_candidates.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_13_\annotations\ner_candidates_clean.json")

KEEP_SECTION_TITLES = True

REMOVE_EXACT = {
    # Bruit administratif / template
    "société",
    "administration fiscale",
    "thésaurus",
    "nom du projet",
    "mot clé 1",
    "mot clé 2",
    "mot clé 3",
    "etc",
    "coût total",
    "000 000 €",
    "000\u00a0000 €",
    "année n",
    "personnel",
    "personnels et jeunes docteurs",
    "industriels",
    "laboratoires publics de recherche",

    # Faux personnes / génériques
    "utilisateur",
    "usager final",
    "opérateurs",
    "auteurs",
    "les auteurs",

    # Auteurs état de l'art
    "c. ratté-fortin",
    "c. ratté-fortin8",
    "o. valeria",
    "b. d. harvey",
    "b. d. harvey9",
    "a. el-alem",
    "a. el-alem et al",
    "m. a. syariz",
    "busari et al",
    "lin et al",
    "qian et al",
    "r. beck",
    "i. yim",
    "i. yim et al",
    "j. pyo",
    "j. c. pyo",
    "j. c. pyo et al",
    "bosse et al",
    "k. r. bosse",
    "k. r. bosse et al",
    "d. c. rolland et al",
    "rolland et al",
    "s. kim et al",
    "w. song",
    "cao et al",
    "yan et al",
    "jeon et al",
    "ai et al",
    "jeong et al",
    "schaeffer et al",
    "soriano-gonzález et al",
    "soriano-gonzalez et al",
    "brockmann et al",

    # Lieux génériques pas utiles pour NER R&D
    "lac",
    "lacs",
    "grands lacs",
    "lacs et réservoirs",
    "rivière",
    "terre",
    "centre",
    "zone géographique",
    "9 zones climatiques",

    # Dates vagues ou exemples non utiles
    "aujourd’hui",
    "aujourd'hui",
    "jour-même",
    "date",
    "date cible",
    "3 jours",
    "week-ends",
    "deux ans",
    "6 novembre",
    "7 novembre",
    "8 novembre",

    # Génériques trop faibles
    "objectif",
    "objectif de prédiction",
    "objectifs",
    "résultat",
    "résultats",
    "les résultats",
    "résultats obtenus",
    "crop",

    # Faux labels / placeholders
    "200 et 2019",
    "domaine",
    "esn",
    "objectifs de r&d",

    "lacs du québec méridional",
    "lacs du sud du québec",
    "lacs d'abitibi",
    "témiscamingue",
    "malartic",
    "chine",
    "lac erie eutrophique",
    "lac saint-charles",
    "lac st-charles",
    "upper laguna madre",
    "corpus christi",
    "texas",
    "lac tai",
    "corée du sud",
    "états-unis",
    "québec",
    "alberta",
    "canada",
}

AUTO_FIX = {
    "agence spatiale européenne": "ORGANISME",
    "european spatial agency": "ORGANISME",
    "european space agency": "ORGANISME",
        # ORGANISMES
    "scalian": "ORGANISME",
    "watershed monitoring": "ORGANISME",
    "watershed monitoring europe": "ORGANISME",
    "waterShed Monitoring Europe": "ORGANISME",
    "oms": "ORGANISME",
    "who": "ORGANISME",
    "esa": "ORGANISME",
    "nasa": "ORGANISME",
    "usgs": "ORGANISME",
    "international satellite cloud climatology project": "ORGANISME",

    # PROJETS / SOLUTIONS
    "beps-ia": "TECHNOLOGIE_RD",
    "nerthus": "TECHNOLOGIE_RD",
    "water quality": "DOMAINE_RD",

    # DOMAINES
    "qualité de l’eau": "DOMAINE_RD",
    "qualité de l'eau": "DOMAINE_RD",
    "télédétection": "DOMAINE_RD",
    "teledetection": "DOMAINE_RD",
    "imagerie satellitaire": "DOMAINE_RD",
    "imagerie satellitaire multispectrale": "DOMAINE_RD",
    "images satellite": "DOMAINE_RD",
    "images satellites": "DOMAINE_RD",
    "machine learning": "DOMAINE_RD",
    "deep learning": "DOMAINE_RD",
    "apprentissage automatique": "DOMAINE_RD",
    "apprentissage profond": "DOMAINE_RD",
    "génie des matériaux": "DOMAINE_RD",
    "limnologie": "DOMAINE_RD",
    "météorologie": "DOMAINE_RD",
    "cyanobactéries": "DOMAINE_RD",
    "efflorescence de cyanobactéries": "DOMAINE_RD",
    "efflorescences de cyanobactéries": "DOMAINE_RD",
    "blooms algaux": "DOMAINE_RD",
    "algal blooms": "DOMAINE_RD",
    "harmful algal blooms": "DOMAINE_RD",

    # TECHNOLOGIES / OUTILS / MODELES
    "cnn": "TECHNOLOGIE_RD",
    "réseau neuronal convolutif": "TECHNOLOGIE_RD",
    "réseau neuronal convolutif (cnn)": "TECHNOLOGIE_RD",
    "réseaux neuronaux convolutifs": "TECHNOLOGIE_RD",
    "réseaux neuronaux profonds": "TECHNOLOGIE_RD",
    "lstm": "TECHNOLOGIE_RD",
    "cnn/lstm": "TECHNOLOGIE_RD",
    "cnn-lstm": "TECHNOLOGIE_RD",
    "habnet": "TECHNOLOGIE_RD",
    "nasnet-mobile": "TECHNOLOGIE_RD",
    "waternet": "TECHNOLOGIE_RD",
    "autoencoder": "TECHNOLOGIE_RD",
    "auto-encodeurs": "TECHNOLOGIE_RD",
    "xgboost": "TECHNOLOGIE_RD",
    "xgb": "TECHNOLOGIE_RD",
    "extratree": "TECHNOLOGIE_RD",
    "gradient boost": "TECHNOLOGIE_RD",
    "gradient boosting": "TECHNOLOGIE_RD",
    "extreme gradient boosting": "TECHNOLOGIE_RD",
    "support vector": "TECHNOLOGIE_RD",
    "multiple linear regression": "TECHNOLOGIE_RD",
    "forêt aléatoire": "TECHNOLOGIE_RD",
    "forêts aléatoires": "TECHNOLOGIE_RD",
    "random forest": "TECHNOLOGIE_RD",
    "rf": "TECHNOLOGIE_RD",
    "shap": "TECHNOLOGIE_RD",
    "treeshap": "TECHNOLOGIE_RD",
    "smote": "TECHNOLOGIE_RD",
    "pca": "METHODE_RD",
    "pacf": "METHODE_RD",
    "partial autocorrelation function": "METHODE_RD",
    "inla": "TECHNOLOGIE_RD",
    "open-meteo": "TECHNOLOGIE_RD",
    "api open-meteo": "TECHNOLOGIE_RD",
    "era5": "TECHNOLOGIE_RD",
    "climate data store": "TECHNOLOGIE_RD",
    "snap": "TECHNOLOGIE_RD",
    "snap 10.0": "TECHNOLOGIE_RD",
    "snap 8.0": "TECHNOLOGIE_RD",
    "gpt": "TECHNOLOGIE_RD",
    "snapista": "TECHNOLOGIE_RD",
    "rasterio": "TECHNOLOGIE_RD",
    "geotiff": "TECHNOLOGIE_RD",
    "geodataframe": "TECHNOLOGIE_RD",
    "c2rcc": "TECHNOLOGIE_RD",
    "qa_pixel": "TECHNOLOGIE_RD",
    "scan line corrector": "TECHNOLOGIE_RD",
    "slc": "TECHNOLOGIE_RD",
    "global ocean turbulence model": "TECHNOLOGIE_RD",
    "cyanobacterial index": "TECHNOLOGIE_RD",
    "floating algae index": "TECHNOLOGIE_RD",
    "fai": "TECHNOLOGIE_RD",
    "risk_accuracy": "RESULTAT_RD",
    "certainty": "RESULTAT_RD",
    "r2": "RESULTAT_RD",
    "rmse": "RESULTAT_RD",
    "nse": "RESULTAT_RD",
    "f1-score": "RESULTAT_RD",
    "false omission rate": "RESULTAT_RD",
    "accuracy": "RESULTAT_RD",
    "kappa": "RESULTAT_RD",

    # SATELLITES / CAPTEURS
    "sentinel-2": "EQUIPEMENT_RD",
    "sentinel -2": "EQUIPEMENT_RD",
    "sentinel-2a": "EQUIPEMENT_RD",
    "sentinel-3": "EQUIPEMENT_RD",
    "sentinel-3/meris/olci": "EQUIPEMENT_RD",
    "landsat 7": "EQUIPEMENT_RD",
    "landsat 8": "EQUIPEMENT_RD",
    "landsat 9": "EQUIPEMENT_RD",
    "landsat 7/8/9": "EQUIPEMENT_RD",
    "landsat 8/9": "EQUIPEMENT_RD",
    "landsat-8": "EQUIPEMENT_RD",
    "modis": "EQUIPEMENT_RD",
    "moderate resolution imaging spectrometer": "EQUIPEMENT_RD",
    "satellite sentinel-3": "EQUIPEMENT_RD",
    "capteur modis": "EQUIPEMENT_RD",

    # VARIABLES / MATERIAUX / INDICATEURS
    "chlorophylle-a": "MATERIAU_SPECIFIQUE",
    "chl-a": "MATERIAU_SPECIFIQUE",
    "phycocyanine": "MATERIAU_SPECIFIQUE",
    "la phycocyanine": "MATERIAU_SPECIFIQUE",
    "cyanobactéries": "MATERIAU_SPECIFIQUE",
    "microcystis": "MATERIAU_SPECIFIQUE",
    "anabaena": "MATERIAU_SPECIFIQUE",
    "matières organiques en suspension": "MATERIAU_SPECIFIQUE",
    "total suspended matter": "MATERIAU_SPECIFIQUE",
    "tsm": "MATERIAU_SPECIFIQUE",
    "kd_z90max": "MATERIAU_SPECIFIQUE",
    "sédiments calcaires": "MATERIAU_SPECIFIQUE",
    "phosphore total": "MATERIAU_SPECIFIQUE",
    "azote total": "MATERIAU_SPECIFIQUE",
    "oxygène dissous": "MATERIAU_SPECIFIQUE",
    "ph": "MATERIAU_SPECIFIQUE",
    "nox": "MATERIAU_SPECIFIQUE",

    # METHODES
    "démarche expérimentale": "METHODE_RD",
    "classification": "METHODE_RD",
    "régression": "METHODE_RD",
    "classification binaire": "METHODE_RD",
    "classification à quatre niveaux de risque": "METHODE_RD",
    "prédiction": "METHODE_RD",
    "prédiction des efflorescences": "METHODE_RD",
    "prédiction du risque d’efflorescence": "METHODE_RD",
    "prédiction du risque d'efflorescence": "METHODE_RD",
    "inpainting": "METHODE_RD",
    "correction atmosphérique": "METHODE_RD",
    "rééchantillonnage": "METHODE_RD",
    "resampling": "METHODE_RD",
    "interpolation bilinéaire": "METHODE_RD",
    "interpolation linéaire": "METHODE_RD",
    "croisement géospatial": "METHODE_RD",
    "crop": "METHODE_RD",
    "suppression des images": "METHODE_RD",
    "réduction de dimension": "METHODE_RD",
    "apprentissage par transfert": "METHODE_RD",
    "monte carlo dropout scheme": "METHODE_RD",
    "méthode fai": "METHODE_RD",
    "floating algae index": "METHODE_RD",
    "échantillonnage in situ": "METHODE_RD",
    "prélèvements in-situ": "METHODE_RD",
    "prélèvements in situ": "METHODE_RD",
    "microscopie inverse": "METHODE_RD",
    "modèle stochastique": "METHODE_RD",
    "modèle de fréquence régional non-stationnaire": "METHODE_RD",
    "simulation des prévisions météorologiques": "METHODE_RD",

    # VERROUS
    "verrou": "VERROU_TECH",
    "verrou 1": "VERROU_TECH",
    "verrou 2": "VERROU_TECH",
    "verrou 3": "VERROU_TECH",
    "verrou 4": "VERROU_TECH",
    "verrou 5": "VERROU_TECH",
    "généricité de la solution de prédiction": "VERROU_TECH",
    "conditions météorologiques et atmosphériques": "VERROU_TECH",
    "problème de dimensionnalité": "VERROU_TECH",
    "dimensionnalité": "VERROU_TECH",
    "forte augmentation de la dimension du problème": "VERROU_TECH",
    "non-utilisation de données d’échantillonnage": "VERROU_TECH",
    "non-utilisation de données d'echantillonnage": "VERROU_TECH",
    "définition de plusieurs niveaux de risque": "VERROU_TECH",
    "couverture nuageuse": "VERROU_TECH",
    "surapprentissage": "VERROU_TECH",
    "déséquilibre des données": "VERROU_TECH",
    "fausses alarmes": "VERROU_TECH",
    "taux de fausse alarme": "VERROU_TECH",
    "faible taux de fausses alarmes": "VERROU_TECH",
    "données limnologiques difficiles à obtenir": "VERROU_TECH",
    "absence de données in-situ": "VERROU_TECH",
    "faible quantité de données": "VERROU_TECH",
}

SECTION_TITLES = {
    "démarche expérimentale, travaux r&d réalisés": "METHODE_RD",
    "raisonnement scientifique et démarche expérimentale appliquée": "METHODE_RD",
    "résultats obtenus": "RESULTAT_RD",
    "verrous scientifiques, techniques, technologiques": "VERROU_TECH",
    "verrous à lever": "VERROU_TECH",
    "verrou 1 : la généricité de la solution de prédiction": "VERROU_TECH",
    "verrou 2 : conditions météorologiques et atmosphériques": "VERROU_TECH",
    "verrou 3 : données en entrée et problème de dimensionnalité": "VERROU_TECH",
    "verrou 4 : non-utilisation de données d’échantillonnage": "VERROU_TECH",
    "verrou 4 : non-utilisation de données d'echantillonnage": "VERROU_TECH",
    "verrou 5 : définition de plusieurs niveaux de risque": "VERROU_TECH",
    "état de l’art externe": "METHODE_RD",
    "etat de l’art externe": "METHODE_RD",
    "état de l’art interne": "METHODE_RD",
    "insuffisance des solutions existantes": "VERROU_TECH",
    "traitement des images satellite et extraction des informations": "METHODE_RD",
    "calcul du pourcentage de pixels « réellement » exploitables": "METHODE_RD",
    "données météorologiques": "METHODE_RD",
    "données limnologiques": "METHODE_RD",
    "simulation des prévisions météorologiques": "METHODE_RD",
    "sorties du modèles": "RESULTAT_RD",
    "tâches": "METHODE_RD",
}

DATE_KEEP_PATTERNS = [
    r"^\d{4}$",
    r"^année\s+\d{4}$",
    r"^ann[ée]e\s+\d{4}$",
    r"^(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+\d{4}$",
    r"^\d{1,2}\s+(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+\d{4}$",
    r"^été\s+\d{4}$",
    r"^février\s+\d{4}$",
]

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def lower_norm(text: str) -> str:
    return norm(text).lower()

def is_valid_date(text: str) -> bool:
    t = lower_norm(text)
    return any(re.match(p, t, flags=re.IGNORECASE) for p in DATE_KEEP_PATTERNS)

def is_placeholder_money(text: str) -> bool:
    t = lower_norm(text).replace("\u00a0", " ")
    return t in {"000 000 €", "coût total"} or bool(re.fullmatch(r"0+\s*0*\s*0*\s*€.*", t))

def clean_entity(ent):
    text = norm(ent.get("text", ""))
    label = ent.get("label", "")
    low = lower_norm(text)

    if not text:
        return None

    # titres de section utiles
    if low in SECTION_TITLES:
        if KEEP_SECTION_TITLES:
            ent["text"] = text
            ent["label"] = SECTION_TITLES[low]
            ent["status"] = "force_cleaned"
            return ent
        return None

    # suppression directe
    if low in REMOVE_EXACT:
        return None

    # URLs / emails
    if "@" in text or low.startswith("http") or low.startswith("www.") or low.endswith(".com") or low.endswith(".fr"):
        return None

    # montants placeholders
    if label == "MONTANT_CIR":
        if is_placeholder_money(text):
            return None

    # dates
    if label == "DATE_PERIODE":
        if not is_valid_date(text):
            return None
        ent["text"] = text
        return ent

    # auteurs état de l'art
    if label == "PERSONNE":
        if low.endswith("et al") or low.endswith("et al.") or " et al" in low:
            return None
        if low in {"auteurs", "les auteurs", "utilisateur", "usager final", "opérateurs", "personnel"}:
            return None
        if re.match(r"^[a-z]\.\s?[a-z]?\.\s?[a-z\-]+", low):
            return None

    # faux lieux génériques
    if label == "LIEU":
        if low in {
            "lac", "lacs", "grands lacs", "rivière", "terre", "centre",
            "zone géographique", "lacs et réservoirs", "9 zones climatiques"
        }:
            return None

    # résultats/objectifs trop génériques
    if label == "RESULTAT_RD" and low in {"résultat", "résultats", "les résultats", "résultats obtenus", "crop"}:
        return None

    if label == "OBJECTIF_RD" and low in {"objectif", "objectifs", "objectif de prédiction"}:
        return None

    # correction directe
    if low in AUTO_FIX:
        ent["text"] = text
        ent["label"] = AUTO_FIX[low]
        ent["status"] = "force_cleaned"
        return ent

    # patterns auteurs
    if " et al" in low and label == "PERSONNE":
        return None

    # patterns techno
    if re.search(r"\b(cnn|lstm|xgboost|xgb|shap|treeshap|smote|modis|c2rcc|snap|snapista|rasterio|geotiff|qa_pixel)\b", low):
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if re.search(r"\b(sentinel|landsat|modis)\b", low):
        ent["label"] = "EQUIPEMENT_RD"
        ent["status"] = "force_cleaned"

    if "chlorophylle" in low or "phycocyanine" in low or "tsm" in low or "kd_z90max" in low:
        ent["label"] = "MATERIAU_SPECIFIQUE"
        ent["status"] = "force_cleaned"

    if "cyanobact" in low:
        if label in {"ORGANISME", "MATERIAU_SPECIFIQUE"}:
            ent["label"] = "MATERIAU_SPECIFIQUE"
        elif label in {"DOMAINE_RD", "VERROU_TECH", "METHODE_RD"}:
            ent["label"] = label
        else:
            ent["label"] = "DOMAINE_RD"
        ent["status"] = "force_cleaned"

    if "télédétection" in low or "teledetection" in low or "imagerie satellitaire" in low:
        ent["label"] = "DOMAINE_RD"
        ent["status"] = "force_cleaned"

    if "classification" in low or "régression" in low or "inpainting" in low or "correction atmosphérique" in low:
        ent["label"] = "METHODE_RD"
        ent["status"] = "force_cleaned"

    if "verrou" in low or "dimensionnalité" in low or "couverture nuageuse" in low or "surapprentissage" in low:
        ent["label"] = "VERROU_TECH"
        ent["status"] = "force_cleaned"

    if "fausse alarme" in low or "fausses alarmes" in low:
        ent["label"] = "VERROU_TECH"
        ent["status"] = "force_cleaned"

    # cas fréquents mal labelisés
    if label == "ORGANISME" and low in {"cnn", "c2rcc", "beps-ia"}:
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if label == "LIEU" and low in {"autoencoder"}:
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if label == "LIEU" and low == "télédétection":
        ent["label"] = "DOMAINE_RD"
        ent["status"] = "force_cleaned"

    if label == "VERROU_TECH" and low == "machine learning":
        ent["label"] = "DOMAINE_RD"
        ent["status"] = "force_cleaned"

    if len(text) <= 2 and low not in {"ph", "rf"}:
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

print("✅ Force clean projet 13 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")
print(f"Output  : {OUTPUT}")