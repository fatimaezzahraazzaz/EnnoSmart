# Patch 03 — ANR tabular JSON + cache

Remplacer dans :

C:\EnnoSmart\train\dataset_v2_builder\

les fichiers :
- common.py
- collect_anr.py

Le parser accepte désormais :
- liste de dictionnaires
- JSON pandas "split" : columns + data
- tableau 2D
- JSON orienté colonnes
- mappings imbriqués

Le fichier ANR brut est ensuite conservé dans :
C:\EnnoSmart\train\data_v2\raw\anr\anr_source_latest.json

Ainsi les prochains essais ne nécessiteront plus de télécharger ~139 Mo.

## Relance

python ".\train\dataset_v2_builder\collect_anr.py" --root "C:\EnnoSmart" --max-projects 6000

Puis, si Passages > 0 :

python ".\train\dataset_v2_builder\build_candidates.py" --root "C:\EnnoSmart"
