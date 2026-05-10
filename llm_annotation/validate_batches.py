"""
====================================================================
VALIDATION RAPIDE DES PREMIERS BATCHES ANNOTÉS
====================================================================

À lancer APRÈS avoir annoté 3-5 batches dans Claude.ai
Vérifie que les annotations sont de bonne qualité AVANT de continuer.

Usage:
    python validate_batches.py
"""

import json
import re
from pathlib import Path
from collections import Counter

# Configuration
BASE_DIR = Path(r"C:\EnnoSmart")
WORK_DIR = BASE_DIR / "llm_annotation"
PROMPTS_DIR = WORK_DIR / "prompts"
RESPONSES_DIR = WORK_DIR / "responses"

LABELS_CORE = [
    "VERROU_TECH", "METHODE_RD", "TECHNOLOGIE_RD", "EQUIPEMENT_RD",
    "COMPOSANT_TECHNIQUE", "MATERIAU_SPECIFIQUE", "DOMAINE_RD",
    "RESULTAT_RD", "OBJECTIF_RD",
]


def extract_json_from_response(content: str):
    """Extrait le JSON d'une réponse Claude (gère les markdown wrappers)"""
    
    # Cas 1 : bloc ```json ... ```
    match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    
    # Cas 2 : bloc ``` ... ```
    match = re.search(r'```\s*(.*?)\s*```', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    
    # Cas 3 : JSON brut
    try:
        return json.loads(content.strip())
    except json.JSONDecodeError:
        pass
    
    # Cas 4 : trouver { ... }
    start = content.find('{')
    end = content.rfind('}')
    if start != -1 and end != -1:
        try:
            return json.loads(content[start:end+1])
        except json.JSONDecodeError:
            pass
    
    return None


def validate_batch(batch_id: int) -> dict:
    """Valide un batch annoté"""
    
    response_file = RESPONSES_DIR / f"batch_{batch_id:04d}_response.json"
    mapping_file = PROMPTS_DIR / f"batch_{batch_id:04d}_mapping.json"
    
    result = {
        "batch_id": batch_id,
        "status": "ok",
        "errors": [],
        "warnings": [],
        "stats": {},
    }
    
    # Vérifier que le fichier de réponse existe
    if not response_file.exists():
        result["status"] = "missing"
        result["errors"].append(f"Fichier manquant : {response_file.name}")
        return result
    
    # Charger la réponse
    try:
        with open(response_file, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        result["status"] = "error"
        result["errors"].append(f"Erreur lecture : {e}")
        return result
    
    # Parser le JSON
    response = extract_json_from_response(content)
    if response is None:
        result["status"] = "invalid_json"
        result["errors"].append("JSON invalide ou introuvable")
        return result
    
    # Charger le mapping
    with open(mapping_file, "r", encoding="utf-8") as f:
        mapping = json.load(f)
    
    chunks_by_id = {c["chunk_id"]: c for c in mapping["chunks"]}
    expected_ids = set(chunks_by_id.keys())
    
    # Récupérer les annotations
    annotations = response.get("annotations", [])
    found_ids = {a["chunk_id"] for a in annotations}
    
    # Chunks manquants dans la réponse
    missing_ids = expected_ids - found_ids
    if missing_ids:
        result["warnings"].append(f"{len(missing_ids)} chunks sans réponse")
    
    # Chunks inattendus
    extra_ids = found_ids - expected_ids
    if extra_ids:
        result["warnings"].append(f"{len(extra_ids)} chunks_id inconnus")
    
    # Valider chaque annotation
    stats = {
        "n_chunks_annotated": len(annotations),
        "n_entities_total": 0,
        "n_entities_valid": 0,
        "n_offset_errors": 0,
        "n_label_errors": 0,
        "n_hallucinations": 0,
        "labels": Counter(),
    }
    
    for ann in annotations:
        chunk_id = ann["chunk_id"]
        chunk = chunks_by_id.get(chunk_id)
        if not chunk:
            continue
        
        text = chunk["text"]
        entities = ann.get("entities", [])
        
        for ent in entities:
            stats["n_entities_total"] += 1
            
            label = ent.get("label")
            ent_text = ent.get("text", "")
            start = ent.get("start")
            end = ent.get("end")
            
            # Validation label
            if label not in LABELS_CORE:
                stats["n_label_errors"] += 1
                continue
            
            stats["labels"][label] += 1
            
            # Validation offsets
            if not isinstance(start, int) or not isinstance(end, int):
                stats["n_offset_errors"] += 1
                continue
            
            if start < 0 or end > len(text) or start >= end:
                stats["n_offset_errors"] += 1
                continue
            
            # Vérifier que le texte correspond
            actual_text = text[start:end]
            if actual_text == ent_text:
                stats["n_entities_valid"] += 1
            else:
                # Peut-être un décalage : chercher dans le texte
                if ent_text in text:
                    # Texte présent mais offset incorrect (corrigeable)
                    stats["n_offset_errors"] += 1
                else:
                    # Texte absent = hallucination
                    stats["n_hallucinations"] += 1
    
    result["stats"] = stats
    return result


def main():
    print("=" * 70)
    print("🔍 VALIDATION DES BATCHES ANNOTÉS")
    print("=" * 70)
    
    # Trouver tous les fichiers de réponse
    response_files = sorted(RESPONSES_DIR.glob("batch_*_response.json"))
    
    if not response_files:
        print(f"\n❌ Aucune réponse trouvée dans : {RESPONSES_DIR}")
        print("   Annote d'abord quelques batches dans Claude.ai !")
        return
    
    print(f"\n📂 {len(response_files)} réponses trouvées\n")
    
    # Valider chaque batch
    results = []
    for response_file in response_files:
        # Extraire le batch_id du nom de fichier
        match = re.match(r"batch_(\d+)_response\.json", response_file.name)
        if not match:
            continue
        
        batch_id = int(match.group(1))
        result = validate_batch(batch_id)
        results.append(result)
    
    # Afficher les résultats
    print("=" * 70)
    print(f"{'Batch':<8} {'Status':<15} {'Entités':<10} {'Valides':<10} {'Erreurs':<10}")
    print("-" * 70)
    
    total_entities = 0
    total_valid = 0
    total_hallucinations = 0
    total_offset_errors = 0
    all_labels = Counter()
    
    for r in results:
        batch_id = r["batch_id"]
        status = r["status"]
        stats = r.get("stats", {})
        
        n_ent = stats.get("n_entities_total", 0)
        n_valid = stats.get("n_entities_valid", 0)
        n_errors = (
            stats.get("n_offset_errors", 0) + 
            stats.get("n_hallucinations", 0) + 
            stats.get("n_label_errors", 0)
        )
        
        total_entities += n_ent
        total_valid += n_valid
        total_hallucinations += stats.get("n_hallucinations", 0)
        total_offset_errors += stats.get("n_offset_errors", 0)
        all_labels.update(stats.get("labels", {}))
        
        status_emoji = {
            "ok": "✅",
            "missing": "⚠️",
            "invalid_json": "❌",
            "error": "❌",
        }.get(status, "?")
        
        print(f"{batch_id:<8} {status_emoji} {status:<13} {n_ent:<10} {n_valid:<10} {n_errors:<10}")
        
        # Détails des erreurs
        for err in r.get("errors", []):
            print(f"         └─ ❌ {err}")
        for warn in r.get("warnings", []):
            print(f"         └─ ⚠️  {warn}")
    
    # Résumé global
    print("-" * 70)
    print(f"\n📊 RÉSUMÉ GLOBAL")
    print("=" * 70)
    print(f"Total entités       : {total_entities}")
    print(f"Entités valides     : {total_valid} ({total_valid/max(total_entities,1)*100:.1f}%)")
    print(f"Erreurs offsets     : {total_offset_errors} ({total_offset_errors/max(total_entities,1)*100:.1f}%)")
    print(f"Hallucinations      : {total_hallucinations} ({total_hallucinations/max(total_entities,1)*100:.1f}%)")
    
    # Distribution par label
    print(f"\n📊 DISTRIBUTION PAR LABEL")
    print("-" * 40)
    total_lbl = sum(all_labels.values())
    for label in LABELS_CORE:
        count = all_labels[label]
        pct = count / max(total_lbl, 1) * 100
        bar = "█" * int(pct / 2)
        print(f"  {label:<22} {count:>5} ({pct:>5.1f}%) {bar}")
    
    # VERDICT
    print("\n" + "=" * 70)
    print("🎯 VERDICT")
    print("=" * 70)
    
    halluc_rate = total_hallucinations / max(total_entities, 1) * 100
    valid_rate = total_valid / max(total_entities, 1) * 100
    
    if valid_rate >= 90:
        print("✅ EXCELLENT ! Continue d'annoter avec confiance.")
    elif valid_rate >= 75:
        print("⚠️ BON, mais avec quelques erreurs corrigibles.")
        print("   Le script de merge corrigera la plupart des offsets.")
    elif valid_rate >= 60:
        print("⚠️ MOYEN. Vérifie manuellement quelques réponses.")
        print("   Peut-être reformuler le prompt ou changer de modèle Claude.")
    else:
        print("❌ MAUVAIS. Stop et investigue avant de continuer.")
        print("   Possible : Claude ne suit pas le prompt, mauvais modèle, etc.")
    
    if halluc_rate > 10:
        print(f"\n⚠️ ATTENTION : {halluc_rate:.1f}% d'hallucinations.")
        print("   Claude invente des entités. Renforce le prompt.")
    
    # Vérifier l'équilibre des labels
    label_values = list(all_labels.values())
    if label_values:
        max_lbl = max(label_values)
        min_lbl = min(label_values)
        if max_lbl > 0 and min_lbl > 0:
            ratio = max_lbl / min_lbl
            if ratio > 10:
                print(f"\n⚠️ Déséquilibre fort entre labels (ratio {ratio:.1f}x)")
            else:
                print(f"\n✅ Équilibre des labels OK (ratio {ratio:.1f}x)")


if __name__ == "__main__":
    main()