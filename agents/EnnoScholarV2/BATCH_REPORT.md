# Correction locale : outils batch et limites métier

## Diagnostic de la copie locale

Avant correction : `config.py` définissait 24 appels de fonctions, 10 rounds et
180 secondes. `agent.py` comptait les fonctions exécutées, mais pas web_search.
Les limites levaient ScholarTurnLimitError, convertie en HTTP 504 par le routeur.
Le dernier message était effectivement en 504 dans le log local, pas en 502.
Le 502 correspondait aux autres exceptions backend/upstream.

La phrase « Le nombre d'outils demandé dépasse la limite du MVP » n'était pas
présente dans cette copie. Le nombre exact d'appels et leur répartition lors du
dernier run sont irrécupérables : les anciens logs ne les enregistraient pas.
Les nouveaux articles n'étaient validés dans la session qu'en cas de succès.

Le texte fourni évoquait des améliorations absentes du code local : get_article,
compaction native et budget en tokens. Elles sont maintenant présentes. Les
full texts existaient côté backend mais étaient aussi renvoyés au modèle ; ce
retour a été supprimé. Les abstracts ne sont plus tronqués lors de leur stockage.

## Fichiers préexistants modifiés pour cette correction

- agents/EnnoScholarV2/agent.py
- agents/EnnoScholarV2/config.py
- agents/EnnoScholarV2/models.py
- agents/EnnoScholarV2/service.py
- agents/EnnoScholarV2/prompts.py
- agents/EnnoScholarV2/tools/scientific_search.py
- agents/EnnoScholarV2/tools/fulltext.py
- agents/EnnoScholarV2/README.md
- agents/EnnoScholarV2/.env.example
- backend_api/routers/scholar_v2.py
- tests/ennoscholar_v2/test_agent.py
- tests/ennoscholar_v2/test_router.py

## Nouveaux fichiers

- agents/EnnoScholarV2/context.py
- tests/ennoscholar_v2/test_batching.py
- agents/EnnoScholarV2/BATCH_REPORT.md

## Backup

`D:\EnnoSmart\.ennosmart_patch_backups\scholar_v2_batch_20261009_235141_788`
contient les versions précédentes des fichiers modifiés, des révisions
intermédiaires horodatées, et les empreintes des fichiers protégés.

## Résultat

- Modèle configuré : gpt-5.6-sol, inchangé. Aucun repli automatique.
- Outils : web_search natif, search_scientific batch, read_article, get_article,
  get_fulltext batch, analyze_articles batch, find_similar_articles.
- SEARCH_SCIENTIFIC : jusqu'à 8 requêtes par appel, 3 fournisseurs parallèles,
  déduplication et classement ; tous les candidats du batch sont enregistrés
  sous la limite RAM de 512 articles par session. Seuls top_k retours compacts
  sont transmis au modèle. Les articles payants restent candidats.
- FULLTEXT : jusqu'à 8 articles, 3 workers ; disponibilité, provenance et
  niveaux d'accès uniquement dans le retour principal. Textes et passages en RAM.
- ANALYSE : jusqu'à 8 articles, 3 workers, analyses internes de textes réels,
  base abstract/fulltext explicite, extraits vérifiés et résultats compacts.
- WEB_SEARCH : compté dans le budget ; testé par simulations, pas de nouveau
  test réseau réel dans cette correction.
- SIMILARITÉ : conservée, retours compacts, tests existants réussis.
- MÉMOIRE MULTI-TURN : RAM uniquement ; les limites valident désormais articles
  et messages, accessibles aux tours suivants. Un redémarrage efface les sessions.
- LIMITES : soft 16 / hard 32 configurables. Soft : instruction de synthèse.
  Hard : nouveaux outils arrêtés, synthèse sans outil, HTTP 200 partial=true,
  reason=tool_budget_reached. Limites de durée/rounds/contexte : réponse partielle
  aussi. Échec de synthèse après limite : notices factuelles de secours.
- TRACES : identifiant de tour, comptes par outil, rounds, résultat final ;
  aucun texte de message, full text ou secret dans ces nouvelles traces.
- CONTEXTE : budget conservateur de 48 000 tokens estimé par borne UTF-8 ; ce
  n'est pas un comptage exact. Compaction native Responses à 12 000 tokens et
  maintien des éléments opaques entre les rounds d'un tour. L'historique entre
  tours reste textuel dans la session RAM.
- FRONTEND : inchangé ; les réponses partielles portent une indication visible
  dans le texte Markdown. Aucun nouvel écran.
- V1 MODIFIÉE : NON.
- ENNODIAGNOSTIC MODIFIÉ : NON.
- APPELS OPENAI RÉELS DANS CETTE CORRECTION : NON.
- TESTS : 65 réussis ; requêtes batch, fulltext batch, analyse batch, soft/hard,
  HTTP 200 partiel et conservation multi-turn, erreur réseau OpenAI en 502,
  citations, compaction, budget, isolation et tests antérieurs.
- ISOLATION : empreintes de 207 fichiers protégés vérifiées, 0 modification.
- BUILD FRONTEND : non relancé, frontend inchangé ; build local antérieur réutilisé.
- GIT DIFF : correction limitée aux fichiers V2 et à ses tests/documentation.
  git diff --check réussi. Les changements antérieurs d'intégration V2 restent.

Le comportement d'un modèle réel et la disponibilité des fournisseurs restent
à vérifier manuellement : les tests automatiques interdisent réseau et appels
OpenAI payants. Pas de déploiement ni de connexion OVH.

Le backend et le frontend démarrés par Codex ont été arrêtés ; ports 8000 et
3000 libérés pour leur lancement manuel depuis VS Code. Les commandes sont
documentées dans README.md.
