# EnnoScholar V4 — performance, lots 1 et 2

18 octobre 2026, branche `feature/ennoscholar-v4-diagnostic-link`, sans commit.

Conditions :

- aucun appel OpenAI ;
- aucune conversation existante modifiée (base ouverte en lecture seule pour les mesures) ;
- modèle, tarifs, plafonds de tours, d'outils, de tokens, de coût et délai inchangés.

## Ce qui change

### Lot 1 — cache et sorties

- **Préfixe stable.** Les consignes et les outils ne contiennent plus rien de propre au tour.
  - La consigne du tour (tours restants, article lu, étape) est le **dernier élément de l'entrée** (`Consigne de ce tour : …`).
  - Toutes les requêtes d'une même tâche ont donc un préfixe identique, comme le cache du fournisseur l'exige.
  - Le paramètre `prompt_cache_key` (`ennoscholar-v4-<tâche>`) les regroupe. `ENNOSCHOLAR_V4_PROMPT_CACHE_KEY=0` le retire sans toucher au reste.
- **Références courtes.** Chaque passage d'une lecture porte une `ref` (E1, E2…), et le modèle cite ces refs.
  - Avant toute validation, `resolve_evidence_refs` les remplace par les `evidence_id` exacts. Les contrôles de preuve existants s'appliquent ensuite sans changement.
  - Une ref inconnue reste telle quelle et est refusée par ces contrôles. Les références d'un autre article ne sont jamais réécrites.
- **Journalisation par appel** :
  - durée (`call_seconds`) et tâche ;
  - taille de chaque sortie d'outil (`output_chars`) ;
  - au niveau du run, `timings` : appels, secondes de modèle, durée réelle, analyses parallèles, concurrence maximale.
  - Les tokens en cache étaient déjà enregistrés.

### Lot 2 — profondeur selon l'intention, parallélisme, temps

- **Profondeur décidée par le modèle (`set_turn_goal`).**
  - Un plan est construit à partir des résumés retenus, des fiches déjà enregistrées et du contexte projet. Il ne déclenche des lectures intégrales que si le modèle demande `reading_depth=deep`.
  - Les demandes d'analyse, de comparaison et d'approfondissement lisent les textes complets.
  - Un finaliste laissé « pending » par le modèle n'est plus lu automatiquement ; un article retenu, accepté par le consultant ou nommé l'est.
- **Analyses en parallèle**, jusqu'à 3 par run. Pendant qu'une analyse s'exécute, les lectures suivantes sont préparées et leurs analyses lancées.
  - **Écritures** : les threads ne font que l'appel réseau. Préparation, sauvegarde, preuves, attribution, état et activité restent dans le fil principal, dans l'ordre de lancement. Les cartes obtenues sont identiques à celles du mode séquentiel (test).
  - **Limites de débit** : au plus 4 appels en arrière-plan à la fois dans le processus, tous runs confondus.
  - **Échec d'un appel en arrière-plan** (429 ou transport) : une seule nouvelle tentative, dans le fil principal.
  - **Réservations cumulées** : chaque analyse en cours, y compris celle attendue par le fil principal, garde sa réservation de coût et de tokens jusqu'à son traitement ; un lancement n'a lieu que si le total, plus la réserve de publication, reste sous les plafonds.
  - **Tours, outils et nombre d'analyses** sont comptés comme en séquentiel, sauvegardes à venir incluses.
  - **Annulation ou arrêt** : l'usage réel est compté pour les analyses terminées non traitées ; la réservation est comptée pour celles en cours. Leur lecture reste reprenable.
- **Budget de temps sur le chemin critique.**
  - L'estimation d'une génération longue n'est jamais inférieure à la valeur par défaut (20 % du délai). Dans `85a926db`, 88 s avaient été mesurées avant une analyse de 170 s.
  - Une lecture ne démarre que si elle, puis la réponse finale (au moins la plus longue génération mesurée), tiennent avant l'échéance.

## Mesures avant / après

### Mesuré hors ligne

Mesures faites avec le code réel et des durées d'appel rejouées depuis les traces (analyse : 145 s ; synthèse : 152 s), sans fournisseur réel.

| Mesure | Avant | Après |
|---|---|---|
| 3 analyses + synthèse : durée | 587 s (séquentiel, `bench_before`) | **351 s** en parallèle, contre 660 s en séquentiel avec le même code et la même horloge |
| Même scénario, délai de 600 s | 587 s, à 13 s de l'échéance | **348 s**, 3 analyses |
| Préfixes distincts (consignes + outils) sur 4 appels | 4 | **2** (un par tâche ; les 3 analyses partagent le même) |
| Concurrence maximale d'analyses | 1 | 3, limitée par les réservations (test) |

Le mode séquentiel du nouveau code s'arrête à 2 analyses dans 600 s (506 s) : la nouvelle règle de temps protège la réponse finale.

L'horloge échelonnée compte aussi le temps de calcul Python, multiplié par 100 : environ 60 à 70 s simulées dans les deux modes. La comparaison parallèle/séquentiel est faite à horloge identique.

**Sorties d'analyse avec références courtes** : −19 % de tokens, soit 39 071 → 31 554 sur les 8 fiches réelles des sessions `4a7314e4`, `ed686234` et `23236f69`, recomptées avec `tiktoken` (`o200k_base`). C'est une mesure sur des données réelles, mais à condition que le modèle cite bien les refs.

### Estimé

Ces valeurs sont à vérifier lors de votre essai réel.

- **Cache.** Environ 50 % des tokens d'entrée des runs passés étaient un préfixe répété ; seuls 1,3 % étaient en cache. Le gain dépend de la remise appliquée à gpt-5.6-sol.
  - À vérifier : `cached_input_tokens` par appel dans l'activité.
- **Durée d'une analyse.** Environ −19 % si le temps suit le nombre de tokens de sortie, soit environ 145 s → 118 s.
- **Plan comme `85a926db`** (profondeur `core`) : plus aucune lecture intégrale avant le plan.
  - Les tours 7 et 8 (88 + 170 s, ≈ 0,15 $) et le `get_fulltext` de 46 s disparaissent.
  - Le plan démarre vers 2 min 40 au lieu de 8 min 10, donc sans timeout attendu.
- **Coût en tokens.** Le parallélisme ne le change pas : mêmes appels, mêmes tokens.

## Tests

`tests/ennoscholar_v4/test_performance.py` contient 14 tests :

- préfixe identique par tâche, consigne en dernier, `prompt_cache_key`, désactivation ;
- résolution des refs ;
- refs plus courtes que les identifiants ;
- journalisation ;
- 3 analyses en parallèle, cartes identiques au séquentiel ;
- réservations limitant la concurrence ;
- nouvelle tentative après un échec en arrière-plan ;
- annulation (lectures reprenables, réservation comptée) ;
- estimation de durée ;
- aucune nouvelle vague si elle et la réponse finale ne tiennent pas ;
- plan `core` sans lecture, plan `deep` avec lectures ;
- finalistes « pending » non lus.

Tests adaptés :

- `test_turn_goal.py` : un plan `core` ne lit plus de texte intégral ;
- `test_selection_review.py` : la consigne du tour est lue dans l'entrée.

**Non-régression V4, V3 et V3.1 : 457 tests réussis.** Les tests de performance sont stables sur 3 exécutions successives.

## Limites

- **Pas d'essai réel.**
  - L'acceptation de `prompt_cache_key` par l'API pour gpt-5.6-sol et le taux de cache sont à vérifier au premier essai. En cas d'erreur 400, mettre `ENNOSCHOLAR_V4_PROMPT_CACHE_KEY=0`.
  - La citation effective des refs par le modèle est aussi à vérifier.
- **Débit chez OpenAI.** Les limites de débit du compte restent externes. 3 analyses simultanées par run et 4 appels en arrière-plan par processus sont des choix prudents, non mesurés en charge.
- **Lots non traités** : la sélection unique après recherche (lot 3), la réutilisation des fiches entre conversations (lot 4) et les sections en parallèle (lot 5).
