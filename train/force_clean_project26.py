from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_26_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_26_\annotations\ner_candidates_clean_balanced.json")

REMOVE_EXACT = {
    # faux ajouts causés par regex sans word boundary
    "act",
    "rag",

    # auteurs / références scientifiques restés comme organismes
    "Bobrow & Whalen",
    "Lesser & Storck",
    "Tiwana",
    "Resatsch & Faisst",

    # termes trop génériques
    "profils",
    "génération libre",
    "generation libre",
    "résultats",
    "resultats",
    "verrous scientifiques et technologiques",

    # faux organismes / génériques
    "MBSE",
    "programme KM",
    "organisation",
    "entreprise",
    "sociétés",
    "societes",
    "clients industriels",
    "groupes de discussion",
}

FIX_LABELS = {
    # technologies
    "LLM": "TECHNOLOGIE_RD",
    "LLMs": "TECHNOLOGIE_RD",
    "RAG": "TECHNOLOGIE_RD",
    "Retrieval-Augmented Generation": "TECHNOLOGIE_RD",
    "Graph-RAG": "TECHNOLOGIE_RD",
    "embeddings sémantiques": "TECHNOLOGIE_RD",
    "embeddings semantiques": "TECHNOLOGIE_RD",
    "bases vectorielles": "TECHNOLOGIE_RD",
    "base de données vectorielle": "TECHNOLOGIE_RD",
    "base de donnees vectorielle": "TECHNOLOGIE_RD",
    "recherche sémantique": "TECHNOLOGIE_RD",
    "recherche semantique": "TECHNOLOGIE_RD",
    "architecture ASK": "TECHNOLOGIE_RD",
    "architecture multi-agents": "TECHNOLOGIE_RD",
    "Framework MessageTree": "TECHNOLOGIE_RD",
    "MessageTree": "TECHNOLOGIE_RD",
    "KnowNet": "TECHNOLOGIE_RD",
    "Knownet": "TECHNOLOGIE_RD",
    "Knowledge Graphs": "TECHNOLOGIE_RD",
    "graphes de dépendances": "TECHNOLOGIE_RD",
    "graphes de dependances": "TECHNOLOGIE_RD",
    "MindMap": "TECHNOLOGIE_RD",
    "mindmap": "TECHNOLOGIE_RD",
    "Topic-BERT": "TECHNOLOGIE_RD",
    "BERT-CNN": "TECHNOLOGIE_RD",
    "RoBERTa": "TECHNOLOGIE_RD",
    "DeepSeek OCR": "TECHNOLOGIE_RD",
    "GenFlowchart": "TECHNOLOGIE_RD",
    "DSPy": "TECHNOLOGIE_RD",
    "RBAC": "TECHNOLOGIE_RD",
    "PDF": "TECHNOLOGIE_RD",
    "CDF": "TECHNOLOGIE_RD",
    "features probabilistes": "TECHNOLOGIE_RD",
    "MATLAB": "TECHNOLOGIE_RD",
    "Simulink": "TECHNOLOGIE_RD",
    "Simscape": "TECHNOLOGIE_RD",
    "Stateflow": "TECHNOLOGIE_RD",
    "Microsoft Teams": "TECHNOLOGIE_RD",
    "Teams": "TECHNOLOGIE_RD",
    "Slack": "TECHNOLOGIE_RD",
    "Reddit": "TECHNOLOGIE_RD",

    # matériaux / données
    "corpus Reddit": "MATERIAU_SPECIFIQUE",
    "dataset": "MATERIAU_SPECIFIQUE",
    "datasets": "MATERIAU_SPECIFIQUE",
    "données réelles": "MATERIAU_SPECIFIQUE",
    "donnees reelles": "MATERIAU_SPECIFIQUE",
    "données labellisées internes": "MATERIAU_SPECIFIQUE",
    "donnees labellisees internes": "MATERIAU_SPECIFIQUE",
    "discussions générées": "MATERIAU_SPECIFIQUE",
    "discussions generees": "MATERIAU_SPECIFIQUE",
    "rapports de mission": "MATERIAU_SPECIFIQUE",
    "fiches de mission": "MATERIAU_SPECIFIQUE",
    "documents projets": "MATERIAU_SPECIFIQUE",

    # composants
    "KBA": "COMPOSANT_TECHNIQUE",
    "ACT": "COMPOSANT_TECHNIQUE",
    "KDIR": "COMPOSANT_TECHNIQUE",
    "KM4CS": "COMPOSANT_TECHNIQUE",
    "KAP": "COMPOSANT_TECHNIQUE",
    "KAP-I": "COMPOSANT_TECHNIQUE",
    "KAP-M": "COMPOSANT_TECHNIQUE",
    "Knowledge Broker": "COMPOSANT_TECHNIQUE",
    "Knowledge Broker Agent": "COMPOSANT_TECHNIQUE",
    "Knowledge Checker": "COMPOSANT_TECHNIQUE",
    "Knowledge Augmentation Platform": "COMPOSANT_TECHNIQUE",
    "Assistant": "COMPOSANT_TECHNIQUE",
    "Scribe": "COMPOSANT_TECHNIQUE",
    "chatbot": "COMPOSANT_TECHNIQUE",
    "interface chatbot": "COMPOSANT_TECHNIQUE",
    "KMS": "COMPOSANT_TECHNIQUE",

    # méthodes
    "Human-in-the-Loop": "METHODE_RD",
    "Humain-in-the-loop": "METHODE_RD",
    "HIL": "METHODE_RD",
    "validation hybride": "METHODE_RD",
    "Validation hybride": "METHODE_RD",
    "validation humaine systématique": "METHODE_RD",
    "Validation humaine systématique": "METHODE_RD",
    "validation expérimentale": "METHODE_RD",
    "Validation expérimentale": "METHODE_RD",
    "démarche expérimentale": "METHODE_RD",
    "Démarche expérimentale": "METHODE_RD",
    "architecture expérimentale intégrée": "METHODE_RD",
    "structuration des prompts": "METHODE_RD",
    "few-shot prompting": "METHODE_RD",
    "scoring algorithmique": "METHODE_RD",
    "scoring vectoriel": "METHODE_RD",
    "Analyse de similarité sémantique": "METHODE_RD",
    "analyse de similarité sémantique": "METHODE_RD",
    "chunking": "METHODE_RD",
    "anonymisation": "METHODE_RD",
    "clusterisation": "METHODE_RD",
    "Modélisation log-normale": "METHODE_RD",
    "modélisation log-normale": "METHODE_RD",
    "génération de données synthétiques": "METHODE_RD",
    "génération conversationnelle": "METHODE_RD",
    "Structuration contextuelle": "METHODE_RD",
    "Structuration temporelle": "METHODE_RD",
    "Normalisation avec métadonnées": "METHODE_RD",
    "protocoles expérimentaux": "METHODE_RD",

    # domaines
    "Knowledge Management": "DOMAINE_RD",
    "gestion des connaissances": "DOMAINE_RD",
    "Gestion des connaissances": "DOMAINE_RD",
    "Gestion systémique des connaissances": "DOMAINE_RD",
    "capitalisation des connaissances": "DOMAINE_RD",
    "Capitalisation des connaissances": "DOMAINE_RD",
    "savoir tacite": "DOMAINE_RD",
    "Savoir Tacite": "DOMAINE_RD",
    "savoirs tacites": "DOMAINE_RD",
    "connaissances tacites": "DOMAINE_RD",
    "connaissances implicites": "DOMAINE_RD",
    "communautés de pratique": "DOMAINE_RD",
    "systèmes complexes": "DOMAINE_RD",
    "intelligence artificielle": "DOMAINE_RD",
    "Intelligence artificielle": "DOMAINE_RD",
    "IA générative": "TECHNOLOGIE_RD",
    "Natural Language Processing": "DOMAINE_RD",
    "NLP": "DOMAINE_RD",

    # verrous
    "sécurisation des données sensibles": "VERROU_TECH",
    "biais algorithmiques": "VERROU_TECH",
    "scalabilité": "VERROU_TECH",
    "surapprentissage": "VERROU_TECH",
    "overfitting": "VERROU_TECH",
    "annotation manuelle massive": "VERROU_TECH",
    "heuristiques limitées": "VERROU_TECH",
    "coût de calcul élevé": "VERROU_TECH",
    "cadre unifié": "VERROU_TECH",
    "données visuelles hétérogènes": "VERROU_TECH",
    "manque d’intégration sémantique globale": "VERROU_TECH",
    "détection contextuelle": "VERROU_TECH",
    "évaluation contextuelle": "VERROU_TECH",
    "dynamique temporelle des savoirs": "VERROU_TECH",
    "échanges informels non structurés": "VERROU_TECH",
    "absence de signaux explicites": "VERROU_TECH",
    "forte dépendance au contexte": "VERROU_TECH",
    "perte de performance": "VERROU_TECH",
    "décalage de distribution": "VERROU_TECH",
    "écarts de distribution": "VERROU_TECH",
    "hallucinations": "VERROU_TECH",
    "dilution de la connaissance": "VERROU_TECH",
    "incertitude informationnelle": "VERROU_TECH",
    "boucles répétitives": "VERROU_TECH",
    "instabilité des critères": "VERROU_TECH",
    "hétérogénéité des données": "VERROU_TECH",
    "connaissances en conflit": "VERROU_TECH",

    # objectifs
    "activer les connaissances": "OBJECTIF_RD",
    "valoriser des savoirs": "OBJECTIF_RD",
    "optimiser la captation des connaissances": "OBJECTIF_RD",
    "structurer les connaissances existantes et requises": "OBJECTIF_RD",
    "identifier des opportunités de partage": "OBJECTIF_RD",
    "Identifier les sujets de discussion pertinents": "OBJECTIF_RD",
    "détecter automatiquement les défis scientifiques": "OBJECTIF_RD",
    "identifier le caractère novateur des projets": "OBJECTIF_RD",
    "centraliser l’expertise": "OBJECTIF_RD",
    "fournir un support décisionnel": "OBJECTIF_RD",
    "garantir la qualité des données": "OBJECTIF_RD",
    "restituer les connaissances": "OBJECTIF_RD",

    # résultats
    "faisabilité": "RESULTAT_RD",
    "robustesse du pipeline": "RESULTAT_RD",
    "taux de concordance": "RESULTAT_RD",
    "94 %": "RESULTAT_RD",
    "1 % de faux positifs": "RESULTAT_RD",
    "5 % de faux négatifs": "RESULTAT_RD",
    "97 % de précision": "RESULTAT_RD",
    "83 % en validation": "RESULTAT_RD",
    "chute drastique des performances": "RESULTAT_RD",
    "convergence rapide": "RESULTAT_RD",
    "stagnation de la validation": "RESULTAT_RD",
    "amélioration qualitative": "RESULTAT_RD",
    "amélioration du transfert": "RESULTAT_RD",
    "structuration des résultats": "RESULTAT_RD",

    # organismes utiles
    "SMART4": "ORGANISME",
    "groupe SMART4": "ORGANISME",
    "GLRT": "ORGANISME",
    "GLR TECHNOLOGIES": "ORGANISME",
    "GLR Technologies": "ORGANISME",
    "GROUPE LR TECHNOLOGIES": "ORGANISME",
    "ENERGEO": "ORGANISME",
    "LIBELLIO": "ORGANISME",
    "Sibylone": "ORGANISME",
    "Solent": "ORGANISME",
    "ECO STEERING": "ORGANISME",
    "ECOSTEERING": "ORGANISME",
    "INUC": "ORGANISME",
    "Institut de Recherche en Informatique de Toulouse": "ORGANISME",
    "European Conference on Knowledge Management": "ORGANISME",
}

TEXT_CAPS = {
    "gestion des connaissances": 8,
    "knowledge management": 6,
    "savoir tacite": 6,
    "savoirs tacites": 6,
    "connaissances tacites": 7,
    "connaissances implicites": 6,
    "llm": 8,
    "llms": 8,
    "rag": 6,
    "human-in-the-loop": 7,
    "hil": 4,
    "kba": 5,
    "act": 5,
    "kdir": 5,
    "km4cs": 5,
    "kap": 5,
    "reddit": 4,
    "teams": 4,
    "microsoft teams": 4,
    "embeddings sémantiques": 5,
    "bases vectorielles": 5,
    "dataset": 5,
    "datasets": 5,
    "données réelles": 5,
    "scalabilité": 4,
    "sécurisation des données sensibles": 4,
    "graphes de dépendances": 4,
    "features probabilistes": 4,
    "pdf": 3,
    "cdf": 3,
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

        # Remove lowercase false positives specifically
        if text == "act" or text == "rag":
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

print("✅ Patch projet 26 terminé")
print("✅ Projet 26 = CIR, utilisable pour dataset CIR après validation")
print(f"Avant          : {before}")
print(f"Après          : {after}")
print(f"Removed clean  : {removed}")
print(f"Removed caps   : {removed_caps}")
print(f"Removed nested : {removed_nested}")
print(f"Fixed labels   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")