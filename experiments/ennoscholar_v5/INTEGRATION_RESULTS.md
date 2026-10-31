# Validation finale du raccordement V5 — 27 octobre 2026

- Branche : `codex/ennoscholar-v5-langgraph`.
- **84 tests réussis, 0 échec, 0 erreur, 0 ignoré**, en 190.67 s (41 tests du prototype + 33 cas d’intégration + 10 régressions du démarrage local).
- 1 521 avertissements de dépréciation des dépendances / chemins historiques ; aucun échec masqué.
- TypeScript : `node D:/EnnoSmart/frontend/node_modules/typescript/bin/tsc --project frontend/tsconfig.json --noEmit --incremental false` : code retour 0.
- Syntaxe des 20 fichiers Python ajoutés (V5, routeur, lanceur) : valide.
- `git diff --check` sur les trois fichiers partagés modifiés : valide.
- Empreintes des sources Python V4 et du routeur V4 identiques à celles prises au début de cette intervention. Les modifications V4 préexistantes sont restées intactes.
- Aucun transport réseau réel, appel API payant, serveur applicatif, reset, commit, push ou déploiement. Seul le contrôle `--check-config` du lanceur a été exécuté, en lecture seule ; aucun serveur n’a été démarré.

## Preuves enregistrées

```json
{
  "tests": 84,
  "failures": 0,
  "errors": 0,
  "skipped": 0,
  "seconds": 190.67,
  "api_network_calls": 0,
  "frontend_typecheck": "passed",
  "python_syntax_files": 20,
  "v4_changed_since_start": [],
  "chemistry": {
    "responses_calls": 9,
    "settled_calls": 9,
    "input_tokens": 900,
    "output_tokens": 450,
    "simulated_cost_usd": 0.005958,
    "limits": {
      "usd": 4.0,
      "tokens": 400000,
      "tools": 48,
      "seconds": 600,
      "web": 4
    },
    "requested": 10,
    "missing": 5
  },
  "stokes": {
    "responses_calls": 3,
    "settled_calls": 3,
    "input_tokens": 300,
    "output_tokens": 150,
    "simulated_cost_usd": 0.001986,
    "limits": {
      "usd": 4.0,
      "tokens": 400000,
      "tools": 48,
      "seconds": 600,
      "web": 4
    },
    "page_count": 29,
    "next_page": null,
    "complete_pages": 29
  },
  "startup_regressions": 10,
  "local_v4_configuration_check": "passed_read_only",
  "local_database_and_profiles_unchanged": true
}
```

Les montants et usages ci-dessus sont ceux de réponses **simulées** valorisées par le vrai `MeteredClient` et le catalogue existant. Ils ne prédisent pas le coût d’un travail scientifique réel.

Les tests exécutent le SDK Responses réel avec un transport HTTP simulé, les outils scientifiques existants, les clients académiques sous HTTP simulé, les écritures SQL applicatives, le saver LangGraph officiel SQLite, les routes authentifiées en ASGI sans socket, les reprises et les garde-fous budgétaires. Les fiches, preuves, plan et brouillon sont sauvegardés par les validateurs existants. La reprise après un commit scientifique sans checkpoint ne rappelle pas le modèle ni l’outil déjà reçu.

Le scénario chimie conserve l’objectif de dix publications et les cinq encore manquantes, tout en réalisant sept lectures puis une sauvegarde à travers LangGraph. Le scénario Stokes complète les pages 21–29 du PDF local, rend les méthodes consultables et conserve les preuves / l’analyse initiales. La mémoire V4 est également testée en réutilisation explicite sans modification de ses enregistrements.

La garde d’audit du runner interdit réseau, DNS, sous-processus, fichiers de secrets et écritures dans la base V4 réelle. Les résultats détaillés sont dans `.artifacts/tests.xml`, `.artifacts/pytest.log` et `.artifacts/integration-summary.json`. Les bases de tests et fixtures privées restent ignorées par Git.

## Limites assumées

- Aucun PostgreSQL réel n’a été testé : le saver utilise le schéma isolé de la même base applicative, mais ses droits et son comportement en concurrence restent à valider.
- Aucun test réel de Sol et aucune manipulation du navigateur de l’application redémarrée : cela reste à la main du consultant. L’interface est raccordée aux routes V5, testées hors ligne, et vérifiée par TypeScript.
- Un appel fournisseur incertain est conservé et bloque une répétition automatique ; son rapprochement n’a pas d’interface dédiée.
- La déduplication s’appuie sur identités, versions, arguments, reçus et caches. Elle ne prouve pas l’équivalence sémantique de formulations différentes et ne peut pas intercepter à l’avance les requêtes internes du Web hébergé de Responses.
- La copie conservatoire de l’interface V4 et les imports scientifiques réutilisés devront suivre les évolutions ultérieures de V4.
- Les limites existantes d’interprétation des figures et de qualité des PDF ne sont pas refondues.

Architecture, fichiers, commandes exactes de démarrage, sélection V5 et retour à V4 : [rapport de livraison](../../docs/ennoscholar-v5-integration.md).


## Correction du démarrage local après le refus PostgreSQL

La commande initiale chargeait le profil PostgreSQL général de `D:\EnnoSmart\.env`, alors que l’installation V4 locale utilise `C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart\ennoscholar_v4_test.db`. Le lanceur corrigé reprend explicitement ce SQLite existant et le profil `C:\Users\hp\.codex\runtime\ennoscholar-v4\openai.local.env`, sans réutiliser la clé générale ou une clé héritée.

Les dix tests vérifient la priorité du profil local, le refus d’une base absente/vide/invalide et d’une clé ou d’un modèle local incorrects, l’absence de modification de l’environnement en cas de refus, l’initialisation des seules tables V5 et l’interdiction de recréer une base déplacée. Le contrôle réel `--check-config` a confirmé `local_profile_only`, `gpt-6.1-sol` et la base locale correcte, sans import du backend applicatif. Les empreintes SHA-256 de cette base et des deux fichiers de profil sont identiques avant et après vérification.

Les paramètres d’authentification sont repris du même profil que le lanceur V4, et ses chemins de documents sont conservés. Les nouvelles tables, PDF et checkpoints V5 restent isolés. Aucun serveur, run scientifique ou service PostgreSQL n’a été démarré. Utiliser la commande corrigée avec `--profile local-v4` dans le rapport de livraison.


## Premier diagnostic frontend avec dépendances partagées (correctif insuffisant, remplacé ci-dessous)

Le diagnostic Turbopack signale que la jonction `frontend/node_modules` vers `D:\EnnoSmart\frontend\node_modules` sort de sa racine de système de fichiers. Le script local V5 `dev:v5` sélectionne maintenant `next dev --webpack`. La disponibilité du paramètre `--webpack` a été vérifiée dans l’aide de Next.js 16.2.6 installé ; la configuration du script a été validée sans lancer de serveur ni faire de requête réseau. Le script `dev` existant et les dépendances partagées de V4 restent inchangés. Cette correction de démarrage ne modifie aucune logique scientifique ; les 84 tests ci-dessus concernent la validation backend précédente.


## Résolution du problème des dépendances frontend sur deux volumes

La jonction vers D: provoquait aussi la résolution Webpack invalide `./D:/.../app-next-dev.js`. Elle a été remplacée par une copie physique hors ligne des 26 557 fichiers (environ 450 Mio) dans le `frontend/node_modules` du worktree sur C:. Aucun téléchargement, changement des dépendances V4 source ou installation npm n’a été effectué. L’ancienne jonction et le cache incorrect sont conservés sous `.artifacts`, hors de l’arbre frontend.

Vérifications exécutées :

- `require.resolve` et `realpath` de `next/package.json` et `next/dist/client/app-next-dev.js` pointent dans le worktree C: ; le `node_modules` n’est plus une jonction.
- Compilation effective avec `NEXT_PUBLIC_ENNOSCHOLAR_V5_TEST_ENABLED=1`, `next build --webpack --experimental-build-mode compile` : **code retour 0, compilé en 50 secondes**, toutes les routes applicatives présentes.
- Le preload `scripts/check_ennoscholar_v5_frontend.cjs` bloque HTTP, DNS, TCP et fetch pendant la compilation ; la télémétrie est désactivée. Aucun accès réseau n’a été tenté (pas de journal de refus).
- TypeScript avec les dépendances C: : **code retour 0**.

La validation compile l’application et collecte les données de compilation, sans serveur, sans phase finale de génération des pages et sans appel au backend ou à Sol. Le lancement interactif reste à la main du consultant. La commande reste `npm.cmd run dev:v5 -- --hostname 127.0.0.1 --port 3000`.
