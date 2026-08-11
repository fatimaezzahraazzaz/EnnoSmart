# EnnoSmart — Dataset V2 Builder

But : augmenter rapidement et proprement les deux jeux de données utilisés par :

1. **FastJudge** : classification sémantique des passages
   - objectif
   - verrou
   - methode
   - parametre
   - resultat
   - limite
   - contribution
   - bruit

2. **VerrouDetector** : détection binaire de signaux de verrou scientifique/technologique.

Le pack ne remplace pas la validation humaine. Il produit :
- des exemples **SILVER** à forte confiance ;
- des exemples **A_REVOIR** prioritaires ;
- des **hard negatives** pour améliorer VerrouDetector.

## Installation

Copier le dossier dans :

```powershell
C:\EnnoSmart\train\dataset_v2_builder
```

Puis :

```powershell
Set-Location C:\EnnoSmart
.\.venv\Scripts\Activate.ps1

pip install -r .\train\dataset_v2_builder\requirements_dataset_v2.txt
```

## Exécution rapide

### 1. Collecte ANR

```powershell
python .\train\dataset_v2_builder\collect_anr.py `
  --root C:\EnnoSmart `
  --max-projects 6000
```

Le script interroge l'API officielle data.gouv.fr et choisit automatiquement
la ressource JSON principale la plus récente du dataset ANR DGDS.

### 2. Collecte HAL

Collecte légère (résumés + métadonnées) :

```powershell
python .\train\dataset_v2_builder\collect_hal.py `
  --root C:\EnnoSmart `
  --max-docs 1500
```

Pour télécharger aussi quelques textes complets PDF :

```powershell
python .\train\dataset_v2_builder\collect_hal.py `
  --root C:\EnnoSmart `
  --max-docs 1500 `
  --download-pdfs 300
```

### 3. Construire les candidats pour les deux modèles

```powershell
python .\train\dataset_v2_builder\build_candidates.py `
  --root C:\EnnoSmart
```

Le script essaie automatiquement d'utiliser :

```text
C:\EnnoSmart\modules\NLP\models.py
```

et donc les modèles existants :

```text
fastjudge_role_classifier.pkl
verrou_detector_gold_v2.pkl
```

Si les modèles locaux ne sont pas accessibles, le script continue avec les règles
faibles et marque davantage de lignes `A_REVOIR`.

## Sorties

```text
C:\EnnoSmart\train\data_v2\
├── raw\
│   ├── anr\
│   │   └── anr_passages.jsonl
│   └── hal\
│       ├── hal_passages.jsonl
│       └── pdf\
│
├── candidates\
│   ├── fastjudge_candidates.jsonl
│   ├── verrou_candidates.jsonl
│   ├── fastjudge_review.csv
│   └── verrou_review.csv
│
└── reports\
    ├── anr_collection_report.json
    ├── hal_collection_report.json
    └── candidate_report.json
```

## Comment lire `annotation_status`

- `silver_strong` : accord fort entre le modèle actuel et des règles indépendantes.
- `silver_model_high` : modèle très confiant mais règle faible/absente.
- `review_priority` : conflit ou exemple difficile à vérifier humainement.
- `weak_only` : seulement une règle faible, ne pas entraîner directement dessus.

## Règle importante

Ne mets pas directement toutes les données SILVER dans le train.

Procédure recommandée :

1. vérifier manuellement **100 à 200 exemples par classe** ;
2. corriger les conflits `review_priority` ;
3. garder les vrais dossiers CIR comme GOLD ;
4. splitter **par projet**, jamais par passage.

Le script `finalize_splits.py` peut ensuite créer train/validation/test.
