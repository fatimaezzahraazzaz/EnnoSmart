# V5 — bloc 1 : portée des demandes de rédaction

## Cause reproduite

Avant correction, `wants_full_draft(state, "Finalise la section 1")` renvoie
`True` sur une copie de la session lithium possédant un suivi global antérieur.
Le test de reproduction échoue avant toute modification du code applicatif
(`experiments/ennoscholar_v5/.artifacts/scope-before.xml`).

La surcharge V5 ajoutait Terminer/Achever/Finaliser à `RESUME_REQUEST`, mais pas
à `SECTION_REQUEST` ni à `ORDINAL_SECTION_REQUEST`. `requested_section` ne
trouvait donc pas la cible. `begin_writing` reprenait l'ancien suivi global ou
ouvrait toutes les sections dans son repli V5. La création de mission utilise
ce même détecteur via `writing_job.wants_full_draft`.

Un deuxième point d'entrée, `workflow.reconcile_draft_goal`, pouvait ouvrir
toutes les sections après une décision `set_turn_goal(deliverable="draft")`
lorsqu'une cible n'était pas reconnue.

## Correction limitée à V5

- `agents/EnnoScholarV5/writing_progress.py` : mêmes verbes de finalisation pour
  la reprise et les sélecteurs numérotés/ordinaux ; capture des identifiants et
  résolution de section V4 conservées. Les expressions sont modifiées uniquement
  dans la copie du module chargée sous le namespace V5.
- `writing_request_blocked` : partage du refus d'une demande négative ou d'une
  portée de section non résolue ; un ordre incomplet comme « Finalise. » ne
  devient pas une autorisation globale. Une demande de manuscrit mentionnant
  ensuite les sections déjà enregistrées conserve sa portée globale.
- `workflow.reconcile_draft_goal` : applique ce même refus avant le repli vers
  toutes les sections.

La résolution d'une cible valide utilise toujours le mode `section` existant,
avec la version courante comme baseline. Les brouillons ne sont ni supprimés ni
réécrits par la détection. La rédaction du manuscrit conserve le mode `full` et
sa progression sur les sections déjà sauvegardées.

## Dépendances vérifiées

| Dépendance | Effet attendu |
| --- | --- |
| `requested_section`, `resolve_section` | Même cible pour la planification et les contrôles d'écriture |
| `check_write_scope` | Refus d'écrire une autre section ou de modifier le plan d'une demande ciblée |
| `wants_full_draft`, `service.open_run` | Une seule section n'ouvre pas une mission de manuscrit |
| `workflow.begin`, `reconcile_draft_goal` | Même portée à l'ouverture et après la décision de Sol |
| `runtime.after_workflow`, `write_section(save)` | Sauvegarde de la cible, fin du suivi ciblé et préservation des autres sections |
| Missions, LangGraph, registre de coûts | Code et paramètres inchangés ; couverts par les régressions existantes |

## Validation

Nouveaux tests : `experiments/ennoscholar_v5/tests/test_writing_scope.py`.
Ils couvrent les numéros, ordinaux, identifiants stables, demandes globales,
négations, cibles absentes/introuvables, plage existante, ancien suivi global,
absence de suivi, contrôles de sauvegarde et voie sémantique de Sol.

Résultat ciblé : **54 réussites**, comprenant 49 nouveaux cas et les 5 cas
existants de reprise naturelle. Deux scénarios sauvegardent réellement une
section dans une base de test via LangGraph et le SDK Responses ; trois autres
vérifient qu'un but `draft` simulé ne crée pas une mission non demandée.
Le statut scientifique reste partiel si les sources de la fixture sont limitées,
même lorsque la section demandée est bien enregistrée.

Régression restante : résultat à consigner après exécution.

Commandes depuis le worktree V5 :

```powershell
& 'D:\EnnoSmart\.venv\Scripts\python.exe' -B experiments/ennoscholar_v5/run_offline.py -k 'writing_scope or natural_completion' --disable-warnings --basetemp=experiments/ennoscholar_v5/.artifacts/pytest-scope-targeted --junitxml=experiments/ennoscholar_v5/.artifacts/scope-targeted.xml
& 'D:\EnnoSmart\.venv\Scripts\python.exe' -B experiments/ennoscholar_v5/run_offline.py -k 'not writing_scope and not natural_completion' --disable-warnings --basetemp=experiments/ennoscholar_v5/.artifacts/pytest-scope-regression --junitxml=experiments/ennoscholar_v5/.artifacts/scope-regression.xml
```

Le lanceur bloque les connexions réseau, les sous-processus et la lecture des
fichiers de secrets. Les bases et checkpoints utilisés par les tests sont isolés.
Les réponses scientifiques et le transport HTTP sont simulés ; aucun appel API
payant ni téléchargement réel n'est réalisé.

## Limites

Cette validation établit le comportement logiciel des cas testés, pas une
compréhension universelle du français ni la réussite d'une rédaction scientifique
par le modèle réel. Aucun parcours navigateur ni essai PostgreSQL n'est ajouté
dans ce bloc. Aucun serveur applicatif réel n'est redémarré.

Les autres constats de l'audit, notamment les limites globales multisegments et
l'actualité des décisions de comparaison, restent hors de cette intervention.
Le bloc 2 n'est pas commencé.
