# EnnoScholar V2 — follow-up, analyse et synthèse, 11 octobre 2026

## Résultat

Corrections locales limitées à la politique conversationnelle, au retry d’analyse
sur exhaustion de tokens et aux budgets/repli de synthèse. **182 tests réussis**,
dont **23 nouveaux cas**, en 34,19 s ; un avertissement de dépréciation Starlette/AnyIO.
Aucun appel OpenAI réel, fournisseur scientifique réel, redémarrage ou déploiement.

## Cause exacte des timeouts de synthèse dans le code précédent

`ScholarConfig.timeout` vaut 45 secondes (`ENNOSCHOLAR_V2_HTTP_TIMEOUT`). La variable
n’est pas surchargée dans le `.env` local inspecté. Dans `ScholarChatAgent.run` :

- les réponses conversationnelles utilisaient `min(config.timeout, remaining)` ;
- `finalize` utilisait le même calcul ;
- `retry_synthesis` utilisait encore le même calcul ;
- `remaining` était partout `deadline - time.monotonic()`, avec `deadline` calculée
  au début du tour, avant les recherches, fulltexts et analyses.

La réserve existante de synthèse de 90 s avançait la deadline d’arrêt des outils,
mais n’accordait **aucun timeout propre à la synthèse**. Les appels étaient donc
plafonnés à 45 s, voire moins si la deadline globale avait été consommée. Le retry
pouvait être réduit à quelques secondes, ou totalement abandonné après expiration.

Le client V2 est construit directement dans `agent.py` avec `OpenAI(...,
timeout=config.timeout, max_retries=0)`. Il n’existe aucun `responses_client.py`
dans ce dépôt. L’implémentation du SDK installée (`openai/_client.py`,
`openai/_base_client.py`) confirme que `with_options(timeout=...)` applique cette
valeur au client copié ; aucun fichier du SDK n’a été modifié.

Ce diagnostic établit le mauvais couplage des budgets dans le code. Les logs
fournis seuls ne distinguent pas une latence du modèle d’une latence réseau pour
chaque timeout réel ; aucun run réel n’a été relancé pour le mesurer.

## Correction conversationnelle

Nouvelle instruction séparée dans `conversation.py`, ajoutée aux instructions
de l’agent avant la mémoire active. `prompts.py` et `SYSTEM_PROMPT` sont inchangés.

Le modèle doit d’abord raisonner à partir des notices/analyses en mémoire pour une
question référencée aux articles précédents. Une notice stockée à approfondir se
consulte avec `get_articles`, plutôt que par une nouvelle recherche de son titre.
`search_scientific` reste disponible ; GPT décide de l’utiliser si une information
essentielle manque, si une lacune scientifique exige de nouvelles sources ou si
l’utilisateur demande d’autres articles. Aucun classifieur Python sur des mots.

Les trois tests de follow-up utilisent un modèle simulé : ils vérifient que la
consigne, les 12 notices avec analyses et l’historique sont transmis, puis qu’une
réponse directe ne lance aucun outil. Ils ne prétendent pas mesurer l’adhésion
d’un modèle réel à cette consigne. Un autre test confirme qu’une nouvelle recherche
explicitement demandée reste exécutable.

## Retry d’analyse

`analyze_article` construit une seule requête avec le prompt, le matériau et le
schéma identiques à ceux d’avant. Première limite par défaut : 3 500 tokens.

Uniquement lorsque `status == incomplete` et que la raison est
`max_output_tokens`, une seconde tentative est autorisée, à 6 000 tokens par
défaut. Aucun troisième essai. Une autre raison d’incomplétude, une réponse failed,
une ValidationError ou une erreur réseau ne déclenchent pas ce retry de tokens.

Les réponses incomplètes ne sont jamais validées comme JSON. Chaque diagnostic
`analysis_incomplete` reste journalisé ; un événement `analysis_output_retry`
indique la seconde limite. Si le retry échoue, le diagnostic batch existant reste
actif, sans perdre le PDF ni les autres analyses réussies.

Comparaison AST au backup : prompt scientifique, input/article/passages/contexte,
schéma strict, construction et restitution des evidence_ids, suppression des champs
non étayés et diagnostics de validation **identiques**. Aucun matériau réduit.

La [documentation officielle OpenAI sur les modèles de raisonnement](https://developers.openai.com/api/docs/guides/reasoning)
confirme le couple `incomplete` / `max_output_tokens` ; cette limite inclut aussi
les tokens de raisonnement, pas seulement le JSON visible.

## Budget de synthèse et sécurité

Valeurs choisies : **90 s pour la première synthèse, 60 s pour l’unique retry**.
Elles remplacent le plafond partagé de 45 s pour les réponses avec résultats.
Le choix accorde deux fois l’ancien délai au premier essai et un essai de
récupération borné, sans supprimer les délais de sécurité.

- Une réponse conversationnelle avec articles en mémoire peut déjà être la réponse
  finale : elle reçoit le timeout propre de 90 s, sans réduction par l’ancienne
  deadline outils. Si GPT propose encore des outils, les vérifications de deadline
  existantes continuent de contrôler chacun avant exécution.
- `finalize` ouvre une phase de synthèse seule avec une deadline locale : début de
  synthèse + 90 + 60 s. Le retry réutilise cette deadline, sans la réinitialiser.
  Un retard exceptionnel du premier essai réduit donc le budget du retry.
- Après un timeout d’une réponse conversationnelle, la récupération seule reçoit
  son propre budget de 60 s, même si l’ancienne deadline du tour est expirée.
- Les requêtes de récupération/finalisation ont `tools=[]`, `tool_choice=none`
  et `max_retries=0`. Une seule reprise explicite sur APITimeoutError.
- Le même input déjà compacté est réutilisé ; ni recherche, ni téléchargement,
  ni analyse, ni reconstruction de l’ancien préfixe ne se produisent au retry.
- Une session déjà remplie peut aussi récupérer une synthèse après timeout sans
  avoir exécuté d’outil dans le nouveau tour.

Conséquence volontaire : la deadline initiale continue de borner les **outils**,
mais la synthèse seule peut la dépasser avec son budget propre. Le tour n’est donc
plus coupé au plafond initial simplement parce que les outils l’ont consommé.
Les appels restent protégés par leurs timeouts SDK et la deadline locale de la
phase de finalisation ; aucun retry infini ou timeout non borné.

Si une limite métier a déjà été atteinte (temps/outils/rounds), une synthèse réussie
peut rester `partial=true` pour cette limite. Un simple timeout récupéré en phase
normale donne une réponse normale, sans fallback ni `synthesis_timeout`.

## Fallback

Dernier recours conservé, avec avertissement explicite :

> La synthèse finale n'a pas pu être générée.
> Voici les analyses ou notices conservées des articles concernés…

Au plus cinq notices de la sélection conversationnelle existante, dans son ordre,
avec leur lien, niveau d’accès et courts champs d’analyse déjà enregistrés. Le tri
Python par présence d’analyse/fulltext est supprimé. Aucun classement scientifique
fabriqué, aucune nouvelle conclusion, aucune nouvelle collecte. HTTP 200 et la
mémoire backend sont conservés lorsque les deux synthèses échouent.

## Variables nouvelles

| Variable | Défaut |
| --- | ---: |
| `SCHOLAR_ANALYSIS_MAX_OUTPUT_TOKENS` | 3500 |
| `SCHOLAR_ANALYSIS_RETRY_MAX_OUTPUT_TOKENS` | 6000 |
| `SCHOLAR_SYNTHESIS_TIMEOUT_SECONDS` | 90 |
| `SCHOLAR_SYNTHESIS_RETRY_TIMEOUT_SECONDS` | 60 |

Lecture dans `ScholarConfig.from_env`, documentation dans `.env.example`.
La limite d’analyse de retry est toujours supérieure à celle de la première
tentative. `.env` et le modèle configuré GPT-5.6 Sol ne sont pas modifiés.

## Fichiers modifiés et nouveaux

Existants :

1. `agents/EnnoScholarV2/config.py` — configuration des quatre limites.
2. `agents/EnnoScholarV2/.env.example` — documentation des variables.
3. `agents/EnnoScholarV2/agent.py` — `build_instructions`, `fallback`,
   `synthesis_request`, `retry_synthesis`, `finalize`, timeout de réponse avec mémoire.
4. `agents/EnnoScholarV2/tools/article_analysis.py` — `analyze_article`, retry ciblé.
5. `tests/ennoscholar_v2/test_analysis_diagnostics.py` — attentes adaptées au retry.
6. `tests/ennoscholar_v2/test_scientific_behavior.py` — après expiration des outils,
   une synthèse seule reste possible.
7. `tests/ennoscholar_v2/test_orchestration.py` — nouveau texte explicite du fallback.

Nouveaux : `agents/EnnoScholarV2/conversation.py`,
`tests/ennoscholar_v2/test_followup_synthesis.py` et ce rapport.

## Backup et contrôles

Backup avant premières modifications :

`D:\EnnoSmart\.ennosmart_patch_backups\scholar_v2_followup_synthesis_20261011_041601_980399`

Backups avant modifications supplémentaires :

- `revision_20261011_042007_478024` — test orchestration et nouvelle suite ciblée.
- `revision_20261011_042134_925497` — agent et nouvelle suite ciblée.

Commande finale :

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/ennoscholar_v2 -q
```

**182 passed, 1 warning in 34.19s.** Les 23 nouveaux cas couvrent : mémoire des
follow-ups et recherche explicite, analyses completed sans retry, retry unique
max-token sur abstract/fulltext avec matériau/schéma/preuves conservés, autres
raisons sans retry, deuxième exhaustion, reprise de synthèse avec contexte compacté
identique, succès normal après reprise, double timeout/fallback, synthèse après
ancienne deadline consommée, sécurité du budget local non réinitialisé, ancien
timeout court ne bridant plus la réponse finale, cache mémoire sans outil du tour,
fallback court sans nouveau tri, configuration.

Les 159 tests antérieurs restent présents ; seules trois attentes ciblées ont été
adaptées aux comportements explicitement demandés. Les mocks interdisent les appels
externes/OpenAI. Aucun benchmark de pertinence réel effectué.

Contrôle SHA-256 de la baseline : exactement les sept fichiers existants ci-dessus
modifiés, **770 autres fichiers inchangés**. Syntaxe des 43 fichiers Python V2/tests
vérifiée en mémoire ; `git diff --check` réussi (avertissements CRLF préexistants).

Fournisseurs, ranking, `compact_results`, ActiveSources, resolver fulltext,
provenance, schémas/validation des preuves, V1, EnnoDiagnostic et frontend intacts.
Les changements Git préexistants du projet n’ont pas été réécrits.

Travail terminé et arrêté ; le processus backend actif n’a pas été redémarré.
