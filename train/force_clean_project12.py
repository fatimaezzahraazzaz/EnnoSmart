from pathlib import Path
import json
import re

INPUT = Path(r"C:\EnnoSmart\projects\projet_12_\annotations\ner_candidates.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_12_\annotations\ner_candidates_clean.json")

KEEP_SECTION_TITLES = True

REMOVE_EXACT = {
    # Bruit générique / tableau
    "nous",
    "notre",
    "nos",
    "société",
    "thésaurus",
    "nom",
    "prénom",
    "fonction",
    "heures",
    "chef de projet",
    "chercheur",
    "scientific officer",
    "personnel",
    "personnels et jeunes docteurs",
    "industriels",
    "laboratoires publics de recherche",
    "administration fiscale",

    # Faux personnes
    "agent",
    "concepteur",
    "utilisateur",
    "personnes",

    # Auteurs état de l'art
    "gaudet et al",
    "oestreich et al",
    "yang et al",
    "cocaul et al",
    "perrin et al",
    "perin et al",

    # Faux lieux / contextes
    "position cible",
    "checkpoint",
    "sol",
    "position initiale",
    "monde réel",
    "environnement",
    "environnement sans vent",
    "domaine de simulation",
    "mars",

    # Faux montants / unités mal détectées
    "10 mètres",
    "10m",
    "bonus",
    "malus",
    "0.05",
    "0,05",
    "9.6 nm",
    "9,6 nm",
    "192 nm",
    "100 kg",
    "000 000 €",
    "000\u00a0000 €",
    "coût total",

    # Dates non utiles comme dates
    "19 epochs",
    "24 epochs",
    "29 epochs",
    "37 epochs",
    "50 epochs",
    "52 epochs",
    "200 epochs",
    "epoch 12",
    "dernières années",
    "année n",
    

    # Génériques trop faibles
    "objectif",
    "objectifs",
    "objectif principal",
    "les résultats",
    "résultats",
    "résultat",
    "résultats prometteurs",
    "résultats inattendus",
    "résultats préliminaires",
    "algorithme",
    "méthodes classiques",
    "approches classiques de guidage",
    "algorithmes préprogrammés",
    "algorithmes préprogrammés et rigides",
    "point de passage",
    "centre",
}

AUTO_FIX = {
    # ORGANISMES
    "scalian": "ORGANISME",
    "dga": "ORGANISME",
    "hipocampus r&d": "ORGANISME",
    "perseus": "ORGANISME",
    "ia": "TECHNOLOGIE_RD",
    "l’ia": "TECHNOLOGIE_RD",
    "l'ia": "TECHNOLOGIE_RD",
    "vehicule": "COMPOSANT_TECHNIQUE",
    "véhicule": "EQUIPEMENT_RD",
    "nlm": "COMPOSANT_TECHNIQUE",
    "méthode de correction des réseaux de neurones": "METHODE_RD",
    # PROJETS / LOGICIELS / PLATEFORMES
    "sigma": "TECHNOLOGIE_RD",
    "simulation innovante, générique et macroscopique pour l’astronautique": "TECHNOLOGIE_RD",
    "simulation innovante, générique et macroscopique pour l'astronautique": "TECHNOLOGIE_RD",
    "ai-guidance": "TECHNOLOGIE_RD",
    "logiciel sigma": "TECHNOLOGIE_RD",
    "plateforme d’apprentissage": "TECHNOLOGIE_RD",
    "plateforme d'apprentissage": "TECHNOLOGIE_RD",
    "plateforme de simulation": "TECHNOLOGIE_RD",
    "environnement de simulation": "TECHNOLOGIE_RD",
    "environnement simulé": "TECHNOLOGIE_RD",
    "simulation 3d": "TECHNOLOGIE_RD",
    "jumeau numérique": "TECHNOLOGIE_RD",

    # DOMAINES
    "intelligence artificielle": "DOMAINE_RD",
    "ia": "DOMAINE_RD",
    "machine learning": "DOMAINE_RD",
    "apprentissage automatique": "DOMAINE_RD",
    "apprentissage par renforcement": "DOMAINE_RD",
    "industrie aérospatiale": "DOMAINE_RD",
    "industrie aerospatiale": "DOMAINE_RD",
    "aérospatiale": "DOMAINE_RD",
    "aerospatiale": "DOMAINE_RD",
    "guidage spatial": "DOMAINE_RD",
    "guidage de parachute": "DOMAINE_RD",
    "guidage des engins spatiaux": "DOMAINE_RD",
    "guidage de véhicules": "DOMAINE_RD",
    "guidage des fusées": "DOMAINE_RD",
    "fusées réutilisables": "DOMAINE_RD",
    "reusable rockets": "DOMAINE_RD",
    "vertical landing": "DOMAINE_RD",
    "navigation autonome": "DOMAINE_RD",
    "contrôle d’attitude": "DOMAINE_RD",
    "contrôle d'attitude": "DOMAINE_RD",

    # TECHNOLOGIES / MODELES / ALGORITHMES
    "ppo": "TECHNOLOGIE_RD",
    "algorithme ppo": "TECHNOLOGIE_RD",
    "méthode ppo": "TECHNOLOGIE_RD",
    "proximal policy optimization": "TECHNOLOGIE_RD",
    "ppo-clip": "TECHNOLOGIE_RD",
    "deep deterministic policy gradient": "TECHNOLOGIE_RD",
    "ddpg": "TECHNOLOGIE_RD",
    "réseau de neurones": "TECHNOLOGIE_RD",
    "réseau neuronal": "TECHNOLOGIE_RD",
    "réseau neuronal profond": "TECHNOLOGIE_RD",
    "réseau de neurones profond": "TECHNOLOGIE_RD",
    "réseaux récurrents": "TECHNOLOGIE_RD",
    "réseau de neurones récurrent": "TECHNOLOGIE_RD",
    "modèle d’intelligence artificielle": "TECHNOLOGIE_RD",
    "modèle d'intelligence artificielle": "TECHNOLOGIE_RD",
    "algorithme de guidage": "TECHNOLOGIE_RD",
    "algorithme de correction": "TECHNOLOGIE_RD",
    "politique de contrôle": "TECHNOLOGIE_RD",
    "contrôleur neuronal": "TECHNOLOGIE_RD",
    "lidar": "TECHNOLOGIE_RD",
    "altimétrie radar": "TECHNOLOGIE_RD",

    # EQUIPEMENTS / SYSTEMES
    "mini-apterros": "EQUIPEMENT_RD",
    "mini apterros": "EQUIPEMENT_RD",
    "parachute": "EQUIPEMENT_RD",
    "fusée": "EQUIPEMENT_RD",
    "fusées": "EQUIPEMENT_RD",
    "lanceur": "EQUIPEMENT_RD",
    "lanceurs spatiaux": "EQUIPEMENT_RD",
    "véhicule": "EQUIPEMENT_RD",
    "véhicule simulé": "EQUIPEMENT_RD",
    "engins spatiaux": "EQUIPEMENT_RD",
    "engin spatial": "EQUIPEMENT_RD",
    "drisse droite": "COMPOSANT_TECHNIQUE",
    "drisse gauche": "COMPOSANT_TECHNIQUE",
    "voile": "COMPOSANT_TECHNIQUE",
    "tuyère": "COMPOSANT_TECHNIQUE",
    "réacteur": "COMPOSANT_TECHNIQUE",
    "bloc simulation": "COMPOSANT_TECHNIQUE",
    "bloc véhicule": "COMPOSANT_TECHNIQUE",
    "bloc nlm": "COMPOSANT_TECHNIQUE",
    "bloc vehicle": "COMPOSANT_TECHNIQUE",
    "newton’s law of motion": "COMPOSANT_TECHNIQUE",
    "newton law of motion": "COMPOSANT_TECHNIQUE",
    "commande": "COMPOSANT_TECHNIQUE",
    "loi de commande": "COMPOSANT_TECHNIQUE",
    "fonction de récompense": "COMPOSANT_TECHNIQUE",
    "fonctions de récompense": "COMPOSANT_TECHNIQUE",
    "profils de vent": "COMPOSANT_TECHNIQUE",
    "profil de vent": "COMPOSANT_TECHNIQUE",

    # METHODES
    "démarche expérimentale": "METHODE_RD",
    "expérimentation": "METHODE_RD",
    "essais en vol réels": "METHODE_RD",
    "essais réels": "METHODE_RD",
    "validation expérimentale": "METHODE_RD",
    "simulations numériques": "METHODE_RD",
    "simulation": "METHODE_RD",
    "modélisation physique": "METHODE_RD",
    "modélisation physique simplifiée": "METHODE_RD",
    "modélisation directe de la contrainte physique": "METHODE_RD",
    "modélisation informatique": "METHODE_RD",
    "modélisation du système": "METHODE_RD",
    "modélisation de l’environnement": "METHODE_RD",
    "modélisation de l'environnement": "METHODE_RD",
    "apprentissage par renforcement profond": "METHODE_RD",
    "entraînement progressif": "METHODE_RD",
    "pré-entraînement": "METHODE_RD",
    "fonction de récompense optimisée": "METHODE_RD",
    "mécanisme de récompense/pénalité": "METHODE_RD",
    "système de bonus/malus": "METHODE_RD",
    "approche bonus/malus": "METHODE_RD",
    "reachability analysis": "METHODE_RD",
    "analyse de la portée": "METHODE_RD",
    "fonctions de lyapunov": "METHODE_RD",
    "fonctions de lyapunov auto-apprises": "METHODE_RD",
    "contrainte physique": "METHODE_RD",
    "contraintes physiques": "METHODE_RD",
    "contrainte d’incrément": "METHODE_RD",
    "contrainte d'incrément": "METHODE_RD",
    "méthode statistique": "METHODE_RD",

    # VERROUS
    "verrou": "VERROU_TECH",
    "verrou technologique": "VERROU_TECH",
    "verrous technologiques": "VERROU_TECH",
    "verrous scientifiques, techniques, technologiques": "VERROU_TECH",
    "reality gap": "VERROU_TECH",
    "fidélité": "VERROU_TECH",
    "reproductibilité": "VERROU_TECH",
    "robustesse et généralisation des modèles d’ia": "VERROU_TECH",
    "robustesse et généralisation des modèles d'ia": "VERROU_TECH",
    "difficulté liée à la modélisation des algorithmes de guidage pour le mini-apterros": "VERROU_TECH",
    "gestion des incertitudes": "VERROU_TECH",
    "perturbations environnementales": "VERROU_TECH",
    "contraintes physiques strictes": "VERROU_TECH",
    "respect strict des contraintes physiques": "VERROU_TECH",

    # RESULTATS / OBJECTIFS utiles
    "résultats de l’entrainement sans vent": "RESULTAT_RD",
    "résultats de l'entraînement sans vent": "RESULTAT_RD",
    "résultats de l’entraînement avec vent": "RESULTAT_RD",
    "résultats de l'entrainement avec vent": "RESULTAT_RD",
    "réduction du reality gap": "RESULTAT_RD",
    "évolution réaliste de la poussée": "RESULTAT_RD",
    "contrôle plus fluide": "RESULTAT_RD",
}

SECTION_TITLES = {
    "intitulé de l’opération": "OBJECTIF_RD",
    "intitulé du projet": "OBJECTIF_RD",
    "objectifs visés": "OBJECTIF_RD",
    "performances à atteindre": "OBJECTIF_RD",
    "analyse de l’état de l’art": "METHODE_RD",
    "analyse de l'état de l'art": "METHODE_RD",
    "analyse des connaissances sur le guidage des engins spatiaux par ia": "METHODE_RD",
    "prise de connaissances sur les approches pour la sécurisation d’ia": "METHODE_RD",
    "prise de connaissances sur les approches pour la sécurisation d'ia": "METHODE_RD",
    "analyse des approches existantes pour le guidage de parachute par ia": "METHODE_RD",
    "synthèse des connaissances sur le guidage de vraies fusées": "METHODE_RD",
    "problématique générale": "VERROU_TECH",
    "fidélité (reality gap)": "VERROU_TECH",
    "fidélité (reality gap)": "VERROU_TECH",
    "reproductibilité": "VERROU_TECH",
    "robustesse et généralisation des modèles d’ia": "VERROU_TECH",
    "robustesse et généralisation des modèles d'ia": "VERROU_TECH",
    "verrous technologiques levés": "VERROU_TECH",
    "raisonnement scientifique et démarche expérimentale appliquée": "METHODE_RD",
    "travaux sur la modélisation des algorithmes de guidage du mini-apterros": "METHODE_RD",
    "contribution du logiciel sigma pour les essais réels": "METHODE_RD",
    "première itération : régulation de la poussée via une fonction de récompense": "METHODE_RD",
    "deuxième itération : modélisation directe de la contrainte physique": "METHODE_RD",
    "troisième itération : conception d’une fonction de récompense optimisée pour la trajectoire": "METHODE_RD",
    "troisième itération : conception d'une fonction de récompense optimisée pour la trajectoire": "METHODE_RD",
    "travaux réalisés pour le développement d’une ia pour le guidage de parachute": "METHODE_RD",
    "travaux réalisés pour le développement d'une ia pour le guidage de parachute": "METHODE_RD",
    "généralisation du logiciel de modélisation physique": "METHODE_RD",
    "modélisation physique simplifiée du parachute": "METHODE_RD",
    "entraînement de l’ia au guidage du parachute": "METHODE_RD",
    "entraînement de l'ia au guidage du parachute": "METHODE_RD",
    "résultats de l’entrainement sans vent": "RESULTAT_RD",
    "résultats de l'entraînement sans vent": "RESULTAT_RD",
    "résultats de l’entraînement avec vent": "RESULTAT_RD",
    "résultats de l'entrainement avec vent": "RESULTAT_RD",
    "travaux réalisés pour la mise en œuvre d’une stratégie d’évaluation de la qualité d’un réseau de neurone": "METHODE_RD",
    "généralisation du réseau de neurones à des modèles physiques différents": "METHODE_RD",
}

DATE_KEEP_PATTERNS = [
    r"^\d{4}$",
    r"^année\s+\d{4}$",
    r"^ann[ée]e\s+\d{4}$",
    r"^(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+\d{4}$",
    r"^annÉe\s+\d{4}$",
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

def clean_entity(ent):
    text = norm(ent.get("text", ""))
    label = ent.get("label", "")
    low = lower_norm(text)

    if not text:
        return None

    # Section titles
    if low in SECTION_TITLES:
        if KEEP_SECTION_TITLES:
            ent["text"] = text
            ent["label"] = SECTION_TITLES[low]
            ent["status"] = "force_cleaned"
            return ent
        return None

    # Direct remove
    if low in REMOVE_EXACT:
        return None

    # URLs / emails / admin
    if "@" in text or low.startswith("http") or low.startswith("www.") or low.endswith(".com") or low.endswith(".fr"):
        return None

    # Money placeholders / wrong amounts
    if label == "MONTANT_CIR":
        if is_placeholder_money(text):
            return None
        if re.search(r"\b(mètres?|m|nm|kg|bonus|malus)\b", low):
            return None

    # Dates
    if label == "DATE_PERIODE":
        if not is_valid_date(text):
            return None
        ent["text"] = text
        return ent

    # Remove authors and fake persons
    if label == "PERSONNE":
        if low.endswith("et al") or low.endswith("et al.") or " et al" in low:
            return None
        if low in {"agent", "concepteur", "chef de projet", "chercheur", "scientific officer"}:
            return None

    # Faux lieux
    if label == "LIEU" and low in {
        "position cible", "checkpoint", "sol", "position initiale", "monde réel",
        "environnement", "environnement sans vent", "domaine de simulation", "mars"
    }:
        return None

    # Generic result/objective
    if label == "RESULTAT_RD" and low in {
        "résultats", "résultat", "les résultats", "résultats prometteurs",
        "résultats inattendus", "résultats préliminaires"
    }:
        return None

    if label == "OBJECTIF_RD" and low in {"objectif", "objectifs", "objectif principal"}:
        return None

    # Direct fix
    if low in AUTO_FIX:
        ent["text"] = text
        ent["label"] = AUTO_FIX[low]
        ent["status"] = "force_cleaned"
        return ent

    # Patterns
    if "sigma" in low or "ai-guidance" in low:
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if "ppo" in low or "proximal policy optimization" in low:
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if "reality gap" in low or "verrou" in low:
        ent["label"] = "VERROU_TECH"
        ent["status"] = "force_cleaned"

    if "apprentissage par renforcement" in low or "machine learning" in low:
        ent["label"] = "DOMAINE_RD"
        ent["status"] = "force_cleaned"

    if "réseau de neurones" in low or "réseau neuronal" in low or low in {"ia", "l’ia", "l'ia"}:
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if "mini-apterros" in low or low in {"parachute", "fusée", "fusées", "lanceur"}:
        ent["label"] = "EQUIPEMENT_RD"
        ent["status"] = "force_cleaned"

    if "bloc " in low or low in {"commande", "tuyère", "réacteur", "voile"}:
        ent["label"] = "COMPOSANT_TECHNIQUE"
        ent["status"] = "force_cleaned"

    if "fonction de récompense" in low or "bonus/malus" in low:
        ent["label"] = "METHODE_RD"
        ent["status"] = "force_cleaned"

    if "validation expérimentale" in low or "essais" in low or "simulation" in low or "modélisation" in low:
        ent["label"] = "METHODE_RD"
        ent["status"] = "force_cleaned"

    # Wrong labels from generic terms
    if label == "ORGANISME" and low in {"sigma", "ai-guidance"}:
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if label == "EQUIPEMENT_RD" and low in {"algorithme", "ia", "l’ia", "l'ia"}:
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if label == "DOMAINE_RD" and low in {"algorithme ppo"}:
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if len(text) <= 2 and low not in {"ia"}:
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

print("✅ Force clean projet 12 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")
print(f"Output  : {OUTPUT}")