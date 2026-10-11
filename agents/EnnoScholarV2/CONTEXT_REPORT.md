# Correction du contexte EnnoScholar V2 — 10 octobre 2026

Correction locale uniquement. Aucun appel OpenAI réel dans les tests ou la
reproduction. Aucun changement de modèle, outil scientifique, fulltext,
analyse factuelle, stockage des sessions ou frontend.

## Gestion du contexte

- Les octets UTF-8 ne servent plus de budget de tokens.
- Le contexte visible est estimé avec o200k lorsqu'un vocabulaire local V2
  vérifié est disponible ; sinon estimation hors ligne par caractères, sans
  téléchargement implicite. Le chiffré opaque n'est pas tokenisé comme du texte.
- Les véritables `response.usage.input_tokens` sont également lus et journalisés.
- La cible `ENNOSCHOLAR_V2_MAX_INPUT_TOKENS=48000` déclenche une tentative de
  compaction native, puis une continuation avec les outils disponibles.
- La compaction automatique Responses reste activée à 12000 tokens. Après un
  élément compacté automatique, le préfixe ancien est retiré ; l'état compacté
  et les éléments récents du protocole sont conservés.
- Une réponse de compaction autonome est une fenêtre canonique : elle est
  réutilisée intacte. Un échec de compaction autorise encore une continuation
  raisonnable avec la compaction serveur activée.
- Une seule sélection active, plafonnée à 12 notices, apparaît dans les
  instructions. Les recherches la mettent à jour. Les anciens résultats du
  protocole portent des références, sans listes répétées de notices.
- Déduplication : article ID, DOI normalisé, arXiv sans suffixe de version,
  titre normalisé. Les articles complets restent dans `session.articles`.
- Fulltexts et passages restent backend ; seuls statuts, analyses compactes et
  preuves vérifiées sont présentés au modèle. Batchs et budgets soft/hard conservés.
- Le prompt privilégie search_scientific pour un sujet général ; la similarité
  est surtout recommandée avec un article de référence. L'outil de similarité
  est inchangé.
- `ENNOSCHOLAR_V2_MAX_CONTEXT_BYTES=4000000` est une sécurité extrême distincte,
  vérifiée après une tentative de compaction. Ce garde-fou peut seul déclencher
  `context_safety_bytes_reached`. Le chemin prématuré `context_budget_reached`
  a été supprimé de la boucle.

## Reproduction contrôlée avant/après

Même jeu de données synthétiques : 289 articles backend avec abstracts complets,
8 notices maximum par outil, séquence exacte :

```
search_scientific → find_similar_articles → find_similar_articles → search_scientific
```

L'ancienne version est chargée depuis le backup, avec son ancien prompt et son
ancien calcul. Les appels scientifiques et OpenAI sont simulés. Les tokens sont
des estimations locales o200k du JSON visible, pas la consommation d'un run API réel.

| Fenêtre après les quatre outils | Octets JSON | Tokens estimés | Fulltexts ensuite | Analyses ensuite | Fin |
|---|---:|---:|---:|---:|---|
| Avant | 51 885 | 10 889 | 0 | 0 | context_budget_reached, partial=true |
| Après | 26 430 | 6 051 | 5 | 3 | Réponse complète, partial=false |
| Après, avec état compacté automatique simulé | 24 536 | 5 426 | 5 | 3 | Réponse complète |
| Après, avec pression serveur simulée puis compaction autonome | 23 264 | 4 949 | 5 | 3 | Réponse complète |

Les requêtes normales successives contiennent 0, 8, 12, 12, 12 notices actives.
Le backend conserve les 289 articles. Les séquences corrigées exécutent ensuite
un batch get_fulltext de cinq articles, puis un batch analyze_articles de trois.

### Volume de chaque sortie dans le protocole

Les outils continuent à produire huit candidats compacts. Leur présentation au
modèle est remplacée par des références à la sélection active unique.

| Sortie | Avant : octets / tokens | Après : octets / tokens |
|---|---:|---:|
| search_scientific 1 | 10 904 / 2 242 | 632 / 227 |
| find_similar_articles 1 | 10 800 / 2 214 | 528 / 199 |
| find_similar_articles 2 | 10 818 / 2 215 | 546 / 200 |
| search_scientific 2 | 10 904 / 2 237 | 632 / 222 |

Ces volumes incluent l'enveloppe function_call_output. La sélection active
elle-même est comptée une seule fois dans les totaux de la première table.
Les détails machine sont dans
`D:\EnnoSmart\.codex_tmp\ennoscholar-v2-context\reproduction-before-after-final.json`.

## Validation

`python -m pytest tests/ennoscholar_v2 -q` : **76 tests réussis**.

Le nouveau test couvre les quatre recherches avec 289 articles, le maintien
de la sélection bornée et dédupliquée, la continuation vers fulltext et analyse,
le retrait des anciens couples appel/résultat après compaction automatique,
l'utilisation de la compaction autonome avant une continuation, et la
continuation native lorsque la compaction autonome est indisponible.
Les tests couvrent aussi les quatre identités de déduplication, le compteur
hors ligne et l'exclusion des octets chiffrés du comptage de texte.

Un passage sans vocabulaire local et un passage avec le vocabulaire o200k
vérifié ont réussi. Les tests ne prouvent pas la sélection spontanée des outils
par un modèle réel ni la disponibilité des fournisseurs.

**Isolation : 212 fichiers protégés vérifiés par SHA256, zéro modification.**
V1, EnnoDiagnostic, les outils scientifiques, fulltext, analyse, service RAM,
frontend et fichiers `.env` sont inchangés. `git diff --check` réussi.
Build frontend non relancé : frontend inchangé. Aucun déploiement OVH.

## Fichiers et backup

Fichiers préexistants modifiés :

- agents/EnnoScholarV2/agent.py
- agents/EnnoScholarV2/context.py
- agents/EnnoScholarV2/config.py
- agents/EnnoScholarV2/prompts.py
- agents/EnnoScholarV2/README.md
- agents/EnnoScholarV2/.env.example
- tests/ennoscholar_v2/test_batching.py

Nouveaux fichiers :

- tests/ennoscholar_v2/context_reproduction.py
- tests/ennoscholar_v2/test_context_window.py
- agents/EnnoScholarV2/CONTEXT_REPORT.md

Backup horodaté, avec versions initiales et révisions avant modifications :
`D:\EnnoSmart\.ennosmart_patch_backups\scholar_v2_context_20261010_183558_037`.

Artefacts locaux ignorés : vocabulaire o200k V2 vérifié (3 613 922 octets),
résultats JSON de reproduction et chemin du backup dans `.codex_tmp`.
Le vocabulaire est lu uniquement par V2 ; aucun cache partagé V1 n'est modifié.

Le backend doit être relancé pour charger cette correction. Ses conversations
restent en mémoire RAM et sont réinitialisées au redémarrage.
