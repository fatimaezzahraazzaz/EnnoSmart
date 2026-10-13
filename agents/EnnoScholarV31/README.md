# EnnoScholar V3.1

Version indépendante demandée après la mission V3. Base de référence : `0497f719`.
Branche : `feature/ennoscholar-v3.1`. V1, V2, V3, EnnoDiagnostic et EnnoAmel sont préservés.
Aucun déploiement. **La qualité scientifique réelle n'est pas encore validée par un benchmark V3.1 payé.**

## Architecture réelle

Un seul `ScholarV31ChatAgent` utilise `gpt-5.6-sol` via Responses. Python n'appelle aucun
autre modèle pour analyser, comparer, classer ou identifier les lacunes. GPT lit les
passages puis fournit ses jugements structurés dans la même conversation.

| Fichier | Responsabilité |
|---|---|
| `agent.py` | Boucle adaptative Responses, call_id, replay temporaire du raisonnement chiffré, budgets, annulation |
| `prompts.py` | Contrat scientifique générique, aucune règle spécialisée ni titre de benchmark |
| `store.py` | Notices canoniques, versions distinctes, BM25 sur tout le corpus, fenêtres cumulatives, décisions |
| `sources.py` | Adaptateurs privés des connecteurs V2 : Semantic Scholar, OpenAlex, arXiv, Crossref/DataCite |
| `tools.py` | Contrats stricts, lecture et enregistrement des analyses/comparaisons/gaps du même GPT |
| `evidence.py` | Passages exacts, versions conservées, pages PDF/sections, validation des citations |
| `fulltext.py`, `network.py`, `pdf_store.py` | Récupération publique légale, identité documentaire, extraction paginée V2, cache PDF immutable |
| `session.py`, `service.py` | Tables `scholar_v31_sessions` et `scholar_v31_runs`, propriétaires, historique, checkpoints, baux |
| `backend_api/routers/scholar_v31.py` | API `/scholar-v3-1`, authentification EnnoSmart |
| `frontend/components/ennosmart/ennoscholar-v31-chat.tsx` | Chat, corpus paginé/recherchable, sélection, comparaison, preuves, import PDF |

Les composants I/O déjà éprouvés de V3 sont isolés dans V3.1 ; ils utilisent les clients,
canonicalisation et extracteurs de V2 sans modifier ces modules. Les boucles V2/V3,
leurs priorités de contexte et fenêtres fournisseurs ne sont pas reprises.

## Outils et raisonnement

- `search_scientific` collecte des pages fournisseurs configurables, jusqu'à 100 notices par fournisseur/appel.
- `web_search` est l'outil natif Responses ; les publications trouvées doivent être résolues bibliographiquement.
- `search_collected_articles` cherche dans tous les candidats backend par BM25, identité exacte,
  diversification par axe/fournisseur et fusion avec embeddings locaux facultatifs.
- `get_articles` résout/lit IDs, titres exacts, DOI, arXiv et URLs ; les finalistes restent accessibles.
- `get_fulltext` récupère un PDF/HTML public, contrôle l'identité et conserve l'extraction.
  `page_start`/`page_limit` permettent de lire d'autres pages d'un PDF conservé, y compris
  au-delà de l'extraction initiale bornée de V2.
- `analyze_articles(read)` expose les passages ; `analyze_articles(save)` stocke l'analyse
  produite par GPT avec ses affirmations et leur origine (`source_statement`/`inference`).
- `find_similar_articles` explore références, citations ou approches conceptuelles voisines.
- `compare_articles` fournit une matrice et peut enregistrer la comparaison du GPT avec preuves.
- `identify_research_gaps` distingue limites d'auteurs, inférences entre plusieurs sources,
  et absence dans le corpus avec un périmètre explicite. Il ne déclare aucune éligibilité CIR.
- `verify_bibliography` vérifie DOI/titre/auteurs/année, versions, accès et preuves.
- `record_candidate_decisions` mémorise rétention/rejet/attente avec motif et finalistes ordonnés.
- `update_research_state` conserve sujet, objectifs, hypothèses, verrous, axes, décisions et plan.
- `finalize_response` contrôle les références déclarées et remplace `[paper_ID]`/`[ev_ID]`
  par des liens issus des notices et des preuves. Une référence inventée est bloquée.

Ces outils ne constituent pas un workflow fixe. Aucune obligation de huit requêtes ou
d'un nombre prédéfini d'analyses. Le modèle choisit la recherche et les lectures utiles.
Les budgets sont des plafonds opérationnels, pas des quotas de qualité ou des étapes temporelles imposées.

## Visibilité et classement

La position fournisseur n'est pas un filtre de lecture. Le corpus complet est stocké
côté serveur ; le contexte GPT contient une mémoire compacte et de petites fenêtres.
Chaque fenêtre possède son ID et ses candidats. Deux recherches du même lot ne peuvent
pas écraser leurs résultats. Elles sont marquées « présentées » après une réponse
Responses confirmant leur livraison ; une réponse interrompue conserve « non livrée ».
« Examiné » exige une décision motivée ou une analyse enregistrée ; une exposition
ne signifie pas que le modèle a effectivement évalué la contribution.

Les scores servent uniquement au retrieval. Le classement final est celui de GPT,
révisable par de nouveaux candidats. Ni disponibilité du PDF, ni analyse antérieure,
ni citations ne fournissent de priorité scientifique automatique. Les articles payants restent candidats.
Deux versions liées par un identifiant explicite partagent un travail canonique,
mais gardent leurs notices et textes séparés. Une ressemblance de titre seule n'établit
pas un lien de version ; les relations non établies restent distinctes.

## Preuves et accès

Chaque preuve conserve article, travail canonique, notice source, URL lorsqu'elle existe,
version, empreinte, section, page PDF lorsque disponible et citation exacte. Les snapshots
conservent la version effectivement lue, même si une extraction ultérieure change.
Python compare le passage et sa localisation à ce snapshot avant de valider l'intégrité.
Le soutien sémantique d'une conclusion reste interprété par le GPT ; ce contrôle
ne remplace pas une annotation scientifique indépendante.

Les statuts restent distincts : métadonnées ; abstract ; texte extrait non analysé ;
passages réellement analysés. `fulltext` ne signifie jamais une lecture exhaustive.
La pagination imprimée et l'OCR ne sont pas inférés ; les PDF numérisés sans texte
sont signalés. L'import utilisateur conserve le PDF original avec son empreinte,
mais ne vérifie pas automatiquement titre, DOI ou auteurs.

## API et interface

Routes authentifiées et limitées au propriétaire :
`POST /chat`, `POST/GET /sessions`, `GET /sessions/{id}`, `GET /sessions/{id}/articles`
(offset, limit, query), `GET /sessions/{id}/articles/{article_id}`, `PATCH` sur cette notice
pour sa conservation, `GET /sessions/{id}/memory`, `POST /sessions/{id}/pdf`,
`GET /runs/{id}`, `POST /runs/{id}/cancel`, sous `/scholar-v3-1`.

Page : `/ennoscholar-v3-1`, entrée distincte dans la sidebar. L'historique est serveur.
Le corpus inclut les notices non encore vérifiées et les travaux payants. Les boutons
Analyser, Travaux similaires et Comparer préparent une demande éditable ; Entrée l'envoie.
Les preuves affichent source, version, section et page PDF. L'import accepte 15 Mo par PDF.

## Lancement local

Depuis `D:\EnnoSmart`, avec PostgreSQL démarré, terminal backend. Le lanceur choisit
`.env.native` s'il existe, sinon `.env` ; cela évite d'utiliser les identifiants Docker
contre la base native. Les appels restent bloqués par défaut :

```powershell
$env:ENNOSCHOLAR_V31_PAID_CALLS_AUTHORIZED = "false"
$env:CORS_ORIGINS = "http://localhost:3002,http://127.0.0.1:3002"
.venv\Scripts\python.exe scripts/run_ennoscholar_v31.py --port 8003
```

Deuxième terminal :

```powershell
Set-Location D:\EnnoSmart\frontend
$env:NEXT_PUBLIC_API_URL = "http://localhost:8003"
npm.cmd run dev -- --hostname 127.0.0.1 --port 3002
```

Ouvrir `http://localhost:3002/ennoscholar-v3-1` et se connecter avec le compte EnnoSmart.
La création/relecture des sessions et l'import PDF sont disponibles sans appel OpenAI.
Le chat scientifique retourne 503 tant que le budget payant n'est pas autorisé.

Sur cette installation, le PostgreSQL natif existant se lance si nécessaire avec :

```powershell
& "D:\EnnoSmartTools\postgresql\pgsql\bin\pg_ctl.exe" start -D "D:\EnnoSmartData\postgres-native" -l "D:\EnnoSmart\.codex_tmp\v31-postgres.log"
```

Cette commande réutilise le cluster existant ; elle ne crée ni ne restaure une base.

**Seulement après validation explicite du budget**, dans le terminal backend avant lancement :

```powershell
$env:ENNOSCHOLAR_V31_PAID_CALLS_AUTHORIZED = "true"
$env:ENNOSCHOLAR_V31_MAX_COST_USD = "4"
```

La configuration est dans `.env.example` de ce dossier ; le serveur lit les variables
d'environnement et le `.env` principal habituel. Les tarifs documentés du modèle sont
une estimation, pas une facture. Les requêtes longues et appels interrompus sont
comptabilisés avec une réserve conservatrice ; ne pas changer de modèle sans tarifs explicites.

## Validation

```powershell
.venv\Scripts\python.exe -m pytest tests/ennoscholar_v31 -q --import-mode=importlib
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend run build
```

Le protocole et les limites sont dans `docs/ennoscholar-v31-validation.md`.
La suite indépendante couvre maillages, biologie structurale et tomographie quantique.
Ses titres/DOI sont exclusivement sous `tests/ennoscholar_v31/research`.
Une validation gratuite des registres a confirmé les références DOI ; elle ne mesure
ni la découverte autonome ni la pertinence scientifique de V3.1.

Sources OpenAI consultées le 12 octobre 2026 :
[modèle et tarifs](https://developers.openai.com/api/docs/models/gpt-5.6-sol),
[function calling](https://developers.openai.com/api/docs/guides/function-calling),
[web_search](https://developers.openai.com/api/docs/guides/tools-web-search).
