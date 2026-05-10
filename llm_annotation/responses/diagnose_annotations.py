"""
====================================================================
DIAGNOSTIC DÉTAILLÉ DES ANNOTATIONS
====================================================================

Distingue les VRAIES erreurs (hallucinations) des
FAUSSES erreurs (offsets décalés mais texte présent).

Usage:
    python diagnose_annotations.py
"""

import json
import re
from pathlib import Path
from collections import Counter

BASE_DIR = Path(r"C:\EnnoSmart")
WORK_DIR = BASE_DIR / "llm_annotation"
PROMPTS_DIR = WORK_DIR / "prompts"
RESPONSES_DIR = WORK_DIR / "responses"

LABELS_CORE = [
    "VERROU_TECH", "METHODE_RD", "TECHNOLOGIE_RD", "EQUIPEMENT_RD",
    "COMPOSANT_TECHNIQUE", "MATERIAU_SPECIFIQUE", "DOMAINE_RD",
    "RESULTAT_RD", "OBJECTIF_RD",
]


def extract_json_from_response(content):
    """Extrait le JSON d'une réponse Claude"""
    match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    
    match = re.search(r'```\s*(.*?)\s*```', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    
    try:
        return json.loads(content.strip())
    except json.JSONDecodeError:
        pass
    
    start = content.find('{')
    end = content.rfind('}')
    if start != -1 and end != -1:
        try:
            return json.loads(content[start:end+1])
        except json.JSONDecodeError:
            pass
    
    return None


def diagnose():
    print("=" * 70)
    print("🔬 DIAGNOSTIC DÉTAILLÉ DES ANNOTATIONS")
    print("=" * 70)
    
    response_files = sorted(RESPONSES_DIR.glob("batch_*_response.json"))
    
    # Catégories d'analyse
    stats = {
        "total": 0,
        "perfect": 0,           # Offsets corrects ET texte présent
        "offset_fixable": 0,    # Texte présent mais offset faux (CORRIGEABLE)
        "offset_close": 0,      # Texte légèrement différent (espaces, casse) - CORRIGEABLE  
        "halluciné": 0,         # Texte vraiment absent du chunk
        "label_invalide": 0,    # Label inconnu
    }
    
    examples = {
        "offset_fixable": [],
        "halluciné": [],
        "offset_close": [],
    }
    
    labels_count = Counter()
    
    for response_file in response_files:
        match = re.match(r"batch_(\d+)_response\.json", response_file.name)
        if not match:
            continue
        
        batch_id = int(match.group(1))
        mapping_file = PROMPTS_DIR / f"batch_{batch_id:04d}_mapping.json"
        
        with open(response_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        response = extract_json_from_response(content)
        if not response:
            continue
        
        with open(mapping_file, "r", encoding="utf-8") as f:
            mapping = json.load(f)
        
        chunks_by_id = {c["chunk_id"]: c for c in mapping["chunks"]}
        
        for ann in response.get("annotations", []):
            chunk = chunks_by_id.get(ann["chunk_id"])
            if not chunk:
                continue
            
            text = chunk["text"]
            
            for ent in ann.get("entities", []):
                stats["total"] += 1
                
                label = ent.get("label", "")
                ent_text = ent.get("text", "").strip()
                start = ent.get("start")
                end = ent.get("end")
                
                if label not in LABELS_CORE:
                    stats["label_invalide"] += 1
                    continue
                
                labels_count[label] += 1
                
                # Cas 1 : Parfait
                if (isinstance(start, int) and isinstance(end, int) and 
                    0 <= start < end <= len(text) and 
                    text[start:end] == ent_text):
                    stats["perfect"] += 1
                    continue
                
                # Cas 2 : Texte exact présent dans le chunk → offset CORRIGEABLE
                if ent_text and ent_text in text:
                    stats["offset_fixable"] += 1
                    if len(examples["offset_fixable"]) < 3:
                        examples["offset_fixable"].append({
                            "chunk_id": ann["chunk_id"],
                            "label": label,
                            "ent_text": ent_text,
                            "given_offset": (start, end),
                            "real_offset": text.find(ent_text),
                        })
                    continue
                
                # Cas 3 : Texte légèrement différent (essayer normalisation)
                if ent_text:
                    # Normaliser : espaces, casse
                    norm_text = ' '.join(ent_text.lower().split())
                    norm_chunk = ' '.join(text.lower().split())
                    
                    if norm_text in norm_chunk:
                        stats["offset_close"] += 1
                        if len(examples["offset_close"]) < 3:
                            examples["offset_close"].append({
                                "chunk_id": ann["chunk_id"],
                                "label": label,
                                "ent_text": ent_text,
                            })
                        continue
                
                # Cas 4 : Vraiment halluciné
                stats["halluciné"] += 1
                if len(examples["halluciné"]) < 5:
                    examples["halluciné"].append({
                        "chunk_id": ann["chunk_id"],
                        "label": label,
                        "ent_text": ent_text,
                        "chunk_preview": text[:200] + "..." if len(text) > 200 else text,
                    })
    
    # Affichage
    total = max(stats["total"], 1)
    corrigeable = stats["perfect"] + stats["offset_fixable"] + stats["offset_close"]
    
    print(f"\n📊 ANALYSE SUR {stats['total']} ENTITÉS")
    print("-" * 70)
    print(f"✅ Parfaites (offsets corrects)      : {stats['perfect']:>5} ({stats['perfect']/total*100:>5.1f}%)")
    print(f"🔧 Offsets corrigeables (texte OK)   : {stats['offset_fixable']:>5} ({stats['offset_fixable']/total*100:>5.1f}%)")
    print(f"🔧 Offsets proches (normalisation)   : {stats['offset_close']:>5} ({stats['offset_close']/total*100:>5.1f}%)")
    print(f"❌ Hallucinations (texte absent)     : {stats['halluciné']:>5} ({stats['halluciné']/total*100:>5.1f}%)")
    print(f"❌ Labels invalides                  : {stats['label_invalide']:>5} ({stats['label_invalide']/total*100:>5.1f}%)")
    print("-" * 70)
    print(f"🎯 TOTAL UTILISABLE après merge      : {corrigeable:>5} ({corrigeable/total*100:>5.1f}%)")
    
    # Distribution labels
    print(f"\n📊 DISTRIBUTION PAR LABEL")
    print("-" * 50)
    total_lbl = sum(labels_count.values())
    for label in LABELS_CORE:
        count = labels_count[label]
        pct = count / max(total_lbl, 1) * 100
        bar = "█" * int(pct / 2)
        print(f"  {label:<22} {count:>5} ({pct:>5.1f}%) {bar}")
    
    # Exemples
    print(f"\n📋 EXEMPLES D'OFFSETS CORRIGEABLES (texte bien présent)")
    print("-" * 70)
    for ex in examples["offset_fixable"]:
        print(f"  Label  : {ex['label']}")
        print(f"  Texte  : '{ex['ent_text']}'")
        print(f"  Donné  : offset {ex['given_offset']}")
        print(f"  Réel   : offset {ex['real_offset']}")
        print()
    
    if examples["halluciné"]:
        print(f"❌ EXEMPLES D'HALLUCINATIONS (à investiguer)")
        print("-" * 70)
        for ex in examples["halluciné"][:3]:
            print(f"  Label : {ex['label']}")
            print(f"  Texte : '{ex['ent_text']}'")
            print(f"  Chunk : '{ex['chunk_preview'][:150]}...'")
            print()
    
    # Verdict
    print("=" * 70)
    print("🎯 VERDICT RÉVISÉ")
    print("=" * 70)
    
    usable_rate = corrigeable / total * 100
    halluc_rate = stats["halluciné"] / total * 100
    
    if usable_rate >= 90:
        print(f"✅ EXCELLENT ! {usable_rate:.1f}% d'annotations utilisables après correction auto.")
        print("   Continue à annoter en confiance, le merge va tout corriger.")
    elif usable_rate >= 80:
        print(f"✅ TRÈS BON ! {usable_rate:.1f}% d'annotations utilisables.")
        print("   Le script de merge corrigera les offsets automatiquement.")
    elif usable_rate >= 70:
        print(f"⚠️ ACCEPTABLE : {usable_rate:.1f}% utilisables.")
        print("   Tu peux continuer, mais essaie un meilleur modèle Claude.")
    else:
        print(f"❌ PROBLÈME : seulement {usable_rate:.1f}% utilisables.")
    
    if halluc_rate > 10:
        print(f"\n⚠️ Taux d'hallucinations élevé : {halluc_rate:.1f}%")
        print("   Renforce le prompt avec : 'NE JAMAIS inventer'")
    elif halluc_rate < 5:
        print(f"\n✅ Hallucinations sous contrôle : {halluc_rate:.1f}%")


if __name__ == "__main__":
    diagnose()