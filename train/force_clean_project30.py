from pathlib import Path
import json
import re
from collections import defaultdict

INPUT = Path(r"C:\EnnoSmart\projects\projet_30_\annotations\ner_candidates_clean_balanced.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_30_\annotations\ner_candidates_clean_balanced.json")

REMOVE_EXACT = {
    # faux positifs causés par recherche sans word boundary
    "conv",
    "Conv",

    # titres / formulations trop génériques
    "Les réseaux de neurones profonds",
    "verrous scientifiques",
    "verrous scientifiques et technologiques",
    "verrous techniques et technologiques",
    "démarche expérimentale",
    "Démarche expérimentale",

    # génériques
    "Framework",
    "résultats",
    "resultats",
    "expériences",
    "experiences",
    "objectif",
    "ambition",
    "cible",
}

FIX_LABELS = {
    # domaines
    "systèmes embarqués": "DOMAINE_RD",
    "systemes embarques": "DOMAINE_RD",
    "systèmes embarqués critiques": "DOMAINE_RD",
    "systemes embarques critiques": "DOMAINE_RD",
    "architectures embarquées": "DOMAINE_RD",
    "architectures embarquees": "DOMAINE_RD",
    "architectures hétérogènes": "DOMAINE_RD",
    "architectures heterogenes": "DOMAINE_RD",
    "intelligence artificielle embarquée": "DOMAINE_RD",
    "intelligence artificielle embarquee": "DOMAINE_RD",
    "IA embarquée": "DOMAINE_RD",
    "IA embarquee": "DOMAINE_RD",
    "Deep Learning": "DOMAINE_RD",
    "Deep Learning embarqué": "DOMAINE_RD",
    "Deep Learning embarque": "DOMAINE_RD",
    "réseaux de neurones": "DOMAINE_RD",
    "reseaux de neurones": "DOMAINE_RD",
    "réseaux de neurones profonds": "DOMAINE_RD",
    "reseaux de neurones profonds": "DOMAINE_RD",
    "inférence embarquée": "DOMAINE_RD",
    "inference embarquee": "DOMAINE_RD",
    "apprentissage distribué": "DOMAINE_RD",
    "apprentissage distribue": "DOMAINE_RD",
    "apprentissage fédéré": "DOMAINE_RD",
    "apprentissage federe": "DOMAINE_RD",

    # technologies
    "AIDGE": "TECHNOLOGIE_RD",
    "Framework AIDGE": "TECHNOLOGIE_RD",
    "Framework d’IA embarquée": "TECHNOLOGIE_RD",
    "Framework d'IA embarquee": "TECHNOLOGIE_RD",
    "Framework modulable et extensible": "TECHNOLOGIE_RD",
    "architecture modulaire": "TECHNOLOGIE_RD",
    "architecture logicielle modulaire": "TECHNOLOGIE_RD",
    "plateforme modulaire open-source": "TECHNOLOGIE_RD",
    "pipeline unifié": "TECHNOLOGIE_RD",
    "pipeline unifie": "TECHNOLOGIE_RD",
    "génération de code embarqué": "TECHNOLOGIE_RD",
    "generation de code embarque": "TECHNOLOGIE_RD",
    "AIDGE Export C++": "TECHNOLOGIE_RD",
    "ONNX": "TECHNOLOGIE_RD",
    "CUDA": "TECHNOLOGIE_RD",
    "TensorRT": "TECHNOLOGIE_RD",
    "C++": "TECHNOLOGIE_RD",
    "PyTorch": "TECHNOLOGIE_RD",
    "TensorFlow": "TECHNOLOGIE_RD",
    "TFLite": "TECHNOLOGIE_RD",
    "TVM": "TECHNOLOGIE_RD",
    "MobileNets": "TECHNOLOGIE_RD",
    "MobileNet": "TECHNOLOGIE_RD",
    "MobileNetV2": "TECHNOLOGIE_RD",
    "MnasNet": "TECHNOLOGIE_RD",
    "NASNet": "TECHNOLOGIE_RD",
    "SqueezeNet": "TECHNOLOGIE_RD",
    "SqueezeNext": "TECHNOLOGIE_RD",
    "ResNet18": "TECHNOLOGIE_RD",
    "ResNet50": "TECHNOLOGIE_RD",
    "ResNet20": "TECHNOLOGIE_RD",
    "Inception-V3": "TECHNOLOGIE_RD",
    "FedAvg": "TECHNOLOGIE_RD",
    "FedProx": "TECHNOLOGIE_RD",

    # méthodes
    "démarche expérimentale rigoureuse": "METHODE_RD",
    "demarche experimentale rigoureuse": "METHODE_RD",
    "évaluation expérimentale": "METHODE_RD",
    "evaluation experimentale": "METHODE_RD",
    "validation expérimentale": "METHODE_RD",
    "validation experimentale": "METHODE_RD",
    "quantification": "METHODE_RD",
    "quantification entière": "METHODE_RD",
    "quantification entiere": "METHODE_RD",
    "quantification post-apprentissage": "METHODE_RD",
    "quantification multi-précision": "METHODE_RD",
    "quantification multi-precision": "METHODE_RD",
    "quantification à précision mixte": "METHODE_RD",
    "quantification a precision mixte": "METHODE_RD",
    "pruning": "METHODE_RD",
    "compression": "METHODE_RD",
    "codage entropique": "METHODE_RD",
    "Neural Architecture Search": "METHODE_RD",
    "NAS": "METHODE_RD",
    "HAWQ": "METHODE_RD",
    "LSQ": "METHODE_RD",
    "Learned Step Size Quantization": "METHODE_RD",
    "AdaRound": "METHODE_RD",
    "optimisation architecturale": "METHODE_RD",
    "optimisation dynamique": "METHODE_RD",
    "optimisation multi-cible": "METHODE_RD",
    "orchestration de l’exécution": "METHODE_RD",
    "orchestration de l'execution": "METHODE_RD",
    "orchestration des calculs": "METHODE_RD",
    "compilation multi-cibles": "METHODE_RD",
    "apprentissage fédéré distribué": "METHODE_RD",
    "apprentissage federe distribue": "METHODE_RD",
    "entraînement distribué": "METHODE_RD",
    "entrainement distribue": "METHODE_RD",
    "Federated Averaging": "METHODE_RD",
    "tests unitaires": "METHODE_RD",
    "tests d’intégration": "METHODE_RD",
    "tests d'integration": "METHODE_RD",

    # verrous
    "complexité algorithmique": "VERROU_TECH",
    "complexite algorithmique": "VERROU_TECH",
    "complexité computationnelle": "VERROU_TECH",
    "complexite computationnelle": "VERROU_TECH",
    "coût computationnel": "VERROU_TECH",
    "cout computationnel": "VERROU_TECH",
    "contraintes matérielles": "VERROU_TECH",
    "contraintes materielles": "VERROU_TECH",
    "contraintes embarquées": "VERROU_TECH",
    "contraintes embarquees": "VERROU_TECH",
    "contraintes système": "VERROU_TECH",
    "contraintes systeme": "VERROU_TECH",
    "ressources limitées": "VERROU_TECH",
    "ressources limitees": "VERROU_TECH",
    "mémoire limitée": "VERROU_TECH",
    "memoire limitee": "VERROU_TECH",
    "capacité de calcul": "VERROU_TECH",
    "capacite de calcul": "VERROU_TECH",
    "consommation énergétique": "VERROU_TECH",
    "consommation energetique": "VERROU_TECH",
    "latence": "VERROU_TECH",
    "latence stricte": "VERROU_TECH",
    "empreinte mémoire": "VERROU_TECH",
    "empreinte memoire": "VERROU_TECH",
    "taille binaire": "VERROU_TECH",
    "compacité des binaires": "VERROU_TECH",
    "compacite des binaires": "VERROU_TECH",
    "portabilité": "VERROU_TECH",
    "portabilite": "VERROU_TECH",
    "reproductibilité": "VERROU_TECH",
    "reproductibilite": "VERROU_TECH",
    "préservation de la précision": "VERROU_TECH",
    "preservation de la precision": "VERROU_TECH",
    "dégradation des performances": "VERROU_TECH",
    "degradation des performances": "VERROU_TECH",
    "hétérogénéité matérielle": "VERROU_TECH",
    "heterogeneite materielle": "VERROU_TECH",
    "hétérogénéité des données": "VERROU_TECH",
    "heterogeneite des donnees": "VERROU_TECH",
    "hétérogénéité des systèmes": "VERROU_TECH",
    "heterogeneite des systemes": "VERROU_TECH",
    "compromis précision-latence": "VERROU_TECH",
    "compromis precision-latence": "VERROU_TECH",
    "ordonnancement": "VERROU_TECH",
    "gestion mémoire": "VERROU_TECH",
    "gestion memoire": "VERROU_TECH",
    "confidentialité": "VERROU_TECH",
    "confidentialite": "VERROU_TECH",
    "sécurité": "VERROU_TECH",
    "securite": "VERROU_TECH",
    "bande passante limitée": "VERROU_TECH",
    "bande passante limitee": "VERROU_TECH",
    "charge élevée des échanges": "VERROU_TECH",
    "charge elevee des echanges": "VERROU_TECH",
    "non-IID": "VERROU_TECH",
    "répartition dynamique des charges": "VERROU_TECH",
    "repartition dynamique des charges": "VERROU_TECH",
    "synchronisation des traitements": "VERROU_TECH",
    "communications inter-nœuds": "VERROU_TECH",
    "communications inter-noeuds": "VERROU_TECH",
    "comportements difficilement prédictibles": "VERROU_TECH",
    "comportements difficilement predictibles": "VERROU_TECH",

    # objectifs
    "formaliser un cadre méthodologique": "OBJECTIF_RD",
    "formaliser un cadre methodologique": "OBJECTIF_RD",
    "concevoir la structure des modèles": "OBJECTIF_RD",
    "concevoir la structure des modeles": "OBJECTIF_RD",
    "réduire les coûts mémoire et computationnels": "OBJECTIF_RD",
    "reduire les couts memoire et computationnels": "OBJECTIF_RD",
    "garantir robustesse et précision": "OBJECTIF_RD",
    "garantir robustesse et precision": "OBJECTIF_RD",
    "optimiser la représentation numérique": "OBJECTIF_RD",
    "optimiser la representation numerique": "OBJECTIF_RD",
    "faciliter le déploiement": "OBJECTIF_RD",
    "faciliter le deploiement": "OBJECTIF_RD",
    "permettre le déploiement": "OBJECTIF_RD",
    "permettre le deploiement": "OBJECTIF_RD",
    "former les modèles localement": "OBJECTIF_RD",
    "former les modeles localement": "OBJECTIF_RD",
    "agréger les résultats": "OBJECTIF_RD",
    "agreger les resultats": "OBJECTIF_RD",
    "accroître performance, portabilité et robustesse": "OBJECTIF_RD",
    "accroitre performance, portabilite et robustesse": "OBJECTIF_RD",

    # résultats
    "gains significatifs": "RESULTAT_RD",
    "taux de compression": "RESULTAT_RD",
    "facteur 35 à 49": "RESULTAT_RD",
    "facteur 35 a 49": "RESULTAT_RD",
    "performances proches": "RESULTAT_RD",
    "résultats expérimentaux": "RESULTAT_RD",
    "resultats experimentaux": "RESULTAT_RD",
    "latence proche du natif": "RESULTAT_RD",
    "facteur proche de 1,2": "RESULTAT_RD",
    "réduction de la taille binaire": "RESULTAT_RD",
    "reduction de la taille binaire": "RESULTAT_RD",
    "résultats originaux": "RESULTAT_RD",
    "resultats originaux": "RESULTAT_RD",

    # composants
    "CPU": "COMPOSANT_TECHNIQUE",
    "GPU": "COMPOSANT_TECHNIQUE",
    "FPGA": "COMPOSANT_TECHNIQUE",
    "TPU": "COMPOSANT_TECHNIQUE",
    "multi-cœurs": "COMPOSANT_TECHNIQUE",
    "multi-coeurs": "COMPOSANT_TECHNIQUE",
    "multicœurs": "COMPOSANT_TECHNIQUE",
    "multicoeurs": "COMPOSANT_TECHNIQUE",
    "microcontrôleurs": "COMPOSANT_TECHNIQUE",
    "microcontroleurs": "COMPOSANT_TECHNIQUE",
    "accélérateurs spécialisés": "COMPOSANT_TECHNIQUE",
    "accelerateurs specialises": "COMPOSANT_TECHNIQUE",
    "kernels": "COMPOSANT_TECHNIQUE",
    "kernels optimisés": "COMPOSANT_TECHNIQUE",
    "kernels optimises": "COMPOSANT_TECHNIQUE",
    "kernels spécialisés": "COMPOSANT_TECHNIQUE",
    "kernels specialises": "COMPOSANT_TECHNIQUE",
    "kernels d’opérateurs": "COMPOSANT_TECHNIQUE",
    "kernels d'operateurs": "COMPOSANT_TECHNIQUE",
    "opérateurs": "COMPOSANT_TECHNIQUE",
    "operateurs": "COMPOSANT_TECHNIQUE",
    "SoftMax": "COMPOSANT_TECHNIQUE",
    "MaxPool2D": "COMPOSANT_TECHNIQUE",
    "ConvDepthWise": "COMPOSANT_TECHNIQUE",
    "BatchNorm": "COMPOSANT_TECHNIQUE",
    "backends": "COMPOSANT_TECHNIQUE",
    "noyau central modulaire": "COMPOSANT_TECHNIQUE",
    "tenseurs": "COMPOSANT_TECHNIQUE",
    "graphes": "COMPOSANT_TECHNIQUE",
    "graphes de calcul": "COMPOSANT_TECHNIQUE",
    "ordonnanceur": "COMPOSANT_TECHNIQUE",
    "plugins": "COMPOSANT_TECHNIQUE",
    "datasets": "COMPOSANT_TECHNIQUE",
    "middleware": "COMPOSANT_TECHNIQUE",
    "composants critiques": "COMPOSANT_TECHNIQUE",

    # équipements
    "Raspberry Pi": "EQUIPEMENT_RD",
    "Raspberry Pi 4": "EQUIPEMENT_RD",
    "Intel Core i7": "EQUIPEMENT_RD",
    "x86_64": "EQUIPEMENT_RD",
    "plateformes embarquées": "EQUIPEMENT_RD",
    "plateformes embarquees": "EQUIPEMENT_RD",
    "cibles embarquées": "EQUIPEMENT_RD",
    "cibles embarquees": "EQUIPEMENT_RD",
    "cartes embarquées": "EQUIPEMENT_RD",
    "cartes embarquees": "EQUIPEMENT_RD",

    # matériaux
    "paramètres": "MATERIAU_SPECIFIQUE",
    "parametres": "MATERIAU_SPECIFIQUE",
    "poids": "MATERIAU_SPECIFIQUE",
    "activations": "MATERIAU_SPECIFIQUE",
    "ImageNet": "MATERIAU_SPECIFIQUE",
    "MNIST": "MATERIAU_SPECIFIQUE",
    "FEMNIST": "MATERIAU_SPECIFIQUE",
    "Sent140": "MATERIAU_SPECIFIQUE",
    "données issues de capteurs": "MATERIAU_SPECIFIQUE",
    "donnees issues de capteurs": "MATERIAU_SPECIFIQUE",
    "données distribuées": "MATERIAU_SPECIFIQUE",
    "donnees distribuees": "MATERIAU_SPECIFIQUE",
    "données non étiquetées": "MATERIAU_SPECIFIQUE",
    "donnees non etiquetees": "MATERIAU_SPECIFIQUE",

    # organismes
    "GLR TECHNOLOGIES": "ORGANISME",
    "GLRT": "ORGANISME",
    "Eclipse Foundation": "ORGANISME",
    "fondation Eclipse": "ORGANISME",
    "CEA": "ORGANISME",
    "INRIA": "ORGANISME",
    "INREA": "ORGANISME",
    "Thales": "ORGANISME",
    "Airbus": "ORGANISME",
    "ESIEE PARIS": "ORGANISME",
}

TEXT_CAPS = {
    "aidge": 6,
    "framework aidge": 5,
    "framework d'ia embarquee": 4,
    "architecture modulaire": 5,
    "architecture logicielle modulaire": 4,
    "pipeline unifie": 4,
    "deep learning": 6,
    "deep learning embarque": 5,
    "intelligence artificielle embarquee": 5,
    "ia embarquee": 5,
    "reseaux de neurones": 5,
    "reseaux de neurones profonds": 6,
    "systemes embarques": 6,
    "architectures embarquees": 6,
    "architectures heterogenes": 5,
    "inference embarquee": 5,
    "quantification": 6,
    "quantification multi-precision": 5,
    "apprentissage federe": 5,
    "apprentissage federe distribue": 5,
    "complexite computationnelle": 6,
    "complexite algorithmique": 6,
    "contraintes materielles": 6,
    "contraintes embarquees": 6,
    "latence": 5,
    "portabilite": 5,
    "reproductibilite": 5,
    "cpu": 5,
    "gpu": 5,
    "cuda": 5,
    "tensorrt": 5,
    "onnx": 5,
    "kernels": 6,
    "operateurs": 5,
    "raspberry pi": 4,
    "resnet18": 4,
    "resnet50": 4,
    "mobilenet": 4,
    "fedavg": 4,
    "fedprox": 4,
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

print("✅ Patch projet 30 terminé")
print("✅ Projet 30 = CIR, utilisable pour dataset CIR après validation")
print(f"Avant          : {before}")
print(f"Après          : {after}")
print(f"Removed clean  : {removed}")
print(f"Removed caps   : {removed_caps}")
print(f"Removed nested : {removed_nested}")
print(f"Fixed labels   : {fixed}")

print("\nDistribution finale après patch:")
for label, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{label:25s} {count}")