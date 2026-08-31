# EnnoDiagnostic — Final Memory Fix V7.1

Ce correctif cible uniquement les régressions observées après V7.0.

## Résultat attendu

### Verrous N-1
Le CIR précédent du même projet reste une mémoire active. Lorsqu'un verrou historique
a des indices convergents dans les documents N :

- il est ajouté avec les verrous N ;
- le titre précis mémorisé est conservé lorsqu'il existe ;
- son analyse historique est conservée comme contenu de la carte ;
- les preuves cliquables restent exclusivement les preuves N qui confirment sa continuité ;
- aucun préfixe `Continuité à confirmer — ...` n'est ajouté.

Si le CIR historique n'a pas de sous-titre individuel pour chaque verrou, V7.1 préfère :
1. le titre précis stocké par la mémoire ;
2. le titre canonique reconstruit uniquement à partir de N-1 ;
3. sinon le début exact du paragraphe historique.

### Objectif
Le bug observé où GPT retournait :

`{"Objectif global du projet": "..."}`

au lieu de :

`{"paragraphs":[...]}`

est corrigé. Le texte est normalisé puis repasse dans les mêmes guards factuels.
Si le modèle renvoie encore une section vide alors que des preuves N existent, un retry
strict `objectif_global:shape_retry` est exécuté.

La mémoire N-1 reste une orientation de niveau d'abstraction : elle aide à distinguer
un cas d'étude local de l'objectif global, mais les faits de l'objectif restent ancrés dans N.

### Démarche
Les preuves historiques H ajoutées par V7.0 avaient provoqué des rejets du guard et fait
passer une démarche riche à un seul élément. V7.1 restaure par défaut le comportement
stabilisé avant V7 :

- Démarche = preuves N ;
- Résultats = preuves N ;
- Paramètres = preuves N.

La mémoire reste utilisée en amont pour les verrous et pour Objectif/Synthèse, sans
contaminer les sections factuelles.

### V6 / axes transversaux
Les verrous récupérés depuis N-1 ne sont plus utilisés comme graines pour créer de nouveaux
parents transversaux. Les parents V6 sont calculés uniquement à partir des verrous natifs N.
Cela évite l'explosion 2 verrous N + plusieurs historiques + plusieurs parents.

### Performance
La recherche de continuité commence maintenant directement dans le pack NLP N
(objectifs, méthodes, résultats, paramètres, limites, contributions). Chroma n'est utilisé
qu'en fallback et au maximum sur quelques familles par défaut.

La comparaison CIR N-1 séparée reçoit uniquement les verrous natifs N, et non les verrous
qui viennent justement d'être récupérés depuis N-1.

## Fichiers remplacés

- `agents/EnnoDiagnostic/historical_continuity_reconciler.py`
- `agents/EnnoDiagnostic/diagnostic_static_presenter.py`
- `agents/EnnoDiagnostic/ennodiagnostic_agent.py`

`scientific_axis_synthesizer.py` est fourni complet comme référence de compatibilité, sans
être remplacé par l'installateur.

## Installation

```powershell
cd C:\EnnoSmart

python `
  ".\EnnoDiagnostic_final_memory_fix_v7_1\install_final_memory_fix_v7_1.py" `
  --repo "C:\EnnoSmart"

python `
  ".\EnnoDiagnostic_final_memory_fix_v7_1\verify_final_memory_fix_v7_1.py" `
  --repo "C:\EnnoSmart"
```

Puis redémarrer le backend et lancer directement **Diagnostic**. Ne pas relancer Prepare Sources
pour ce test : on veut comparer V7.1 aux mêmes preuves N.

## Variables optionnelles

Par défaut les réglages sûrs sont déjà actifs :

- `ENNOSMART_DIAG_INCLUDE_HISTORICAL_FACT_ROWS=0`
- `ENNOSMART_DIAG_MEMORY_ENRICH_FACT_SECTIONS=0`
- `ENNOSMART_HISTORICAL_GAP_USE_CHROMA_FALLBACK=1`
- `ENNOSMART_HISTORICAL_GAP_MAX_CHROMA_FAMILIES=3`

Aucun nom de projet, verrou, équipement ou technologie n'est codé en dur par ce correctif.
