# EnnoScholar V2 — restauration scientifique locale

10 octobre 2026. Corrections appliquées après validation du diagnostic. Aucun déploiement OVH, aucun appel OpenAI réel, aucune nouvelle table. Les sessions restent en RAM.

## Diagnostic avant modification

| Composant | État observé avant correction | Problème |
| --- | --- | --- |
| Search batching | `queries` déjà supporté, trois fournisseurs | La profondeur fournisseur utilisait `top_k`, généralement 8 : collecte et sélection compacte confondues. |
| Diversité des queries | Générées librement par GPT | Consigne trop peu explicite pour les sujets complexes ; pas de trace des volumes par query/source. |
| Ranking | Recouvrement lexical et citations | Ne distinguait pas la relation objet/objectif des usages connexes mentionnant les mêmes mots. |
| Registre backend | Métadonnées et abstracts complets, capacité 512 | Capacité insuffisante pour un corpus de 600+ notices. |
| Active articles | Maximum 12, nouvelles notices prioritaires | Une recherche complémentaire pouvait déplacer les meilleurs candidats globaux. |
| Frontend corpus | Le POST renvoyait le registre ; le chat ignorait `articles` | Aucun compteur ni accès séparé aux notices conservées. |
| Get articles | Batch déjà disponible | Correction précédente à conserver. |
| Get fulltext | Batch, texte backend, statut compact | Correction précédente à conserver ; besoin d'encadrer le temps disponible. |
| Analyze articles | Batch, preuves vérifiées | Inatteignable dans le run interrompu après trois outils. |
| Compaction | Native et suppression du préfixe ancien | Déjà corrigée ; ne pas modifier le gestionnaire. |
| Token budget | Comptage estimé distinct des octets | Contexte du run autour de 6k tokens : pas la cause de son arrêt. |
| Time budget | Limite unique de 180 secondes | Déclenchait la finalisation avant analyse ; aucune réserve de finition. |
| Tool budget | Soft 16 / hard 32, outils batch | Trois appels seulement dans ce run : pas la cause de l'arrêt. |
| Final synthesis timeout | Retry sans outils et fallback déjà présents | À conserver, avec le délai restant de la nouvelle limite dure. |

Causes établies : collecte trop superficielle par fournisseur, ranking lexical, sélection active privilégiant les arrivées récentes, limite temporelle unique empêchant l'analyse. Le nombre exact de queries du run réel et les réponses HTTP des fournisseurs n'étaient pas disponibles : impossible d'attribuer l'absence de chaque publication à une query précise ou à une panne API. Le code permettait déjà plusieurs queries ; une recherche littérale unique n'est donc pas une cause prouvée.

## Comportement corrigé

Le modèle fournit une intention structurée propre à la demande : objet, objectif principal, objectifs secondaires, usages connexes, méthodes et domaine. Le ranking cherche une relation objet/objectif dans le titre ou une clause courte de l'abstract, puis utilise les autres indices lexicaux. Il constitue un tri des métadonnées, pas une preuve scientifique. Aucun titre, auteur, DOI ou vocabulaire du benchmark maillage n'est codé dans la logique applicative. Les publications de référence et leurs termes apparaissent uniquement dans les fixtures. Un test sur les protéines change l'ordre quand l'objectif utilisateur change.

Le ranking ne lit ni le statut d'accès ni la disponibilité PDF pour calculer la pertinence. Les articles payants restent éligibles au premier rang. Après une recherche, le corpus complet est reclassé selon l'intention courante ; la sélection active garde les 12 meilleurs globaux, y compris les meilleurs de recherches antérieures. Une recherche complémentaire moins pertinente ne les évince pas. Les recherches thématiques privilégient le batch ; la similarité reste surtout destinée à un article de référence.

La profondeur par fournisseur et par query est **50** par défaut, réglable séparément entre 1 et 100. `top_k` est seulement le top compact (8 conseillé, 12 maximum dans le schéma LLM). Jusqu'à huit axes sont générés par GPT. La collecte conserve les abstracts complets et trace query/source/volume/durée/erreur, sans injecter le corpus dans OpenAI. La capacité du registre passe à 10 000. Les index de déduplication sont temporaires et propres à V2 ; DOI, arXiv et titre normalisé servent à fusionner les notices.

Les modules `context.py`, `fulltext.py`, `article_analysis.py` et `article_reader.py` restent inchangés. Compaction, comptage tokens, fulltexts backend et validation des preuves sont conservés. Les modifications d'orchestration portent sur le classement des candidats et les délais transmis aux helpers existants.

### Délais par défaut

| Réglage | Valeur | Effet |
| --- | --- | --- |
| `ENNOSCHOLAR_V2_SOFT_TIME_LIMIT` | 180 s | Arrête les nouvelles recherches larges, laisse les outils de finition accessibles. |
| `ENNOSCHOLAR_V2_HARD_TIME_LIMIT` | 600 s | Sécurité globale ; résultats déterministes partiels si délai épuisé. |
| `ENNOSCHOLAR_V2_ANALYSIS_RESERVE` | 90 s | Réserve une période pour analyser les meilleurs après la récupération des textes. |
| `ENNOSCHOLAR_V2_SYNTHESIS_RESERVE` | 90 s | Réserve la fin du tour à la synthèse et au retry éventuel. |
| `ENNOSCHOLAR_V2_FULLTEXT_ARTICLE_TIMEOUT` | 35 s | Encadre la recherche légale de chaque texte, dans le délai du batch. |
| `ENNOSCHOLAR_V2_SEARCH_TIMEOUT` | 120 s | Encadre un batch de collecte. |
| `ENNOSCHOLAR_V2_SEARCH_CANDIDATES` | 50 | Profondeur fournisseur, indépendante du top compact. |
| `ENNOSCHOLAR_V2_MAX_ARTICLES` | 10 000 | Capacité du corpus backend. |

L'ancien `ENNOSCHOLAR_V2_TURN_TIMEOUT` reste un alias de la limite dure si le nouveau réglage est absent. Les délais des SDK/requêtes sont bornés au temps restant ; ce sont des limites coopératives, pas une interruption forcée du processus. Soft/hard tool budgets restent 16/32. Les appels unitaires `get_article` restent indisponibles. Le timeout final entraîne un unique retry avec `tools=[]` ; en cas d'échec, HTTP 200 avec `partial=true`, synthèse minimale et corpus conservé, sans relancer les outils.

### API et interface

`POST /scholar-v2/chat` renvoie `articles_total`, `active_article_ids` et au plus 12 notices compactes dans `articles`. Il ne renvoie plus tout le registre.

`GET /scholar-v2/sessions/{session_id}/articles?offset=0&limit=20` expose le corpus authentifié du propriétaire, classé par pertinence. Limite 1–50 ; les notices comprennent leurs abstracts complets et niveaux d'accès, jamais les fulltexts. Conversations étrangères/expirées : 404.

Le chat affiche un panneau repliable **« Articles conservés dans la conversation (N) »**, pages de 20, liens DOI/titres et abstracts repliables. Aucun nouvel écran. Vérification visuelle du composant réel avec une API locale simulée : compteur 650, pages 1–20 puis 21–40 et retour, écran 390 px sans débordement horizontal. Le serveur de QA a été arrêté et son onglet fermé ; les processus backend/frontend de l'utilisateur n'ont pas été arrêtés.

## Validation et reproduction contrôlée

**103 tests V2 réussis**, dernière exécution 18,81 s. Compilation Python réussie (`compileall`, compilation de tous les modules V2, du routeur et des tests). Typecheck et build Next réussis. `git diff --check` réussi. **137 empreintes SHA-256 protégées identiques**, comprenant V1, EnnoDiagnostic, les quatre modules V2 préservés, le point d'entrée backend, la sidebar, les fichiers de dépendances frontend et les vrais fichiers `.env`.

Tous les appels OpenAI, fournisseurs et récupération de texte de ces tests sont simulés ; aucun réseau réel autorisé dans les fixtures. Le service HTTP, le gestionnaire de contexte et la validation des preuves exécutent le vrai code.

| Mesure | Avant, fixture contrôlée | Après, fixture contrôlée |
| --- | --- | --- |
| Profondeur fournisseur | 8 | 50 |
| Premier corpus, quatre queries | 96 | 600 |
| Corpus après recherche complémentaire | — | 650 |
| Publications centrales présentes dans les données fournisseur et retenues | 0/8, placées après les huit premiers résultats pour tester la profondeur | 8/8, devant le bruit |
| Sélection active maximale | 12 | 12 |
| À 190 secondes simulées | 3 outils, `time_budget_reached`, aucune analyse | 4 outils, analyse atteinte, réponse normale |
| Parcours complet | — | 2 recherches + 1 détails batch + 1 fulltext batch + 1 analyse batch : **5 outils** |
| HTTP normal | — | **200**, `partial=false` |
| Double timeout de synthèse | — | **200**, `partial=true`, 650 notices conservées, aucun outil répété |
| Taille du POST normal | Non mesurée dans cette reproduction | **20 502 octets** |

Le cas complet conserve un fulltext simulé et trois analyses avec bases metadata/abstract/fulltext. Une affirmation non supportée est supprimée ; la preuve vérifiée conserve sa page 4. Les 650 notices sont accessibles sans doublons par pagination, y compris celles jamais envoyées à GPT. Les anciens tests de contexte, compaction et orchestration passent également.

Volumes des requêtes modèle du parcours normal :

| Requête | Tokens estimés | Octets sérialisés | Notices actives |
| --- | ---: | ---: | ---: |
| 1 | 2 587 | 11 534 | 0 |
| 2 | 7 181 | 31 805 | 12 |
| 3 | 7 709 | 33 585 | 12 |
| 4 | 11 692 | 56 728 | 12 |
| 5 | 11 988 | 57 707 | 12 |
| 6 | 12 661 | 60 211 | 12 |

Ce sont des estimations locales du payload visible, pas des tokens facturés par OpenAI. L'augmentation lors de `get_articles` correspond aux extraits détaillés de quelques notices actives, pas aux 650 abstracts. La sélection reste bornée. Les scénarios de forte pression et compaction restent couverts par la suite existante.

Résultats bruts : [reproduction JSON](D:/EnnoSmart/.codex_tmp/scholar-v2-scientific-reproduction.json). Capture QA : [affichage mobile](D:/EnnoSmart/.codex_tmp/scholar-v2-ui-mobile.png).

Commandes reproductibles depuis `D:\EnnoSmart` :

```powershell
.venv\Scripts\python.exe -m pytest tests\ennoscholar_v2 -q
.venv\Scripts\python.exe -m compileall -q agents\EnnoScholarV2 backend_api\routers\scholar_v2.py tests\ennoscholar_v2
.venv\Scripts\python.exe tests\ennoscholar_v2\report_scientific_behavior.py .ennosmart_patch_backups\scholar_v2_scientific_20261010_194022_625
cd frontend
npm.cmd run typecheck
npm.cmd run build
```

## Fichiers et isolation

Sauvegarde horodatée avant modifications : `D:\EnnoSmart\.ennosmart_patch_backups\scholar_v2_scientific_20261010_194022_625`, avec sous-dossiers `revision_*` avant les retouches successives. Le changement automatique `next-env.d.ts` produit par le build a été sauvegardé puis annulé.

**Fichiers existants modifiés dans cette correction (16)** :

- `agents/EnnoScholarV2/{agent.py,config.py,models.py,prompts.py,service.py,.env.example}`
- `agents/EnnoScholarV2/tools/scientific_search.py`
- `backend_api/routers/scholar_v2.py`
- `frontend/lib/{api.ts,scholar-v2.ts}`
- `frontend/components/ennosmart/ennoscholar-v2-chat.tsx`
- `tests/ennoscholar_v2/{context_reproduction.py,orchestration_reproduction.py,test_batching.py,test_orchestration.py,test_router.py}`

**Nouveaux fichiers applicatifs/tests/rapport (6)** :

- `agents/EnnoScholarV2/tools/ranking.py`
- `frontend/components/ennosmart/ennoscholar-v2-corpus.tsx`
- `tests/ennoscholar_v2/{scientific_fixture.py,test_scientific_behavior.py,report_scientific_behavior.py}`
- `agents/EnnoScholarV2/SCIENTIFIC_REPORT.md`

Les fichiers temporaires de reproduction et QA restent sous `.codex_tmp`. Les changements Git antérieurs de `.gitignore`, `backend_api/main.py`, sidebar et dépendances frontend appartiennent aux étapes précédentes ; cette correction n'y ajoute rien. V2 et ses tests étant encore non suivis par Git, `git diff --stat` seul ne décrit pas tout le travail : la comparaison est faite avec les backups, sans écraser les changements préexistants.

Modèle configuré dans les fichiers locaux : **`gpt-5.6-sol`**, inchangé ; son accès API n'a pas été retesté. Tools conservés : `web_search`, `search_scientific`, `find_similar_articles`, `read_article`, `get_articles`, `get_fulltext`, `analyze_articles`.

V1 modifiée : **NON**. EnnoDiagnostic modifié : **NON**. Appels OpenAI réels : **NON**. Fournisseurs/fulltext légaux réels : **NON TESTÉS dans cette correction**. Mémoire multi-turn, compaction, batch, pagination, analyse et timeout final : **tests simulés réussis**. Build frontend : **RÉUSSI**.

## Test manuel restant

Redémarrer localement backend et frontend pour charger les changements (un redémarrage backend efface les sessions RAM), puis lancer une nouvelle conversation avec la question exacte :

> Je travaille en mécanique sur la génération automatique de maillages 3D à l’aide de modèles entraînés. Je cherche les articles scientifiques les plus pertinents sur ce sujet, y compris les articles payants. Pour les meilleurs articles, indique-moi si tu as uniquement l’abstract ou si tu as réellement pu accéder au texte complet.

Le test réel appartient à l'utilisateur : vérifier diversité des queries, publications centrales effectivement retrouvées, qualité scientifique du top 5–10, accès réellement vérifié et réponse finale normale. Les fixtures démontrent les mécanismes de sélection et de finition ; elles ne prouvent pas la disponibilité actuelle des publications chez les fournisseurs ni le comportement final du modèle réel.
