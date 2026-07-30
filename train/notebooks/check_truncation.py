"""
Vérifie combien de chunks dépassent 384 tokens (limite GLiNER)
"""
import json
from pathlib import Path

DATASET_DIR = Path(r"C:\EnnoSmart\dataset_final")

for split in ["train.json", "val.json", "test.json"]:
    path = DATASET_DIR / split
    if not path.exists():
        continue
    
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    over_384 = 0
    over_500 = 0
    over_700 = 0
    max_len = 0
    entities_lost = 0
    
    for item in data:
        tokens = item.get("tokenized_text", [])
        n_tokens = len(tokens)
        max_len = max(max_len, n_tokens)
        
        if n_tokens > 384:
            over_384 += 1
            # Compter entités après 384
            for ner in item.get("ner", []):
                if ner[0] >= 384:
                    entities_lost += 1
        if n_tokens > 500:
            over_500 += 1
        if n_tokens > 700:
            over_700 += 1
    
    print(f"\n{'='*60}")
    print(f"{split}")
    print(f"{'='*60}")
    print(f"Total chunks       : {len(data)}")
    print(f"Max tokens         : {max_len}")
    print(f"Chunks > 384 tokens : {over_384} ({over_384/len(data)*100:.1f}%)")
    print(f"Chunks > 500 tokens : {over_500} ({over_500/len(data)*100:.1f}%)")
    print(f"Chunks > 700 tokens : {over_700} ({over_700/len(data)*100:.1f}%)")
    print(f"Entités perdues     : {entities_lost}")