# EnnoSmart Dataset V2 — Teacher Pipeline

Objectif final :
- FastJudge : 16 000 exemples = 2 000 × 8 classes.
- VerrouDetector : 12 000 exemples ≈ 5 000 verrou_evidence + 7 000 non_verrou,
  dont ~3 500 hard negatives.

Les 4 000 FastJudge et 4 522 VerrouDetector déjà créés sont des lots de curation,
pas les tailles finales.

## 1. Construire les files d'annotation

Copier les scripts dans :
C:\EnnoSmart\train\dataset_v2_builder\

Puis :

```powershell
python ".\train\dataset_v2_builder\build_teacher_queues.py" `
  --root "C:\EnnoSmart"
```

Par défaut :
- FastJudge : jusqu'à 2 500 candidats par classe = max 20 000 à faire vérifier par le teacher.
- Verrou : jusqu'à 8 000 positive-like + 4 500 easy negatives + 4 500 hard negatives.

On sur-échantillonne volontairement car le teacher rejettera/corrigera des labels.

## 2. Test du teacher sur seulement 100 exemples

Installer :

```powershell
python -m pip install -r ".\train\dataset_v2_builder\requirements_teacher.txt"
```

Puis :

```powershell
python ".\train\dataset_v2_builder\annotate_teacher.py" `
  --root "C:\EnnoSmart" `
  --task fastjudge `
  --max-items 100
```

et :

```powershell
python ".\train\dataset_v2_builder\annotate_teacher.py" `
  --root "C:\EnnoSmart" `
  --task verrou `
  --max-items 100
```

Le script utilise `OPENAI_API_KEY` et par défaut `gpt-4.1-mini`.

## 3. Annotation complète

Quand le test est bon :

```powershell
python ".\train\dataset_v2_builder\annotate_teacher.py" `
  --root "C:\EnnoSmart" `
  --task fastjudge
```

```powershell
python ".\train\dataset_v2_builder\annotate_teacher.py" `
  --root "C:\EnnoSmart" `
  --task verrou
```

Les appels sont checkpointés : relancer le script reprend là où il s'est arrêté.

## 4. Construire les datasets finaux

```powershell
python ".\train\dataset_v2_builder\finalize_teacher_datasets.py" `
  --root "C:\EnnoSmart" `
  --teacher-min-confidence 0.80
```

Le script garde au maximum :
- 2 000 exemples par classe FastJudge ;
- 5 000 positifs VerrouDetector ;
- 3 500 hard negatives ;
- 3 500 autres non-verrous.

Puis split 70/15/15 **par projet**.
