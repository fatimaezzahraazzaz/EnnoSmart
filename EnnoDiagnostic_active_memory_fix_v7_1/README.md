# EnnoDiagnostic — Active Scientific Memory V7.1

Cette version corrige la régression observée après V7.0 sans toucher au NLP,
Frascati, aux scores, au chat/RAG ni au frontend.

## Ce que V7.1 stabilise

### 1. Verrous de continuité
Le CIR N-1 du même projet sert de mémoire scientifique active.

Ordre de recherche :
1. les sections NLP déjà chargées de l'année N ;
2. seulement si ces indices sont insuffisants, un complément RAG ciblé.

Une famille N-1 peut devenir un verrou visible N uniquement si plusieurs preuves
N convergentes passent les gardes. L'historique seul n'est jamais une preuve N.

Le recovery composite peut combiner :
- limite/incertitude ;
- démarche ;
- résultat ;
- paramètre ;
- objectif/contribution.

### 2. Performance
V7.0 pouvait multiplier les recherches RAG et dépasser 150 s uniquement dans
`historical_continuity`.

V7.1 :
- scanne d'abord `current_sections` / `nlp_result.json` en mémoire ;
- ne lance pas le RAG si les preuves N sont déjà suffisantes ;
- limite par défaut le contrôle de gap à 8 familles ;
- limite l'expansion RAG historique à 1 facette supplémentaire ;
- désactive par défaut la reconstruction LLM coûteuse des familles historiques
  (`ENNOSMART_HISTORICAL_FAMILY_RECONSTRUCTION_USE_LLM=0`), tout en la laissant
  réactivable si nécessaire.

### 3. Parent transversal
Les verrous atomiques ne sont jamais supprimés.
Si le premier passage ne produit aucun parent alors que les atomiques partagent
déjà un pont scientifique dans les preuves N, un retry très ciblé peut proposer
un parent. Un cache stabilise ensuite ce parent tant que le fingerprint exact des
verrous atomiques et de leurs preuves ne change pas.

### 4. Objectif global
Un JSON vide `{"paragraphs":[]}` ne provoque plus immédiatement :
« objectif non stabilisé ».

Si des preuves objectif N existent, V7.1 lance une seule réparation compacte.
Le modèle doit produire un objectif fondé sur le dénominateur commun des preuves N.
La mémoire N-1 sert uniquement à empêcher qu'un cas local soit pris pour
l'objectif global.

Les preuves Objectif/Synthèse sont aussi diversifiées par document afin qu'un
rapport local ne monopolise pas le cadrage.

## Fichiers remplacés

- `agents/EnnoDiagnostic/historical_continuity_reconciler.py`
- `agents/EnnoDiagnostic/scientific_axis_synthesizer.py`
- `agents/EnnoDiagnostic/diagnostic_static_presenter.py`
- `agents/EnnoDiagnostic/ennodiagnostic_agent.py`

`modules/NLP/evidence_graph.py` est inclus uniquement comme référence et n'est pas installé.

## Installation

Décompresser le dossier dans `C:\EnnoSmart`, puis :

```powershell
cd C:\EnnoSmart

python `
  ".\EnnoDiagnostic_active_memory_fix_v7_1\install_active_memory_fix_v7_1.py" `
  --repo "C:\EnnoSmart"

python `
  ".\EnnoDiagnostic_active_memory_fix_v7_1\verify_active_memory_fix_v7_1.py" `
  --repo "C:\EnnoSmart"
```

L'installateur sauvegarde automatiquement les 4 fichiers remplacés.

## Test à faire

Ne relance pas Prepare Sources.
Redémarre le backend puis relance Diagnostic sur exactement les mêmes documents.

À vérifier :
1. les verrous atomiques précédents sont toujours présents ;
2. le parent transversal ne disparaît plus sans changement des preuves ;
3. une continuité N-1 peut apparaître si plusieurs indices N la confirment ;
4. l'objectif ne doit plus être vide ni réduit à un seul équipement/campagne
   si plusieurs preuves N montrent un objectif plus large ;
5. les résultats/démarches/paramètres existants doivent rester inchangés sauf
   ajout de continuités N réellement prouvées.
