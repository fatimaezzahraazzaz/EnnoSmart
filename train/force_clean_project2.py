from pathlib import Path
import json
import re

INPUT = Path(r"C:\EnnoSmart\projects\projet_2_\annotations\ner_candidates.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_2_\annotations\ner_candidates_clean.json")

REMOVE = {
    "personnel",
    "personnel soignant",
    "personnel hospitalier",
    "professionnels de santé",
    "chef de projet",
    "chercheur",
    "nom",
    "organisation",
    "objectifs du projet",
    "objectifs",
    "objectif",
    "démarche expérimentale",
    "résultats",
    "résultat",
    "laboratoire",
    "industrie",
    "entreprises",
    "consommateurs",
    "fabricants",
    "industriels",
    "établissements de santé",
    "services hospitaliers",
    "hôpitaux",
    "grand publique",
    "laboratoires de recherche",
    "ministère de la défense",
    "prestataire",
    "fournisseur",
    "groupe",
    "utilisateurs",
    "établissements concernés",
    "laboratoire de recherche",
    "directeur général",
    "pdg",
    "fonction",
    "heures",
    "nom de l’interlocuteur",
    "numéro de téléphone",
    "adresse électronique",
     "antonin.leblanc",
    "fbproduct",
    "personnel soignant ou technique",
    "services de santé",
    "texture",
    "aujourd’hui",
    "dernières années",
    "premiers mois",
    "20110901",
    "8 h",
    "objectifs fixés",
    "dernier objectif de ce projet",
}

AUTO_FIX = {
    "fb product": "ORGANISME",
    "fb.int’l": "ORGANISME",
    "fbroduct": "ORGANISME",
    "franklab": "ORGANISME",
    "steriscience": "ORGANISME",
    "inra": "ORGANISME",
    "inrae": "ORGANISME",
    "institut pasteur": "ORGANISME",
    "gredeco": "ORGANISME",
    "ionisos": "ORGANISME",
    "midac": "ORGANISME",
    "cosmetic office": "ORGANISME",
    "inci": "ORGANISME",
    "ocde": "ORGANISME",
    "dga": "ORGANISME",
    "gamasonic": "ORGANISME",
    "alkapharm": "ORGANISME",
    "anios": "ORGANISME",
    "method": "ORGANISME",

    "roger bonnet": "PERSONNE",
    "antonin leblanc": "PERSONNE",
    "leblanc antonin": "PERSONNE",
    "jean-loïc baratoux": "PERSONNE",
    "madame igel": "PERSONNE",
    "monsieur bonnet": "PERSONNE",

    "pcr": "METHODE_RD",
    "atpmétrie": "METHODE_RD",
    "méthode d’atpmétrie": "METHODE_RD",
    "méthode d’atp-métrie": "METHODE_RD",
    "méthodes de tamis moléculaires": "METHODE_RD",
    "méthodes de précipitation": "METHODE_RD",
    "méthode classique en boîte de pétrie": "METHODE_RD",
    "patch test": "METHODE_RD",
    "études expérimentales": "METHODE_RD",

    "agents tensioactifs": "COMPOSANT_TECHNIQUE",
    "tensioactifs": "COMPOSANT_TECHNIQUE",
    "edta": "COMPOSANT_TECHNIQUE",
    "mdga": "COMPOSANT_TECHNIQUE",
    "soude": "COMPOSANT_TECHNIQUE",
    "potasse": "COMPOSANT_TECHNIQUE",
    "stabilisateur de mousse": "COMPOSANT_TECHNIQUE",
    "un stabilisateur de mousse": "COMPOSANT_TECHNIQUE",
    "agent hydratant": "COMPOSANT_TECHNIQUE",
    "huile a": "COMPOSANT_TECHNIQUE",
    "huile b": "COMPOSANT_TECHNIQUE",
    "tensioactif ta1": "COMPOSANT_TECHNIQUE",
    "solvant a": "COMPOSANT_TECHNIQUE",
    "f29": "COMPOSANT_TECHNIQUE",
    "x1": "COMPOSANT_TECHNIQUE",
    "an": "COMPOSANT_TECHNIQUE",

    "savon": "MATERIAU_SPECIFIQUE",
    "gélose": "MATERIAU_SPECIFIQUE",
    "inox": "MATERIAU_SPECIFIQUE",
    "mousse": "MATERIAU_SPECIFIQUE",
    "huiles": "MATERIAU_SPECIFIQUE",
    "graisses": "MATERIAU_SPECIFIQUE",
    "matière première": "MATERIAU_SPECIFIQUE",

    "bacs avec système de dosage automatique": "EQUIPEMENT_RD",
    "bacs de prétrempage": "EQUIPEMENT_RD",
    "systèmes de dosage automatique": "EQUIPEMENT_RD",
    "bacs à dosage automatique": "EQUIPEMENT_RD",
    "équipements adaptés": "EQUIPEMENT_RD",
    "équipements spécialisés": "EQUIPEMENT_RD",
    "cornéométrique": "EQUIPEMENT_RD",

    "secteur médical": "DOMAINE_RD",
    "bloc opératoire": "DOMAINE_RD",
    "biomédecine": "DOMAINE_RD",
    "secteur hospitalier": "DOMAINE_RD",

    "université d'angers": "ORGANISME",
    "international nomenclature of cosmetic ingredients": "ORGANISME",
    "groupe de recherche et d’évaluation en dermatologie et cosmétologie": "ORGANISME",
    "unit vim": "ORGANISME",
    "franklab taïwan": "ORGANISME",
    "geobacillus stearothermophilus": "COMPOSANT_TECHNIQUE",
    "stabilisateurs de mousse": "COMPOSANT_TECHNIQUE",
    "stabilisateur an": "COMPOSANT_TECHNIQUE",
    "la pastille jaune": "COMPOSANT_TECHNIQUE",
    "pastille bleu": "COMPOSANT_TECHNIQUE",
    "pulvérisateur": "EQUIPEMENT_RD",
    "solvant b": "COMPOSANT_TECHNIQUE",
    "tensioactif ta2": "COMPOSANT_TECHNIQUE",
}

BAD_LABEL_TEXT = {
    ("dermatologue", "PERSONNE"),
    ("personnel qualifié", "PERSONNE"),
    ("16-18 femmes", "PERSONNE"),
    ("nous", "ORGANISME"),
    ("équipe r&d", "ORGANISME"),
    ("organismes accrédités", "ORGANISME"),
    ("laboratoire", "LIEU"),
    ("industrielle", "LIEU"),
    ("salle d’opération", "LIEU"),
    ("salles d’opération", "LIEU"),
    ("environnements hospitaliers", "LIEU"),
    ("milieu hospitalier", "LIEU"),
}

PHASE_PATTERN = re.compile(r"^phase\s+\d+$", re.IGNORECASE)
JALON_PATTERN = re.compile(r"^j\d+$", re.IGNORECASE)

def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()

def clean_entity(ent):
    text = clean_text(ent.get("text", ""))
    label = ent.get("label", "")
    lower = text.lower()

    if not text:
        return None

    if lower in REMOVE:
        return None

    if (lower, label) in BAD_LABEL_TEXT:
        return None

    if "@" in text or lower.endswith(".com") or lower.endswith(".fr"):
        return None

    if label == "DATE_PERIODE" and text.isdigit() and int(text) < 1900:
        return None

    if PHASE_PATTERN.match(text):
        ent["label"] = "JALON"
        ent["status"] = "force_cleaned"

    elif JALON_PATTERN.match(text):
        ent["label"] = "JALON"
        ent["status"] = "force_cleaned"

    elif lower in AUTO_FIX:
        ent["label"] = AUTO_FIX[lower]
        ent["status"] = "force_cleaned"

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

print("✅ Force clean projet 2 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")
print(f"Output  : {OUTPUT}")