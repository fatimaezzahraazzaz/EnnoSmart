# EnnoScholar V2 — optimisation des performances, 11 octobre 2026

## Résultat et périmètre

Optimisation locale de l’I/O, de la concurrence et des caches. Aucun redémarrage,
déploiement, appel OpenAI réel ou appel réel aux fournisseurs scientifiques.
Le backend déjà démarré utilise encore le code qu’il avait chargé : ces changements
prendront effet après le redémarrage effectué par l’utilisateur.

Validation finale : **159 tests réussis**, un avertissement de dépréciation
Starlette/AnyIO ; 41 fichiers Python compilés en mémoire ; `git diff --check`
réussi, avec seulement les avertissements CRLF sur des fichiers déjà modifiés
avant cette intervention.

## Causes trouvées

1. Les fournisseurs étaient déjà exécutés en parallèle entre eux, mais chaque
   fournisseur parcourait ses queries séquentiellement. Les connexions HTTP
   étaient également recréées hors client injecté ; les queries identiques
   n’avaient pas de cache partagé à l’échelle du tour conversationnel.
2. La résolution fulltext partageait essentiellement la deadline d’un article
   entre ses pistes. Un timeout HTTP de lecture est un délai d’inactivité : un
   téléchargement livrant lentement des fragments pouvait continuer longtemps.
   Il manquait un délai total annulant réellement le téléchargement d’une URL.
3. Le cache fulltext propre à une notice existait, mais la réutilisation entre
   versions déjà associées et la validité d’un résultat en cache n’étaient pas
   suffisamment explicites. Deux versions pouvaient télécharger le même travail.
4. Les analyses étaient déjà parallèles par groupes de trois, mais une analyse
   compatible déjà enregistrée pouvait être recalculée au tour suivant.

Le run historique fourni termine à environ 440 s ; les outils fulltext occupent
environ 242 s entre les deux jalons fournis. Ces jalons ne suffisent pas à attribuer
exactement cette durée à chaque URL. La nouvelle télémétrie permet cette mesure.

## Fichiers et fonctions

Fichiers existants modifiés uniquement par cette intervention :

| Fichier | Fonctions / changements |
| --- | --- |
| `config.py` | `ScholarConfig`, `from_env` : configuration des délais et concurrences |
| `.env.example` | Documentation des nouvelles variables ; aucun changement de `.env` |
| `agent.py` | `StoredArticle`, description de `get_fulltext`, branche batch de `execute_tool`, `run` : cache par tour, réutilisation et concurrence |
| `tools/scientific_search.py` | `ScientificSearch.__init__`, `turn_cache`, `_query`, `_request`, `_get`, `arxiv`, `search_scientific`, `search_batch`, `lookup_doi` : I/O et ordonnancement |
| `tools/fulltext.py` | `get_fulltext` ; nouvelles fonctions `valid_fulltext`, `unavailable_fulltext`, `candidate_sources` |
| `tools/article_reader.py` | `fetch_url`, `read_fulltext` ; exceptions distinguant identité incorrecte et texte indisponible |
| `tests/ennoscholar_v2/test_article_access.py` | Trois faux téléchargements acceptent les nouveaux arguments de timeout |

Nouveaux fichiers :

- `performance.py` : `cached_work_fulltext`, `analysis_signature`.
- `tests/ennoscholar_v2/test_performance.py` : 22 nouveaux cas de test.
- `tests/ennoscholar_v2/performance_benchmark.py` : comparaison hors ligne avec le code sauvegardé.
- Ce rapport.
- Résultats JSON dans `.codex_tmp/scholar-v2-performance-20261011-033932.json`
  et `.codex_tmp/scholar-v2-performance-final-20261011.json`.

## Backups

Backup initial avant modification des sept fichiers existants :

`D:\EnnoSmart\.ennosmart_patch_backups\scholar_v2_performance_20261011_033932_960824`

Ce dossier contient aussi `baseline.json` et `modified_paths.json`. Les révisions
intermédiaires ont leur copie préalable dans ses sous-dossiers horodatés :

- `revision_20261011_034714_898425`
- `revision_20261011_034903_873905`

Les sept fichiers modifiés correspondent exactement à la liste prévue ; les
**766 autres fichiers de la baseline sont identiques par SHA-256**. Ce contrôle
est relatif au début de cette intervention, indépendamment des changements Git
antérieurs du projet.

## Recherche : couverture et cache conservés

- Concurrence maximale par défaut : Semantic Scholar 2, OpenAlex 3, arXiv 1.
- Les trois groupes fournisseurs restent parallèles. Les résultats sont collectés
  dans l’ordre des queries soumises, puis fusionnés selon la logique existante,
  et non dans l’ordre d’arrivée réseau.
- Espacement Semantic Scholar : au moins 1 s entre départs ; OpenAlex : au moins
  0,1 s. `429`/`Retry-After` repousse les départs suivants, sans boucle de retry
  supplémentaire. Une attente incompatible avec la deadline échoue proprement.
- arXiv reste sérialisé et attend 3 s après la fin de la requête précédente,
  y compris lorsqu’un client HTTP est injecté.
- Clé du cache : fournisseur + query normalisée + limite. Seuls les espaces hors
  expressions entre guillemets sont normalisés ; casse, opérateurs et expressions
  citées sont préservés. Une même clé en vol partage un seul résultat réseau.
- Les notices retournées du cache sont copiées pour éviter qu’un appel modifie
  les données du suivant. Cache et client HTTP partagé sont libérés à la fin du
  tour ; aucun cache scientifique permanent.
- Profondeur fournisseur inchangée : 50 résultats par défaut, configurable jusqu’à
  100 comme auparavant ; maximum de queries et notices compactes inchangé.

Les fonctions de normalisation/déduplication et les conversions fournisseurs
ont été comparées par AST : aucune différence scientifique.

## Fulltext : délais et provenance

Ordre effectif : cache valide de la notice ou d’une version déjà associée → PDF
connu → arXiv/prépublication connue → autres URLs de versions publiques connues
→ page article → métadonnées/résolution DOI. Les URLs restent vérifiées ; une
URL classée `oa` dans la télémétrie est une piste de version publique, sans
certification automatique de son accès ou de son identité.

Premier texte valide : retour immédiat. Une tentative timeout, HTTP invalide,
identité différente ou contenu inutilisable passe à la piste suivante.
L’ancienne limite de huit URLs par article reste inchangée ; le compteur des logs
peut inclure en plus la résolution des métadonnées DOI.

Le téléchargement est asynchrone dans son worker, avec délai total couvrant DNS,
headers, redirections et corps. Les validations de destination publique,
d’identité, de taille et les parseurs PDF/HTML restent conservés. Les délais
réseau ne constituent pas une interruption forcée d’un parseur CPU ni un quota
sur la qualité scientifique.

Le batch compte au plus trois workers, avec une deadline propre de 60 s par
défaut, toujours ramenée à la deadline qui protège les réserves existantes
d’analyse et de synthèse. Le délai par article reste 35 s. Un article mis en
attente démarre seulement si le budget le permet ; sinon sa notice et son
abstract sont conservés, avec statut de résolution incomplète.

Une publication et une prépublication **déjà associées** partagent le résultat
valide et un verrou de travail dans le batch. Aucun nouvel algorithme de
rapprochement scientifique. La provenance du texte reste son URL réelle et
`source_article_id`, sans réinjecter le texte complet dans GPT.

Les nouvelles deadlines peuvent rendre une piste lente indisponible dans ce
tour ; elles restent configurables. Les essais simulés confirment l’absence de
perte sur leurs jeux de données, pas une équivalence mesurée sur tous les
serveurs scientifiques réels.

## Analyse

Empreinte de compatibilité fondée sur le modèle, le contexte utilisateur utile,
la notice et le matériau fulltext réellement disponible avec sa provenance.
Une modification d’abstract, de texte, de modèle ou de contexte invalide le cache.
Une ancienne analyse sans empreinte n’est pas supposée compatible.

La première analyse continue d’utiliser l’implémentation existante et jusqu’à
trois workers indépendants. Abstract seul : aucune récupération fulltext
automatique. Les preuves, leur restitution exacte et les diagnostics
`AnalysisIncompleteError` / `ValidationError` restent inchangés.

## Configuration nouvelle

Préfixe de toutes les variables : `ENNOSCHOLAR_V2_`.

| Suffixe | Défaut | Bornes / rôle |
| --- | ---: | --- |
| `SEMANTIC_CONCURRENCY` | 2 | 1–4 requêtes simultanées |
| `OPENALEX_CONCURRENCY` | 3 | 1–4 requêtes simultanées |
| `SEMANTIC_REQUEST_INTERVAL` | 1 | Minimum 1 s |
| `OPENALEX_REQUEST_INTERVAL` | 0.1 | Minimum 0,1 s |
| `FULLTEXT_BATCH_TIMEOUT` | 60 | Secondes, minimum 1 |
| `FULLTEXT_ATTEMPT_TIMEOUT` | 12 | Secondes par URL, minimum 1 |
| `FULLTEXT_CONNECT_TIMEOUT` | 5 | Secondes, minimum 0,5 |
| `FULLTEXT_HEADERS_TIMEOUT` | 10 | Secondes, minimum 0,5 |
| `FULLTEXT_READ_TIMEOUT` | 10 | Secondes d’inactivité, minimum 0,5 |
| `FULLTEXT_CONCURRENCY` | 3 | 1–3 articles |
| `ANALYSIS_CONCURRENCY` | 3 | 1–3 analyses |

Les valeurs sont les défauts du code ; `.env` n’a pas été édité. Le modèle
GPT-5.6 Sol configuré, les réserves, les budgets outils et le contexte scientifique
ne sont pas remplacés.

## Tests et benchmark

Commande de validation exécutée :

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/ennoscholar_v2 -q
```

Résultat final : **159 passed, 1 warning in 40.06s**. Les nouveaux tests couvrent
concurrence fournisseurs, espacement réel arXiv avec horloge simulée, isolation
par tour, cache single-flight, `Retry-After`, priorité des sources, arrêt au
premier succès, timeout suivi de repli, cache vide/valide, versions associées,
article lent indépendant, deadline laissant la synthèse possible, réutilisation
et invalidation d’analyse, ainsi que headers/corps lent/redirections annulés
avant le délai total.

Le benchmark importe les fichiers Python du backup initial pour son scénario
« avant », puis les fichiers actuels pour « après ». Appels réseau/OpenAI interdits.
Huit queries × trois fournisseurs × 50 résultats, six fulltexts avec première
URL lente, deuxième HTTP 404 et troisième réussie ; six analyses puis répétition
du même batch. Chronométrage réel du scénario complet avec latences artificielles.

| Mesure wall-clock | Avant | Après |
| --- | ---: | ---: |
| Recherche | 0,7018 s | 0,4526 s |
| Fulltext | 0,2061 s | 0,0961 s |
| Première analyse | 0,0636 s | 0,0642 s |
| Analyse répétée | 0,0638 s | 0,0021 s |
| **Total** | **1,0355 s** | **0,6153 s** |

Gain total simulé : **40,6 %** ; fulltext : **53,4 %**. Première analyse
sensiblement identique, car elle était déjà concurrente.

Dans les deux scénarios : **1 200 candidats backend**, **8 notices compactes dans
exactement le même ordre**, **6 fulltexts réussis**, **18 tentatives**. Pic de
concurrence fournisseurs : avant 1/1/1, après 2/3/1. Appels d’analyse : 12 avant,
6 après grâce au cache compatible du second batch.

Le benchmark accélère artificiellement l’attente arXiv dans son stub ; les tests
séparés vérifient les vraies 3 secondes. Il ne prédit donc pas la durée du run
réel de 440 s : arXiv, le réseau et les générations GPT restent déterminants.

## Exemple du format de logs

Exemple illustratif, les durées ci-dessous ne sont pas celles d’un run réel :

```text
EnnoScholar V2 search provider=openalex query="scientific query" elapsed=0.420 cache_hit=false
EnnoScholar V2 search provider=openalex query="scientific query" elapsed=0.001 cache_hit=true
EnnoScholar V2 fulltext article_id=art_example source_candidate=direct_pdf attempt=1 elapsed=12.001 status=timeout
EnnoScholar V2 fulltext article_id=art_example source_candidate=arxiv attempt=2 elapsed=1.200 status=success
EnnoScholar V2 fulltext article_id=art_example total_elapsed=13.202 attempts=2 cache_hit=false
EnnoScholar V2 fulltext article_id=art_example total_elapsed=0.001 attempts=0 cache_hit=true source_article_id=art_preprint
EnnoScholar V2 analysis article_id=art_example elapsed=0.001 cache_hit=true
```

Aucun texte de PDF dans ces logs. Les erreurs existantes d’analyse continuent
d’être journalisées avec leur diagnostic.

## Invariants contrôlés

`SYSTEM_PROMPT`, coverage check, `compact_results`, ActiveSources, pertinence,
sélection des 12 actifs, modèles Pydantic, déduplication, registre scientifique,
preuves, analyse scientifique, Web, batch get_articles, compaction et limites
outils sont conservés. Seule la description de l’outil fulltext précise son coût.
Frontend, V1 et EnnoDiagnostic : aucune modification de cette intervention.

Le diff Git global contient également des travaux antérieurs. Le diff de cette
intervention est celui des sept fichiers comparés à leur backup, plus les nouveaux
fichiers listés ci-dessus ; aucun déploiement ni redémarrage n’a été effectué.
