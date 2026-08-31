# Correctif installateur V4.1

Cette archive V4.1 corrige uniquement le bug de l’installateur Python `bad escape \s` rencontré lors du remplacement de blocs contenant des expressions régulières. Le contenu fonctionnel du patch V4 reste inchangé.

# EnnoDiagnostic — Final Fix V4

Patch **incrémental** pour `codex/ovh-deployment-v2`. Il ne remplace pas tout le dossier EnnoDiagnostic et ne supprime pas les corrections de provenance déjà présentes.

## Installation Windows / PowerShell

Décompresser le ZIP dans `C:\EnnoSmart`, puis :

```powershell
cd C:\EnnoSmart
python "C:\EnnoSmart\EnnoDiagnostic_final_fix_v4\apply_patch_v4.py" --repo "C:\EnnoSmart"
```

Le script crée automatiquement un backup sous :

```text
C:\EnnoSmart\.ennosmart_patch_backups\final_fix_v4_YYYYMMDD_HHMMSS
```

Puis vérifier :

```powershell
python "C:\EnnoSmart\EnnoDiagnostic_final_fix_v4\verify_patch_v4.py" --repo "C:\EnnoSmart"
```

## Test recommandé

1. Redémarrer le backend.
2. Redémarrer le worker Celery.
3. **Ne pas relancer Préparer les sources** pour le premier contrôle.
4. Relancer uniquement **Diagnostic** sur le même `nlp_result.json`.

Cela permet de comparer la nouvelle logique aval sans changer le NLP.

## Ce qu'il faut voir dans les logs

Au premier run après V4 :
- moins d'appels `verrou_reformulation_retry` ; idéalement aucun en mode rapide ;
- aucun enchaînement massif `verrou_title_repair` ;
- `scientific_axis_consolidation` peut rester à 1 appel ;
- si aucun N-1 n'existe : `Comparaison CIR précédent ignorée ... préflight` ;
- les sections résultats ne doivent plus être rejetées seulement pour un terme générique déjà présent ailleurs dans le projet.

Au **deuxième Diagnostic seul** sur le même NLP, le cache de reformulation des verrous doit être réutilisé.

## Variables optionnelles

Le patch active par défaut le mode rapide des réparations de verrous. Pour retrouver l'ancien comportement très coûteux :

```powershell
$env:ENNOSMART_DIAG_VERROU_FAST_REPAIR="0"
```

Pour garder le mode rapide :

```powershell
$env:ENNOSMART_DIAG_VERROU_FAST_REPAIR="1"
```

## Rollback

Utiliser le chemin de backup affiché par `apply_patch_v4.py` :

```powershell
python "C:\EnnoSmart\EnnoDiagnostic_final_fix_v4\rollback_patch_v4.py" `
  --repo "C:\EnnoSmart" `
  --backup "C:\EnnoSmart\.ennosmart_patch_backups\final_fix_v4_YYYYMMDD_HHMMSS"
```
