# EnnoScholar V4 — bilan du 13 octobre 2026

Ce document conserve le bilan de livraison initial. Les corrections de finalisation,
la régression des 70 notices et leur validation réelle sont décrites dans
[le bilan des fenêtres et du contexte](ennoscholar-v4-context-windows.md), avec les
mesures, l'authentification consultant réelle et le lancement SQLite sur ce poste.

## Livraison et isolation

Worktree : `C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart`.
Branche : `feature/ennoscholar-v4-chat`, base `46c2665`, descendant de
`review/ennoscholar-v2-20261012` (`0497f719`). Aucune fusion, aucun déploiement ni push.
Les modifications locales du dossier initial `D:\EnnoSmart` restent intactes.

Fichiers ajoutés :

- `agents/EnnoScholarV4/` : agent, configuration, contrats d'outils, clients scientifiques,
  Article Store, preuves, téléchargement/cache, modèles SQLAlchemy et service de sessions.
- `agents/EnnoScholarV4/tools/` : résolution et extraction, analyse scientifique et plans.
- `backend_api/routers/scholar_v4.py` et migration additive
  `backend_api/db/migrations/20261013_ennoscholar_v4.sql`.
- `frontend/app/ennoscholar-v4/page.tsx`,
  `frontend/components/ennosmart/ennoscholar-v4-chat.tsx`, `frontend/lib/scholar-v4.ts`.
- `scripts/start_ennoscholar_v4.py`, `scripts/run_ennoscholar_v4.py`.
- `tests/ennoscholar_v4/`, références synthétiques et rapports de validations réelles.
- Ce rapport et `agents/EnnoScholarV4/README.md`.

Fichiers existants modifiés uniquement par ajout V4 : `.env.example`, `.gitignore`,
`backend_api/main.py`, `frontend/components/ennosmart/app-shell.tsx`, `frontend/lib/api.ts`.
Les fichiers des anciens agents, leurs routes/pages et composants restent identiques à la base.
Le fichier Next généré automatiquement est restauré avant livraison.

## Fonctions opérationnelles

Chat autonome sans projet/diagnostic, Responses API avec vrai GPT et function calling,
recherche Web native, OpenAlex/Semantic Scholar/arXiv, DOI vérifiés, notices payantes conservées,
sélection scientifique motivée par le modèle, recherche adaptative, récupération publique de PDF/HTML,
analyses sauvegardées avec extraits exacts et pages/sections, références contextuelles,
similarité/citations, comparaison, plans à identifiants stables et rédaction sourcée.

Les erreurs d'accès, d'extraction et de fournisseur sont distinguées des lectures réussies.
Historique et checkpoints appartiennent à l'utilisateur ; annulation, erreurs réseau, imports
PDF/DOCX/TXT/Markdown, Markdown, tableaux, citations, copie et interface responsive sont implémentés.
La rédaction et les opérations de plans sont vérifiées par tests automatisés ; tous les formats
et chaque outil n'ont pas fait l'objet d'une démonstration réelle indépendante avec GPT.

## Vérifications exécutées

| Commande / contrôle | Résultat |
|---|---|
| `python -m pytest tests/ennoscholar_v4 tests/ennoscholar_v3 tests/ennoscholar_v31 --import-mode=importlib -q --disable-warnings` | **96 tests réussis** |
| `python -m pytest tests/ennoscholar_v2 -q --disable-warnings` | **302 tests réussis** |
| `npm run typecheck` | Réussi |
| `npm run build` | Réussi ; route `/ennoscholar-v4` générée |
| `python -m compileall -q agents/EnnoScholarV4 backend_api/routers/scholar_v4.py scripts/run_ennoscholar_v4.py scripts/start_ennoscholar_v4.py` | Réussi |
| Comparaison Git des anciens agents/routes/pages avec `46c2665` | Aucun changement |
| Navigateur réel, fixture backend isolée | Sidebar, activité, envoi, Maj+Entrée, Markdown, citation, rechargement, arrêt, import texte, erreur réseau avec conservation du brouillon, responsive : réussis ; aucune exception JS |
| Navigateur → vrai service backend V4 → vrai `gpt-6.1-sol` → chat | **Réussi**, 18,4 s, 721 tokens de sortie, 13 liens cliquables, réponse complète |
| Recherche scientifique réelle, conversation vide | **Réussi**, 54 notices, 2 recherches scientifiques, 4 appels Web, 8 résolutions de texte, réponse sourcée en 264,8 s ; coût estimé 0,533 USD |
| Analyse réelle dans la conversation existante | Trois analyses intégrales sauvegardées (MeshingNet3D, DDPM-Polycube, DL-Polycube), une comparaison ; intégrité exacte des preuves vérifiée |
| Plan réel à partir du corpus existant | Réussi, trois sections et références associées ; aucun appel Web |
| Modification réelle ciblée du deuxième titre | Réussi, deux appels Responses, un seul outil `manage_outline`, **zéro appel Web**, 8,4 s |
| Jeu de pertinence à sept catégories, GPT réel et attentes manuelles | **Précision 1,0 / rappel 1,0** pour ce cas synthétique limité |

Les tests V2 ont été exécutés séparément : leur suite utilise des imports historiques
incompatibles avec une collecte globale en mode `importlib`. Les 17 erreurs de collecte
de la commande globale initiale n'étaient pas des régressions V2 ; la commande propre
à V2 a ensuite exécuté ses 302 tests avec succès.

Un premier essai réel a atteint le plafond initial de tokens avant synthèse. Le contexte
et la réserve de synthèse ont été corrigés, puis l'essai depuis une conversation vide a réussi.
La consultation seule n'avait pas enregistré d'analyse dans les premiers essais ; les outils
de sauvegarde et la sauvegarde lors de la synthèse sont testés, et trois analyses ont ensuite
été effectivement enregistrées. Aucun essai incomplet n'est présenté comme un succès.

Le test navigateur a également détecté et permis de corriger l'annulation prématurée de
la requête d'affichage de la réponse à la fin d'un traitement.

Les rapports vérifiables sont dans :

- `tests/ennoscholar_v4/research/observed_live_20261013.json` ;
- `tests/ennoscholar_v4/research/observed_relevance_20261013.json` ;
- `tests/ennoscholar_v4/research/observed_ui_live_20261013.json`.

## Non exécuté et limites restantes

- **PostgreSQL réel et démarrage de l'application complète sur son profil PostgreSQL** :
  tentative de connexion refusée sur `127.0.0.1:5432` ; Docker Desktop non démarré.
  Aucune table ni donnée de cette base n'a été modifiée. Persistance et isolation validées
  avec SQLAlchemy/SQLite ; migration PostgreSQL générée, mais non appliquée/testée ici.
- La démonstration navigateur avec GPT réel utilise une **authentification de test et une
  base SQLite isolée**, ainsi que la bibliothèque réelle déjà recueillie. Elle ne constitue
  pas un login sur le compte consultant et la base PostgreSQL de production.
- Pas de benchmark scientifique multi-domaines validé par des experts, ni mesure indépendante
  exhaustive du rappel sur toute la littérature. Le score 1,0 du jeu synthétique ne prouve
  pas une qualité générale équivalente à ChatGPT. Certaines références pertinentes peuvent
  encore manquer et les résultats dépendent du sujet, du modèle et des fournisseurs.
- Semantic Scholar a renvoyé des `429` pendant les essais ; les autres fournisseurs ont continué.
  Les PDF payants, protégés ou introuvables restent décrits avec le niveau réellement consulté.
- Pas d'OCR. Téléchargements limités, documents Word sans macros, extraction parfois partielle.
  Python vérifie les preuves exactes ; leur interprétation scientifique reste celle du modèle.
- Le budget de coût est une estimation conservatrice ; facturation réelle et frais particuliers
  du compte peuvent différer. Les erreurs/quota OpenAI sont explicites.
- Le pool de traitements est local au processus API ; une interruption du worker est récupérée
  par le bail en base, sans reprise automatique d'un appel OpenAI interrompu.

## Lancement

Depuis le worktree, avec PostgreSQL correspondant au profil disponible :

```powershell
& D:\EnnoSmart\.venv\Scripts\python.exe scripts/start_ennoscholar_v4.py --env-file D:\EnnoSmart\.env --port 8002
```

Dans un autre terminal :

```powershell
cd C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart\frontend
$env:NEXT_PUBLIC_API_URL='http://127.0.0.1:8002'
npm ci
npm run dev
```

Ouvrir `http://localhost:3000/ennoscholar-v4` et se connecter avec l'authentification existante.
Variables et schéma détaillés dans le README V4 et `.env.example` : `OPENAI_API_KEY`,
`DATABASE_URL`, paramètres `ENNOSCHOLAR_V4_*`, clés fournisseurs facultatives.
Il faut exécuter le frontend et le backend de **ce worktree** pour utiliser V4.
