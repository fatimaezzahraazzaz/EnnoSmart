from pathlib import Path
import json
import re

INPUT = Path(r"C:\EnnoSmart\projects\projet_10_\annotations\ner_candidates.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_10_\annotations\ner_candidates_clean.json")

KEEP_SECTION_TITLES = True

REMOVE_EXACT = {
    # Bruit administratif / bon de livraison / footer
    "10 rue jean de la fontaine",
    "51210 montmirail",
    "78 avenue du 19 mars 1962",
    "78370 plaisir",
    "ennodev",
    "ennodev sas",
    "registre du commerce et des sociétés",
    "r.c.s versailles",
    "93 000 eur €",
    "a nous retourner signe",
    "bon de livraison",
    "mode n° du",
    "n° du bon",
    "cachet date + nom + signature",

    # Placeholders / montants faux
    "coût total",
    "000 000 €",
    "000\u00a0000 €",
    "[x]",
    "[oui / non]",
    "[nom(s) chef(s) projet]",
    "nom",
    "prénom",
    "fonction",
    "heures",

    # Bruit générique
    "nous",
    "notre",
    "nos",
    "objectif",
    "objectifs",
    "objectifs spécifiques recherchés",
    "résultat",
    "résultats",
    "les résultats",
    "résultats des études",
    "résultat final",
    "résultats très performants",
    "plateforme",
    "plateformes",
    "plateformes sources",
    "plateformes de médias sociaux",
    "médias sociaux",
    "réseaux sociaux",
    "blogs",
    "forums",
    "presse en ligne",
    "personnes",
    "personnes ordinaires",
    "utilisateurs",
    "utilisateur",
    "utilisateurs internautes",
    "individus",
    "chercheurs",
    "influenceur",
    "influenceurs",
    "partis politiques",

    # Dates vagues
    "période de trente jours",
    "périodes spécifiques",
    "période prolongée",
    "ces dernières années",

    # Domaines/thèmes politiques trop génériques comme entités R&D
    "économie",
    "immigration",
    "laïcité",
    "environnement",
    "sécurité",
    "santé",
    "web",
    "domaine de la santé",

    # Faux organismes / termes génériques
    "ministère de la défense",
    "domaine de politique en langue française",
    "domaine de la politique",
    "domaine politique",
    "domaine de l'écoute sociale",

    # Auteurs état de l'art à supprimer
    "stewart",
    "arnold",
    "crawford",
    "cole-lewis et al",
    "ballestar et al",
    "hou et al",
    "anderson et al",
    "new york",
    "londres",
    "mumbai",
    "sao paulo",
    "beijing",
    "sites d’avis consommateurs",
    "sites d'avis consommateurs",
}

AUTO_FIX = {
    # ORGANISMES
    "abinnov": "ORGANISME",
    "cluster17": "ORGANISME",
    "institut de sondage cluster17": "ORGANISME",
    "drari": "ORGANISME",
    "délégation régionale académique à la recherche et à l’innovation": "ORGANISME",
    "délégation régionale académique à la recherche et à l'innovation": "ORGANISME",
    "dga": "ORGANISME",
    "who": "ORGANISME",
    "oms": "ORGANISME",
    "organisation mondiale de la santé": "ORGANISME",
    "organisation mondiale de la sante": "ORGANISME",
    "twitter": "ORGANISME",
    "facebook": "ORGANISME",
    "youtube": "ORGANISME",
    "instagram": "ORGANISME",
    "sina weibo": "ORGANISME",
    "bluelight": "ORGANISME",
    "opiophile": "ORGANISME",

    # PERSONNES utiles dossier
    "mr aymen sahnoun": "PERSONNE",
    "aymen sahnoun": "PERSONNE",

    # DOMAINES
    "intelligence artificielle": "DOMAINE_RD",
    "intelligence artificielle : apprentissage": "DOMAINE_RD",
    "intelligence artificielle\xa0: apprentissage": "DOMAINE_RD",
    "traitement automatique des langues": "DOMAINE_RD",
    "traitement automatique des langues et de la parole": "DOMAINE_RD",
    "intelligence artificielle : traitement automatique des langues et de la parole": "DOMAINE_RD",
    "intelligence artificielle\xa0: traitement automatique des langues et de la parole": "DOMAINE_RD",
    "nlp": "DOMAINE_RD",
    "traitement du langage naturel": "DOMAINE_RD",
    "natural language processing": "DOMAINE_RD",
    "social listening": "DOMAINE_RD",
    "écoute sociale": "DOMAINE_RD",
    "ecoute sociale": "DOMAINE_RD",
    "écoute social": "DOMAINE_RD",
    "ecoute social": "DOMAINE_RD",
    "domaine politique": "DOMAINE_RD",
    "domaine de la politique": "DOMAINE_RD",
    "domaine de politique en langue française": "DOMAINE_RD",

    # TECHNOLOGIES / MODELES
    "batrend": "TECHNOLOGIE_RD",
    "abitheme model": "TECHNOLOGIE_RD",
    "abithememodel": "TECHNOLOGIE_RD",
    "distilcamembert": "TECHNOLOGIE_RD",
    "mdeberta": "TECHNOLOGIE_RD",
    "bart-large": "TECHNOLOGIE_RD",
    "camembert": "TECHNOLOGIE_RD",
    "labse": "TECHNOLOGIE_RD",
    "bert": "TECHNOLOGIE_RD",
    "roberta": "TECHNOLOGIE_RD",
    "oscar": "TECHNOLOGIE_RD",
    "multinli": "TECHNOLOGIE_RD",
    "mnli": "TECHNOLOGIE_RD",
    "nli": "TECHNOLOGIE_RD",
    "glove": "TECHNOLOGIE_RD",
    "fasttext": "TECHNOLOGIE_RD",
    "tf-idf": "TECHNOLOGIE_RD",
    "tokenizer de keras": "TECHNOLOGIE_RD",
    "tokenizer pré-entraîné": "TECHNOLOGIE_RD",
    "tokenizer préentraîné": "TECHNOLOGIE_RD",
    "xgboost": "TECHNOLOGIE_RD",
    "knn": "TECHNOLOGIE_RD",
    "svm": "TECHNOLOGIE_RD",
    "support vector machines": "TECHNOLOGIE_RD",
    "classificateur bayes naïf multinomial": "TECHNOLOGIE_RD",
    "classificateur bayesien naïf multinomial": "TECHNOLOGIE_RD",
    "bayes naïf multinomial": "TECHNOLOGIE_RD",
    "nb": "TECHNOLOGIE_RD",
    "arbre de décision": "TECHNOLOGIE_RD",
    "forêts aléatoires": "TECHNOLOGIE_RD",
    "rf": "TECHNOLOGIE_RD",
    "dt": "TECHNOLOGIE_RD",
    "optuna": "TECHNOLOGIE_RD",
    "gridsearchcv": "TECHNOLOGIE_RD",
    "scikit-learn": "TECHNOLOGIE_RD",
    "keras": "TECHNOLOGIE_RD",
    "ocr": "TECHNOLOGIE_RD",
    "scraping": "TECHNOLOGIE_RD",
    "grattage web": "TECHNOLOGIE_RD",
    "api": "TECHNOLOGIE_RD",
    "apis": "TECHNOLOGIE_RD",
    "captcha": "TECHNOLOGIE_RD",

    # METHODES
    "démarche expérimentale": "METHODE_RD",
    "raisonnement scientifique et démarche expérimentale appliquée": "METHODE_RD",
    "apprentissage automatique": "METHODE_RD",
    "apprentissage supervisé": "METHODE_RD",
    "apprentissage non supervisé": "METHODE_RD",
    "classification automatique": "METHODE_RD",
    "surveillance automatique sur le web et les réseaux sociaux": "METHODE_RD",
    "surveillance automatique": "METHODE_RD",
    "surveillance en temps réel": "METHODE_RD",
    "collecte automatique des données": "METHODE_RD",
    "analyse quantitative": "METHODE_RD",
    "analyse qualitative": "METHODE_RD",
    "analyse contextuelle": "METHODE_RD",
    "analyse des données": "METHODE_RD",
    "analyse de données avancés": "METHODE_RD",
    "traitement des données": "METHODE_RD",
    "méthodes de transformation des données": "METHODE_RD",
    "réglage d’hyperparamètres": "METHODE_RD",
    "réglage d'hyperparamètres": "METHODE_RD",
    "fine tuning": "METHODE_RD",
    "similarité cosinus": "METHODE_RD",
    "représentations vectorielles de mots": "METHODE_RD",
    "word embeddings": "METHODE_RD",
    "incorporation des mots": "METHODE_RD",

    # VERROUS
    "verrou": "VERROU_TECH",
    "verrous majeurs": "VERROU_TECH",
    "complexité liée à la surveillance en temps réel sur plusieurs millions de sources": "VERROU_TECH",
    "complexité liée à l’analyse des réactions et des opinions des gens dans un domaine spécifique": "VERROU_TECH",
    "détermination en temps réel des influenceurs des opinions partagés sur le web et les réseaux sociaux": "VERROU_TECH",
    "détermination des influenceurs en temps réel": "VERROU_TECH",
    "surveillance des données en temps réel": "VERROU_TECH",
    "surveillance des données sur des plateformes hétérogènes": "VERROU_TECH",
    "analyse des réactions et des opinions des gens quels que soient les domaines visés": "VERROU_TECH",
    "latence minimale": "VERROU_TECH",
    "diversité des plateformes": "VERROU_TECH",
    "formats de données différents": "VERROU_TECH",
    "variation linguistique": "VERROU_TECH",
    "limitation linguistique": "VERROU_TECH",

    # RESULTATS / OBJECTIFS
    "score f1": "RESULTAT_RD",
    "f-score": "RESULTAT_RD",
    "exactitude": "RESULTAT_RD",
    "accuracy": "RESULTAT_RD",
    "exactitude de 91 %": "RESULTAT_RD",
    "f-score de 92 %": "RESULTAT_RD",
    "objectif de performance": "OBJECTIF_RD",
    "performances visées": "OBJECTIF_RD",
    "performance de 90 %": "OBJECTIF_RD",
}

SECTION_TITLES = {
    "démarche expérimentale, travaux r&d réalisés": "METHODE_RD",
    "raisonnement scientifique et démarche expérimentale appliquée": "METHODE_RD",
    "travaux antérieurs": "METHODE_RD",
    "description des travaux": "METHODE_RD",
    "phase exploratoire : evaluation des modèles de traitement de langage existants": "METHODE_RD",
    "phase exploratoire : évaluation des modèles de traitement de langage existants": "METHODE_RD",
    "première itération : évaluation des modèles de traitement de langage existants": "METHODE_RD",
    "première approche : évaluation des représentations vectorielles de mots en français": "METHODE_RD",
    "deuxième itération : approche basée sur l’évaluation des représentations vectorielles de mots en français": "METHODE_RD",
    "deuxième approche : mise en œuvre d’une méthodologie basée sur l’apprentissage supervisé": "METHODE_RD",
    "troisième itération : mise en œuvre d’une méthodologie basée sur l’apprentissage supervisé": "METHODE_RD",
    "troisième approche : mis en œuvre d’un nouveau modèle du traitement du langage naturel.": "METHODE_RD",
    "quatrième itération : mis en œuvre d’un nouveau modèle du traitement du langage naturel.": "METHODE_RD",
    "verrous et incertitudes scientifiques, techniques, technologiques": "VERROU_TECH",
    "complexité liée à la surveillance en temps réel sur plusieurs millions de sources": "VERROU_TECH",
    "complexité liée à l’analyse des réactions et des opinions des gens dans un domaine spécifique": "VERROU_TECH",
    "détermination en temps réel des influenceurs des opinions partagés sur le web et les réseaux sociaux": "VERROU_TECH",
    "détermination des influenceurs en temps réel": "VERROU_TECH",
    "objectifs visés": "OBJECTIF_RD",
    "performances à atteindre": "OBJECTIF_RD",
    "conclusion et contribution scientifique, technique ou technologique": "RESULTAT_RD",
}

DATE_KEEP_PATTERNS = [
    r"^\d{4}$",
    r"^\d{1,2}/\d{1,2}/\d{2,4}$",
    r"^\d{4}-\d{4}$",
    r"^(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+\d{4}$",
    r"^\d{1,2}\s+(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+\d{4}$",
    r"^(mai|juin|juillet|avril)\s+\d{4}$",
    r"^\d{1,2}\s+novembre\s+au\s+\d{1,2}\s+décembre\s+\d{4}$",
    r"^juin et juillet\s+\d{4}$",
]

REFERENCE_CHUNK_MARKERS = [
    "cole-lewis et al",
    "ballestar et al",
    "hou et al",
    "anderson et al",
    "stewart et arnold",
    "crawford",
    "dans la littérature",
    "état de l’art externe",
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
    return t in {"000 000 €", "coût total"} or bool(re.fullmatch(r"0+\s*0*\s*0*\s*€", t))

def is_reference_chunk(text: str) -> bool:
    low = lower_norm(text)
    return any(marker in low for marker in REFERENCE_CHUNK_MARKERS)

def clean_entity(ent, ref_chunk=False):
    text = norm(ent.get("text", ""))
    label = ent.get("label", "")
    low = lower_norm(text)

    if not text:
        return None

    if low in SECTION_TITLES:
        if KEEP_SECTION_TITLES:
            ent["text"] = text
            ent["label"] = SECTION_TITLES[low]
            ent["status"] = "force_cleaned"
            return ent
        return None

    if low in REMOVE_EXACT:
        return None

    # URLs / emails / IDs admin
    if "@" in text or low.startswith("http") or low.startswith("www.") or low.endswith(".com") or low.endswith(".fr"):
        return None
    if "siret" in low or "tva" in low or "r.c.s" in low or "rcs" in low or "naf" in low:
        return None

    # Montants placeholders / capital social non CIR
    if label == "MONTANT_CIR":
        if is_placeholder_money(text):
            return None
        if "93 000" in low:
            return None

    # Dates
    if label == "DATE_PERIODE" and not is_valid_date(text):
        return None

    # Dans état de l'art, supprimer auteurs comme PERSONNE
    if ref_chunk and label == "PERSONNE":
        return None

    # Faux personnes
    if label == "PERSONNE" and low in {
        "personnes", "personnes ordinaires", "utilisateur", "utilisateurs",
        "utilisateurs internautes", "individus", "chercheurs",
        "influenceur", "influenceurs"
    }:
        return None

    # Faux organismes génériques
    if label == "ORGANISME" and low in {
        "médias sociaux", "plateformes", "plateforme", "plateformes de médias sociaux",
        "réseaux sociaux", "blogs", "forums", "presse en ligne",
        "plateformes sources", "partis politiques", "registre du commerce et des sociétés"
    }:
        return None

    # Faux lieux admin ou état de l'art non utiles
    if label == "LIEU":
        if low in {"grand est"}:
            # Pas grave mais peu utile au NER R&D
            return None
        if re.search(r"\b(rue|avenue|montmirail|plaisir)\b", low):
            return None

    # Génériques résultats/objectifs
    if label == "RESULTAT_RD" and low in {
        "résultat", "résultats", "les résultats", "résultat final",
        "résultats des études", "résultats très performants"
    }:
        return None

    if label == "OBJECTIF_RD" and low in {
        "objectif", "objectifs", "objectifs spécifiques recherchés"
    }:
        return None

    # Correction directe
    if low in AUTO_FIX:
        ent["text"] = text
        ent["label"] = AUTO_FIX[low]
        ent["status"] = "force_cleaned"
        return ent

    # Patterns technologies NLP
    tech_patterns = [
        r"\bDistilCamemBERT\b",
        r"\bmDeBERTa\b",
        r"\bbart-large\b",
        r"\bCamemBERT\b",
        r"\bLaBSE\b",
        r"\bABiThemeModel\b",
        r"\bTF-IDF\b",
        r"\bXGBoost\b",
        r"\bKNN\b",
        r"\bSVM\b",
        r"\bOptuna\b",
        r"\bGridSearchCV\b",
        r"\bNLP\b",
        r"\bOCR\b",
    ]
    if any(re.search(p, text, flags=re.IGNORECASE) for p in tech_patterns):
        ent["label"] = "TECHNOLOGIE_RD" if low != "nlp" else "DOMAINE_RD"
        ent["status"] = "force_cleaned"

    if "batrend" in low:
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if "écoute social" in low or "ecoute social" in low or "social listening" in low:
        ent["label"] = "DOMAINE_RD"
        ent["status"] = "force_cleaned"

    if "classification automatique" in low or "surveillance automatique" in low:
        ent["label"] = "METHODE_RD"
        ent["status"] = "force_cleaned"

    if "apprentissage" in low or "machine learning" in low:
        ent["label"] = "METHODE_RD"
        ent["status"] = "force_cleaned"

    if "verrou" in low or "complexité liée" in low:
        ent["label"] = "VERROU_TECH"
        ent["status"] = "force_cleaned"

    if len(text) <= 2 and low not in {"ia", "nb", "rf", "dt"}:
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

print("✅ Force clean projet 10 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")
print(f"Output  : {OUTPUT}")