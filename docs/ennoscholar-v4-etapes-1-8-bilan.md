# EnnoScholar V4 — bilan des étapes 1 à 8 et protocole de tests réels

Date : 16 octobre 2026. Worktree : `C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart`.

Aucun appel OpenAI, aucune recherche réelle et aucun test payant n'ont été lancés. Aucun plafond
(`config.py`) n'a été modifié. Aucune donnée, session ou cache n'a été supprimé. Les autres agents
EnnoSmart et `D:\EnnoSmart` n'ont pas été touchés.

## Cause démontrée de l'échec multi-articles

Exécution `6f5f57f4` (session `c138b18c`, `ennoscholar_v4_test.db`) :

- **DL-Polycube et DDPM-Polycube ont été collectés mais jamais présentés au modèle.**
  - Ils occupaient les positions 27 et 22 d'un lot de 29 résultats.
  - Une recherche ne montrait que ses 8 premières notices, dans l'ordre de réponse des fournisseurs.
- **`search_collected_articles` ignorait la requête du modèle.** Sa recherche « polycube » a renvoyé des articles hors sujet.
- **Le plafond de 80 nouveaux candidats a été consommé par deux requêtes (50 + 29).**
  - Les recherches suivantes n'ont rien rapporté (déduction : leur réponse n'était pas enregistrée).
  - AMBER n'a jamais été collecté.
- **Les 6 prioritaires n'ont reçu qu'une fenêtre chacun**, soit environ 6 passages sur 60, avant l'épuisement du budget de tours.
- **Aucune comparaison n'a été faite** : elle exigeait une lecture complète des articles.

Le moteur de requêtes et les adaptateurs n'étaient pas en cause. Ils sont inchangés.

## Corrections par étape

| Étape | Correction | Fichiers |
| --- | --- | --- |
| 1. Visibilité | Alternance des fournisseurs selon leur propre rang. Index des titres non présentés du lot (`batch_index`). `search_collected_articles` ordonné par recouvrement lexical avec la requête (présentation seulement, aucun score scientifique). Index des candidats : ceux du message en cours d'abord, puis les non examinés. | `store.py`, `toolkit.py`, `context.py` |
| 2. Collecte | Le plafond inchangé ne compte que les œuvres réellement nouvelles : les doublons entre fournisseurs et les œuvres redécouvertes fusionnent. Une recherche refusée ne consomme plus de tour et le refus est tracé (`refused_searches`). Le budget restant est indiqué au modèle. | `store.py`, `toolkit.py` |
| 3. Sélection | Après `record_candidate_decisions`, le modèle reçoit les candidats du message jamais vus et les finalistes sans texte intégral connu. Il dispose d'au plus 2 tours de révision avant les lectures longues. Une demande de découverte (« quels sont les travaux les plus pertinents ») attend la nouvelle sélection. Les anciennes tâches d'un autre sujet ne sont plus approfondies. | `agent.py`, `toolkit.py`, `scientific_reading.py` |
| 4. Lecture | Rotation entre les articles sélectionnés : la fenêtre suivante va à l'article qui en a reçu le moins. Fenêtres ciblées sur les dimensions manquantes de chaque fiche (méthodes, protocole, résultats, limites). Fenêtres toujours pleines (6 passages). Aucun passage lu deux fois. Pas de retéléchargement après un échec. `analyze_article(read)` accepte des `evidence_ids` exacts. | `scientific_reading.py`, `toolkit.py` |
| 5. Comparaison | Un article devient comparable quand ses dimensions principales sont documentées (relation marquée partielle). Quand le budget de lecture s'épuise lors d'une demande d'état de l'art ou de comparaison, l'agent compare ce qui est documenté au lieu de passer directement à la publication. L'approfondissement continue tant que le budget le permet. | `agent.py`, `argumentation.py`, `toolkit.py`, `scientific_reading.py` |
| 6. Synthèse et mémoire | Termes propres à d'anciens tests supprimés (AUROC/PRO/Mean, ATHENATEST, SAR2SAR, MERLIN). Les fiches stockées ne sont jamais compressées (test). Chaque article garde méthode, résultat et limite dans le contexte final. La rédaction indique toutes les affirmations disponibles (`analysis_available`) et comment obtenir la fiche complète sans recherche. | `context.py`, `prompts.py`, `writing_context.py` |
| 7. Validation humaine | Nouvel outil `record_consultant_decisions` (garder, en attente, écarter, rétablir, ordre), attribué aux mots exacts du consultant. Les décisions du consultant prévalent sur un nouveau classement du modèle. Les articles en attente ne sont ni lus, ni comparés, ni sélectionnés. « Maintenant rédige » lance la rédaction quand un plan existe. Accepter un article ne valide ni une affirmation, ni le plan, ni une éligibilité CIR. API : `PATCH …/articles/{id}` accepte `decision` (compatible avec `selected`) ; nouvelle route `POST …/articles/order`. | `decisions.py` (nouveau), `toolkit.py`, `service.py`, `models.py`, `catalogue.py`, `writing_progress.py`, `prompts.py`, `routers/scholar_v4.py` |
| 8. Interface | Panneau « Publications proposées » (garder, en attente, écarter, ↑↓, fiche et preuves exactes, demander d'autres publications). Décisions et fiche disponibles dans la bibliothèque, avec une catégorie « en attente ». Éditeur de plan (renommer, déplacer, fusionner, retirer, ajouter, rédiger une section). Espace de rédaction accessible dès que le plan existe. | `scholar-v4-selection.tsx` (nouveau), `ennoscholar-v4-chat.tsx`, `scholar-v4-workspace.tsx`, `lib/scholar-v4.ts` |

Les performances (requêtes refusées gratuites, pas de relecture ni de retéléchargement, fenêtres pleines,
réutilisation des fiches au lieu d'une nouvelle recherche, arrêt de l'approfondissement quand le budget
se resserre) sont intégrées aux étapes 2, 4, 5 et 7.

## Tests

Suite V4 : **208 réussis**, contre 179 au départ. Commande :

`PYTHONPATH=".codex_tmp/evidence_transmission_deps;.codex_tmp/offline_test_deps" .codex_tmp/lot34venv/Scripts/python.exe -m pytest tests/ennoscholar_v4 --import-mode=importlib -q -p no:cacheprovider --ignore=tests/ennoscholar_v4/test_router_session.py`

Nouveaux fichiers de tests :
- `test_candidate_visibility.py`
- `test_collection_budget.py`
- `test_selection_review.py`
- `test_multi_article_reading.py`
- `test_comparison_flow.py`
- `test_synthesis_memory.py`
- `test_consultant_decisions.py`
- `test_service_decisions.py`

Autres vérifications :
- Les suites V3 et V3.1 passent (50 tests).
- Le typecheck frontend (`tsc --noEmit`) passe.
- Le cas d'analyse approfondie simulée d'un article seul (36 passages, 20 preuves, 5/5/4) reste vert.

## Limites restantes

- **Tests simulés uniquement.** Le comportement d'un vrai modèle face à `batch_index`, à la révision de sélection et à `record_consultant_decisions` reste à vérifier.
- **Fichiers de test non exécutables ici.** `tests/*/test_router_session.py` exige `python-multipart`, absent hors ligne. Les nouvelles routes sont testées au niveau du service.
- **Interface non vérifiée visuellement.** Ce worktree n'a ni `.venv` ni `.env` pour lancer le backend.
- **Pas de rendu LaTeX des équations.** KaTeX n'est pas installé ; l'ajouter (`remark-math`, `rehype-katex`) demande un téléchargement et votre accord.
- **Contexte de synthèse finale limité.** Avec 6 articles, la limite de 24 000 caractères (inchangée) transmet environ 1 à 2 affirmations par catégorie et par article. Les fiches complètes restent stockées et accessibles à la rédaction.
- **Transmission EnnoDiagnostic → EnnoScholar non implémentée.** C'était l'étape 9, hors du périmètre validé.
- **Pas de réutilisation entre sessions.** Une nouvelle conversation part d'une bibliothèque vide ; la réutilisation des textes intégraux d'une session à l'autre n'a pas été vérifiée.
- **Détection par expressions régulières.** La découverte (`selection_ready`) et « Maintenant rédige » restent des heuristiques.

## Protocole de tests réels (à lancer seulement avec votre accord)

Chaque essai part d'une nouvelle conversation. Pour chacun, relever dans `scholar_v4_runs.data` :
- `tool_counts`, `rounds`, `usage` (tokens, coût estimé), `duration_seconds` ;
- `exploration_stop_reason` et `library_counts` ;
- les événements `responses` (`offered_article_ids`).

1. **DL-Polycube seul** (« Approfondis DL-Polycube… »).
   - Au moins 30 passages examinés, au moins 15 preuves sauvegardées.
   - Au moins 4 mécanismes, au moins 4 résultats chiffrés, au moins 3 limites.
   - Aucune recherche inutile.
2. **Maillage 3D par IA, multi-articles** (la demande du dernier test).
   - DL-Polycube et DDPM-Polycube apparaissent dans `offered_article_ids` ou dans un `batch_index`, puis font l'objet d'une décision.
   - Au moins 4 finalistes. Chaque finaliste en texte intégral reçoit au moins 2 fenêtres et atteint `core_covered`.
   - Au moins une relation de comparaison enregistrée.
   - La réponse cite pour chaque finaliste au moins un résultat et une limite avec preuve.
   - Proportion de candidats non évalués signalée. Temps au plus 600 s, coût au plus 4 USD (plafonds actuels).
3. **Verrou fourni.**
   - Les travaux proches du verrou sont identifiés.
   - Les limites démontrées par les publications sont distinguées des difficultés propres au projet.
   - Aucune limite n'est inventée.
4. **Validation dans le chat** : « Garde le 1 et le 3, mets le 2 en attente, place le 3 en premier ».
   - `consultant_decisions` est enregistré avec la citation exacte.
   - Aucune recherche ni lecture n'est relancée.
5. **Suivis**.
   - Demandes : « Compare le deuxième article au premier », « Montre-moi les résultats numériques », « Cherche des publications plus récentes ».
   - Les deux premières sont traitées sans nouvelle recherche. La troisième recherche sans répéter les requêtes déjà faites.
6. **Plan et rédaction**.
   - Ajouter une partie sur les GNN, réordonner, puis « Maintenant rédige ».
   - Les sections sont rédigées avec citations, tableaux comparatifs fondés sur les preuves et numeric_claims vérifiés.
   - Une modification du texte dans l'interface conserve les analyses.
   - Export DOCX et PDF.

## Corrections P1 à P5 après le premier test réel (run 5768a223)

Diagnostic (traces SQLite, lecture seule) :
- Le lanceur force `gpt-5.6-sol` (`openai.local.env`, changé avec la clé). Toutes les exécutions réussies utilisaient `gpt-6.1-sol`.
- Le modèle forcé écrit des requêtes booléennes. Résultat : Semantic Scholar renvoie 0 notice sans avertissement, et arXiv des articles d'apprentissage automatique hors sujet.
- Deux requêtes larges (`limit_per_source` = 20) ont saturé le plafond de 80 dès le premier tour. Quatre recherches ciblées (hexaédrique, décomposition en blocs) ont ensuite été refusées.
- `search_collected_articles` a renvoyé 0 résultat pour des étiquettes `family` inconnues.
- Aucune `record_candidate_decisions` n'a eu lieu en 6 tours. Il n'y a donc eu ni vérification, ni lecture, ni comparaison.
- La publication a refusé toutes les références non vérifiées.

| Correction | Effet | Fichiers |
| --- | --- | --- |
| P1 Modèle | Non appliquée : le passage à `gpt-5.6-sol` accompagnait un changement de clé, et la disponibilité de `gpt-6.1-sol` avec la nouvelle clé ne peut pas être confirmée sans appel OpenAI. Ligne à modifier si vous le confirmez : `ENNOSCHOLAR_V4_MODEL=gpt-6.1-sol` dans `C:\Users\hp\.codex\runtime\ennoscholar-v4\openai.local.env` (supprimer alors les deux lignes de prix, ou mettre 2 et 10). | — |
| P2 Requêtes | Une requête booléenne est convertie en mots-clés pour Semantic Scholar et arXiv ; OpenAlex reçoit la requête d'origine. La requête envoyée à chaque fournisseur est tracée, et un fournisseur sans résultat est signalé au modèle. Consignes : un axe par requête, 4 à 10 mots-clés, `limit_per_source` de 8 à 15. | `sources.py`, `toolkit.py`, `prompts.py` |
| P3 Collecte | Plafond de 80 inchangé, mais partagé : une recherche garde au plus un tiers de la place restante (minimum 10), en conservant le meilleur rang de chaque fournisseur. Une étiquette `family` inconnue est ignorée et signalée. Les mots génériques ne servent plus à ordonner la recherche locale. Les comptes réels sont journalisés. | `toolkit.py`, `store.py`, `agent.py` |
| P4 Sélection | Au dernier tour d'exploration, si des candidats ont été présentés et qu'aucune décision n'a été prise, `record_candidate_decisions` est imposée avec leurs abstracts. La lecture automatique démarre ensuite si la demande l'exige. | `agent.py`, `context.py` |
| P5 Publication | En phase de publication seulement, une vérification bibliographique bornée est tentée une seule fois : 6 références au plus, présentées mais non vérifiées, DOI ou arXiv uniquement. | `publication.py`, `toolkit.py`, `agent.py` |

Tests : 297 réussis (V4 avec routes 234, V3 et V3.1 63) avec `D:\EnnoSmart\.venv\Scripts\python.exe`.

Deux anciens tests ont été adaptés au partage du plafond :
- le fixture de 70 notices place les articles pertinents dans la part d'une recherche ;
- le compte de `batch_index` passe de 29 à 27.

Nouveaux tests :
- `test_provider_queries.py`
- `test_collection_quality.py`
- `test_selection_checkpoint.py`
- `test_final_verification.py`

## Corrections du 17 octobre 2026 (runs 80a58dae et 0c604eb8)

| Défaut confirmé (trace) | Cause | Correction (fichier, fonction) |
| --- | --- | --- |
| Résultats pertinents situés au-delà de la part de collecte, perdus sans trace | Part limitée à un tiers de la place restante ; lignes abandonnées sans signalement | `toolkit.py` `ScholarTools.search` : part portée à la moitié de la place restante (plafond de 80 inchangé) ; nouvelle fonction `unstored_report` qui signale les résultats non enregistrés avec la pagination de chaque fournisseur ; `store.py` `ArticleStore.insert` mémorise les lignes abandonnées |
| arXiv : une requête OR devenait une conjonction de tous les mots, d'où 0 résultat | `provider_query` réduisait la requête à des mots-clés, puis la réécriture arXiv exigeait chaque mot | `sources.py` `provider_query`, `_parse`, `_arxiv` : booléen explicite (alternatives entre guillemets reliées par OR, concepts reliés par AND, NOT traduit en ANDNOT) ; `_keywords` pour Semantic Scholar (pas de booléen, traits d'union remplacés par des espaces, termes exclus retirés) |
| Erreurs 429 de Semantic Scholar sur des recherches parallèles | Appels concurrents | `sources.py` `V4ScientificSearch._get` : un appel à la fois, espacé d'environ 1,1 s (`_get_paced` reprend l'ancien corps) |
| Sélection jugée sur des titres seuls (`batch_index`) | La révision ne fournissait que des titres | `agent.py` `selection_candidates` ; révision et point de contrôle avec abstracts dans `selection_candidates` ; `context.py` enregistre une fenêtre de présentation |
| Ancien focus traité comme article nommé (10 fenêtres pour AMBER, 1 pour les autres) | Filtre lexical sensible aux fautes de frappe | `agent.py` `run` : le focus n'est prioritaire que sur référence explicite (« cet article », « celui-ci »…) |
| Arrêt sur `context_budget` après la comparaison | Matrice avec les fiches complètes et compaction sans borne finale | `toolkit.py` (matrice résumée), `context.py` `compact_exchange` (référence aux objets conservés, jamais au-delà du budget) |
| Lecture qui consomme les outils nécessaires à la comparaison | Aucune réserve d'outils | `agent.py` `argument_tool_reserve`, arrêt de lecture vers la comparaison si au moins deux travaux sont comparables, vérification bibliographique groupée |
| Résultats chiffrés absents des articles lus une seule fois | Ordre de lecture dominé par les passages de méthode | `scientific_reading.py` `source_rows` : ordre alterné méthodes, protocole, résultats/tableaux, limites (même ensemble de passages) |
| Comparaisons descriptives | Consigne non quantitative | Consigne de comparaison (`agent.py`) : valeurs, métrique, données et conditions, sinon protocoles déclarés non comparables |
| Familles de solutions non couvertes | Aucun signal de couverture | `toolkit.py` `axes_status` (indicatif, lexical) et consigne générique dans `prompts.py`, sans aucun titre codé en dur |

Configuration OpenAI (P6). Le lanceur local `C:\Users\hp\.codex\runtime\ennoscholar-v4\start_backend.py` (hors dépôt) a été durci :
- il ne lit plus jamais `OPENAI_API_KEY` ni les variables `ENNOSCHOLAR_V4_*` depuis `D:\EnnoSmart\.env` ni depuis l'environnement hérité ;
- il exige `openai.local.env` avec `ENNOSCHOLAR_V4_MODEL=gpt-5.6-sol` et des tarifs ;
- il refuse une clé identique à l'ancienne clé partagée et contrôle le modèle effectif.

Constat sans afficher de secret :
- la clé présente dans `openai.local.env` est l'ancienne clé partagée, différente de la clé indiquée par l'utilisateur, d'où le refus de démarrage ;
- la nouvelle clé n'est configurée localement nulle part ; c'est à l'utilisateur de la configurer.

Tests : 308 réussis (V4 245, V3 et V3.1 63), avec `D:\EnnoSmart\.venv\Scripts\python.exe`.

## Mémoire persistante et contexte par tâche (17 octobre 2026, run 61b4624c)

Symptôme : « Rédige l'état de l'art complet… » avec 2/7 sections enregistrées s'arrête sur `context_budget` sans nouvelle section.

Cause reconstituée à partir des traces (14 tours, 24 `analyze_article`, 2 `compare_articles`, 0 `write_section`) :
1. **Relectures inutiles.** En rédaction complète, l'agent a relu en profondeur les 4 articles déjà analysés : 12 tours, alors que leurs dimensions clés étaient déjà couvertes.
2. **Mémoire non bornée.** `model_context` dépassait son propre budget : 37 000 à 40 000 caractères pour 18 000. Son repli « contexte volumineux » réinjectait sans limite les paquets de focus (`section_drafting`, `argumentation_focus`, `writing_context`, `scientific_reading_status`, messages).
3. **Focus périmés.** Un focus de comparaison laissé par le run précédent et la dernière section touchée étaient renvoyés avec chaque requête.
4. **Échange non compacté.** L'échange précédent (résultat complet de `compare_articles` : matrice et paquet, environ 47 000 caractères) pouvait occuper jusqu'à 66 000 caractères sans être compacté. La somme dépassait 85 000 caractères.
5. **Comparaison exhaustive inapplicable.** Une fenêtre de comparaison ne portait qu'environ 4 des 355 affirmations, car chaque preuve était transmise en entier (citation de 1 200 caractères). La comparaison exhaustive aurait demandé environ 90 tours.

Corrections (aucun plafond, modèle ni configuration OpenAI modifié) :

| Fichier | Fonction | Correction |
| --- | --- | --- |
| `context.py` | `context_task`, `TASK_KEYS`, `fit_focused`, `reading_digest`, `model_context` | Contexte propre à chaque tâche (lecture, comparaison, plan, section, sélection, reprise). Limite garantie sur la requête réellement envoyée (y compris l'échappement JSON). Le paquet de la tâche est reconstruit plus petit, sans jamais tronquer une affirmation. Projections toujours copiées. |
| `writing_context.py` | `section_terms`, `rank_claims`, `DEFERRABLE_FIELDS` | Pour chaque section : affirmations complètes les plus proches de la section en premier. Les champs secondaires sont reportés avant les méthodes, résultats et limites. `analysis_available` annonce tout ce qui existe. |
| `toolkit.py` | `get_analysis_claims` / `analysis_claims`, `compare_articles` | Relecture sans perte, page par page, d'une fiche complète avec ses preuves exactes. Après l'enregistrement d'une relation, le paquet déjà transmis n'est plus renvoyé. |
| `argumentation.py` | `bounded_argument_packet(proof_mode="reference")` | Fenêtres de comparaison et de plan argumentaire avec preuves par référence exacte (identifiant, section, page). Les citations restent stockées et les chiffres sont revérifiés à l'enregistrement. |
| `agent.py` | `request_inputs`, `run` | Budget commun mémoire et échange, l'échange étant compacté dans la place restante. Focus périmés recalculés à partir des reçus durables. Reprise de rédaction sans relecture des fiches déjà couvertes. `ARGUMENT_WINDOWS = 3` fenêtres par lot, en reprenant les positions conservées. `get_analysis_claims` est disponible pendant la rédaction d'une section. |
| `instructions.py` (nouveau) | `capture_instructions`, `relevant_instructions`, `instruction_packet` | Consignes du consultant en mots exacts, avec origine (message), portée (projet, plan, section, article, rédaction), nature (préférence ou décision), statut (active, remplacée, annulée) et ordre. Seules les consignes actives et pertinentes sont transmises, distinctes des faits scientifiques. |

Rejeu hors ligne de la requête réelle, sur une copie en mémoire de la session (modèle simulé) :
- avant : arrêt `context_budget` à 2/7 ;
- après : 7/7 sections en 11 tours (3 comparaisons, 3 plans argumentaires, 5 sections) ;
- requête maximale de 23 611 caractères, pour une limite de 85 000 ;
- sections 1 et 2 et fiches scientifiques inchangées.

Tests ajoutés (`tests/ennoscholar_v4/test_context_memory.py`) :
- A : reprise réelle de 2/7 à 7/7 dans le budget ; ingrédients du run réel dans des budgets serrés.
- B : plan puis rédaction complète en une demande, brouillons non approuvés.
- C : mémoire intacte et récupération sans perte (identifiants, passages connus, recherche ciblée).
- D : consignes globales, ciblées et remplacées.
- E : interruption à 3/7, puis reprise à la section 4.
- F : corpus de 324 notices, dont 24 fiches détaillées.
- G : comparaison de fiches à longues citations, en fenêtres bornées.

Tests : 333 réussis (V4, V3, V3.1).

## Résultats numériques et rédaction continue (17 octobre 2026, run 23e259a8)

Cause confirmée sur la preuve réelle `ev_e9f8caa4e35c19bb64d2af5b`. La section 5 citait le tableau 2 d'AdaptNet, « CPU time comparison… », de colonnes `Model | Meshnet | Graphnet` et de ligne `Prediction | 0.68 s | 0.79 s`. Les métriques y sont des libellés de ligne, les unités sont imprimées dans les cellules, et le tableau n'a ni `unit` ni `metric_labels`. Or `validate_number` :
- comparait la valeur à la cellule entière ;
- exigeait l'unité au niveau du tableau ;
- n'acceptait comme métrique que la colonne ou `metric_labels`.

Même l'affirmation exacte (0.79, s, Prediction, colonne Graphnet) était refusée. Les trois tentatives du modèle ont produit, dans l'ordre, les trois erreurs observées : valeur différente, unité différente, métrique absente. La troisième a arrêté toute la rédaction (`section_save_failed`).

Corrections :
- **`scientific_review.validate_number`**
  - La valeur reste exactement le nombre écrit dans la cellule, avec ou sans l'unité imprimée.
  - L'unité doit être celle de la cellule, de l'en-tête ou de la légende ; elle ne peut être vide que si la source n'en indique aucune.
  - La métrique doit reprendre mot pour mot le libellé de colonne, le libellé de ligne, une étiquette de légende ou un terme de la légende.
  - Chaque refus nomme la preuve, l'article, le tableau, la légende, les colonnes, la ligne, la cellule, les unités et les métriques acceptées. Il indique aussi qu'il faut retirer le chiffre et le signaler comme manquant si la métrique est introuvable.
  - Aucun contrôle n'est assoupli.
- **`toolkit.NumericClaim`** : chaque champ est décrit dans le schéma.
- **`agent.writing_failure`** : une section encore refusée après 3 corrections est mise de côté avec sa cause, et les sections suivantes sont rédigées dans la même exécution. Deux sections refusées d'affilée sans aucune sauvegarde indiquent une cause systémique et arrêtent l'exécution. Le document n'est jamais déclaré complet.
- **`writing_progress`** : statut `blocked` ; `next_section` ignore les sections mises de côté, et une nouvelle demande les retente. Le rappel « Plan en attente de validation humaine » est supprimé des messages de progression. Le message final indique que l'approbation finale reste à faire par le consultant, et `approval_required` est conservé.

Tests : `tests/ennoscholar_v4/test_numeric_claims.py`, reproduction du tableau réel. Rejeu hors ligne sur la session réelle à 4/7 : sections 5, 6 et 7 enregistrées en 7 tours. 345 tests réussis.

## Vérification P0/P1 hors ligne — reprise du 17 octobre 2026

Revue effectuée dans `C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart`, à partir de `git status` et des différences locales, sans commit, changement de branche, restauration destructive, recherche réseau ni appel API. Le travail non commité de Claude a été conservé ; une copie des fichiers locaux modifiés a également été prise avant les corrections de cette revue. Aucune base de session réelle n'a été utilisée ou modifiée par ces vérifications. Modèle, tarifs et plafonds globaux inchangés.

### P0 : attribution et intégrité scientifique

Les ajouts de Claude fournissent les identités des publications dans le contexte de rédaction, marquent `subject=background` les affirmations étayées exclusivement par des passages de travaux antérieurs sans autoréférence, contrôlent l'attribution avant sauvegarde et auditent les sections déjà enregistrées sans les réécrire. Les projections ne modifient pas les fiches conservées.

La revue a identifié et corrigé les défauts suivants :

| Défaut reproduit | Correction limitée |
| --- | --- |
| Une affirmation déclarée absente du texte était supprimée, puis remplacée par une correspondance automatique phrase/citation. Le retour ne gardait que cinq libellés tronqués à 160 caractères, sans origine ni preuves. | `toolkit.py` : sauvegarde refusée avec `saved=false`, explication et retour intégral de toutes les affirmations concernées (`claims_not_in_text`, `rejected_claims`, avec origine et preuves). Le rédacteur doit recopier la phrase exacte ou rétablir l'information étayée dans le texte. Les simples différences d'apostrophes, espaces, emphase ou marqueurs restent normalisées. Le brouillon précédent demeure intact. |
| Une affirmation pouvait employer une preuve consultée de l'article mais absente des preuves déclarées du brouillon. | `toolkit.py` : contrôle d'inclusion des preuves de chaque affirmation dans `evidence_ids` avant sauvegarde. |
| Un nom présent ailleurs dans l'article, ou dans une autre affirmation de sa fiche, suffisait pour accepter une phrase citant un passage qui ne le documentait pas. | `attribution.py` : seuls les passages cités dans la phrase et les affirmations de fiche appuyées par ces mêmes preuves peuvent documenter un nom extérieur à l'identité de la publication. Une autre citation déjà consultée du même article ne suffit plus. |
| Un nom de méthode cité dans l'abstract devenait une identité de la publication, même s'il s'agissait d'une méthode de référence. | `attribution.py` : les identités sont dérivées du titre et des auteurs ; les composants et travaux antérieurs demandent leurs preuves liées. |
| Une phrase nommant deux publications pouvait ne citer les preuves que d'une seule. | `attribution.py` : contrôle des publications nommées qui restent sans preuve dans la phrase, y compris dans une comparaison. Un auteur commun à plusieurs publications est désambiguïsé par les marqueurs d'article, pour ne pas imposer les preuves de tous ses travaux. |
| Le point de « et al. » pouvait séparer les noms de la citation placée juste après. | `attribution.py` : segmentation protégeant les abréviations « et al. » et « et coll. ». |
| Une attribution signalée dans une ancienne section laissait néanmoins `integrity_passed=true`. | `scientific_review.py` : `unsupported_attribution` fait désormais échouer le contrôle d'intégrité, sans modifier le brouillon historique. |

Cas vérifiés sur les extraits locaux des tests, sans consultation externe des articles :

- **Qian / Kong** : les quadrangles de surface produits par le travail de Qian sont distingués du gabarit ellipsoïdal et d'UNet-GCN rapportés à propos de Kong dans les travaux antérieurs. La formulation « leur article cite… » avec la preuve correspondante reste acceptée ; citer seulement la méthode propre de Qian ne documente pas Kong.
- **AdaptNet / MeshGraphNets** : la dépendance architecturale de Meshnet/Graphnet envers MeshGraphNets reste utilisable avec sa preuve. Un autre passage sur la prédiction d'un champ de métrique anisotrope n'autorise pas à lui attribuer n'importe quel résultat de MeshGraphNets. Le tableau CPU réel reproduit dans `test_numeric_claims.py` reste couvert.
- **Généricité** : les mêmes contrôles sont rejoués avec d'autres noms d'auteurs et de méthodes. Aucun nom d'article, d'auteur, d'architecture ou de domaine n'a été ajouté comme règle de production.

### P1 : récupération des preuves et consommation de contexte

Les optimisations de Claude sont conservées : consignes propres à chaque tâche, réutilisation des fiches déjà couvertes, fenêtres de comparaison prioritaires, affirmations entières et localisateurs des preuves dans `get_analysis_claims`, relecture des citations exactes via `analyze_article(read, evidence_ids)`, suivi distinct des tokens en cache et de raisonnement sans modification des prix.

Le quota global de **quatre appels par section bloquait réellement une cinquième page, un cinquième article ou un cinquième champ**, même si ces lectures étaient distinctes et nécessaires. Dans `agent.py`, la limite de quatre porte maintenant sur les relectures réussies **strictement identiques** : même section, même article résolu, même ensemble de champs, même position. Les arguments invalides ne consomment pas ce quota ; les champs sont normalisés indépendamment de leur ordre. L'outil reste proposé pour les nouvelles lectures. Les plafonds existants de temps, tours, outils, tokens et coût restent applicables et peuvent toujours interrompre une exécution.

### Validation de la reprise

- État reçu : **358 tests réussis**, résultat annoncé par Claude reproduit avant correction.
- État final : **372 tests réussis** (V4, V3 et V3.1), dont 14 nouveaux cas de non-régression ; dernière exécution en 59,87 s. Aucun échec. `git diff --check` passe en tenant compte des fins de ligne Windows (`core.whitespace=cr-at-eol`).
- Nouveau fichier `tests/ennoscholar_v4/test_review_safeguards.py` : refus sans altération d'un brouillon existant, retour intégral de sept affirmations longues, preuves déclarées, attributions erronées et corrigées, noms alternatifs, auteur commun à plusieurs publications, audit historique, cinq pages successives, cinq articles et cinq champs distincts.
- Tests de Claude adaptés dans `test_section_claims.py` et `test_attribution.py` : correction explicite d'une déclaration absente puis rédaction des sept sections ; borne sur les lectures identiques, tout en conservant l'outil pour les lectures utiles.
- Deux fixtures de `test_writing.py` citent maintenant leurs preuves dans la phrase ou la ligne du tableau concernée ; les assertions de conservation des paragraphes et d'export DOCX/PDF sont maintenues.
- Commande de non-régression (depuis le worktree) :

```powershell
D:\EnnoSmart\.venv\Scripts\python.exe -m pytest tests/ennoscholar_v4 tests/ennoscholar_v3 tests/ennoscholar_v31 --import-mode=importlib -q --tb=short --disable-warnings
```

`--import-mode=importlib` évite la collision entre fichiers de tests homonymes des trois versions. Les tests utilisent des clients simulés et des bases de test en mémoire. Les avertissements de dépréciation existants sont hors du périmètre de cette revue. Contrairement à la limite historique signalée plus haut, les tests des routes sont exécutables avec cet interpréteur partagé.

### Limites pour le prochain essai réel

- **Pas de validation sémantique automatique complète.** Une correspondance `citation_mapping` mémorise une relation texte/preuve ; elle ne démontre pas que la source implique l'affirmation. Le code contrôle la provenance, l'attribution reconnaissable et les cellules numériques déclarées. Les contresens, comparaisons abusives et omissions non déclarées demandent toujours une revue scientifique. Un brouillon enregistré n'est pas un document scientifiquement approuvé.
- **Heuristiques d'attribution.** Noms à capitales internes, noms composés et « X et al. » sont reconnus ; les alias absents du titre, noms entièrement en minuscules, homonymes et passages mêlant travaux antérieurs et contributions propres peuvent nécessiter une formulation ou une citation plus explicite. La présence d'un nom dans un passage ne garantit pas le sens de toute la phrase rédigée.
- **Budget fini.** La récupération distincte n'est plus arrêtée au quatrième appel ; l'exhaustivité d'un corpus arbitrairement grand reste bornée par les budgets globaux. Les preuves intégrales restent stockées et accessibles.
- **Essai utilisateur restant.** Vérifier avec le modèle réel la correction d'une déclaration refusée, l'utilisation de `next_offset`, l'attribution Qian/Kong et AdaptNet/MeshGraphNets, les chiffres et les avertissements d'audit. Aucun comportement réel du modèle ni gain financier mesuré n'est déduit des clients simulés.

## Échec de révision ciblée — run 9dff3e0c (17 octobre 2026)

Le premier essai réel de révision de la seule section 5 a consommé 13 appels LLM, 47 récupérations de fiches et cinq `write_section(read)`, sans aucune tentative de sauvegarde. Il n'a relancé ni lecture scientifique, ni comparaison, ni blueprint. Coût estimé enregistré : 1,228584 USD, pour 288 756 tokens d'entrée et 3 678 de sortie. L'audit global intervient après l'arrêt et n'en est pas la cause.

La reconstruction du contexte confirme que le texte de la section disparaissait du paquet et que seule la première des quatre fiches y subsistait. La correction transmet le texte brut complet et les quatre identités, donne priorité aux preuves utiles, conserve des reçus de récupération et réserve le dernier tour existant à `write_section(save)`. Les plafonds et les validations restent inchangés. Le faux positif GCN-Polycube est corrigé par une prise en compte étroite des citations en fin de paragraphe, limitée aux composants propres déjà étayés de la publication explicitement nommée.

Le diagnostic détaillé, le tableau des treize tours, les limites de journalisation, la méthode de calcul du coût et les résultats du rejeu sur copie figurent dans [le rapport du run 9dff3e0c](ennoscholar-v4-revision-ciblee-9dff3e0c.md). Aucun document réel n'a été révisé par ces vérifications ; toutes les sections et leurs versions restent conservées dans la base d'origine.

Validation finale de cette correction : **379 tests réussis** (V4, V3 et V3.1), dont sept nouveaux cas dans `test_targeted_revision.py`. Le rejeu simulé après correction enregistre uniquement une nouvelle version de la section 5 en un tour, sans altérer les versions antérieures. Le backend doit être redémarré pour charger ces changements avant le prochain essai réel.
