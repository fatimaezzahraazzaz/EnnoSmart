# EnnoScholar V4 — bilan des étapes 2 à 5

Date : 16 octobre 2026.

Worktree modifié exclusivement : `C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart`.

Les étapes 2, 3, 4 et 5 sont terminées et validées localement. Aucun test scientifique réel n’a été lancé.

## Vérifications successives

| Étape | Tests réussis après correction |
| --- | ---: |
| 2 — Lecture approfondie | 118 |
| 3 — Orchestration et budget | 140 |
| 4 — Comparaison | 145 |
| 5 — Synthèse, fenêtres et régressions complètes | 178 |

Résultat final : **178 réussis, zéro échec, zéro erreur de collecte, zéro test ignoré**. La suite émet 185 avertissements du runtime de test.

Les deux fichiers de tests progressifs/reprise déjà présents au début ne pouvaient pas être collectés : leurs fonctions attendues n’existaient pas encore. Ils ont été intégrés à la validation finale après implémentation. Les tests API ont été ajoutés à la campagne dès restauration de leurs dépendances depuis les caches locaux, sans téléchargement. Les comptes des premières étapes correspondent aux sous-ensembles effectivement exécutables à chaque étape.

Le cas approfondi entièrement simulé valide 36 passages examinés, 20 preuves sauvegardées, 5 mécanismes, 5 résultats et 4 limites. Il ne s’agit pas d’une nouvelle exécution réelle de DL-Polycube.

## Code réellement modifié

Seulement six fichiers Python de l’agent V4 ont changé par rapport à leur état initial de cette intervention :

- `agents/EnnoScholarV4/scientific_reading.py` : `refresh_task`, `reading_tasks`, `update_coverage`, `next_task`, `reading_allowance`, `recovery_context`, `prepare_reading`, `completed`.
- `agents/EnnoScholarV4/evidence.py` : `choose_passages`.
- `agents/EnnoScholarV4/agent.py` : `ScholarChatAgent.run`.
- `agents/EnnoScholarV4/argumentation.py` : `claim_entries`, `packet_claims`, `article_reading_status`, `comparison_source_signature`, `relation_current`, `refresh_relations`, `comparison_batches`, `argument_packet`, `bounded_argument_packet`, `source_signature`, `save_blueprint`. `active_articles` conserve son mécanisme de sélection des sources actives ; les filtres de préparation de comparaison sont séparés.
- `agents/EnnoScholarV4/context.py` : `ANALYSIS_FIELDS`, `model_context`.
- `agents/EnnoScholarV4/toolkit.py` : schémas `Analysis`, `AnalyzeArgs`, `CompareArgs`, `BlueprintArgs` et branches lecture/sauvegarde/comparaison/lecture du blueprint de `ScholarTools.execute`.

Tests ajoutés : `test_reading_depth.py`, `test_reading_orchestration.py`, `test_comparison_readiness.py`, `test_synthesis_windows.py`. Le helper du fichier préexistant `test_progressive_reading.py` transmet maintenant le vrai paquet de preuves lors de sa simulation de réception ; il ne contourne pas le contrôle de l’étape 1. Les autres modifications de tests déjà présentes au début ont été conservées.

## Mécanismes corrigés

### Lecture

Une sauvegarde ne termine plus automatiquement la tâche. Le suivi utilise l’empreinte du document, un curseur stable, une fenêtre en attente, les preuves examinées et les preuves incorporées dans la fiche cumulative. Une interruption conserve la fenêtre à retransmettre. Un document modifié redémarre son curseur sans supprimer les anciennes analyses, citations ou snapshots.

Les dimensions problème, contribution, méthodes, mécanismes, paramètres, protocole, résultats, métriques et limites sont suivies séparément. L’outil accepte une revue explicite sans affirmation lorsqu’un passage n’apporte pas de résultat : elle exige les preuves effectivement reçues, les champs examinés et une justification. Elle ne crée aucune affirmation ni analyse complète fictive. Abstract seul et corps tronqué restent partiels.

Le LLM peut continuer à demander ses propres lectures ciblées, requêtes, tableaux et offsets. Les lectures ciblées supplémentaires ne font pas sauter les fenêtres suivantes. Les passages déjà reçus ne vident plus artificiellement une nouvelle fenêtre lorsqu’il reste des passages non fournis.

### Orchestration

Une demande de nouvelle découverte laisse la recherche et la décision du modèle établir les priorités. Les articles prioritaires reçoivent une première couverture avant approfondissement ; le rang scientifique lui-même n’est pas recalculé en Python. Une ancienne cible de téléchargement ne devient pas implicitement la cible d’une nouvelle demande multi-articles.

La limite cachée de cinq articles a été supprimée au profit des limites configurées. L’allocation existante de lecture est partagée avec les continuations scientifiques sans ajout répété par fenêtre. Les contrôles portent sur tours, outils, temps, tokens, coût estimé et réserve de synthèse. Les causes d’arrêt restent distinctes et les tâches incomplètes reprenables. Les tentatives bibliographiques infructueuses ne sont pas répétées automatiquement sans progrès dans le même run.

### Comparaison

Les lectures prioritaires encore réalisables empêchent une comparaison prématurée. Les matrices indiquent abstract/texte intégral, couverture et caractère partiel. Une supériorité demande des protocoles documentés, des résultats et des preuves du corps ; les nombres absents des preuves citées sont rejetés, dans les résultats déclarés comme dans l’énoncé de la relation.

Les relations sont liées à une signature des sources et des analyses. Une relation identique est réutilisée. Une ancienne relation reste conservée mais devient obsolète si sa source change ; elle est exclue du paquet scientifique courant et du blueprint tant qu’elle n’est pas réexaminée.

### Synthèse et plan CIR

Les paquets ne sont plus limités aux quatre premiers articles ni à une affirmation par catégorie. Les fenêtres équilibrent plusieurs affirmations des méthodes, résultats et limites, puis transmettent les autres catégories. Les citations et conditions scientifiques ne sont pas raccourcies pour tenir dans le budget : des objets entiers sont différés avec leurs offsets et compteurs.

La provenance répétée est factorisée dans un index de sources, référencé depuis les preuves ; les citations exactes et identifiants restent présents. Les comparaisons et blueprints peuvent consommer plusieurs fenêtres et reprendre leurs offsets. Les outils de comparaison et de lecture du blueprint exposent également ces fenêtres récupérables. Une fenêtre impossible à transmettre signale un blocage explicite.

Le contexte final distingue les preuves, inférences, incertitudes et analyses partielles ; les faits du projet restent séparés des résultats publiés. L’éligibilité CIR n’est jamais déduite automatiquement.

## Préservation vérifiée

Dix-neuf contrôles de code ont réussi. La comparaison utilise l’état du worktree avant intervention, y compris ses modifications non enregistrées de l’étape 1.

- `sources.py`, `store.py`, `network.py`, `config.py`, `prompts.py`, `publication.py` et `tools/article_analysis.py` sont inchangés octet pour octet.
- Les branches `search_scientific`, `search_collected_articles`, `find_similar_articles` et `record_candidate_decisions`, ainsi que les fonctions `search` et `verify`, sont inchangées structurellement.
- `transmitted_evidence_ids`, `evidence_projection`, `compact_exchange`, `delivered`, `request_inputs` et le bloc protégeant les preuves du focus actif sont inchangés structurellement.
- Aucun plafond financier, temporel, de tokens, d’outils ou de configuration de production n’a été modifié.
- Aucun changement du frontend ni d’un autre agent ; aucune opération Git modifiant l’historique.
- Aucun fichier lu ou copié depuis `D:\EnnoSmart`. Aucun appel LLM externe, téléchargement, recherche scientifique réelle ou test payant.

Les snapshots initiaux, rapports JUnit et contrôles de préservation sont conservés localement dans `.codex_tmp/remaining_corrections_20261016/`.

## Redémarrage et limites connues

Le service V4 est exécuté dans le backend FastAPI : `agents/EnnoScholarV4/service.py` utilise un `ThreadPoolExecutor` nommé `scholar-v4`, et la lecture documentaire utilise le pool `scholar-v4-reading`. Aucun worker Celery séparé n’est appelé par cette route V4. Le processus à relancer pour charger le code est donc le backend Uvicorn/FastAPI servant `/scholar-v4`, depuis ce worktree C:, avec tous ses processus serveurs s’il est lancé avec plusieurs workers Uvicorn. Les threads V4 seront recréés avec ce processus.

Aucun processus Python/Uvicorn/Celery applicatif actif n’a été détecté sous Windows au contrôle final. Aucun serveur n’a été démarré ou redémarré pendant l’intervention.

Ce worktree ne possède actuellement ni `.venv\Scripts\python.exe` ni `.env`. Le script `run_backend.ps1` attend cet environnement. Il faut donc utiliser un environnement backend déjà configuré pour lancer ces sources C: ; l’environnement isolé employé pour les tests ne constitue pas une configuration de production. Aucune configuration n’a été créée ou copiée pour contourner cette limite.

Les tests locaux vérifient les contrats et reprises avec des sources et réponses simulées. Ils ne valident pas le comportement scientifique d’un vrai LLM ni l’accès actuel aux publications. Si une source ou le budget manque, une analyse ou comparaison reste explicitement partielle. Les essais réels à effectuer ensuite par l’utilisateur restent DL-Polycube seul, puis la demande multi-articles prévue.
