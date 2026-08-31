# EnnoDiagnostic V5.5 — Objectif/Résultats + performance ciblée

Ce correctif part de la V5.4 et ne modifie pas la logique de création/regroupement des verrous.

## Corrigé

- Objectif : un objectif projet explicite provenant d'un document projet/transcription courante peut rester valide même si la fenêtre NLP a perdu le pronom « nous ». Les formulations « mesurer / évaluer / comparer » restent des finalités et ne sont plus prises pour un résultat déjà acquis.
- Résultats : la décision `observed_project_result` du gate commun est conservée jusqu'au dernier guard ; un résultat validé n'est plus reclassé ensuite comme simple cible/contexte.
- Zone lente `FAST_NLP_AUTHORITY -> CURRENT_PROJECT_ONLY` : le rapport de preuves Frascati réutilise directement les `evidence_ids` déjà calculés par le NLP au lieu de reranker sémantiquement le catalogue complet plusieurs fois.
- Préflight historique : si aucun dossier d'année antérieure n'existe sous `.../years/`, la réconciliation N-1 et la comparaison CIR précédent retournent immédiatement sans charger CIR_MEMORY.
- Instrumentation : le log PERF expose maintenant `frascati_summary` et `ai_detection_report` en plus des étapes existantes.

## Verrous protégés

Le paquet ne contient pas :
- `consultant_verrou_synthesizer.py`
- `scientific_axis_synthesizer.py`

L'installateur vérifie aussi par hash que ces fonctions de `ennodiagnostic_agent.py` restent identiques :
- `_load_nlp_lock_group_sources`
- `_load_recovered_missing_lock_candidates`
- `build_llm_reformulated_verrous`
- `_enrich_verrous_with_frascati`

## Installation

Arrêter backend + worker, puis :

```powershell
cd C:\EnnoSmart
python `
  "C:\EnnoSmart\EnnoDiagnostic_targeted_fix_v5_5\install_balanced_fast_fix_v5_5.py" `
  --repo "C:\EnnoSmart"

python `
  "C:\EnnoSmart\EnnoDiagnostic_targeted_fix_v5_5\verify_balanced_fast_fix_v5_5.py" `
  --repo "C:\EnnoSmart"
```

Redémarrer backend + worker puis lancer **Diagnostic directement**. Il n'est pas nécessaire de refaire « Préparer les sources » si les documents et `nlp_result.json` n'ont pas changé.

## Variables de secours

Pour revenir temporairement à l'ancien rapport de preuves Frascati :

```powershell
$env:ENNOSMART_DIAG_FAST_FRASCATI_EVIDENCE="0"
```

Pour forcer l'ancien lecteur historique même sans dossier local N-1 :

```powershell
$env:ENNOSMART_DIAG_TRUST_LOCAL_YEAR_PREFLIGHT="0"
```
