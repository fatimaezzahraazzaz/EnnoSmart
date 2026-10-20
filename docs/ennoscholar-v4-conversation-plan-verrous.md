# EnnoScholar V4 : compréhension, couverture des verrous et plan

Corrections du 17 octobre 2026 sur `feature/ennoscholar-v4-diagnostic-link`, non commitées, à partir du run réel `5768bedd` (session `ed686234`).

## Le run analysé

**Contexte**

- Conversation liée au diagnostic 1, avec quatre verrous « Garder ».
- Message envoyé : « donne moi le plan pour rediger mon etat de l'art avec les verrou et pour chaque paragraphe cherche les artcile pertinaat ».

**Mesures du run**

| Indicateur | Valeur |
|---|---|
| Arrêt | `timeout` à 600,2 s |
| Appels au modèle | 10, dont 9 d'exploration et 1 de publication interrompu |
| Tokens | 199 156 en entrée, 8 996 en sortie |
| Coût | 0,98 $ mesuré et 0,39 $ réservé non rapporté (l'appel coupé) |
| Recherches | 6 recherches scientifiques, 65 notices |
| Outils | 8 appels `get_project_context`, 2 analyses en texte intégral |
| Réponse | synthèse de secours, aucun plan |

### Causes confirmées par la trace

1. **Le temps est passé dans deux longues analyses.** Les tours 7 et 9 ont duré 147 s et 142 s (2 773 et 2 495 tokens de sortie).
   - Le contrôle de temps ne regardait que le temps restant avant un tour, pas la durée de ce tour. Le tour 9 a démarré avec 232 s restantes (au-dessus de la réserve de 150 s).
   - La publication a donc commencé avec 86 s seulement et a été coupée par l'échéance.
   - Le délai de 90 s par requête ne borne pas un flux qui continue de produire.
2. **Le plan n'avait pas d'étape propre.**
   - « plan » et « état de l'art » déclenchaient le parcours le plus lourd : recherche, sélection, lecture automatique de chaque finaliste, comparaisons, blueprint, puis synthèse.
   - `manage_outline` n'a jamais été appelé. Le plan n'existait qu'en texte dans la synthèse finale.
3. **Le contexte du diagnostic était vide de faits techniques.**
   - 256 des 270 faits sont « non rattachés à un verrou » dans ce diagnostic.
   - Le paquet de 3 000 caractères ne transmettait que les titres et justifications des verrous : aucun fait technique sur 256.
   - Le modèle a donc appelé `get_project_context` 8 fois, sur les tours 1, 2, 4, 5 et 8.
   - Ces pages étaient très lourdes : 26,6 k caractères pour les verrous (avec le `source_json` brut), 41 k pour la synthèse.
   - Le tour 1 n'a servi qu'à ces récupérations, et le tour 2 a reçu 47 k caractères.
4. **L'outil projet détournait la lecture.** En mode lié, la lecture d'un article utilisait `tool_choice="required"` avec `get_project_context` disponible. Au tour 8, le modèle a relu le projet au lieu d'enregistrer l'analyse, ce qui a coûté un tour supplémentaire.
5. **La couverture n'était pas suivie par verrou.**
   - Il y a eu une requête par « famille ».
   - SegArchA/SegArchB n'a reçu qu'une requête générique.
   - programmation dynamique a été cherché sous « LiDAR road boundary extraction geometric trajectory », sans HMM, programmation dynamique ni biais systématique des contours.
   - Les 4 finalistes portaient sur les verrous 1 et 2. Rien ne signalait que les verrous 3 et 4 restaient non couverts.
6. **Semantic Scholar a répondu 429 à chaque requête**, et arXiv était vide pour 3 requêtes. Les résultats sont donc venus surtout d'OpenAlex. C'est une cause externe, visible dans `warnings`.
7. **Coût fixe des tours de conversation.** Chaque tour de conversation envoyait 17,5 k caractères de consignes et 48,4 k caractères de schémas d'outils. Ces schémas incluaient des outils sans entrée possible : rédaction sans plan, revue sans brouillon, comparaison sans fiche.

### Hypothèse non vérifiée

La lenteur de génération (environ 19 tokens/s sur les longues sorties) dépend du fournisseur et de la charge. Le code mesure désormais cette durée sur le run au lieu de la supposer.

## Corrections

### 1. Compréhension conversationnelle

Le nouvel outil `set_turn_goal` enregistre la lecture que **le modèle** fait du message. Il est appelé au premier tour, en parallèle des premiers outils :

- le livrable : `answer`, `literature_search`, `plan`, `plan_revision`, `comparison`, `deep_reading` ou `draft` ;
- les verrous visés ;
- la profondeur de lecture ;
- un éventuel changement de périmètre (`focus_lock_ids`), avec les mots exacts du consultant ;
- la demande reformulée.

L'orchestration adapte ses étapes existantes à cette lecture. Les expressions régulières restent le comportement par défaut lorsqu'aucun objectif n'est déclaré : le mode Express et les anciens parcours sont inchangés.

- **« Je préfère commencer par le deuxième. »** Le périmètre change avec `origin=consultant_message` et la citation exacte. Une citation qui ne figure pas dans le message, ou un verrou hors de la sélection confirmée, est refusé.
- **« Ce plan ne me convainc pas, améliore-le. »** Une lecture ou une comparaison déclenchée uniquement par l'état stocké (`work_type=state_of_art`, ou une lecture laissée par un tour précédent) attend désormais le premier tour du modèle. La lecture restante ne reprend que si le tour la demande encore. Le plan peut être refondu par la nouvelle action `manage_outline(replace)`, réservée à un plan sans brouillons ; l'ancienne version est conservée.
- **Pas de rédaction pour une demande de plan.** `draft` n'ouvre une rédaction que si la demande ne précise pas « ne rédige pas » ou « plan avant de rédiger ». Une demande explicite de rédaction garde son propre parcours.
- **Consignes ajoutées** :
  - comprendre dans le contexte ;
  - stratégie de recherche ;
  - plan argumentatif ;
  - ton naturel.

### 2. Couverture des verrous

`lock_coverage` dresse un état factuel par verrou actif, sans quota :

- requêtes attribuées ;
- publications retenues ;
- publications analysées ;
- sections du plan ;
- statut : `analysed`, `candidates_retained`, `searched_no_relevant_result` ou `not_searched`.

Cet état s'appuie uniquement sur les `lock_ids` déclarés par le modèle dans `search_scientific`, `record_candidate_decisions` et les sections du plan. Les attributions sont validées contre les verrous actifs. Les recherches et décisions sans verrou sont comptées, jamais devinées.

- **Visibilité** : l'état est transmis aux tâches de conversation, de sélection, de plan et de publication, ainsi qu'avec chaque recherche liée.
- **Rappel des verrous manquants** : tant qu'un verrou visé n'a reçu aucune recherche, la consigne du tour le nomme.
- **Sélection** : la consigne demande des finalistes couvrant chaque verrou pertinent, et de laisser visible un verrou sans candidat pertinent.

### 3. Recherche

- Les requêtes déjà faites par verrou figurent dans `lock_coverage`.
- La consigne `SEARCH_STRATEGY` demande de partir de la difficulté et des essais du projet. Elle demande aussi de traduire en vocabulaire scientifique (synonymes, méthodes voisines, travaux fondateurs), de varier les formulations et d'exploiter les publications trouvées (`find_similar_articles`, références) plutôt que de répéter une requête.
- Cette consigne est aussi transmise aux tâches de sélection et de reprise.
- Aucun appel ni plafond de recherche n'est ajouté.

### 4. Contexte EnnoDiagnostic

**Le paquet actif garde son plafond de 4 200 caractères.** Il en fait un meilleur usage :

- titres des verrous ;
- objectif principal ;
- faits liés au verrou ;
- puis `project_technical_context` : les méthodes, démarches, paramètres, métriques et résultats généraux du projet.

Règles de ce contexte technique :

- les faits sont ordonnés par proximité lexicale avec les verrous actifs et alternés par catégorie ;
- les énoncés sont entiers, sans doublons ;
- chaque fait est étiqueté « non rattaché à un verrou » ;
- `lexical_overlap_with_locks` indique explicitement que la proximité n'est pas une relation établie.

Les justifications et `objective_points` sont ajoutés seulement s'il reste de la place. L'entité canonique d'un verrou, qui reformule son titre, reste récupérable.

**Ce que change le budget.** Avec 18 000 caractères de mémoire, le paquet obtient `budget//4` au lieu de `budget//6`, soit 4 200 au lieu de 3 000. Sur la session réelle, il transmet désormais 8 faits techniques au lieu de 0, par exemple :

- le lissage par programmation dynamique ;
- les indicateurs Dice et « wrong-confident » ;
- l'agrégation par intersections puis union ;
- les seuils de confiance ;
- l'essai 5 de la méthode géométrique.

**`get_project_context` côté modèle.** Les pages transmises au modèle omettent le `source_json` brut et les chemins de provenance. Les statuts sont conservés : rôle, temporalité, revue. La vue consultant (route API) et le snapshot sont inchangés. Mesures sur la session réelle :

| Page | Avant | Après |
|---|---|---|
| Verrous | 26 639 | 2 721 caractères |
| Faits actifs | 16 946 | 2 456 caractères |
| Faits généraux | 18 576 | 4 330 caractères |
| Synthèse | 41 069 | 14 514 caractères (les points structurés sont paginés par `kind=facts`) |

**Lecture d'un article** : ni paquet projet, ni consigne liée, ni outil projet. L'outil `analyze_article` est de nouveau forcé. `get_project_context` reste disponible pour les comparaisons, le blueprint et le plan.

### 5. Plan scientifique

Lorsque l'objectif déclaré est `plan` ou `plan_revision` :

1. Les finalistes sont lus seulement sur leurs dimensions essentielles, dans la limite du temps disponible.
2. Aucune comparaison ni blueprint n'est imposé, sauf si `reading_depth=deep`.
3. Une fois la recherche, la sélection et les lectures possibles terminées, une étape plan demande `manage_outline`. Elle intervient aussi au lieu d'une synthèse lorsque le modèle veut publier, ou lorsqu'un budget est atteint.

Chaque section reçoit :

- `scientific_objective` (la question scientifique) ;
- `lock_ids` (nouveau, validé contre les verrous sélectionnés) ;
- les publications et leur rôle, réutilisables entre sections ;
- `experimental_results` (méthodes et résultats à exploiter) ;
- `project_connection` (comparaison au projet) ;
- `limitations` ;
- `missing_information`.

Le contexte du plan comprend :

- `plan_sources` : les publications retenues avec leur résumé, leur motif de sélection et leurs verrous ;
- `lock_coverage` ;
- les fiches enregistrées ;
- le contexte projet.

**La réponse est le plan enregistré**, rendu sans appel supplémentaire au modèle :

- le message naturel du modèle (`consultant_message`) ;
- les sections ;
- la couverture de chaque verrou, y compris « recherché, aucune publication pertinente retenue » et « absent du plan ».

Le plan reste un brouillon non approuvé. Un `finalize_response` du modèle avant ce plan est différé et remplacé par l'étape plan.

### 6. Réponses et tâches incomplètes

- Un plan provisoire nomme ses lacunes. La réponse ne bascule plus vers la synthèse de secours lorsqu'un plan a été enregistré.
- Les validations scientifiques de publication sont inchangées.
- Une réponse conversationnelle sans outil reste directe.

### 7. Tokens, coût et temps

**Durée mesurée des appels.** Avant de lancer l'analyse d'un article, ou de poursuivre une lecture en cours, l'orchestrateur vérifie que l'appel attendu se termine avant la réserve de publication, qui reste à 150 s.

- L'estimation de durée est la plus longue génération longue mesurée dans le run.
- Avant toute mesure, elle vaut 20 % de l'échéance, soit 120 s pour 600 s.
- Une lecture qui ne tient pas est conservée pour un tour suivant. Le temps restant sert alors au livrable demandé.
- Aucun budget, délai, plafond, modèle ou tarif n'est modifié.

**Schémas d'outils réduits.** Au tour de conversation, le modèle ne reçoit pas les outils dont l'entrée n'existe pas encore :

- `write_section` et `search_for_section` sans plan ;
- revue et visuels sans brouillon ;
- fiches, lacunes et blueprint sans analyse ;
- comparaison sans deux analyses.

| Élément mesuré | Avant | Après | Écart |
|---|---|---|---|
| Schémas des outils, premier tour lié | 49 341 | 33 615 caractères (y compris `set_turn_goal`, 1 847 caractères) | −15 726 |
| Consignes de conversation | 16 303 | 18 230 caractères | +1 927 |
| Consignes de plan | 4 175 | 4 719 caractères | +544 |
| Consignes de sélection | 3 406 | 3 993 caractères | +587 |
| Contexte et consignes d'un tour de lecture liée | — | — | environ −5 400 caractères |

Le contexte de lecture n'inclut plus le paquet projet, la consigne liée ni l'outil projet.

**Une fois un plan et des fiches enregistrés**, tous les outils redeviennent disponibles. Le tour de conversation coûte alors environ 1 900 caractères de plus qu'avant : consignes de compréhension, de recherche et de plan, plus `set_turn_goal`.

**Estimation pour le scénario du run réel.** C'est une estimation, pas une facturation. Sur le chemin corrigé, l'essai rejoué hors ligne fait 6 appels :

| Étape | Appels |
|---|---|
| Conversation : objectif et recherches | 2 |
| Sélection | 1 |
| Analyses | 2 |
| Plan | 1 |
| Publication et lectures du projet | 0 |

Le plan est enregistré à t ≈ 395 s sur 600 s. En reprenant les tokens réels des appels analogues (4,2 caractères par token mesurés sur ce run, aux tarifs locaux de 4 $ et 20 $ par million) et les réductions mesurées ci-dessus, le coût est estimé entre 0,55 $ et 0,70 $ :

| Étape | Coût estimé |
|---|---|
| Deux tours de conversation et de recherche | 0,16 à 0,20 $ |
| Tour de décision | ≈ 0,10 $ |
| Deux analyses | ≈ 0,19 $ |
| Plan | ≈ 0,08 $ |
| Checkpoint de sélection éventuel | ≈ 0,07 $ |

Le run réel avait coûté 0,98 $ mesurés et 0,39 $ réservés, sans plan.

## Tests

`tests/ennoscholar_v4/test_turn_goal.py` contient 9 tests hors ligne, sans réseau ni OpenAI :

- **Reproduction du run.**
  - Quatre verrous, faits du projet non rattachés, message réel et durées réelles (analyses de 145 s).
  - Le modèle factice recherche d'abord deux verrous ; la consigne nomme alors les verrous 3 et 4.
  - Deux lectures ont lieu ; la troisième est conservée parce qu'elle finirait dans la réserve.
  - Le plan est enregistré avec un verrou par section, sans `finalize_response`, avant l'échéance.
  - La couverture vaut `analysed`, `analysed`, `candidates_retained` et `searched_no_relevant_result`.
  - Lecture : aucun outil, paquet ni consigne projet, et l'outil forcé.
- **Changement de périmètre en langage naturel**, avec la citation exacte ; refus d'une citation inventée, d'un verrou non sélectionné et de verrous en Express.
- **Révision du plan** : aucune lecture préalable, un seul tour, `replace` et versions conservées.
- **`replace` refusé** si des brouillons existent ; verrous inconnus refusés.
- **Demande de plan** : aucune rédaction ouverte, et une publication prématurée différée vers le plan.
- **Pages projet côté modèle** sans charge brute ; snapshot intact.
- **Express** : `set_turn_goal` offert, aucun outil sans entrée.
- **Session réelle `ed686234`**, lue en lecture seule (le test est ignoré si la base est absente) :
  - le paquet de 4 200 caractères transmet méthodes, résultats et paramètres, dont programmation dynamique ;
  - aucune attribution à un verrou ;
  - quatre verrous dans la couverture ;
  - snapshot inchangé.

Non-régression : V4, V3 et V3.1. Le résultat figure dans le bilan.

## Limites restantes

- **Pas d'essai LLM réel.** La qualité des requêtes, des sélections et des plans dépend du modèle. Les tests prouvent l'orchestration, pas la pertinence scientifique.
- **Couverture par verrou** : elle repose sur les `lock_ids` déclarés par le modèle. Une recherche non attribuée reste comptée comme telle.
- **Rapprochement lexical** : il ordonne seulement le contexte général. Il ne crée aucune relation.
- **Semantic Scholar** : la limitation 429 sans clé API dégrade la découverte et `find_similar_articles`. Une clé `SEMANTIC_SCHOLAR_API_KEY` relève de la configuration et de la décision du consultant ; elle n'est pas modifiée ici.
- **Estimation de durée** : elle reste empirique sur le run. Le premier appel long utilise 20 % de l'échéance.
- **Objectif `draft`** : il ne rédige que si la demande ne s'y oppose pas. Une ambiguïté réelle doit être levée par le modèle en posant une question.
- **Validation finale** : le plan rendu n'est jamais approuvé automatiquement. L'approbation humaine reste requise.
- **Avant un essai réel** : redémarrer le backend.
