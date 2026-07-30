# EnnoScholar complet corrigé — version 2.0.0

Ce dossier contient l’agent complet, pas seulement un patch. Les modules sains de
l’archive d’origine ont été conservés et les chemins problématiques ont été
remplacés ou sécurisés.

## Contrat de fonctionnement

1. EnnoDiagnostic fournit les verrous confirmés.
2. EnnoScholar conserve exactement leurs identifiants, titres et ordre.
3. Les recherches et Article Cards alimentent les Phases 4 à 4.7.
4. La Phase 4.7 construit l’histoire scientifique canonique globale.
5. Le consultant peut modifier les grands titres du plan.
6. La Phase 5 démarre après approbation et autorisation de rédaction, puis ne cite
   que les Article Cards du dossier courant.

EnnoScholar ne crée plus de `scholar_topic_*` et ne reconstruit jamais les verrous
depuis le NLP. Une incohérence d’identifiant, de titre ou d’ordre bloque le flux.

## Installation Windows

Dans PowerShell :

```powershell
.\install_ennoscholar_v139_clean.ps1
```

Destination différente :

```powershell
.\install_ennoscholar_v139_clean.ps1 -Destination "D:\EnnoSmart\agents\EnnoScholar"
```

Le script sauvegarde l’installation précédente, copie récursivement tous les
modules — y compris les Phases 4 à 5 — et exécute les contrôles.

## Contrôles

```powershell
.\check_ennoscholar_v139_clean.ps1
```

Ou directement, sur Windows, Linux ou macOS :

```bash
python check_ennoscholar_complete.py --package /chemin/vers/EnnoScholar
```

## Plan consultant

Le module `consultant_plan_service.py` gère le cycle proposé, modifié, approuvé,
puis autorisé pour la rédaction. Le backend du chat doit persister le JSON produit
et transmettre son chemin à la Phase 5.

Exemples :

```bash
python -m EnnoScholar.consultant_plan_service propose \
  --phase47 phase_4_7_scientific_narrative.json \
  --output consultant_plan.json

python -m EnnoScholar.consultant_plan_service approve \
  --contract consultant_plan.json

python -m EnnoScholar.consultant_plan_service authorize \
  --contract consultant_plan.json
```

## Variables principales

- `ENNOSMART_ROOT_DIR` : racine de stockage ; défaut compatible
  `C:\EnnoSmart`.
- `ENNOSCHOLAR_CONFIRMED_VERROUS_PATH` : contrat EnnoDiagnostic explicite.
- `ENNOSCHOLAR_CONSULTANT_PLAN_PATH` : plan consultant persistant.
- `ENNOSCHOLAR_VERROU_ALIASES` : aliases configurables au format JSON.
- `ENNOSCHOLAR_MEMORY_V2_ENABLED` : mémoire antérieure, désactivée par défaut.
- `ENNOSCHOLAR_MEMORY_V2_TOP_K` : nombre de candidats mémoire, défaut `0`.
- `ENNOSCHOLAR_SAVE_PROMPTS` : sauvegarde des prompts, désactivée par défaut.

La mémoire antérieure ne sert jamais de preuve pour la Phase 5.

## Dépendances

Le cœur utilise Python 3.10+ et la bibliothèque standard. Les fonctions locales
de traduction et de reranking BGE nécessitent les dépendances optionnelles
énumérées dans `requirements-optional.txt`.
