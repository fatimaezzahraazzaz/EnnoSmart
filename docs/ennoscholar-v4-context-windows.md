# V4 — correction des fenêtres et validation réelle, 13 octobre 2026

Worktree : `C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart`.
Branche : `feature/ennoscholar-v4-chat`. Modifications précédentes conservées.
Aucun push ni merge ; sources du dossier initial `D:\EnnoSmart` inchangées.

## Correction et portée

Le run en régression `3c831892586f47b3804b73dfbf22ccd0` enregistrait 70 notices
et 68 abstracts, mais supprimait leur échange trop volumineux (environ 82 544
caractères encapsulés) et forçait la synthèse sur les 20 derniers titres seuls.

- `store.window` transmet jusqu'à huit notices, avec titres, IDs, métadonnées utiles
  et abstracts jusqu'à 1 600 caractères ; la troncature est indiquée. La collecte et
  les données complètes restent intactes. Les notices omises restent non exposées.
- `request_inputs`/`compact_exchange` réduisent la transmission d'un échange excessif
  sans forcer la finalisation. Les fenêtres suivantes sont disponibles par les outils
  locaux existants, sans nouvelle recherche fournisseur.
- `model_context` conserve les abstracts utilisables sans finalistes, la question
  active, un catalogue transversal de titres et des preuves réparties entre les travaux.
  Passages consultés et analyses structurées sauvegardées sont distingués.
- `abstract_deliveries` audite les abstracts présents dans le payload, avec longueur
  et troncature, plutôt que de déduire une transmission de leur existence en base.
- `catalogue.library_groups`, API/service et frontend distinguent travaux retenus,
  compléments retenus, candidats non évalués, à confirmer et écartés. Les autres notices
  ne deviennent pas automatiquement des références pertinentes.
- `publication_accepted`, `coverage_sufficient`, `research_status` distinguent
  publication technique et jugement de couverture. `completed` signifie fin du run.
- Le fallback garde les contrôles et peut fournir des extraits exacts d'abstracts
  validés, sans inventer de sélection/contribution ou de lecture intégrale.
- Le rendu masque les marquages internes Web non compatibles Markdown ; les liens
  bibliographiques et preuves vérifiés restent affichés, les brouillons restent tracés.

`ScientificSources`, connecteurs, `ScholarTools.search`, `ArticleStore.insert/search`,
prompt scientifique original, récupération/extraction et validateurs de preuves sont
préservés. Comparaison AST des fonctions de recherche/collecte avec `1a36af6` ; diff
inchangé pour moteurs/extraction/evidence/ArticleAnalyzer ; anciens agents/routes/pages
comparés octet par octet avec `46c2665`. Aucun classement scientifique Python ajouté.

## Tests réussis

- **122 tests V4/V3/V3.1**, **302 tests V2**, soit **424 tests**.
- `npm run typecheck`, `npm run build`, `git diff --check`.
- Cas 70 notices/68 abstracts, sans finaliste, ensemble supérieur à 85 000 caractères :
  abstracts effectivement transmis au client simulé, sélection de trois travaux situés
  hors de la première fenêtre, publication étayée sur leurs abstracts, stockage intact.
- Fenêtres successives exposant les 68 abstracts sans fournisseur ; récupération d'un
  corpus existant ; répartition de preuves entre deux articles ; refus/réparation ;
  budget/fallback ; catégories/ownership ; persistance de l'ordre et couverture distincte.
- Chrome/Playwright réel : tableau, liens, listes secondaires repliées/chargées,
  restauration après rechargement, identifiants d'approfondissement, rendu mobile à
  390 px, aucun défaut runtime ni marquage Web interne visible.

```powershell
& D:\EnnoSmart\.venv\Scripts\python.exe -m pytest tests/ennoscholar_v4 tests/ennoscholar_v3 tests/ennoscholar_v31 --import-mode=importlib -q --disable-warnings
& D:\EnnoSmart\.venv\Scripts\python.exe -m pytest tests/ennoscholar_v2 -q --disable-warnings
```

## Rejeux réels de la demande exacte

Backend réel, compte consultant réel, SQLite et `gpt-6.1-sol`, aucun fixture LLM.

| Run | Entrée | Sortie | Durée | Coût estimé USD | Résultat |
|---|---:|---:|---:|---:|---|
| Ancien `78053599…` | 264 248 | 10 122 | 485,328 s | 0,679716 | `round_budget`, aucune synthèse |
| Régression `3c831892…` | 13 682 | 1 230 | 43,282 s | 0,039664 | Une piste sur titre, données écartées |
| Fenêtres, premier essai `4eb1df04…` | 133 479 | 7 753 | 327,032 s | 0,384488 | 7 travaux ; couverture incomplète |
| Rejeu final `abc23ba7…` | 118 413 | 5 426 | 265,297 s | 0,331086 | 5 travaux, analyse ciblée DDPM, publication acceptée |

Dernier essai : **55,2 % de tokens d'entrée en moins**, **45,3 % de temps en moins**
que l'ancien run. Comparaison de la même demande, pas benchmark déterministe : les
requêtes choisies par GPT, les fournisseurs et les textes disponibles varient. Le
faible coût du run en régression ne représente pas une optimisation réussie.

Les deux essais sont conservés. Le premier recueille 70 notices, dont 45 abstracts ;
29 notices sont exposées. Il corrige un refus DOI et distingue 7 travaux retenus,
61 candidats non évalués, 1 à confirmer et 1 écarté.

Le dernier est la session `8f35796738b34cc897874fe0e63ead56`, run
`abc23ba7a9ad45f798563b228927e6f7`, **Rejeu exact maillages 3D — fenêtres et preuves**.
Il conserve 20 notices/15 abstracts ; neuf IDs d'abstracts sont réellement présents
dans les payloads. Le contexte le plus volumineux fait 39 318 caractères.

Le tableau présente **MeshingNet3D, DDPM-Polycube, TetGAN, DL-Polycube et CubeFlipNet**.
DDPM possède une analyse sur cinq preuves du texte complet, avec mécanisme, résultats
et limites. Les autres contributions sont préliminaires sur abstracts/extraits Web.
MeshingNet3D n'a pas d'abstract formel en base dans cette session : résumés éditeur/auteur
consultés par le Web natif, extraction intégrale échouée, aucune analyse intégrale.

Finalisation dès la première tentative, aucun fallback. Le modèle déclare la couverture
suffisante pour sa réponse tout en signalant une sélection non exhaustive. Bibliothèque :
5 retenus, 11 non évalués, 1 à confirmer, 3 écartés. Aucun complément supplémentaire
retenu n'est fabriqué. Les 84 URL découvertes restent en trace ; 6 sources utiles sont
listées sous la réponse. Le tableau et les listes sont réellement affichés dans le chat.

« Approfondis l'article 4 » cible **DL-Polycube**, préserve l'ordre, utilise les sources
existantes et sauvegarde une analyse sur abstract : **38,516 s**, **44 034 tokens entrée**,
**1 099 sortie**, **0,099058 USD**, zéro recherche Web/scientifique. Mesure séparée du rejeu.

Artefacts dans `tests/ennoscholar_v4/research` :

- `observed_context_windows_final_answer_20261013.md` : réponse réellement générée,
  avec normalisation du seul marquage interne Web, identique au rendu du frontend.
- `observed_context_windows_final_replay_20261013.json` : mesures, abstracts transmis,
  métadonnées au moment de la publication et bibliothèque courante après les suivis.
- `observed_context_windows_final_ui_20261013.json` : contrôles navigateur et suivi.
- `observed_context_windows_final_article4_answer_20261013.md` : réponse du suivi.
- `observed_context_windows_*` : premier essai conservé, y compris sa couverture insuffisante.

## Limites et non exécuté

PostgreSQL réel/migration non testés : serveur indisponible, tests de persistance et
d'isolation avec SQLAlchemy/SQLite. Pas de benchmark expert exhaustif du rappel
scientifique. 3DMeshNet/NVMG ne sont pas dans la dernière sélection et peuvent nécessiter
une continuation ; les moteurs n'ont pas été modifiés pour favoriser le test.
Accès payants/inconnus, extractions échouées et lectures partielles restent signalés.
La validation prouve la provenance exacte des passages, pas automatiquement toutes les
interprétations scientifiques. Les réserves de publication restent soumises aux plafonds
globaux et aux disponibilités API ; le fallback ne fabrique pas une analyse experte.

## Lancement PowerShell

Backend :

```powershell
Set-Location C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart
$env:DATABASE_URL='sqlite:///./ennoscholar_v4_test.db'
$env:CORS_ORIGINS='http://localhost:3000,http://127.0.0.1:3000'
& D:\EnnoSmart\.venv\Scripts\python.exe scripts/start_ennoscholar_v4.py --env-file D:\EnnoSmart\.env --port 8002
```

Frontend, autre terminal :

```powershell
Set-Location C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart\frontend
$env:NEXT_PUBLIC_API_URL='http://127.0.0.1:8002'
npm run dev
```

Ouvrir `http://localhost:3000/ennoscholar-v4`. Le profil initial est uniquement lu ;
SQLite conserve le compte consultant et les conversations locales.
