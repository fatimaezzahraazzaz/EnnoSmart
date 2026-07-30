from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_28_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_28_\annotations\ner_candidates_clean_balanced.json")

REMOVE_EXACT = {
    # faux positifs causés par recherche sans word boundary
    "em",
    "Em",

    # trop générique / bruit
    "objectif",
    "objectif principal",
    "résultats",
    "resultats",
    "algorithme",
    "matériel",
    "materiel",
    "Informatique",
    "fournisseurs d’e-commerce",
    "fournisseurs d'e-commerce",
    "entreprises du monde de la mode",
    "Fournisseurs de logiciels",
    "domaine de la mode",

    # titres / admin
    "Développement expérimental",
    "Démarche expérimentale",
    "démarche expérimentale",
}

FIX_LABELS = {
    # objectifs mal captés comme verrous
    "quantifier l'incertitude": "OBJECTIF_RD",
    "réduire l'incertitude": "OBJECTIF_RD",
    "reduire l'incertitude": "OBJECTIF_RD",
    "réduire la dimension": "OBJECTIF_RD",
    "reduire la dimension": "OBJECTIF_RD",
    "augmenter les performances": "OBJECTIF_RD",
    "traiter les données manquantes": "OBJECTIF_RD",
    "traiter les donnees manquantes": "OBJECTIF_RD",
    "développer un modèle hybride de recommandation": "OBJECTIF_RD",
    "developper un modele hybride de recommandation": "OBJECTIF_RD",
    "Développer une plateforme B2B Matchmaking intelligente": "OBJECTIF_RD",
    "développer une plateforme B2B Matchmaking intelligente": "OBJECTIF_RD",
    "fournir automatiquement des recommandations": "OBJECTIF_RD",
    "connecter les entreprises": "OBJECTIF_RD",
    "permettre de réseauter": "OBJECTIF_RD",
    "permettre de reseauter": "OBJECTIF_RD",

    # technologies
    "Matchmaking": "TECHNOLOGIE_RD",
    "matchmaking": "TECHNOLOGIE_RD",
    "Matchmaking B2B": "TECHNOLOGIE_RD",
    "B2B Matchmaking": "TECHNOLOGIE_RD",
    "plateforme B2B Matchmaking intelligente": "TECHNOLOGIE_RD",
    "algorithme hybride": "TECHNOLOGIE_RD",
    "algorithme hybride de matchmaking": "TECHNOLOGIE_RD",
    "algorithme non supervisé": "TECHNOLOGIE_RD",
    "algorithme non supervise": "TECHNOLOGIE_RD",
    "DIDL": "TECHNOLOGIE_RD",
    "Distributed Intelligence based Deep Learning": "TECHNOLOGIE_RD",
    "Distributed Intelligence based Deep Learning DIDL": "TECHNOLOGIE_RD",
    "Distributed Intelligence based Deep Learning for Graph Matching and Crowdsourcing": "TECHNOLOGIE_RD",
    "Graph Matching": "TECHNOLOGIE_RD",
    "Knowledge Graphs": "TECHNOLOGIE_RD",
    "knowledge graphs": "TECHNOLOGIE_RD",
    "MICE": "TECHNOLOGIE_RD",
    "algorithme EM": "TECHNOLOGIE_RD",
    "Expectation Maximization": "TECHNOLOGIE_RD",
    "LARS": "TECHNOLOGIE_RD",
    "TensorFlow": "TECHNOLOGIE_RD",
    "SVM": "TECHNOLOGIE_RD",
    "Q-learning": "TECHNOLOGIE_RD",
    "CHARM": "TECHNOLOGIE_RD",
    "K-RecSys": "TECHNOLOGIE_RD",
    "OWL-S": "TECHNOLOGIE_RD",
    "UDDI": "TECHNOLOGIE_RD",
    "Latent Dirichlet Allocation": "TECHNOLOGIE_RD",

    # méthodes
    "développement expérimental": "METHODE_RD",
    "developpement experimental": "METHODE_RD",
    "démarche expérimentale": "METHODE_RD",
    "demarche experimentale": "METHODE_RD",
    "voie expérimentale": "METHODE_RD",
    "voie experimentale": "METHODE_RD",
    "régression logistique": "METHODE_RD",
    "regression logistique": "METHODE_RD",
    "arbre de décision": "METHODE_RD",
    "arbre de decision": "METHODE_RD",
    "classification": "METHODE_RD",
    "imputation générale": "METHODE_RD",
    "imputation generale": "METHODE_RD",
    "imputation MICE": "METHODE_RD",
    "méthode EM": "METHODE_RD",
    "methode EM": "METHODE_RD",
    "validation croisée": "METHODE_RD",
    "validation croisee": "METHODE_RD",
    "méthode de validation croisée": "METHODE_RD",
    "methode de validation croisee": "METHODE_RD",
    "raisonnement approximatif": "METHODE_RD",
    "logique floue": "METHODE_RD",
    "théorie de Dempster-Shafer": "METHODE_RD",
    "theorie de Dempster-Shafer": "METHODE_RD",
    "règles d'association": "METHODE_RD",
    "regles d'association": "METHODE_RD",
    "optimisation multi-objectifs": "METHODE_RD",
    "Maximum Mean Discrepancy": "METHODE_RD",
    "MMD": "METHODE_RD",
    "distance de Minkowski": "METHODE_RD",
    "apprentissage distribué": "METHODE_RD",
    "apprentissage distribue": "METHODE_RD",
    "apprentissage à faible précision": "METHODE_RD",
    "apprentissage a faible precision": "METHODE_RD",
    "optimisation des hyperparamètres": "METHODE_RD",
    "optimisation des hyperparametres": "METHODE_RD",
    "analyse de sentiments": "METHODE_RD",
    "Analyse de sentiments": "METHODE_RD",
    "opinion mining": "METHODE_RD",
    "Matching textuel et thématique": "METHODE_RD",
    "matching textuel et thématique": "METHODE_RD",
    "clustering thématique": "METHODE_RD",

    # domaines
    "intelligence artificielle": "DOMAINE_RD",
    "Intelligence artificielle": "DOMAINE_RD",
    "Machine Learning": "DOMAINE_RD",
    "machine learning": "DOMAINE_RD",
    "apprentissage automatique": "DOMAINE_RD",
    "apprentissage supervisé": "DOMAINE_RD",
    "apprentissage supervise": "DOMAINE_RD",
    "apprentissage non supervisé": "DOMAINE_RD",
    "apprentissage non supervise": "DOMAINE_RD",
    "ingénierie des connaissances": "DOMAINE_RD",
    "ingenierie des connaissances": "DOMAINE_RD",
    "systèmes multi-agents": "DOMAINE_RD",
    "systemes multi-agents": "DOMAINE_RD",
    "fouille de données": "DOMAINE_RD",
    "fouille de donnees": "DOMAINE_RD",
    "web sémantique": "DOMAINE_RD",
    "Web sémantique": "DOMAINE_RD",
    "web semantique": "DOMAINE_RD",
    "Soft computing": "DOMAINE_RD",
    "semi-conducteurs": "DOMAINE_RD",
    "Semi-conducteurs": "DOMAINE_RD",
    "marché des semi-conducteurs": "DOMAINE_RD",
    "marche des semi-conducteurs": "DOMAINE_RD",

    # verrous
    "données hétérogènes": "VERROU_TECH",
    "donnees heterogenes": "VERROU_TECH",
    "données multi-sources": "VERROU_TECH",
    "donnees multi-sources": "VERROU_TECH",
    "données manquantes": "VERROU_TECH",
    "donnees manquantes": "VERROU_TECH",
    "données incomplètes": "VERROU_TECH",
    "donnees incompletes": "VERROU_TECH",
    "non-disponibilité de données": "VERROU_TECH",
    "non-disponibilite de donnees": "VERROU_TECH",
    "Caractère aléatoire": "VERROU_TECH",
    "caractère aléatoire": "VERROU_TECH",
    "incertitude": "VERROU_TECH",
    "non-linéarité": "VERROU_TECH",
    "non linearite": "VERROU_TECH",
    "relation de Pareto": "VERROU_TECH",
    "non-convexité": "VERROU_TECH",
    "non-convexite": "VERROU_TECH",
    "minima locaux": "VERROU_TECH",
    "hétérogénéité": "VERROU_TECH",
    "heterogeneite": "VERROU_TECH",
    "données non structurées": "VERROU_TECH",
    "donnees non structurees": "VERROU_TECH",
    "mise à l’échelle": "VERROU_TECH",
    "mise a l'echelle": "VERROU_TECH",
    "scalabilité": "VERROU_TECH",
    "scalabilite": "VERROU_TECH",
    "rareté des données": "VERROU_TECH",
    "rarete des donnees": "VERROU_TECH",
    "comportement distributif des données": "VERROU_TECH",
    "comportement distributif des donnees": "VERROU_TECH",
    "comportement clairsemé": "VERROU_TECH",
    "comportement clairseme": "VERROU_TECH",
    "problème de démarrage à froid": "VERROU_TECH",
    "probleme de demarrage a froid": "VERROU_TECH",
    "cold-start issue": "VERROU_TECH",
    "sous-ajustement": "VERROU_TECH",
    "bruits de quantification": "VERROU_TECH",
    "dépassement de gradient": "VERROU_TECH",
    "depassement de gradient": "VERROU_TECH",
    "mises à jour de poids imprécises": "VERROU_TECH",
    "mises a jour de poids imprecises": "VERROU_TECH",
    "goulot d’étranglement": "VERROU_TECH",
    "goulot d'etranglement": "VERROU_TECH",

    # matériaux / données
    "règles métier": "MATERIAU_SPECIFIQUE",
    "regles metier": "MATERIAU_SPECIFIQUE",
    "règles du marché": "MATERIAU_SPECIFIQUE",
    "Règles du marché": "MATERIAU_SPECIFIQUE",
    "regles du marche": "MATERIAU_SPECIFIQUE",
    "corpus d’apprentissage": "MATERIAU_SPECIFIQUE",
    "corpus d'apprentissage": "MATERIAU_SPECIFIQUE",
    "corpus de données": "MATERIAU_SPECIFIQUE",
    "corpus de donnees": "MATERIAU_SPECIFIQUE",
    "base d’apprentissage": "MATERIAU_SPECIFIQUE",
    "base d'apprentissage": "MATERIAU_SPECIFIQUE",
    "base de données": "MATERIAU_SPECIFIQUE",
    "base de donnees": "MATERIAU_SPECIFIQUE",
    "données B2B": "MATERIAU_SPECIFIQUE",
    "donnees B2B": "MATERIAU_SPECIFIQUE",
    "données commerciales": "MATERIAU_SPECIFIQUE",
    "donnees commerciales": "MATERIAU_SPECIFIQUE",
    "données étiquetées": "MATERIAU_SPECIFIQUE",
    "donnees etiquetees": "MATERIAU_SPECIFIQUE",
    "ensemble de données": "MATERIAU_SPECIFIQUE",
    "ensemble de donnees": "MATERIAU_SPECIFIQUE",

    # composants
    "MUST String Navigator": "COMPOSANT_TECHNIQUE",
    "Must String Navigator": "COMPOSANT_TECHNIQUE",
    "DIDL String Navigator": "COMPOSANT_TECHNIQUE",
    "DIDL string Navigator": "COMPOSANT_TECHNIQUE",
    "String Navigator": "COMPOSANT_TECHNIQUE",
    "graphe biparti": "COMPOSANT_TECHNIQUE",
    "graphes bipartites": "COMPOSANT_TECHNIQUE",
    "espace vectoriel": "COMPOSANT_TECHNIQUE",
    "vecteurs d'engagement flous": "COMPOSANT_TECHNIQUE",
    "modèle de requête": "COMPOSANT_TECHNIQUE",
    "modele de requete": "COMPOSANT_TECHNIQUE",

    # résultats
    "31 faux négatifs": "RESULTAT_RD",
    "31 faux negatifs": "RESULTAT_RD",
    "7 faux positifs": "RESULTAT_RD",
    "27 faux négatifs": "RESULTAT_RD",
    "27 faux negatifs": "RESULTAT_RD",
    "0 faux positif": "RESULTAT_RD",
    "15 faux négatifs": "RESULTAT_RD",
    "15 faux negatifs": "RESULTAT_RD",
    "courbe ROC": "RESULTAT_RD",
    "Matrice de confusion": "RESULTAT_RD",
    "matrice de confusion": "RESULTAT_RD",
    "précision du modèle": "RESULTAT_RD",
    "precision du modele": "RESULTAT_RD",
    "Mean Average Recall": "RESULTAT_RD",
    "Couverture": "RESULTAT_RD",
    "couverture": "RESULTAT_RD",
    "similarité intra-liste": "RESULTAT_RD",
    "similarite intra-liste": "RESULTAT_RD",
    "augmentation significative": "RESULTAT_RD",

    # organismes
    "MUST": "ORGANISME",
    "Laboratoire CEDRIC": "ORGANISME",
    "Conservatoire National des Arts et Métiers de Paris": "ORGANISME",
    "Conservatoire National des Arts et Metiers de Paris": "ORGANISME",
}

TEXT_CAPS = {
    "matchmaking": 7,
    "matchmaking b2b": 7,
    "b2b matchmaking": 6,
    "développement expérimental": 4,
    "developpement experimental": 4,
    "démarche expérimentale": 4,
    "demarche experimentale": 4,
    "machine learning": 6,
    "intelligence artificielle": 6,
    "régression logistique": 7,
    "regression logistique": 7,
    "arbre de décision": 5,
    "arbre de decision": 5,
    "classification": 6,
    "imputation générale": 5,
    "imputation generale": 5,
    "imputation mice": 6,
    "mice": 5,
    "algorithme em": 4,
    "méthode em": 3,
    "methode em": 3,
    "données manquantes": 7,
    "donnees manquantes": 7,
    "incertitude": 6,
    "non-linéarité": 5,
    "non linearite": 5,
    "relation de pareto": 5,
    "règles métier": 5,
    "regles metier": 5,
    "règles du marché": 5,
    "regles du marche": 5,
    "espace vectoriel": 5,
    "semi-conducteurs": 5,
    "marché des semi-conducteurs": 4,
    "marche des semi-conducteurs": 4,
    "must": 4,
    "logique floue": 6,
    "opinion mining": 5,
    "raisonnement approximatif": 5,
    "théorie de dempster-shafer": 4,
    "theorie de dempster-shafer": 4,
    "sous-ajustement": 3,
    "mise à l’échelle": 5,
    "scalabilité": 5,
    "didl": 5,
    "distributed intelligence based deep learning": 4,
    "must string navigator": 5,
    "string navigator": 4,
}

def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()

def strip_accents(s):
    table = str.maketrans({
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "à": "a", "â": "a", "ä": "a",
        "î": "i", "ï": "i",
        "ô": "o", "ö": "o",
        "ù": "u", "û": "u", "ü": "u",
        "ç": "c",
        "’": "'",
        "–": "-",
        "—": "-",
        "œ": "oe",
        "\n": " ",
        "\u00a0": " ",
    })
    return s.translate(table)

def key(s):
    return strip_accents(norm(s).lower())

remove_keys = {key(x) for x in REMOVE_EXACT}
fix_keys = {key(k): v for k, v in FIX_LABELS.items()}
cap_keys = {key(k): v for k, v in TEXT_CAPS.items()}

def dedup_entities(entities):
    seen = set()
    out = []
    for ent in entities:
        sig = (
            ent.get("label"),
            key(ent.get("text", "")),
            ent.get("start"),
            ent.get("end"),
        )
        if sig in seen:
            continue
        seen.add(sig)
        out.append(ent)
    return out

def remove_nested_entities(entities):
    entities = sorted(
        entities,
        key=lambda e: (
            e.get("start", 0),
            -(e.get("end", 0) - e.get("start", 0))
        )
    )

    final = []
    for ent in entities:
        s = ent.get("start", 0)
        e = ent.get("end", 0)
        label = ent.get("label", "")
        nested = False

        for kept in final:
            ks = kept.get("start", 0)
            ke = kept.get("end", 0)
            klabel = kept.get("label", "")

            if ks <= s and e <= ke:
                if label == klabel:
                    nested = True
                    break

                if klabel in {
                    "OBJECTIF_RD",
                    "RESULTAT_RD",
                    "VERROU_TECH",
                    "METHODE_RD",
                    "TECHNOLOGIE_RD",
                    "DOMAINE_RD",
                    "COMPOSANT_TECHNIQUE",
                    "MATERIAU_SPECIFIQUE",
                }:
                    nested = True
                    break

        if not nested:
            final.append(ent)

    return final

with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

before = 0
after = 0
removed = 0
fixed = 0
removed_caps = 0
removed_nested = 0

seen_text = defaultdict(int)

for item in data:
    new_entities = []

    for ent in item.get("entities", []):
        before += 1

        text = norm(ent.get("text", ""))
        k = key(text)

        if not text:
            removed += 1
            continue

        # Supprimer faux EM uniquement si minuscule exact
        if text == "em":
            removed += 1
            continue

        if k in remove_keys:
            removed += 1
            continue

        if k in fix_keys:
            new_label = fix_keys[k]
            if ent.get("label") != new_label:
                ent["label"] = new_label
                ent["status"] = "patch_balanced"
                fixed += 1

        if k in cap_keys:
            if seen_text[k] >= cap_keys[k]:
                removed_caps += 1
                continue
            seen_text[k] += 1

        ent["text"] = text
        new_entities.append(ent)

    before_nested = len(new_entities)
    new_entities = dedup_entities(new_entities)
    new_entities = remove_nested_entities(new_entities)
    removed_nested += before_nested - len(new_entities)

    item["entities"] = new_entities
    item["annotation_status"] = "clean_balanced_patched"
    item["project_tax_type"] = "CIR"
    item["use_for_cir_training"] = True

    after += len(new_entities)

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

counts = defaultdict(int)
for item in data:
    for ent in item.get("entities", []):
        counts[ent["label"]] += 1

print("✅ Patch projet 28 terminé")
print("✅ Projet 28 = CIR, utilisable pour dataset CIR après validation")
print(f"Avant          : {before}")
print(f"Après          : {after}")
print(f"Removed clean  : {removed}")
print(f"Removed caps   : {removed_caps}")
print(f"Removed nested : {removed_nested}")
print(f"Fixed labels   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")