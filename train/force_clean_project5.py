from pathlib import Path
import json
import re

INPUT = Path(r"C:\EnnoSmart\projects\projet_5_\annotations\ner_candidates.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_5_\annotations\ner_candidates_clean.json")

KEEP_SECTION_TITLES = True

REMOVE_EXACT = {
    # Bruit tableau / générique
    "nom",
    "prénom",
    "fonction",
    "heures",
    "nous",
    "auteurs",
    "auteur",
    "acteurs du secteur",
    "industrie automobile",
    "ministère de la défense",

    # Trop générique pour GLiNER
    "résultats",
    "résultat",
    "résultats obtenus",
    "résultats de cette étude",
    "résultats de l’étude",
    "résultats des essais",
    "résultats expérimentaux",
    "essais",
    "matériau",
    "matériaux",
    "joint",
    "joints",

    # Références bibliographiques / auteurs
    "park et al",
    "b. andro",
    "salissou",
}

AUTO_FIX = {
    # PERSONNES
    "n. chevallier": "PERSONNE",
    "ayed maryem": "PERSONNE",
    "catteau antonin": "PERSONNE",
    "chevallier nicolas": "PERSONNE",
    "garot martin": "PERSONNE",
    "havard alexandre": "PERSONNE",
    "roussel lisa": "PERSONNE",
    "ait younes tarik": "PERSONNE",

    # ORGANISMES
    "cevaa": "ORGANISME",
    "stellantis": "ORGANISME",
    "dga": "ORGANISME",

    # LOGICIELS / OUTILS / EQUIPEMENTS
    "testlab": "EQUIPEMENT_RD",
    "spectral testing": "EQUIPEMENT_RD",
    "actran": "EQUIPEMENT_RD",
    "msc actran": "EQUIPEMENT_RD",
    "msc apex": "EQUIPEMENT_RD",
    "apex": "EQUIPEMENT_RD",
    "marc mentat": "EQUIPEMENT_RD",
    "abaqus": "EQUIPEMENT_RD",

    "petite cabine": "EQUIPEMENT_RD",
    "chambre climatique": "EQUIPEMENT_RD",
    "chambres climatiques": "EQUIPEMENT_RD",
    "environnement anéchoïque": "EQUIPEMENT_RD",
    "chambre anéchoïque": "EQUIPEMENT_RD",
    "enceinte climatique": "EQUIPEMENT_RD",
    "enceinte thermique": "EQUIPEMENT_RD",
    "tube d’impédance": "EQUIPEMENT_RD",
    "tube d'impédance": "EQUIPEMENT_RD",
    "banc de compression": "EQUIPEMENT_RD",
    "banc de compression de joint": "EQUIPEMENT_RD",
    "banc d’essai": "EQUIPEMENT_RD",
    "banc d’essais": "EQUIPEMENT_RD",
    "congélateur": "EQUIPEMENT_RD",
    "congélateur de laboratoire": "EQUIPEMENT_RD",
    "vibromètre laser doppler": "EQUIPEMENT_RD",
    "microphones": "EQUIPEMENT_RD",
    "microphone": "EQUIPEMENT_RD",
    "haut-parleurs": "EQUIPEMENT_RD",
    "emporte-pièce": "EQUIPEMENT_RD",

    # METHODES
    "démarche expérimentale": "METHODE_RD",
    "protocole expérimental": "METHODE_RD",
    "protocoles expérimentaux": "METHODE_RD",
    "analyse par éléments finis": "METHODE_RD",
    "méthodes par éléments finis": "METHODE_RD",
    "éléments finis": "METHODE_RD",
    "analyse acoustique": "METHODE_RD",
    "analyse statique": "METHODE_RD",
    "modélisation numérique": "METHODE_RD",
    "simulations numériques": "METHODE_RD",
    "modèle numérique": "METHODE_RD",
    "modèle ef": "METHODE_RD",
    "modèle de biot": "METHODE_RD",
    "modèle d’ogden": "METHODE_RD",
    "modèle d'ogden": "METHODE_RD",
    "loi de johnson cook": "METHODE_RD",
    "johnson cook": "METHODE_RD",
    "méthode de salle réverbérante": "METHODE_RD",
    "méthode énergétique simplifiée": "METHODE_RD",
    "méthode normalisée à deux microphones": "METHODE_RD",
    "campagnes expérimentales": "METHODE_RD",
    "mesures acoustiques": "METHODE_RD",
    "pertes d’insertion": "METHODE_RD",
    "insertion loss": "METHODE_RD",
    "sound pressure level": "METHODE_RD",
    "spl": "METHODE_RD",
    "transformée de fourier": "METHODE_RD",

    # VERROUS
    "verrou technique": "VERROU_TECH",
    "verrou technique majeur": "VERROU_TECH",
    "manque de connaissances": "VERROU_TECH",
    "incertitude": "VERROU_TECH",
    "sources d’incertitude": "VERROU_TECH",
    "sources d'incertitude": "VERROU_TECH",

    # COMPOSANTS
    "systèmes d’étanchéité des véhicules automobiles": "COMPOSANT_TECHNIQUE",
    "systèmes d'étanchéité des véhicules automobiles": "COMPOSANT_TECHNIQUE",
    "systèmes d’étanchéité": "COMPOSANT_TECHNIQUE",
    "systèmes d'étanchéité": "COMPOSANT_TECHNIQUE",
    "joints d’étanchéité": "COMPOSANT_TECHNIQUE",
    "joints d'étanchéité": "COMPOSANT_TECHNIQUE",
    "pièce d’étanchéité": "COMPOSANT_TECHNIQUE",
    "pièces d'étanchéité": "COMPOSANT_TECHNIQUE",
    "joint sous compression": "COMPOSANT_TECHNIQUE",
    "joints élastomères": "COMPOSANT_TECHNIQUE",
    "joints de bulbe": "COMPOSANT_TECHNIQUE",
    "joint bulbe": "COMPOSANT_TECHNIQUE",
    "joint omega": "COMPOSANT_TECHNIQUE",
    "joint de glace": "COMPOSANT_TECHNIQUE",
    "joint demi-lune": "COMPOSANT_TECHNIQUE",
    "joint cellulaire": "COMPOSANT_TECHNIQUE",
    "joint de profil b": "COMPOSANT_TECHNIQUE",
    "plaque support": "COMPOSANT_TECHNIQUE",
    "une plaque support": "COMPOSANT_TECHNIQUE",
    "mâchoire amovible": "COMPOSANT_TECHNIQUE",
    "une mâchoire amovible": "COMPOSANT_TECHNIQUE",
    "barre": "COMPOSANT_TECHNIQUE",
    "une barre": "COMPOSANT_TECHNIQUE",
    "pièces latérales": "COMPOSANT_TECHNIQUE",
    "deux pièces latérales": "COMPOSANT_TECHNIQUE",
    "porte-échantillons": "COMPOSANT_TECHNIQUE",
    "vis de réglage": "COMPOSANT_TECHNIQUE",

    # MATERIAUX
    "ethylène propylène diène monomère": "MATERIAU_SPECIFIQUE",
    "éthylène propylène diène monomère": "MATERIAU_SPECIFIQUE",
    "epdm": "MATERIAU_SPECIFIQUE",
    "tpe": "MATERIAU_SPECIFIQUE",
    "élastomère": "MATERIAU_SPECIFIQUE",
    "élastomères": "MATERIAU_SPECIFIQUE",
    "élastomères thermoplastiques": "MATERIAU_SPECIFIQUE",
    "caoutchouc": "MATERIAU_SPECIFIQUE",
    "caoutchouc epdm": "MATERIAU_SPECIFIQUE",
    "mousse": "MATERIAU_SPECIFIQUE",
    "mousses": "MATERIAU_SPECIFIQUE",
    "mousses polymères": "MATERIAU_SPECIFIQUE",
    "mousse de polyuréthane": "MATERIAU_SPECIFIQUE",
    "matières plastiques": "MATERIAU_SPECIFIQUE",
    "matériaux isolants à base d’aérogel": "MATERIAU_SPECIFIQUE",
    "matériaux isolants à base d'aérogel": "MATERIAU_SPECIFIQUE",
    "aérogel": "MATERIAU_SPECIFIQUE",
    "aluminium": "MATERIAU_SPECIFIQUE",
    "acier": "MATERIAU_SPECIFIQUE",
    "tôle d’acier": "MATERIAU_SPECIFIQUE",
    "tôle d'acier": "MATERIAU_SPECIFIQUE",
    "tôle d’acier plein ou perforée": "MATERIAU_SPECIFIQUE",
    "tôle d'acier plein ou perforée": "MATERIAU_SPECIFIQUE",
    "tôles en acier": "MATERIAU_SPECIFIQUE",
    "bois": "MATERIAU_SPECIFIQUE",
    "bloc béton": "MATERIAU_SPECIFIQUE",
    "feutre": "MATERIAU_SPECIFIQUE",
    "feutres": "MATERIAU_SPECIFIQUE",
    "mastic": "MATERIAU_SPECIFIQUE",
    "masses lourdes": "MATERIAU_SPECIFIQUE",
    "masse lourde": "MATERIAU_SPECIFIQUE",
    "matériaux réfléchissants": "MATERIAU_SPECIFIQUE",
    "feuilles d'aluminium": "MATERIAU_SPECIFIQUE",
    "feuilles d’aluminium": "MATERIAU_SPECIFIQUE",
    "air": "MATERIAU_SPECIFIQUE",
}

SECTION_TITLES = {
    "objectifs visés": "OBJECTIF_RD",
    "objectifs de l’opération": "OBJECTIF_RD",
    "objectifs de l'opération": "OBJECTIF_RD",
    "résultats obtenus": "RESULTAT_RD",
    "démarche expérimentale": "METHODE_RD",
    "description des travaux réalisés l’année 2024": "METHODE_RD",
    "description des travaux réalisés l'année 2024": "METHODE_RD",
    "verrous et incertitudes scientifiques, techniques, technologiques": "VERROU_TECH",
}

REFERENCE_PATTERNS = [
    r".+\bet al\.?$",
    r"^[A-Z]\.\s?[A-Z][A-Za-zÀ-ÿ\-]+$",
    r"^[A-Z][a-zà-ÿ\-]+ et al\.?\d*$",
]

DATE_KEEP_PATTERNS = [
    r"^\d{4}$",
    r"^ann[ée]e\s+\d{4}$",
    r"^(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+\d{4}$",
    r"^\d{1,2}h$",
    r"^\d{1,2}\s?h$",
]

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def lower_norm(text: str) -> str:
    return norm(text).lower()

def is_reference_author(text: str) -> bool:
    t = norm(text)
    low = t.lower()
    if low in REMOVE_EXACT:
        return True
    return any(re.match(p, t, flags=re.IGNORECASE) for p in REFERENCE_PATTERNS)

def is_valid_date(text: str) -> bool:
    t = lower_norm(text)
    return any(re.match(p, t, flags=re.IGNORECASE) for p in DATE_KEEP_PATTERNS)

def is_too_generic_entity(text: str, label: str) -> bool:
    low = lower_norm(text)

    if low in REMOVE_EXACT:
        return True

    # éviter d'apprendre des mots trop génériques
    if label in {"RESULTAT_RD", "METHODE_RD"} and low in {
        "résultats", "résultat", "essais", "test", "tests"
    }:
        return True

    if label == "COMPOSANT_TECHNIQUE" and low in {"joint", "joints"}:
        return True

    if label == "MATERIAU_SPECIFIQUE" and low in {"matériau", "matériaux"}:
        return True

    return False

def clean_entity(ent):
    text = norm(ent.get("text", ""))
    label = ent.get("label", "")
    low = text.lower()

    if not text:
        return None

    # section titles
    if low in SECTION_TITLES:
        if KEEP_SECTION_TITLES:
            ent["text"] = text
            ent["label"] = SECTION_TITLES[low]
            ent["status"] = "force_cleaned"
            return ent
        return None

    if is_too_generic_entity(text, label):
        return None

    # urls / mails
    if "@" in text or low.startswith("http") or low.startswith("www.") or low.endswith(".com") or low.endswith(".fr"):
        return None

    # auteurs références
    if label == "PERSONNE" and is_reference_author(text):
        return None

    # mots génériques mal classés
    if label == "PERSONNE" and low in {"nom", "prénom", "auteur", "auteurs"}:
        return None

    if label == "ORGANISME" and low in {
        "nous", "industrie automobile", "acteurs du secteur", "ministère de la défense"
    }:
        return None

    # dates : garder années / mois année / 24h, supprimer dates biblio selon contexte dans loop plus bas
    if label == "DATE_PERIODE" and not is_valid_date(text):
        return None

    # correction directe
    if low in AUTO_FIX:
        ent["text"] = text
        ent["label"] = AUTO_FIX[low]
        ent["status"] = "force_cleaned"
        return ent

    # Corrections spécifiques par patterns
    if re.match(r"^joint\s+", low):
        ent["label"] = "COMPOSANT_TECHNIQUE"
        ent["status"] = "force_cleaned"

    if low in {"testlab", "actran", "apex", "marc mentat"}:
        ent["label"] = "EQUIPEMENT_RD"
        ent["status"] = "force_cleaned"

    ent["text"] = text
    return ent

def is_reference_chunk(text: str) -> bool:
    low = lower_norm(text)
    return any(x in low for x in [
        " et al",
        "article",
        "thèse",
        "publié en",
        "publiée en",
        "travail de recherche",
    ])

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

        # Dans les références scientifiques, on supprime auteurs et années isolées
        if ref_chunk:
            ent_text = norm(ent.get("text", ""))
            ent_label = ent.get("label", "")

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

print("✅ Force clean projet 5 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")
print(f"Output  : {OUTPUT}")