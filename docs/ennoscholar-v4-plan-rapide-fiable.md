# EnnoScholar V4 — plan rapide et fiable (corrections 1 à 4, rejeu du run `e7bae458`)

18 octobre 2026, branche `feature/ennoscholar-v4-diagnostic-link`, sans commit.

Conditions :

- aucun appel OpenAI ;
- base SQLite lue en lecture seule ; le rejeu travaille sur une copie en mémoire ;
- modèle, tarifs et plafonds inchangés.

## Cause traitée

Dans `e7bae458`, l'orchestrateur a lancé, avant toute lecture du message, comparaisons et blueprint sur les deux anciens finalistes : 377 s et 0 résultat. Le déclencheur était les mots « état de l'art » et « plan ».

Ensuite, le plan a été forcé avec 134 s restantes. Il a été coupé par l'échéance, ce qui a produit la synthèse de secours.

## Corrections

### 1. Compréhension avant exécution

Si les mots du message pouvaient déclencher une lecture, une comparaison ou une rédaction, un appel court a lieu d'abord :

- `set_turn_goal` est forcé ;
- le contexte est réduit à 9 000 caractères ;
- les consignes « compréhension » sont dédiées.

Le modèle déclare alors livrable, verrous et profondeur, et **les étapes suivent cette déclaration**, jamais les mots :

- un plan `core` n'ouvre ni comparaison, ni blueprint, ni lecture intégrale ;
- une rédaction détectée par les mots est annulée si le modèle comprend autre chose : l'état de rédaction précédent est restauré.

Le mode dégradé ne s'applique que si l'appel ne déclare rien (erreur). Le comportement antérieur s'applique alors à l'identique.

### 2. Sélection juste après les recherches

- Pour un plan ou une recherche bibliographique, la sélection a lieu dès que les recherches ont couvert tous les verrous visés, au lieu d'attendre la fin des tours.
- Si un verrou n'a aucune publication retenue et que le temps du plan reste garanti, **une seule** ronde de recherche ciblée est proposée. Une seconde sélection n'a lieu que si cette ronde rapporte de nouveaux candidats.
- **Un candidat « pending » n'est jamais finaliste** (`not_retained`).

### 3. Budget et production du plan

**Plan par parties en parallèle** : une partie cadre (introduction et synthèse), puis une partie par verrou actif, ou par axe déclaré en Express (2 à 4 axes).

- Chaque partie part d'un contexte propre, sans l'échange de recherche (77 k caractères dans `e7bae458`). Elle est générée par l'outil interne `propose_plan_sections`.
- Les sections sont nettoyées (identifiants inconnus retirés), assemblées dans l'ordre puis enregistrées une seule fois par `manage_outline`, avec les validations habituelles.
- Une partie en échec devient une section provisoire signalée.
- **Réservations et limite de débit.**
  - La réservation de coût et de tokens est cumulée pour toutes les parties avant leur lancement.
  - Au plus 6 appels en arrière-plan à la fois dans le processus.
  - Si les réservations dépassent les plafonds, le plan est généré en un seul appel.

**Temps du plan.**

- Estimation : 90 s pour une partie, 180 s (ou la plus longue génération mesurée) pour un plan en un appel.
- L'étape plan démarre au plus tard quand le temps restant atteint cette estimation plus une marge de 20 s.
- S'il reste moins que l'estimation, aucun appel voué à être coupé n'est lancé.

### 4. Réponse fiable : plan provisoire sans appel au modèle

Si le temps manque ou si le plan est coupé, la réponse est un **plan provisoire** et non la synthèse de secours. Il est structuré par verrou, ou par axe en Express :

- **publications retenues**, avec leur statut (« analysée (extraits du texte intégral) », « analysée sur résumé » ou « retenue sur résumé, non analysée ») et leur motif ;
- **candidats non évalués à examiner (non validés)**, attribués au verrou de la recherche qui les a trouvés ;
- **lacunes** ;
- une question pour poursuivre.

Il n'affirme aucun résultat.

Dans le plan complet, une publication non retenue citée par le modèle est marquée « candidat non évalué, à vérifier ».

### Réduction des tours et des temps morts

- Les recherches d'un même tour interrogent les fournisseurs en parallèle. Les statistiques par fournisseur sont gardées par fil d'exécution, et le stockage reste séquentiel dans l'ordre du modèle.
- Le temps est mesuré **par étape** (`timings.stages`) : compréhension, conversation et recherche, sélection, lecture, argumentation, plan, rédaction, publication.

## Mesures hors ligne

**Comment c'est mesuré.**

- Les durées des appels sont celles mesurées dans les traces réelles :
  - compréhension : 5 s ;
  - tours de conversation : 9,4 / 7,2 / 10,8 s ;
  - comparaisons : 17,7 s ;
  - blueprints : 139,7 et 121,4 s ;
  - sélection : 39 s ;
  - plan en un appel : 160 s ;
  - partie de plan : 45 s ;
  - recherches : 2 à 15 s.
- L'horloge simulée accélère ×100 les appels et les recherches, en comptant une seule fois les appels simultanés. Le traitement local reste en temps réel.
- Les décisions du modèle factice sont déterministes.

### Rejeu exact du run `e7bae458`

État de la session reconstitué avant le run ; chaque requête reçoit les notices réelles de cette requête.

| | Code avant corrections | Après |
|---|---|---|
| Résultat | timeout, synthèse de secours | **plan enregistré**, 6 sections (introduction, 4 verrous, synthèse) |
| Durée | > 600 s (719 s simulées) | **168 s** (3 rejeux identiques) |
| Argumentation automatique | 342 s | **0** |
| Compréhension | — | 5,6 s |
| Conversation + recherches | 27 s de modèle + recherches séquentielles | **36,6 s** au total (7 recherches, en parallèle par tour) |
| Sélection | aucune | **80,9 s** (2 sélections : initiale, puis après la ronde de lacune) |
| Plan | coupé | **45,2 s** (5 parties en parallèle) |
| Appels LLM | 10, dont un coupé | 10, dont 5 parties parallèles |

Le coût estimé du rejeu est d'environ 0,45 $, avec des tokens d'entrée estimés à 4 caractères par token, contre 0,83 $ plus 0,26 $ réservés en réel. **C'est une estimation**, pas une mesure de facturation.

### Plan en maillage 3D (Express, 3 axes déclarés)

| | Un seul appel de plan | Plan par parties |
|---|---|---|
| Durée | 177 s | **61 s** |
| Étape plan | 160 s | **45 s** |
| Appels LLM | 3 | 6 (4 parties en parallèle) |

## Tests

`tests/ennoscholar_v4/test_plan_pipeline.py` contient 14 tests :

- les mots seuls ne lancent jamais de comparaison : le plan compris n'en lance pas, la comparaison comprise si ;
- une rédaction détectée par les mots n'est pas ouverte quand un plan est compris ;
- un candidat « pending » n'est jamais finaliste ;
- plan LiDAR par verrou en parallèle, avec cadre (5 appels simultanés, étape plan inférieure à 100 s) ;
- une partie en échec devient une section provisoire ; les candidats non retenus sont étiquetés ;
- sans temps pour le plan : plan provisoire, sans aucun appel ;
- plan coupé par l'échéance : plan provisoire et réservation comptée ;
- le plan provisoire ne présente jamais un candidat comme validé ;
- recherches d'un tour en parallèle (au moins 15 s gagnées), résultats identiques au mode séquentiel ;
- statistiques fournisseurs gardées par fil d'exécution ;
- maillage : plan par axes en parallèle ; sans axes, un seul appel de plan ;
- rejeu exact de `e7bae458` : plan livré, pas d'argumentation, durée inférieure à 300 s, coût estimé inférieur au coût réel.

Le harnais de rejeu est `tests/ennoscholar_v4/replay_e7bae458.py`. Le test correspondant est ignoré si la base locale est absente.

Tests adaptés au nouveau parcours :

- les faux modèles partagés reçoivent l'appel de compréhension (sans objectif déclaré, donc le comportement antérieur s'applique) ;
- `test_turn_goal.py` et `test_selection_pipeline.py` passent au plan par parties, avec sélection après recherche et ronde de lacune ;
- `test_performance.py` et `test_attribution.py` comptent l'appel de compréhension.

**Non-régression V4, V3 et V3.1 : 471 tests réussis**, stables sur 3 exécutions.

## Limites

- **Modèle factice.** Les durées sont réelles, mais les choix du modèle factice sont déterministes. Un vrai modèle peut faire plus de tours de recherche ou des parties plus longues.
- **Estimations de durée.** 90 s par partie et 180 s pour un plan entier sont des estimations ; les `call_seconds` de l'essai réel permettront de les ajuster.
- **Cohérence entre verrous.** Elle repose sur la partie cadre et sur les verrous multiples par section ; elle est à juger sur l'essai réel.
- **Appel de compréhension.** Il ajoute environ 5 s et un petit contexte, quand une étape automatique était possible.
- **Plan provisoire.** Il organise l'existant sans le juger : les candidats non évalués restent à évaluer.
- **Autres agents.** Les autres agents EnnoSmart et la configuration ne sont pas modifiés.
