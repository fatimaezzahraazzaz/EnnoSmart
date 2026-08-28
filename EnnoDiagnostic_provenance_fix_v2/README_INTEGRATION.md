# EnnoDiagnostic — correctif provenance V2 stricte

Cible : EnnoSmart, branche analysée `codex/ovh-deployment-v2`.

## Important avant installation

Cette V2 est conçue **pour l'état local où la V1 est déjà installée**. C'est le cas si `verify_patch.py` V1 t'a donné tous les `[OK]`.

La V2 **ne relance pas** `Préparer les sources`, ne modifie pas `nlp_result.json`, ne touche pas `section_context_config.py` et ne change aucune fonction de détection/regroupement/reformulation des verrous.

Elle corrige le défaut observé pendant le test AI-CODE : en V1, un passage `ambiguous_current_dossier` pouvait encore devenir un fait du projet. En V2 :

- détection de verrou / `uncertainty` / `novelty` : corpus complet conservé ;
- objectif / synthèse / démarche / résultat / paramètre : `project_direct` uniquement ;
- conclusion technique d'éligibilité : faits techniques `project_direct` uniquement ;
- `ambiguous_current_dossier` = jamais un fait projet ;
- littérature externe = jamais une action/résultat/paramètre du projet ;
- paragraphe sans `evidence_id` après retry = erreur bloquante ;
- score Frascati = indice/couverture documentaire, jamais « critères validés », « part acquise » ou probabilité d'acceptation.

## Installation PowerShell

Décompresse le ZIP par exemple dans :

```text
C:\EnnoSmart\EnnoDiagnostic_provenance_fix_v2
```

Puis exécute **uniquement le fichier Python**, pas le dossier :

```powershell
cd C:\EnnoSmart

python "C:\EnnoSmart\EnnoDiagnostic_provenance_fix_v2\apply_patch_v2.py" --repo "C:\EnnoSmart"
```

Le script vérifie d'abord que V1 est présente. S'il ne reconnaît pas l'état local, **il s'arrête avant d'écrire**. S'il commence à modifier puis rencontre une erreur, il restaure automatiquement l'état V1.

Il crée une sauvegarde sous :

```text
C:\EnnoSmart\.ennosmart_patch_backups\provenance_fix_v2_YYYYMMDD_HHMMSS
```

et un diff :

```text
C:\EnnoSmart\EnnoDiagnostic_provenance_fix_v2.diff
```

## Vérification

Après `[OK] Correctif V2 appliqué et compilé`, lance :

```powershell
python "C:\EnnoSmart\EnnoDiagnostic_provenance_fix_v2\verify_patch_v2.py" --repo "C:\EnnoSmart"
```

Résultat attendu : uniquement des `[OK]`, notamment :

```text
[OK] ambiguous bloqué comme objectif projet
[OK] project_core seul ne suffit plus comme preuve projet
[OK] vraie preuve project_direct autorisée
[OK] littérature toujours disponible pour les verrous
[OK] Vérification V2 complète terminée.
```

Tu peux aussi compiler manuellement :

```powershell
python -m py_compile `
  "agents/EnnoDiagnostic/evidence_provenance.py" `
  "agents/EnnoDiagnostic/ennodiagnostic_agent.py" `
  "agents/EnnoDiagnostic/diagnostic_static_presenter.py" `
  "agents/EnnoDiagnostic/structured_eligibility_writer.py"
```

Aucune sortie = compilation réussie.

## Voir les changements

```powershell
git diff -- `
  agents/EnnoDiagnostic/ennodiagnostic_agent.py `
  agents/EnnoDiagnostic/diagnostic_static_presenter.py `
  agents/EnnoDiagnostic/structured_eligibility_writer.py `
  agents/EnnoDiagnostic/evidence_provenance.py
```

Ne commit/push pas avant le test fonctionnel AI-CODE.

## Test fonctionnel après installation

Redémarre le backend **et le worker Celery** pour charger le nouveau code. Puis relance directement **Diagnostic** sur AI-CODE avec les sources déjà préparées. **Ne relance pas Préparer les sources** pour cette comparaison.

On doit vérifier en priorité que :

- les groupes/cartes de verrous restent issus du même pipeline ;
- MoA ne devient plus un objectif AI-CODE ;
- l'évaluation externe de 19 LLM ne devient plus un résultat projet ;
- les paramètres 12 encodeurs / 12 décodeurs / Adam ne deviennent plus des paramètres projet ;
- une revue de 102 publications peut être mentionnée comme état de l'art seulement si elle est correctement attribuée, jamais comme expérience/résultat projet ;
- les vraies preuves projet restent utilisables ;
- le score 90 % est décrit comme indice documentaire de l'opération de référence.

## Rollback V2 → V1

Si tu veux annuler uniquement la V2 et revenir exactement à l'état V1 sauvegardé :

```powershell
python "C:\EnnoSmart\EnnoDiagnostic_provenance_fix_v2\apply_patch_v2.py" --repo "C:\EnnoSmart" --rollback
```

Le rollback compile également les fichiers restaurés.
