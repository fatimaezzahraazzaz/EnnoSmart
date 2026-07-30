from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_29_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_29_\annotations\ner_candidates_clean_balanced.json")

REMOVE_EXACT = {
    "Recherche et Développement d’une architecture numérique évolutive",
    "Recherche et Developpement d’une architecture numerique evolutive",
    "Démarche expérimentale",
    "développement expérimental",
    "recherche et développement",
    "recherche et developpement",
    "neutralité carbone",
    "objectifs européens de neutralité carbone",
}

FIX_LABELS = {
    # objectifs
    "garantir la disponibilité": "OBJECTIF_RD",
    "garantir la disponibilite": "OBJECTIF_RD",
    "garantir la sécurité": "OBJECTIF_RD",
    "garantir la securite": "OBJECTIF_RD",
    "garantir l’interopérabilité": "OBJECTIF_RD",
    "garantir l'interoperabilite": "OBJECTIF_RD",
    "réduire les indisponibilités": "OBJECTIF_RD",
    "reduire les indisponibilites": "OBJECTIF_RD",
    "gérer des volumes massifs de données": "OBJECTIF_RD",
    "gerer des volumes massifs de donnees": "OBJECTIF_RD",
    "assurer une communication en temps réel": "OBJECTIF_RD",
    "assurer une communication temps reel": "OBJECTIF_RD",
    "développer une architecture numérique avancée": "OBJECTIF_RD",
    "developper une architecture numerique avancee": "OBJECTIF_RD",
    "définir et valider une plateforme technique centralisée": "OBJECTIF_RD",
    "definir et valider une plateforme technique centralisee": "OBJECTIF_RD",
    "anticiper les pannes": "OBJECTIF_RD",
    "accroitre la performance globale": "OBJECTIF_RD",
    "maintenir la cohérence": "OBJECTIF_RD",
    "maintenir la coherence": "OBJECTIF_RD",
    "optimisation continue": "OBJECTIF_RD",

    # méthodes
    "tests de montée en charge": "METHODE_RD",
    "tests de montee en charge": "METHODE_RD",
    "tests intensifs": "METHODE_RD",
    "tests de performance": "METHODE_RD",
    "tests de vulnérabilité": "METHODE_RD",
    "tests de vulnerabilite": "METHODE_RD",
    "audits de configuration": "METHODE_RD",
    "conditions réseau dégradées": "METHODE_RD",
    "conditions reseau degradees": "METHODE_RD",
    "scénarios de basculement réseau": "METHODE_RD",
    "scenarios de basculement reseau": "METHODE_RD",
    "banc d’essai expérimental": "METHODE_RD",
    "banc d'essai experimental": "METHODE_RD",
    "résultats expérimentaux": "RESULTAT_RD",
    "resultats experimentaux": "RESULTAT_RD",

    # composants
    "systèmes de monitoring": "COMPOSANT_TECHNIQUE",
    "systemes de monitoring": "COMPOSANT_TECHNIQUE",
    "couche de sécurité": "COMPOSANT_TECHNIQUE",
    "couche de securite": "COMPOSANT_TECHNIQUE",
    "plateforme centrale": "COMPOSANT_TECHNIQUE",
    "plateforme de supervision": "COMPOSANT_TECHNIQUE",
    "plateforme technique centralisée": "COMPOSANT_TECHNIQUE",
    "plateforme technique centralisee": "COMPOSANT_TECHNIQUE",
    "bases de données": "COMPOSANT_TECHNIQUE",
    "bases de donnees": "COMPOSANT_TECHNIQUE",
    "files de messages": "COMPOSANT_TECHNIQUE",
    "gateway intermédiaire": "COMPOSANT_TECHNIQUE",
    "gateway intermediaire": "COMPOSANT_TECHNIQUE",
    "mécanismes de heartbeat": "COMPOSANT_TECHNIQUE",
    "mecanismes de heartbeat": "COMPOSANT_TECHNIQUE",
    "buffers temporaires": "COMPOSANT_TECHNIQUE",
    "couche capteurs": "COMPOSANT_TECHNIQUE",
    "couche réseau": "COMPOSANT_TECHNIQUE",
    "couche reseau": "COMPOSANT_TECHNIQUE",
    "couche cloud": "COMPOSANT_TECHNIQUE",
    "couche middleware MQTT": "COMPOSANT_TECHNIQUE",

    # technologies
    "ORIOS": "TECHNOLOGIE_RD",
    "architecture numérique évolutive": "TECHNOLOGIE_RD",
    "architecture numerique evolutive": "TECHNOLOGIE_RD",
    "architecture distribuée": "TECHNOLOGIE_RD",
    "architecture distribuee": "TECHNOLOGIE_RD",
    "architecture distribuée orientée événements": "TECHNOLOGIE_RD",
    "architecture distribuee orientee evenements": "TECHNOLOGIE_RD",
    "architecture micro-services": "TECHNOLOGIE_RD",
    "micro-services": "TECHNOLOGIE_RD",
    "RabbitMQ": "TECHNOLOGIE_RD",
    "WebSocket": "TECHNOLOGIE_RD",
    "OCPP": "TECHNOLOGIE_RD",
    "OCPI": "TECHNOLOGIE_RD",
    "MQTT": "TECHNOLOGIE_RD",
    "Kubernetes": "TECHNOLOGIE_RD",
    "Docker-Compose": "TECHNOLOGIE_RD",
    "Spring Boot": "TECHNOLOGIE_RD",
    "Dojot": "TECHNOLOGIE_RD",
    "Blynk": "TECHNOLOGIE_RD",
    "SIMA": "TECHNOLOGIE_RD",
    "IEC 61850-90-8": "TECHNOLOGIE_RD",
    "EVCC": "TECHNOLOGIE_RD",
    "SECC": "TECHNOLOGIE_RD",
    "V2G": "TECHNOLOGIE_RD",
    "V2H": "TECHNOLOGIE_RD",

    # verrous
    "communication en temps réel": "VERROU_TECH",
    "communication temps réel": "VERROU_TECH",
    "communication bidirectionnelle": "VERROU_TECH",
    "interopérabilité multi-constructeurs": "VERROU_TECH",
    "interoperabilite multi-constructeurs": "VERROU_TECH",
    "interopérabilité CPO/EMSP": "VERROU_TECH",
    "interoperabilite CPO/EMSP": "VERROU_TECH",
    "flux massifs de données": "VERROU_TECH",
    "flux massifs de donnees": "VERROU_TECH",
    "volumes massifs de données": "VERROU_TECH",
    "volumes massifs de donnees": "VERROU_TECH",
    "transactions critiques": "VERROU_TECH",
    "sources hétérogènes": "VERROU_TECH",
    "sources heterogenes": "VERROU_TECH",
    "bornes hétérogènes": "VERROU_TECH",
    "bornes heterogenes": "VERROU_TECH",
    "équipements hétérogènes": "VERROU_TECH",
    "equipements heterogenes": "VERROU_TECH",
    "scalabilité": "VERROU_TECH",
    "scalabilite": "VERROU_TECH",
    "montée en charge": "VERROU_TECH",
    "montee en charge": "VERROU_TECH",
    "latence": "VERROU_TECH",
    "résilience": "VERROU_TECH",
    "resilience": "VERROU_TECH",
    "cybersécurité": "VERROU_TECH",
    "cybersecurite": "VERROU_TECH",
    "cybersécurité industrielle": "VERROU_TECH",
    "cybersecurite industrielle": "VERROU_TECH",
    "disponibilité": "VERROU_TECH",
    "disponibilite": "VERROU_TECH",

    # résultats
    "99,5 %": "RESULTAT_RD",
    "disponibilité opérationnelle supérieure à 99,5 %": "RESULTAT_RD",
    "disponibilite operationnelle superieure a 99,5 %": "RESULTAT_RD",
    "temps de réponse critiques inférieurs à une seconde": "RESULTAT_RD",
    "temps de reponse critiques inferieurs a une seconde": "RESULTAT_RD",
    "détection des incidents en moins de cinq secondes": "RESULTAT_RD",
    "detection des incidents en moins de cinq secondes": "RESULTAT_RD",
    "absence de perte de données": "RESULTAT_RD",
    "absence de perte de donnees": "RESULTAT_RD",
    "stabilité des échanges": "RESULTAT_RD",
    "stabilite des echanges": "RESULTAT_RD",
    "robustesse globale": "RESULTAT_RD",
    "saturation mémoire proche de 96 %": "RESULTAT_RD",
    "saturation memoire proche de 96 %": "RESULTAT_RD",
    "latence moyenne": "RESULTAT_RD",

    # domaines
    "Smartgrid": "DOMAINE_RD",
    "mobilité électrique": "DOMAINE_RD",
    "mobilite electrique": "DOMAINE_RD",
    "véhicules électriques": "DOMAINE_RD",
    "vehicules electriques": "DOMAINE_RD",
    "IRVE": "DOMAINE_RD",
    "infrastructures de recharge pour véhicules électriques": "DOMAINE_RD",
    "infrastructures de recharge pour vehicules electriques": "DOMAINE_RD",
    "réseau de bornes de recharge": "DOMAINE_RD",
    "reseau de bornes de recharge": "DOMAINE_RD",
    "réseau national de recharge": "DOMAINE_RD",
    "reseau national de recharge": "DOMAINE_RD",

    # équipements
    "équipements terrain": "EQUIPEMENT_RD",
    "equipements terrain": "EQUIPEMENT_RD",
    "stations de recharge": "EQUIPEMENT_RD",
    "EVSE": "EQUIPEMENT_RD",
    "Raspberry Pi": "EQUIPEMENT_RD",
    "MacBook Pro": "EQUIPEMENT_RD",
    "SONOFF": "EQUIPEMENT_RD",

    # data
    "données techniques": "MATERIAU_SPECIFIQUE",
    "donnees techniques": "MATERIAU_SPECIFIQUE",
    "données de fonctionnement": "MATERIAU_SPECIFIQUE",
    "donnees de fonctionnement": "MATERIAU_SPECIFIQUE",
    "métriques": "MATERIAU_SPECIFIQUE",
    "metriques": "MATERIAU_SPECIFIQUE",
    "logs": "MATERIAU_SPECIFIQUE",
    "transactions": "MATERIAU_SPECIFIQUE",
    "sessions de recharge": "MATERIAU_SPECIFIQUE",
    "événements critiques": "MATERIAU_SPECIFIQUE",
    "evenements critiques": "MATERIAU_SPECIFIQUE",
}

TEXT_CAPS = {
    "bornes de recharge": 7,
    "véhicules électriques": 5,
    "vehicules electriques": 5,
    "supervision en temps réel": 7,
    "supervision temps réel": 7,
    "architecture distribuée": 6,
    "architecture distribuee": 6,
    "rabbitmq": 6,
    "websocket": 6,
    "ocpp": 6,
    "ocpi": 6,
    "mqtt": 6,
    "kubernetes": 5,
    "dojot": 4,
    "blynk": 3,
    "scalabilité": 5,
    "scalabilite": 5,
    "résilience": 5,
    "resilience": 5,
    "latence": 5,
    "disponibilité": 4,
    "disponibilite": 4,
    "communication en temps réel": 5,
    "communication bidirectionnelle": 5,
    "flux massifs de données": 5,
    "flux massifs de donnees": 5,
    "transactions critiques": 5,
    "maintenance prédictive": 5,
    "maintenance predictive": 5,
    "monitoring": 5,
    "centralisation des logs": 4,
    "détection d’anomalies": 5,
    "detection d'anomalies": 5,
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
                    "EQUIPEMENT_RD",
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

print("✅ Patch projet 29 terminé")
print("✅ Projet 29 = CIR, utilisable pour dataset CIR après validation")
print(f"Avant          : {before}")
print(f"Après          : {after}")
print(f"Removed clean  : {removed}")
print(f"Removed caps   : {removed_caps}")
print(f"Removed nested : {removed_nested}")
print(f"Fixed labels   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")