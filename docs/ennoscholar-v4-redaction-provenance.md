# Rédaction CIR : preuves transmises, provenance typée et relations EnnoDiagnostic

19 octobre 2026, branche `feature/ennoscholar-v4-diagnostic-link`, sans commit.

Conditions :

- aucun appel OpenAI ;
- base `ennoscholar_v4_test.db` lue en lecture seule ; les rejeux travaillent sur des copies en mémoire ou dans des fichiers temporaires ;
- modèle, tarifs et plafonds inchangés ;
- validateurs conservés.

Point de départ : le run réel `b52f582a` (session LiDAR `23236f69`, « Continue la rédaction… ») :

- 6 propositions parallèles, 5 refus, 1 section sauvegardée ;
- un appel séquentiel de réparation de 299 s ;
- arrêt à 602 s ; 0,52 $ estimés, plus 0,22 $ réservés.

## Causes confirmées et corrections

### 1. Preuves perdues à la compression

**Cause**

- Le paquet de section ajoutait les passages sources en dernier et les retirait en premier.
- La mémoire d'une section était limitée à 18 000 caractères, alors qu'un passage exact fait 1,2 à 1,9 k caractères.
- Reconstruit hors ligne sur la vraie session : pour les 14 sections, **0 passage complet transmis** ; 1 à 5 preuves de fiches citées sans leur texte.

**Correction** : `writing_context.section_dossier`.

- Une affirmation de fiche ne voyage **jamais sans ses passages exacts**. Elle est choisie selon la question de la section, en alternant les champs (résultats, méthodes, mécanismes, protocole, paramètres, limites) et les publications.
- Le budget est servi dans cet ordre :
  1. lignes de tableau, jusqu'à 30 % (les nombres sont validés sur leurs cellules) ;
  2. affirmations avec preuves, jusqu'à 75 % ;
  3. autres passages des sources analysées, jusqu'à 90 % ;
  4. ensuite seulement, blueprint, sections précédentes et relations.
- Chaque source porte son état et le nombre d'affirmations disponibles par champ (`claims_available`).
- Les vues inutiles à une section ne lui sont plus envoyées : index du corpus, couverture des verrous, articles sélectionnés, registre de lecture.
- Mémoire d'une section : `SECTION_CONTEXT_CHARS = 40 000` ; d'une partie de plan : `PLAN_PART_CONTEXT_CHARS = 26 000`. Ces valeurs restent **dans** `max_context_chars` (85 000, inchangé).
- Le contenu final est vérifié : dans la file parallèle, une section `ready` sans aucun passage transmis n'est pas générée. La trace de chaque appel indique `proofs_sent`.
- `evidence.exact_source_valid` met en cache l'empreinte de chaque snapshot de document (contenu adressé, jamais modifié). La construction d'un contexte de section passe de **3 à 15 s à 0,01-0,06 s**.

### 2. Section « prête » mal définie

**Cause** : une source épuisée ou indisponible, sans aucune fiche, rendait la section « prête ». La génération était ensuite refusée.

**Correction**

- `writing_context.source_readiness` : chaque source est `analysed` (fiche avec preuves exactes utilisables), `card_without_proofs`, `to_analyse` ou `unavailable` (limite documentaire nommée). La présence d'un résumé ne vaut jamais lecture.
- `agent.section_state` : chaque section est dans l'un de ces états.
  - `ready` : au moins une source analysée ; les sources indisponibles sont nommées comme limites et ne bloquent pas.
  - `needs_analysis` : une lecture en cours, ou aucune source citable alors que des lectures peuvent encore aboutir.
  - `framing` : aucune publication citable, mais des faits projet. La section est rédigée sans résultat bibliographique, avec les faits `[projet:…]`.
  - `blocked` : rien de citable ; la section est mise de côté avec sa cause.
- Le chemin séquentiel ne lance plus une section non prête. Si aucune lecture ne peut plus démarrer, les sources en attente deviennent des limites documentaires.

### 3. Preuves scientifiques et preuves du projet

**Cause**

- Des identifiants EnnoDiagnostic `ev_fast_…` étaient cherchés parmi les preuves d'articles (« Comparaison sans preuve réellement lue »).
- Une phrase sur l'essai SegArchA/SegArchB **du projet** exigeait une citation d'article (« Attribution non vérifiable »).
- Des `claims` paraphrasaient le texte.

**Correction** : nouveau module `provenance_check.py`.

- `WriteArgs.project_evidence_ids` et `DraftClaim` avec un type : `scientific`, `project`, `comparison` ou `interpretation`.
- Les identifiants diagnostic donnés comme preuves ou marqueurs d'article sont reconnus et réécrits `[projet:id]`. Ils doivent exister dans le snapshot, être des citations vérifiées et avoir été effectivement transmis au modèle (`delivered_project_evidence_ids`, alimenté par `ScholarTools.delivered`).
- Validation par type :
  - un résultat de publication exige une preuve de l'article (une preuve projet ne l'établit pas) ;
  - un fait du projet exige une preuve projet (une publication ne prouve pas ce que le projet a fait) ;
  - une comparaison exige les deux ;
  - une interprétation est marquée comme inférence, avec ses appuis.
- Chaque phrase qui cite une preuve devient une affirmation typée par ses preuves.
- Attribution : un nom (SegArchA, SegArchB…) présent dans le passage **projet** cité dans la même phrase est justifié par cette preuve. Il ne l'est pas par le passage d'un autre essai.
- Les marqueurs `[projet:…]` sont rendus en référence documentaire, par exemple « (projet : projet_essais.pdf, Essai 3) ».
- Section de cadrage : texte et preuves projet suffisent ; aucun marqueur de publication sans publication analysée.
- **Inchangé** : une affirmation déclarée absente du texte reste refusée, avec la liste exacte. Les contrôles d'attribution entre publications, de nombres, de calculs et de bibliographie sont conservés. La consigne demande désormais `claims` vides ou recopiés mot pour mot.

### 4. Réparations lentes et mal réparties

**Cause** : les sections refusées étaient exclues des lots parallèles, puis réparées une à une en séquentiel, en commençant par la première ; plus de 430 s ont été passées sur une seule section.

**Correction** : une seule file de rédaction (`parallel_sections`).

- Jusqu'à 3 tâches par lot.
- Les sections refusées y reviennent avec leur dernière proposition et le motif exact (`recovery_exchange`), **par nombre de tentatives croissant**.
- 3 tentatives au plus par section.
- Chaque section valide est sauvegardée aussitôt (registre `TaskResults`, idempotence par `operation_id` inchangée).
- Si deux sections sont mises de côté sans aucune sauvegarde entre elles, la rédaction s'arrête (`section_save_failed`) : la cause est systémique.

### 5. Annulation coûteuse et reprise

**Cause**

- `cancelled()` rechargeait la conversation (JSON de 8 Mo) à chaque événement du flux.
- `recovery_exchange()` ne retrouvait plus le dernier refus après `[refused, refused, failed]`.

**Correction**

- `service.cancelled` lit deux colonnes (statut d'annulation, propriétaire et `active_run_id`) au plus une fois par seconde. Un ancien worker dont le bail est repris s'arrête. Propriété et accès au projet sont revérifiés toutes les 30 s.
- `TaskResults.last_refusal` retrouve la dernière tentative refusée qui contient un appel complet, même après un échec ou une interruption plus récents, y compris après rechargement du JSON.

### 6. Relations EnnoDiagnostic exploitées

**Données réelles** : le snapshot LiDAR ne contient aucun lien explicite entre faits (`fact_group` à 1 fait, `rnd_approach.steps` vide). Les liens établis sont documentaires :

- chaque verrou cite ses propres passages ;
- chaque fait garde ses passages et la section du document dont il vient.

**Correction** : `diagnostic_link.work_units` et `lock_dossier`.

- Pour chaque verrou, les **unités de travail** du projet : une section du document projet (essai, étude), avec ses rôles.
  - Rôles : démarches, méthodes, paramètres, résultats, limites, objectifs. Chaque fait garde ses preuves.
  - Lien de l'unité au verrou : `declared` (diagnostic) ou `lock_evidence_passage` (passage cité comme preuve du verrou).
  - Les autres faits de la même section sont marqués `same_document_section` : rapprochement documentaire à confirmer, jamais causal.
  - `roles_not_documented` liste les lacunes, qui ne sont jamais complétées.
- `rnd_reference_date` : la date de démarrage R&D du diagnostic, ou un statut `unknown` explicite. L'année CIR n'en tient pas lieu.
- Le dossier est transmis aux parties de plan (9 000 caractères), aux sections (9 000, pour les verrous de la section) et aux comparaisons. Il n'est jamais réduit à des identifiants : plancher de 4 500 caractères, omissions comptées.
- La consigne de section demande de confronter ce que les publications ont établi, et dans quelles conditions, à ce que le projet a tenté et obtenu.

**LiDAR, constaté** :

| Verrou | Unité de travail | Rôles documentés | Non documentés |
|---|---|---|---|
| 1 | Essai 1 | 2 démarches, 5 paramètres, 2 résultats | méthodes, limites |
| 3 | Essai 3 (SegArchA/SegArchB) | 1 démarche, 1 résultat | méthodes, paramètres, limites |

Le découpage du PDF projet range des passages de l'Essai 5 sous le titre « Essai 4 ». Chaque fait garde donc son propre énoncé et sa preuve.

### Exploitation réelle des relations dans la rédaction

**Constat** : transmettre les unités de travail ne garantit pas que le texte les exploite.

**Correction** : `provenance_check.project_relations`.

- Chaque brouillon enregistre `project_relations` : pour chaque preuve projet citée, le verrou, l'unité de travail, le rôle (démarche, méthode, paramètre, résultat, limite) et le fait.
- Il enregistre aussi `confrontation` : unités disponibles et citées pour les verrous de la section, rôles cités, phrases de comparaison, unités réellement comparées à une publication, preuves rattachées à d'autres verrous.
- La revue déterministe signale :
  - `project_confrontation_missing` : section liée à des verrous dont les travaux existent, mais aucun n'est cité ;
  - `project_comparison_missing` : travaux du projet cités sans phrase les reliant à une publication ;
  - `project_fact_outside_section_locks` : preuve d'un autre verrou.
- Ces signaux ne bloquent pas la sauvegarde. La qualité sémantique de la comparaison reste une revue humaine.

**Rejeu `b52f582a`, section par section** :

| Sections | Phrases de comparaison publication/projet | Revue |
|---|---|---|
| 6 propositions réelles, écrites avant la correction sans preuve projet disponible, puis réparées | 0 | signalées |
| 6 sections écrites par le rédacteur simulé conforme à la consigne | 6 sections confrontent une démarche et un résultat de la même unité du projet à une publication | sans signal |

Une section simulée cite une preuve d'un autre verrou : elle est détectée par la revue.

## Mesures

Ce sont des simulations hors ligne, **pas des mesures réelles**.

| | Avant | Après |
|---|---|---|
| Passages exacts transmis par section (14 sections LiDAR) | 0 | 1 à 9, et toutes les preuves des affirmations transmises |
| Faits projet par section | 0 (diagnostic réduit) | 2 à 11, structurés par unité |
| Construction du contexte d'une section | 3 à 15 s | 0,01 à 0,06 s |
| Taille d'une requête de section | ~19-20 k caractères (7,4-9,4 k tokens réels) | ~32-38 k caractères (≈ 9-11 k tokens estimés) |
| Contrôle d'annulation (copie SQLite, JSON de 8 Mo) | 52,6 ms × chaque événement (≈ 106 s pour 2 000 événements) | 7,7 ms, au plus 1 fois par seconde (≈ 0,5 s pour 60 s) |
| Rejeu `b52f582a` | réel : 1 section, 602 s, 9 appels, 0,52 $ + 0,22 $ réservés | simulé : 14/14 sections, 465 s, 22 appels, 3 appels au plus par section, ≈ 1,72 $ estimés |
| Rejeu `2fd068ff` | réel : 0/12 en 146 s | simulé : 12/12 |

Le rejeu `b52f582a` utilise les **6 propositions réelles** du modèle. Celles-ci restent refusées pour des motifs exacts : affirmations absentes du texte, nom SegArchA sans preuve projet, section sans preuve. Les réparations et les autres sections sont **simulées** par un rédacteur qui applique le motif du refus. La qualité de la réparation réelle reste à mesurer.

Le coût estimé augmente, car chaque section reçoit ses preuves (≈ 4 000 tokens d'entrée de plus par section, ≈ 0,016 $ au tarif local), et parce que le rejeu rédige 14 sections au lieu d'une.

## Tests

- `tests/ennoscholar_v4/test_provenance_drafting.py` (17 tests) :
  - affirmation scientifique, fait projet et comparaison sauvegardés avec leurs preuves ;
  - `ev_fast_…` reroutés vers le projet ;
  - nom d'architecture justifié par la preuve projet, et refusé avec la preuve d'un autre essai ;
  - type d'affirmation validé selon son origine ;
  - affirmation absente du texte, preuve projet inconnue, non consultée ou non vérifiée : refusées ;
  - mauvaise attribution entre publications : refusée ;
  - section de cadrage ;
  - reprise après `[refused, refused, interrupted]` ;
  - unités de travail réelles par verrou, avec leurs lacunes ;
  - contenu transmis réel sur le LiDAR et le maillage 3D (`4a7314e4`) ;
  - rejeu `b52f582a` ;
  - interruption puis reprise, sans régénérer les sections sauvegardées ;
  - relations projet enregistrées par brouillon et confrontation manquante signalée par la revue.
- Non-régression V4, V3 et V3.1 : **517 tests réussis**.
- Rejeu : `tests/ennoscholar_v4/replay_b52f582a.py`.
- Tests existants adaptés au nouveau paquet, avec la même intention : affirmations complètes, comptes annoncés, preuves transmises, sections précédentes non rédigées signalées, mémoire de section bornée par `SECTION_CONTEXT_CHARS`.

## Limites restant à vérifier en réel

- Qualité de l'argumentation produite par le vrai modèle avec ce dossier ; taux de refus et de réparation réels.
- Durée réelle d'une sauvegarde de section avec ~10 k tokens d'entrée.
- Coût réel, dont la part en cache.
- Cohérence entre sections rédigées en parallèle.
- Les unités de travail dépendent du découpage du document projet par EnnoDiagnostic.
- La réutilisation des fiches entre conversations n'est pas implémentée.
