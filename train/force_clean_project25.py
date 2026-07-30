from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_25_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_25_\annotations\ner_candidates_clean_balanced.json")

REMOVE_EXACT = {
    # faux organismes / acteurs génériques
    "plateforme centrale",
    "équipes logistiques",
    "equipes logistiques",
    "clients finaux",
    "clients",
    "prestataires",
    "équipes internes",
    "equipes internes",
    "équipes terrain",
    "equipes terrain",
    "gestionnaires de parc",
    "logistique",

    # emails / signatures / organismes non utiles
    "f-r-e-d.fr",
    "f-r-e-d",
    "ennodev",
    "ENNODEV Conseil en CIR",
    "Ennodev Conseil en CIR",

    # normes / certifications trop bruitées pour ce projet CII
    "PCI-DSS",
    "ISO/IEC 27001",
    "SOC 1 TII",
    "SOC 2 T II",
    "FedRAMP",
    "RGPD",
    "FIPS 140-2",
    "NIST 800-171",
    "HIPAA/HITECH",

    # tables / concurrents dans tableau trop répétitifs si besoin
    "hiflow",
    "myexpressdriver",

    # trop générique
    "notifications",
    "documents logistiques",
    "flux opérationnels",
    "flux documentaires",
}

FIX_LABELS = {
    # technologies
    "Ezy Retail": "TECHNOLOGIE_RD",
    "EZY Retail": "TECHNOLOGIE_RD",
    "Ezy Parc": "TECHNOLOGIE_RD",
    "EZY Parc": "TECHNOLOGIE_RD",
    "Eyz Parc": "TECHNOLOGIE_RD",
    "EYZ Parc": "TECHNOLOGIE_RD",
    "ESY Retail / Parc": "TECHNOLOGIE_RD",
    "application mobile": "TECHNOLOGIE_RD",
    "interface applicative mobile": "TECHNOLOGIE_RD",
    "interface mobile": "TECHNOLOGIE_RD",
    "plateforme digitale intégrée": "TECHNOLOGIE_RD",
    "plateforme digitale integree": "TECHNOLOGIE_RD",
    "système unifié": "TECHNOLOGIE_RD",
    "systeme unifie": "TECHNOLOGIE_RD",
    "système digitalisé": "TECHNOLOGIE_RD",
    "systeme digitalise": "TECHNOLOGIE_RD",
    "module d’estimation de l’impact carbone": "TECHNOLOGIE_RD",
    "module d'estimation de l'impact carbone": "TECHNOLOGIE_RD",
    "base de données": "TECHNOLOGIE_RD",
    "base de donnees": "TECHNOLOGIE_RD",
    "OVH Cloud": "TECHNOLOGIE_RD",
    "AWS": "TECHNOLOGIE_RD",
    "NAS QNAP": "TECHNOLOGIE_RD",
    "TLS 1.3": "TECHNOLOGIE_RD",
    "SHA256": "TECHNOLOGIE_RD",
    "Let’s Encrypt": "TECHNOLOGIE_RD",

    # domaines
    "logistique automobile": "DOMAINE_RD",
    "convoyage": "DOMAINE_RD",
    "transport automobile": "DOMAINE_RD",
    "gestion de parc": "DOMAINE_RD",
    "gestion de flotte": "DOMAINE_RD",
    "gestion de parc automobile": "DOMAINE_RD",
    "impact carbone": "DOMAINE_RD",
    "analyse carbone": "DOMAINE_RD",
    "démarche environnementale": "DOMAINE_RD",
    "demarche environnementale": "DOMAINE_RD",
    "pilotage environnemental": "DOMAINE_RD",

    # méthodes
    "analyse comparative": "METHODE_RD",
    "analyse et calcul de l’impact carbone": "METHODE_RD",
    "analyse et calcul de l'impact carbone": "METHODE_RD",
    "calcul de l’impact carbone": "METHODE_RD",
    "calcul de l'impact carbone": "METHODE_RD",
    "démarche de conception itérative": "METHODE_RD",
    "demarche de conception iterative": "METHODE_RD",
    "conception itérative": "METHODE_RD",
    "conception iterative": "METHODE_RD",
    "dématérialisation des documents": "METHODE_RD",
    "dematerialisation des documents": "METHODE_RD",
    "automatisation des flux": "METHODE_RD",
    "automatisation des processus": "METHODE_RD",
    "automatisation documentaire": "METHODE_RD",
    "remontée documentaire automatisée": "METHODE_RD",
    "remontee documentaire automatisee": "METHODE_RD",
    "validation instantanée des documents": "METHODE_RD",
    "validation instantanee des documents": "METHODE_RD",
    "archivage automatisé": "METHODE_RD",
    "archivage automatise": "METHODE_RD",
    "structuration des données": "METHODE_RD",
    "structuration des donnees": "METHODE_RD",
    "analyse de données": "METHODE_RD",
    "analyse de donnees": "METHODE_RD",
    "interopérabilité entre systèmes": "METHODE_RD",
    "interoperabilite entre systemes": "METHODE_RD",

    # verrous
    "processus hétérogènes": "VERROU_TECH",
    "processus heterogenes": "VERROU_TECH",
    "risques d’erreurs": "VERROU_TECH",
    "risques d'erreurs": "VERROU_TECH",
    "pertes d’information": "VERROU_TECH",
    "pertes d'information": "VERROU_TECH",
    "manque de visibilité en temps réel": "VERROU_TECH",
    "manque de visibilite en temps reel": "VERROU_TECH",
    "processus historiquement dissociés": "VERROU_TECH",
    "processus historiquement dissocies": "VERROU_TECH",
    "ruptures d’information": "VERROU_TECH",
    "ruptures d'information": "VERROU_TECH",
    "ruptures fonctionnelles": "VERROU_TECH",
    "limitations sur la gestion fine des parcs": "VERROU_TECH",
    "couverture fonctionnelle limitée": "VERROU_TECH",
    "couverture fonctionnelle limitee": "VERROU_TECH",
    "absence de gestion intégrée du parc": "VERROU_TECH",
    "absence de gestion integree du parc": "VERROU_TECH",
    "sécurisation des échanges": "VERROU_TECH",
    "securisation des echanges": "VERROU_TECH",

    # objectifs
    "améliorer l’efficacité opérationnelle": "OBJECTIF_RD",
    "ameliorer l'efficacite operationnelle": "OBJECTIF_RD",
    "améliorer la traçabilité": "OBJECTIF_RD",
    "ameliorer la tracabilite": "OBJECTIF_RD",
    "assurer la gestion complète": "OBJECTIF_RD",
    "assurer la gestion complete": "OBJECTIF_RD",
    "permettre le suivi en temps réel": "OBJECTIF_RD",
    "permettre le suivi en temps reel": "OBJECTIF_RD",
    "centraliser la gestion des stocks": "OBJECTIF_RD",
    "automatiser la collecte": "OBJECTIF_RD",
    "améliorer la coordination": "OBJECTIF_RD",
    "ameliorer la coordination": "OBJECTIF_RD",
    "réduire l’utilisation du papier": "OBJECTIF_RD",
    "reduire l'utilisation du papier": "OBJECTIF_RD",
    "réduire les erreurs humaines": "OBJECTIF_RD",
    "reduire les erreurs humaines": "OBJECTIF_RD",
    "optimiser le pilotage global": "OBJECTIF_RD",

    # résultats
    "continuité opérationnelle": "RESULTAT_RD",
    "continuite operationnelle": "RESULTAT_RD",
    "continuité informationnelle": "RESULTAT_RD",
    "continuite informationnelle": "RESULTAT_RD",
    "fiabilisation des données": "RESULTAT_RD",
    "fiabilisation des donnees": "RESULTAT_RD",
    "structuration des processus": "RESULTAT_RD",
    "mise en cohérence des processus": "RESULTAT_RD",
    "mise en coherence des processus": "RESULTAT_RD",
    "amélioration du pilotage global": "RESULTAT_RD",
    "amelioration du pilotage global": "RESULTAT_RD",
    "vision consolidée": "RESULTAT_RD",
    "vision consolidee": "RESULTAT_RD",

    # composants fonctionnels
    "gestion des demandes de transport": "COMPOSANT_TECHNIQUE",
    "gestion des missions convoyeur": "COMPOSANT_TECHNIQUE",
    "suivi opérationnel en temps réel": "COMPOSANT_TECHNIQUE",
    "suivi operationnel en temps reel": "COMPOSANT_TECHNIQUE",
    "état des lieux digitalisé": "COMPOSANT_TECHNIQUE",
    "etat des lieux digitalise": "COMPOSANT_TECHNIQUE",
    "communication client SMS": "COMPOSANT_TECHNIQUE",
    "gestion des statuts véhicule": "COMPOSANT_TECHNIQUE",
    "gestion des statuts vehicule": "COMPOSANT_TECHNIQUE",
    "gestion des entrées": "COMPOSANT_TECHNIQUE",
    "gestion des entrees": "COMPOSANT_TECHNIQUE",
    "gestion des sorties": "COMPOSANT_TECHNIQUE",
    "demandes de préparation véhicule": "COMPOSANT_TECHNIQUE",
    "demandes de preparation vehicule": "COMPOSANT_TECHNIQUE",
    "suivi documentaire": "COMPOSANT_TECHNIQUE",
    "localisation précise du véhicule": "COMPOSANT_TECHNIQUE",
    "localisation precise du vehicule": "COMPOSANT_TECHNIQUE",

    # organismes utiles
    "FRED": "ORGANISME",
    "F.R.E.D": "ORGANISME",
    "FRANCE REAL ESTATE DEVELOPPEMENT": "ORGANISME",
    "Hiflow": "ORGANISME",
    "Driiveme": "ORGANISME",
    "OTOQI": "ORGANISME",
    "Bring My Car": "ORGANISME",
    "Pop Valet": "ORGANISME",
    "My Express Driver": "ORGANISME",
}

TEXT_CAPS = {
    "convoyage": 7,
    "gestion de parc": 6,
    "gestion de flotte": 5,
    "logistique automobile": 5,
    "impact carbone": 6,
    "analyse carbone": 4,
    "ezy retail": 6,
    "ezy parc": 5,
    "eyz parc": 5,
    "plateforme digitale intégrée": 4,
    "plateforme digitale integree": 4,
    "application mobile": 4,
    "interface mobile": 4,
    "démarche environnementale": 4,
    "demarche environnementale": 4,
    "analyse et calcul de l’impact carbone": 4,
    "calcul de l’impact carbone": 4,
    "automatisation des flux": 4,
    "automatisation des processus": 4,
    "dématérialisation des documents": 4,
    "dematerialisation des documents": 4,
    "interopérabilité entre systèmes": 4,
    "interoperabilite entre systemes": 4,
    "processus hétérogènes": 4,
    "risques d’erreurs": 4,
    "pertes d’information": 4,
    "ruptures d’information": 4,
    "continuité informationnelle": 4,
    "fiabilisation des données": 4,
    "flux opérationnels": 0,
    "flux documentaires": 0,
    "documents logistiques": 0,
    "notifications": 0,
    "fred": 5,
    "f.r.e.d": 4,
    "hiflow": 3,
    "driiveme": 3,
    "otoqi": 3,
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
    item["project_tax_type"] = "CII"
    item["use_for_cir_training"] = False
    after += len(new_entities)

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

counts = defaultdict(int)
for item in data:
    for ent in item.get("entities", []):
        counts[ent["label"]] += 1

print("✅ Patch projet 25 terminé")
print("⚠️ Projet 25 = CII, garder séparé du dataset CIR")
print(f"Avant          : {before}")
print(f"Après          : {after}")
print(f"Removed clean  : {removed}")
print(f"Removed caps   : {removed_caps}")
print(f"Removed nested : {removed_nested}")
print(f"Fixed labels   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")