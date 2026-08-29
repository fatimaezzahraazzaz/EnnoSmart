# EnnoDiagnostic — Role Restore V3

Ce correctif reste sur l'architecture `codex/ovh-deployment-v2` et corrige uniquement la perte aval des éléments déjà classés par le NLP : `methode`, `resultat`, `parametre`.

Il ne modifie pas `modules/NLP/*`, `frascati_assessment.py`, `demarche_legibility.py`, la réconciliation CIR N/N-1, ni la détection/regroupement des verrous.

## Principe

- le rôle NLP redevient l'autorité sémantique ;
- les regex du presenter restent des gardes (template/planning/cible), pas un deuxième classifieur ;
- une preuve ambiguë du corpus courant peut alimenter uniquement la section correspondant à son rôle NLP ;
- état de l'art, auteurs externes, CIR précédent et F0 ne deviennent jamais des faits projet ;
- objectif et synthèse restent stricts `project_direct` afin d'éviter le retour de MoA/ChatTester comme objectifs projet.

## Installation PowerShell

Décompresser le dossier dans `C:\EnnoSmart`, puis :

```powershell
cd C:\EnnoSmart
python "C:\EnnoSmart\EnnoDiagnostic_role_restore_v3\apply_patch_v3.py" --repo "C:\EnnoSmart"
```

Puis vérifier :

```powershell
python "C:\EnnoSmart\EnnoDiagnostic_role_restore_v3\verify_patch_v3.py" --repo "C:\EnnoSmart"
```

Le script crée automatiquement une sauvegarde sous `.ennosmart_patch_backups\role_restore_v3_...` et restaure cette sauvegarde si l'application ou la compilation échoue.

## Test fonctionnel

Après succès : redémarrer backend + worker Celery et relancer **Diagnostic uniquement** avec les mêmes sources préparées.

À contrôler :
- les vraies démarches NLP réapparaissent ;
- les résultats observés NLP réapparaissent ;
- les paramètres/contraintes NLP réapparaissent ;
- MoA / 19 LLM / AthenaTest ne reviennent pas comme faits projet ;
- une cible/planification n'est pas présentée comme résultat acquis ;
- le calcul Frascati reste inchangé.

## Rollback

```powershell
python "C:\EnnoSmart\EnnoDiagnostic_role_restore_v3\apply_patch_v3.py" --repo "C:\EnnoSmart" --rollback
```
