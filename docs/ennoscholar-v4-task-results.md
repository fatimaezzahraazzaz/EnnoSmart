# Résultats de tâches, sauvegarde progressive et reprise

Correction du 19 octobre 2026, dans l'orchestrateur V4 existant. Aucun nouveau
service, schéma SQL, modèle ou plafond de dépenses. Les reçus sont stockés dans
le JSON de session existant et checkpointés par le service authentifié.

## Contrat commun

`task_results[task_id]`, géré par `task_results.TaskResults` :

| Champ | Sens |
|---|---|
| `id`, `kind`, `target`, `signature` | Identité stable du travail et empreinte de ses dépendances scientifiques. |
| `status` | `pending`, `running`, `generated`, `refused`, `failed`, `interrupted`, `saved`. Généré ne signifie jamais sauvegardé. |
| `content` | Appels retournés puis contenu validé ; propositions et artefacts sont distingués par le statut. |
| `refusal` | Résultat exact du validateur, avec champs annexes (affirmations refusées, métrique et preuve source). |
| `evidence_ids` | Preuves réellement présentes dans l'input ; les citations et snapshots exacts restent dans leurs registres existants. |
| `saved` | Reçu de sauvegarde : ID de brouillon et version documentaire, ou ID de partie de plan. |
| `attempts` | Historique des tentatives, chacune avec ID, exécution, empreinte d'input, contenu brut, arguments, preuves, refus, usage, réserve, durée et reçu de sauvegarde. |

L'usage fournisseur absent est explicitement inconnu, avec réservation prudente ;
il n'est pas présenté comme gratuit. Une seule fonction comptabilise entrée,
sortie, cache, reasoning, recherche web et contexte long, exactement une fois par
tentative. Les échecs et résultats récoltés à l'arrêt ne disparaissent plus des
mesures. Le nombre de lots (`rounds`) reste distinct du nombre d'appels.

Les signatures couvrent la révision du plan, la section, les analyses, l'identité
des sources, le contenu documentaire et les consignes actives. Elles ignorent les
UUID de messages et les tentatives de téléchargement sans changement de contenu.
Une répétition de consigne identique n'invalide pas un travail scientifique.

## Exécution et validation

Les workers réseau ne modifient pas la session. `collect_finished()` collecte les
futures achevées au fil de l'eau ; le thread propriétaire applique les outils et
les écritures séquentiellement. Son bloc de sortie collecte également les
résultats déjà terminés lorsqu'une interruption est observée.

Plans et sections passent par `run_task_jobs()` / `collect_job()`. Chaque
proposition complète est checkpointée avant validation. Parsing JSON, schéma et
refus scientifique sont traités au niveau de la tâche. Une partie valide du plan
est validée par le même `validate_sections()` que l'assemblage, puis sauvegardée
indépendamment dans le registre durable. Les identifiants inconnus ne sont plus
supprimés silencieusement pour faire passer un plan.

Une section passe toujours par `ScholarTools.execute('write_section')`, ses
contrôles d'attribution, d'affirmations, de nombres, d'intégrité des preuves et de
bibliographie. L'option interne `settle=True` permet seulement d'achever une
sauvegarde **locale** après le délai : elle ne permet aucun nouvel appel réseau
et ne court-circuite aucun contrôle scientifique. Elle n'est pas un paramètre
d'outil exposé au modèle.

Un `operation_id` associé à l'empreinte des arguments rend la sauvegarde d'un
brouillon idempotente. La même opération retourne le même brouillon ; un contenu
différent avec le même ID est refusé.

## Reprise

- Une proposition déjà reçue est revalidée localement avant de demander une
  nouvelle génération.
- Les parties de plan déjà sauvegardées avec les mêmes dépendances sont réutilisées.
  Une reprise ne génère que les parties manquantes/refusées.
- Une section refusée reçoit son appel original et le résultat d'outil complet,
  y compris après sérialisation/rechargement. Ses voisines sauvegardées ne sont
  pas régénérées.
- Un travail resté `running` après la perte du worker devient `interrupted` à la
  reprise. L'historique n'est pas effacé et l'usage distant non reçu reste inconnu.
- Les parties achevées après la clôture du coordinateur ne peuvent pas écrire
  tardivement dans la session ; le bail existant empêche un ancien worker de
  remplacer le travail d'un nouveau run.

Les appels dont le fournisseur n'a pas communiqué le résultat ne peuvent pas être
reconstitués sans interroger ce fournisseur. La reprise garantit l'absence de
doublon de **sauvegarde locale**, pas l'exactly-once d'une requête distante dont
l'issue est inconnue.

## Complétude et progression

Le plan conserve `generation.required_task_ids`, `missing_task_ids`, `complete`.
Un assemblage comprenant des emplacements provisoires demeure partiel, même si
sa sauvegarde a réussi. La rédaction calcule ses sections terminées à partir des
brouillons présents, pas du nombre d'appels ou des intentions du modèle. En fin
d'exécution, une rédaction incomplète n'est plus laissée `running`.
Une synthèse générique ne peut pas faire terminer un objectif de plan sans plan
sauvegardé, y compris lorsque la réserve d'outils de finalisation est atteinte.
Propositions et assemblage respectent ensemble le plafond d'outils existant.

Les lectures réellement nouvelles restent possibles. Des pages répétées, même
demandées avec des listes de champs différentes, ne remettent plus indéfiniment
la sauvegarde à plus tard : les affirmations reçues sont identifiées par leur
texte et leurs preuves. Les sections prêtes peuvent être écrites sans attendre
une autre section dont les sources sont encore indisponibles. Une analyse déjà
terminée est collectée avant de préparer d'autres téléchargements synchrones.

Les checkpoints de propositions ne réindexent pas tout le corpus ; les sauvegardes
validées mettent toujours à jour les tables dédiées dans la même transaction.

## Run LiDAR mesuré

Run `843fbe5d102a4cf090d73383b52b9d12`, du 18/10 à 23:30:57 au 23:40:58
(Paris), délai atteint à 600,109 s. La section « 1/14 » était déjà enregistrée
dans le run précédent : zéro nouvelle section sauvegardée pendant ce tour.

- Neuf récupérations de texte intégral : 219,1 s d'intervalles d'outil.
- Deux vérifications bibliographiques : 41,5 s.
- Huit réponses de lecture scientifique : somme des durées 243,2 s, avec chevauchements.
- Neuf réponses de section : somme 77,8 s ; cinq appels `write_section`, tous en `read`.
- Vingt-six appels de lecture de fiches, cinq récupérations de contexte projet.
- Dernier appel sans réponse finale : environ 125,5 s avant l'arrêt global.
- Aucune tentative `write_section(save)` ; donc aucun refus de sauvegarde dans ce run.

Les catégories ne s'additionnent pas : lecture en arrière-plan et récupération
des sources se chevauchent. Le compteur d'étape « compréhension : 89,3 s » inclut
des préparations documentaires ; son appel modèle n'a duré que 9,3 s. Le code
attribue désormais cette préparation à la lecture.

Le défaut principal est l'orchestration de la progression vers le livrable,
aggravée par les accès documentaires coûteux ; les validateurs n'ont pas bloqué
une sauvegarde qui n'a jamais été demandée. Le coût enregistré est 1,103676 USD
pour 187 459 tokens d'entrée et 17 692 de sortie, plus 0,256144 USD de réserve
pour usage non communiqué. Ce sont des estimations du logiciel, pas une facture.

## Validation et limites

Tests hors réseau, client modèle factice, bases temporaires isolées et lectures
des archives SQLite en mode `ro`. Les cas couvrent JSON invalide, réponse échouée,
preuves inconnues, refus exact, comptabilité unique, annulation, perte de worker,
reprise ciblée, idempotence et pages répétées. Les replays historiques utilisent
la version de plan archivée correspondante plutôt que les nouveaux plans écrits
depuis leur création. Les assertions d'ordre strict ont été remplacées par la
vérification des artefacts et de la concurrence effective.

Ces tests ne prédisent pas la latence ni la qualité scientifique d'un modèle réel.
Une source inaccessible ou un contenu refusé doit rester une lacune visible. Les
600 secondes et les plafonds financiers n'ont pas été augmentés. Le backend actif
n'a pas été redémarré : il faudra charger le code corrigé avant le prochain test
réel, qui n'a pas été lancé dans ce travail.

Résultats : 433 tests réussis lors du passage complet V4, puis 53 tests ciblés
réussis sur les derniers ajustements de finalisation, de budget et de persistance,
dont les cas de checkpoint avant perte du worker. Compilation Python en mémoire
et `git diff --check` valides. Empreinte de la base de conversations inchangée
avant/après le travail :
`7661e02927ed4973541d3eaaf1be3337a82378a9e25dd6f66b3fea8c824c29b6`.
