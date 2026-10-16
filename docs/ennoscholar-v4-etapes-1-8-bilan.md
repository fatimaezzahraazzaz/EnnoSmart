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
