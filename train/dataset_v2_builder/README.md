# Full teacher Qwen3 8B

Qwen3 8B est le meilleur modèle testé sur le GOLD provisoire 191.

Résultats :
- FastJudge : Accuracy 68.75 %, Macro-F1 57.32 %
- VerrouDetector : Accuracy 89.47 %, Macro-F1 79.06 %

Cette étape NE FINALISE PAS encore les datasets.
Elle annote les files complètes pour mesurer les effectifs fiables par classe.

## Installation

Copier :
- annotate_full_qwen3_8b.py
- analyze_full_qwen3_8b.py

dans :

C:\EnnoSmart\train\dataset_v2_builder\

## FastJudge

```powershell
Set-Location "C:\EnnoSmart"
& ".\.venv\Scripts\Activate.ps1"

python ".\train\dataset_v2_builder\annotate_full_qwen3_8b.py" `
    --root "C:\EnnoSmart" `
    --model "qwen3:8b" `
    --task fastjudge `
    --batch-size 4
```

## VerrouDetector

```powershell
python ".\train\dataset_v2_builder\annotate_full_qwen3_8b.py" `
    --root "C:\EnnoSmart" `
    --model "qwen3:8b" `
    --task verrou `
    --batch-size 4
```

## Analyse

```powershell
python ".\train\dataset_v2_builder\analyze_full_qwen3_8b.py" `
    --root "C:\EnnoSmart"
```

Le script est checkpointé : une relance reprend les IDs déjà écrits.
Ne pas lancer l'entraînement avant cette analyse.
