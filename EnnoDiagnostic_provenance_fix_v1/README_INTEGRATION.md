# Correctif EnnoDiagnostic — provenance des preuves (v1)

Cible analysée : branche `codex/ovh-deployment-v2`.

## But

Corriger la contamination des sections **Objectif / Démarche / Résultats / Paramètres / Conclusion d'éligibilité** par des résultats provenant de l'état de l'art, **sans modifier la logique qui détecte, regroupe ou reformule les verrous**.

Le principe est de séparer deux dimensions :

- `role`: verrou, méthode, résultat, paramètre, etc. ;
- `evidence_origin` / `actor_scope`: projet courant, littérature externe, N-1, ambigu, calcul backend.

Un article scientifique peut donc toujours aider à détecter/documenter un verrou ou la nouveauté, mais il ne peut plus devenir une expérience ou un résultat attribué au projet courant.

## Fichiers concernés

- `agents/EnnoDiagnostic/evidence_provenance.py` — **nouveau**
- `agents/EnnoDiagnostic/ennodiagnostic_agent.py`
  - `_nlp_passage_proof`
  - `_purpose_score`
  - aucune fonction de regroupement/détection de verrous
- `agents/EnnoDiagnostic/diagnostic_static_presenter.py`
- `agents/EnnoDiagnostic/structured_eligibility_writer.py`

`section_context_config.py` n'est pas modifié.

## Installation Windows / PowerShell

Depuis n'importe quel dossier où tu as extrait ce ZIP :

```powershell
python .\apply_patch.py --repo "C:\EnnoSmart"
```

Le script :

1. vérifie les trois fichiers de la branche cible ;
2. crée une sauvegarde dans `C:\EnnoSmart\.ennosmart_patch_backups\...` ;
3. applique toutes les modifications de manière atomique ;
4. ajoute `evidence_provenance.py` ;
5. compile les quatre fichiers Python ;
6. exécute un mini-test de provenance ;
7. génère `C:\EnnoSmart\EnnoDiagnostic_provenance_fix_v1.diff` pour audit.

Ensuite :

```powershell
python .\verify_patch.py --repo "C:\EnnoSmart"
```

Puis dans le repo :

```powershell
cd C:\EnnoSmart

git diff -- `
  agents/EnnoDiagnostic/ennodiagnostic_agent.py `
  agents/EnnoDiagnostic/diagnostic_static_presenter.py `
  agents/EnnoDiagnostic/structured_eligibility_writer.py `
  agents/EnnoDiagnostic/evidence_provenance.py
```

Relance ensuite **exactement ton même test diagnostic** pour comparer avant/après.

## Résultat attendu

- les mêmes candidats/groupes de verrous restent disponibles ;
- l'état de l'art reste utilisable pour `uncertainty` / `novelty` ;
- un passage sous « État de l'art », même s'il dit `We evaluated ...`, ne peut plus devenir un résultat du projet ;
- une preuve externe ne peut plus devenir une démarche, un paramètre ou un objectif du projet ;
- PydanticAI refuse une mauvaise attribution d'acteur ;
- une erreur de provenance est un **hard grounding error**, jamais un simple `warning_only` ;
- le pourcentage Frascati est présenté comme **indice documentaire**, jamais comme « X % du projet éligible/défendable ».

## Rollback

Pour restaurer automatiquement la dernière sauvegarde :

```powershell
python .\apply_patch.py --repo "C:\EnnoSmart" --rollback
```

## Vérification importante sur les verrous

Ne teste pas `len(verrous) == 4` en dur. Compare plutôt les identifiants/titres/groupes avant et après sur le même corpus. Le nombre doit rester une conséquence des documents et de la logique existante, pas du patch.
