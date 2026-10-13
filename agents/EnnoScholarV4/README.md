# EnnoScholar V4

Assistant conversationnel indépendant : **ScholarChatAgent**, un modèle `gpt-6.1-sol`
via Responses, sans orchestration V1, framework multi-agents, Chroma, embeddings ou reranker.
V1/V2/V3/V3.1, Diagnostic, Amélioration et l'authentification sont conservés.

## Fonctionnement

- `agent.py` : choix automatique des outils, reprise de `call_id`, raisonnement chiffré
  temporaire en mode `store=False`, événements d'activité, annulation et budgets.
- `context.py` : notices transmises par fenêtres, catalogue de titres pour cibler
  les autres abstracts, compression de l'échange sans arrêt prématuré de recherche.
  Les preuves déjà consultées sont réparties entre les finalistes pour la synthèse.
- `publication.py` : publication vérifiée, refus explicites et réparations bornées,
  synthèse de secours sur les références validées et extraits exacts disponibles.
- `catalogue.py` : distinctions fondées sur les jugements enregistrés : pertinent,
  complément retenu, candidat non évalué, à confirmer ou écarté.
- `toolkit.py` : contrats stricts validés côté serveur. Recherche, identification,
  lecture, analyse, comparaison, similarité, sélection motivée, plans et rédaction.
- `sources.py` : instances privées des clients I/O V2 pour OpenAlex, Semantic Scholar,
  arXiv et vérification DOI Crossref/DataCite. Retry borné, fournisseurs indépendants.
- `store.py` : déduplication conservative et conservation de toutes les notices.
  Les candidats sont présentés au GPT sans classement scientifique numérique.
- `tools/fulltext.py`, `tools/extraction.py`, `network.py`, `pdf_store.py` : téléchargement
  public limité, DNS épinglé et revalidé à chaque redirection, extraction paginée,
  provenance et cache immutable des PDF. Aucun contournement d'accès.
- `tools/article_analysis.py`, `evidence.py` : analyses du **même modèle**, preuves exactes,
  versions de source et validation des citations. Une affirmation est interprétée par
  le modèle ; Python vérifie son extrait et sa provenance, sans prétendre démontrer
  automatiquement l'implication scientifique entre l'extrait et l'affirmation.
- `tools/outline.py` : identifiants stables, modifications ciblées, associations et versions.
- `session.py`, `service.py` : checkpoints PostgreSQL/SQLAlchemy, propriétaires, messages,
  analyses et plans persistants, baux de session et traitements bornés en arrière-plan.

La recherche Web utilise directement **`web_search` natif Responses**, l'équivalent de
`search_web(query)` dans l'architecture demandée. Les formulations viennent du GPT.
Ses URL/annotations sont conservées ; les articles trouvés peuvent être résolus avec
`get_article`. Pour une version auteur, le GPT transmet à `get_fulltext` des URL découvertes
sur le Web. La récupération ne suppose jamais qu'une page éditeur contient le texte intégral.

`analyze_article(read)` expose des passages ; `save` stocke les analyses structurées
du modèle. `finalize_response(analyses=...)` permet également de sauvegarder ces analyses
avec la synthèse. `write_section(read/save)` conserve la rédaction du modèle après
vérification des références et preuves ; cet outil ne produit pas un texte simulé.

Les états d'accès sont distincts : `metadata_only`, `abstract_only`, `fulltext_available`,
`fulltext_extracted`, `fulltext_analyzed`, `access_blocked`, `extraction_failed`.
La présence d'un lien PDF n'est pas une lecture. `fulltext_analyzed` exige une analyse
réellement sauvegardée et étayée. L'extraction et la consultation peuvent rester partielles.

## Configuration

`OPENAI_API_KEY` côté serveur et `DATABASE_URL` sont nécessaires au chat persistant
(PostgreSQL pour le profil prévu, SQLite pour la validation locale de ce poste).
`OPENALEX_API_KEY` et `SEMANTIC_SCHOLAR_API_KEY` améliorent l'accès aux fournisseurs.
Leur indisponibilité ne doit pas empêcher les autres fournisseurs de continuer.

Les paramètres `ENNOSCHOLAR_V4_*` sont regroupés dans `config.py` et illustrés dans `.env.example` :
modèle et raisonnement, Web activé, six tours d'exploration et jusqu'à trois tentatives
de publication/réparation distinctes, 80 nouveaux
candidats, dix lectures approfondies, 48 outils, quatre appels Web, 400 000 tokens cumulés,
600 secondes, plafond estimé de 4 USD par message. Ce sont des limites, pas des quotas à remplir.
Les prix par million de tokens sont configurables ; défaut standard : entrée 2 USD,
sortie 10 USD. Le coût affiché est une estimation conservatrice, pas une facture OpenAI.
Une erreur d'accès au modèle est explicite ; aucun remplacement silencieux.

Une fenêtre transmet au plus huit notices et leurs abstracts (jusqu'à 1 600 caractères
par notice ; le champ `abstract_truncated` indique un extrait). Les notices complètes
restent dans le store et `get_article`. `search_collected_articles(exclude_presented=true)`
expose les fenêtres suivantes sans nouveau fournisseur ni classement scientifique ajouté.
Le lot collecté n'est pas réduit. Un contexte volumineux réduit la transmission, sans
forcer la publication avant l'examen des candidats. Les événements `responses` enregistrent
les abstracts réellement présents dans le payload, leur longueur et leur troncature.

`publication_accepted` distingue l'acceptation technique, `coverage_sufficient` conserve
le jugement du modèle sur la couverture et `research_status` vaut `sufficient`, `incomplete`
ou `not_assessed`. Le statut d'exécution `completed` signifie fin du traitement, pas
exhaustivité scientifique. Les groupes de bibliothèque sont accessibles par le paramètre
API `category`, sans modifier les moteurs ou le jugement de pertinence du modèle.

`ENNOSCHOLAR_V4_PDF_DIRECTORY` peut déplacer le cache. Limites : 15 Mo/document,
500 pages PDF, un million de caractères extraits. Les dépassements sont signalés.
Les formats privés acceptés sont PDF, DOCX sans macros, TXT UTF-8 et Markdown.
Pas d'OCR ; les documents sans texte exploitable sont refusés.

## Démarrage sur ce poste

Le travail est dans `C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart`, branche
`feature/ennoscholar-v4-chat`, issue du commit `46c2665` (descendant de la branche de
référence `review/ennoscholar-v2-20261012`). Le dossier `D:\EnnoSmart` avec ses modifications
locales et suppressions non commitées n'a pas été modifié.

Depuis le worktree, avec la base SQLite locale déjà utilisée sur ce poste :

```powershell
Set-Location C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart
$env:DATABASE_URL='sqlite:///./ennoscholar_v4_test.db'
$env:CORS_ORIGINS='http://localhost:3000,http://127.0.0.1:3000'
& D:\EnnoSmart\.venv\Scripts\python.exe scripts/start_ennoscholar_v4.py --env-file D:\EnnoSmart\.env --port 8002
```

Dans un autre terminal :

```powershell
Set-Location C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart\frontend
$env:NEXT_PUBLIC_API_URL='http://127.0.0.1:8002'
npm ci
npm run dev
```

Ouvrir `http://localhost:3000/ennoscholar-v4` et se connecter normalement. La sidebar
contient V4 pour les rôles existants ; les restrictions de V1/Amélioration restent inchangées.
Aucun projet/diagnostic n'est nécessaire. `project_id=null`, `selected_verrous=[]` sont
réservés à une intégration future, non implémentée.
Le profil `.env` initial est uniquement lu. PostgreSQL peut remplacer SQLite avec son
URL habituelle ; sa migration réelle reste à vérifier quand le serveur sera disponible.

## Schéma et migration

Sept tables V4 uniquement : `scholar_v4_sessions`, `scholar_v4_runs`,
`scholar_v4_messages`, `scholar_v4_articles`, `scholar_v4_session_articles`,
`scholar_v4_analyses`, `scholar_v4_outlines`.
Les tables sont enregistrées sur le metadata existant et créées additivement au démarrage.
La migration SQL équivalente est `backend_api/db/migrations/20261013_ennoscholar_v4.sql`.
Aucune modification de colonne, suppression ou mise à jour des anciennes tables.

Le checkpoint JSON de session reste l'état transactionnel exact ; les tables dédiées
indexent messages, notices, analyses et versions de plans dans la même transaction.
Les notices et analyses privées sont isolées par session. Les mêmes octets PDF sont stockés
une seule fois par SHA-256. Aucune route ne donne un accès global à ce cache.

## Vérification

Voir `docs/ennoscholar-v4-validation.md` pour les commandes, résultats réels et limites.
Les rapports de recherche ne contiennent pas de clé API ou de document privé.

Sources OpenAI vérifiées le 13 octobre 2026 :
[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
[function calling](https://developers.openai.com/api/docs/guides/function-calling),
[Web search](https://developers.openai.com/api/docs/guides/tools-web-search).
