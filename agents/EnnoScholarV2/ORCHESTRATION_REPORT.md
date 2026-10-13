# Correction locale de l'orchestration — 10 octobre 2026

## Cause vérifiée avant modification

Le catalogue ne proposait que `get_article(article_id=...)`, avec un identifiant obligatoire.
Le modèle ne pouvait donc pas demander les détails de plusieurs candidats en un appel.
La boucle exécutait séparément chaque appel proposé, sans interdiction des séries unitaires.
Le prompt ne proposait aucun batch de notices et mentionnait encore le lecteur unitaire.
Les logs fournis ne contiennent pas les arguments : les 16 identifiants et leur répartition
entre réponses OpenAI ne peuvent pas être établis à partir de ces logs.

La soft limit de 16 était seulement une instruction injectée à la prochaine requête OpenAI.
Elle n'était pas réévaluée pendant l'exécution d'un groupe d'appels proposé dans une même
réponse. Par exemple, deux recherches puis seize notices dans un groupe conduisaient à
18 appels avant la prochaine instruction soft ; fulltext et analyse conduisaient ensuite
à 20, sous la hard limit de 32. Cette séquence a été reproduite avec l'agent sauvegardé.

`APITimeoutError` n'était pas intercepté autour de la requête Responses de la boucle
principale. Le routeur renvoyait donc 502. Le secours existant concernait seulement les
finalisations pour limite de budget. Le service ne validait pas le registre temporaire
après une exception ; une nouvelle session pouvait perdre les résultats du tour.

## Modification

- Seul `get_articles(article_ids=[...])` est désormais exposé au modèle pour les notices.
  Un appel accepte 1 à 12 identifiants, déduplique les identifiants répétés et conserve
  les erreurs par article sans supprimer les autres résultats. Les métadonnées, DOI,
  source, abstract compact (jusqu'à 4 000 caractères) et analyses existantes sont lus
  via le lecteur interne existant. Aucun texte intégral n'est renvoyé.
- Le lecteur unitaire reste uniquement interne pour compatibilité ; une proposition
  `get_article` dans la boucle est refusée, avec redirection vers le batch. Aucun appel
  unitaire du modèle n'est exécuté, même si une ancienne réponse en propose 17.
- Le prompt conseille de détailler quelques candidats réellement utiles, en un batch,
  puis de regrouper fulltext et analyse. Le choix des étapes reste libre.
- Dès la soft limit, si des articles ont été collectés, la recherche scientifique et
  la similarité sortent du catalogue disponible. Le batch de notices reste disponible
  tant qu'aucun batch n'a réussi dans le tour. Ensuite il est retiré. Les propositions
  déjà reçues sont également vérifiées pendant leur exécution. `get_fulltext`,
  `analyze_articles`, `read_article` et `web_search` restent accessibles : aucun arrêt
  automatique à 16 ne rend la vérification fulltext impossible.
- Si la requête suivante timeout après une vérification fulltext ou une analyse
  réussie, le backend demande UNE synthèse supplémentaire avec `tools=[]` et
  `tool_choice="none"`. Les résultats existants sont réutilisés ; aucun outil n'est
  relancé. Cette reprise possède son propre délai API borné par la configuration
  existante, même si le délai du tour a expiré.
- Si cette reprise timeout aussi : HTTP 200, `partial=true`,
  `reason="synthesis_timeout"`, références, disponibilité réelle et résumés d'analyse
  vérifiés. Le service valide normalement les résultats en RAM. La même protection
  est appliquée au finaliseur existant pour limites. Un timeout avant toute collecte
  utile reste une erreur amont, sans prétendre avoir effectué une recherche.

## Reproduction contrôlée

SDK OpenAI, recherche scientifique et récupération fulltext simulés ; route HTTP,
service, registre, validation des preuves et gestion du contexte réels. Réseau et
appels OpenAI payants interdits pendant la reproduction. Le modèle simulé adapte
ses propositions au catalogue : 8 candidats utiles en batch au lieu de 16 unitaires.
Cela vérifie le protocole et la récupération ; ce n'est pas une nouvelle exécution
réelle de GPT ni une mesure de sa latence.

| Résultat mesuré | Agent sauvegardé | Correction, réessai réussi | Correction, deux timeouts |
|---|---:|---:|---:|
| Articles collectés dans le tour | 289 | 289 | 289 |
| `search_scientific` | 2 | 2 | 2 |
| `get_article` exécutés comme appels du modèle | 16 | 0 | 0 |
| `get_articles` | 0 | 1 | 1 |
| `get_fulltext` | 1 | 1 | 1 |
| `analyze_articles` | 1 | 1 | 1 |
| Total outils | 20 | 5 | 5 |
| HTTP | 502 | 200 | 200 |
| `partial` | absent | false | true |
| Articles conservés dans la session après la réponse | 0 | 289 | 289 |
| Textes complets / analyses conservés | 0 / 0 | 3 / 3 | 3 / 3 |
| Requêtes de reprise sans outils | 0 | 1 | 1 |

La baisse des appels d'outils est de 75 % sur ce scénario contrôlé. Les limites
soft/hard ne comptent pas les requêtes internes d'analyse comme des appels d'outils
supplémentaires du modèle ; le batch d'analyse continue d'utiliser son fonctionnement
existant.

| Fenêtre envoyée à Responses | Avant : octets / tokens estimés | Après : octets / tokens estimés |
|---|---:|---:|
| Après récupération des notices | 66 220 / 13 074 | 49 996 / 9 824 |
| Après fulltext et analyse, avant synthèse | 60 131 / 12 563 | 53 507 / 10 856 |
| Reprise de synthèse sans outils | — | 50 681 / 10 107 |

Ces mesures utilisent le compteur local existant et des articles de test synthétiques.
Le gestionnaire de contexte n'a pas été modifié : la sélection détaillée réutilise
ses méthodes existantes, reste bornée à 12 notices et les sorties d'outils contiennent
uniquement les références nécessaires. Les données détaillées apparaissent une seule
fois dans la sélection active.

## Validation

- `python -m pytest tests/ennoscholar_v2 -q` : **91 tests réussis**.
- Reproduction avant/après : ancienne version 20 appels et HTTP 502 ; nouvelle version
  5 appels et HTTP 200 pour les deux variantes de timeout.
- Vérification du suivi de conversation après deux timeouts : 289 articles toujours
  disponibles dans la même session ; fulltexts et analyses conservés.
- Vérification de la soft limit basse : découverte arrêtée, batch de notices encore
  possible, puis fulltext et analyse atteignables.
- 140 fichiers protégés comparés par SHA-256 : **aucun changement**, notamment V1,
  EnnoDiagnostic, outils scientifiques, `context.py`, configuration, stockage/service,
  modèles, routeurs et fichiers d'environnement.
- Comparaison AST avant/après : `register`, `read_article`, branches scientific search,
  similarité, fulltext et analyse identiques. Bloc de pression du contexte, compaction
  et sécurité en octets identique textuellement.
- `git diff --check` : réussi. V1 et EnnoDiagnostic : aucun diff par rapport à HEAD.
- Frontend : inchangé ; build non relancé pour cette modification backend.
- Appels OpenAI réels : **NON**. Déploiement / connexion OVH : **NON**.

## Fichiers et sauvegardes

Fichiers existants modifiés pour cette correction :

- `agents/EnnoScholarV2/agent.py`
- `agents/EnnoScholarV2/prompts.py`
- `tests/ennoscholar_v2/test_batching.py` (deux propositions simulées migrées au batch)

Nouveaux fichiers :

- `tests/ennoscholar_v2/orchestration_reproduction.py`
- `tests/ennoscholar_v2/test_orchestration.py`
- `tests/ennoscholar_v2/report_orchestration.py`
- `agents/EnnoScholarV2/ORCHESTRATION_REPORT.md`
- Rapport de mesures local : `.codex_tmp/scholar-v2-orchestration-reproduction.json`

Sauvegarde initiale avant toute modification :
`D:\EnnoSmart\.ennosmart_patch_backups\scholar_v2_orchestration_20261010_190237_367`.
Les sous-dossiers `revision_...` conservent également les versions avant les retouches
ultérieures. Le manifeste `protected-sha256.json` permet de revérifier l'isolation.

Le dépôt contenait déjà des modifications d'intégration et les fichiers V2 non suivis.
La correction ne modifie aucun de ces fichiers d'intégration ni le frontend et ne crée
aucun commit. Relancer le backend local pour charger le catalogue et l'orchestration.
