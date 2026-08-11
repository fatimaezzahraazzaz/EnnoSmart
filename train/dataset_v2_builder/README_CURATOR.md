# Dataset V2 — Curator

Ce script ne réentraîne aucun modèle.

Il transforme les 136k candidats bruts en deux batches équilibrés à annoter / vérifier.

## Installation

Copier `curate_dataset_v2.py` dans :

C:\EnnoSmart\train\dataset_v2_builder\curate_dataset_v2.py

## Exécution recommandée

```powershell
Set-Location "C:\EnnoSmart"
& ".\.venv\Scripts\Activate.ps1"

python ".\train\dataset_v2_builder\curate_dataset_v2.py" `
    --root "C:\EnnoSmart" `
    --fast-per-class 500 `
    --verrou-positives 2000 `
    --verrou-easy-negatives 2000 `
    --verrou-hard-negatives 2000
```

## Sorties

```text
C:\EnnoSmart\train\data_v2\annotation_batches\
├── fastjudge_annotation_batch.csv
├── verrou_annotation_batch.csv
└── curation_report.json
```

### FastJudge

Le batch contient 500 passages par classe, donc 4 000 lignes équilibrées.
Il mélange :
- exemples silver_strong
- exemples modèle très confiant
- cas difficiles à revoir

Le champ `human_label` doit devenir la vérité finale.

### VerrouDetector

Le batch cherche :
- 2 000 candidats positifs
- 2 000 négatifs faciles
- 2 000 hard negatives

Ce sont des candidats à annoter, PAS encore des vérités terrain.

Ne pas entraîner tant que `human_label` n'est pas rempli ou qu'un protocole
d'annotation supplémentaire n'a pas été appliqué.
