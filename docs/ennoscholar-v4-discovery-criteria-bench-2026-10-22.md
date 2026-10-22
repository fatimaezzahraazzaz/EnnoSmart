# EnnoScholar V4 — besoin structuré, classement par critères et banc d'évaluation

Date : 22 octobre 2026. Périmètre : découverte et classement de V4 uniquement. Ce travail s'ajoute aux corrections de Codex du même jour (classement cumulatif, remplacement explicite, revue de découverte, pistes bibliographiques), sans les remplacer.

## Diagnostic sur les runs réels (lecture seule)

Les jugements de pertinence du banc sont indépendants des classements produits.

| Run | Ce qui s'est passé |
|---|---|
| `04382dc6` | TetGAN et 3DMeshNet sont retenus par Luna (« génère des maillages tétraédriques », « structurés 3D hexaédriques ») mais restent hors du top 5. Les places sont prises par les premiers retenus : NeuralMesh (puces, thermique), Watertight (segmentation + réparation), modèle biomédical, AMBER et HypeR, ces deux derniers notés *related* par Luna elle-même. Aucun remplacement n'a été proposé ; aucun profil de pertinence n'a été rempli. P@5 = 0,20. |
| `1e5e467e` | Luna écrit que MeshingNet n'établit pas de démonstration volumique 3D et que HypeR adapte un maillage existant sans le générer. Les deux sont pourtant finalistes. P@5 = 0,40. |

La cause n'est pas la recherche : les bons candidats étaient trouvés et souvent jugés pertinents. Elle tient à deux points.

1. Le jugement « direct / connexe » restait implicite et instable : TetGAN est noté *related*, NeuralMesh *direct*.
2. Le classement cumulatif conservait les premiers venus tant que Luna ne proposait pas de remplacement explicite.

## Corrections

### 1. Besoin structuré — `research.NeedProfile`, `turn.TurnGoalArgs.research_need`, `turn.record_goal`

Pour une recherche bibliographique, le même appel de compréhension (`set_turn_goal`, sans appel supplémentaire) déclare :
- l'objet étudié ;
- le problème ;
- le résultat technique attendu ;
- les méthodes acceptables ;
- le contexte d'application ;
- les contraintes ;
- 1 à 8 critères vérifiables, dont au moins un essentiel ;
- le sens de *direct*, *connexe* et *fondamental* pour ce besoin ;
- une carte de familles d'approches, avec une requête par famille ;
- les incertitudes.

Les contrôles portent sur l'unicité des critères, la présence d'un critère essentiel et des requêtes distinctes.

Le besoin est conservé à part de l'objectif (`state["research_need"]`, avec un historique). Il entre dans `need_signature` : un changement de besoin rend les jugements antérieurs non actuels. Les sessions sans besoin structuré gardent leur signature.

Il est transmis (`context.COMMON_KEYS`) aux requêtes de sélection, de découverte, de revue, de conversation et de finalisation, mais pas aux fenêtres de lecture. Aucun contenu propre à un domaine n'est codé.

### 2. Qualification des candidats — `RelevanceProfile.criteria`, `missing_information`

Chaque décision de sélection porte, pour chaque critère du besoin :
- un statut : `met`, `partial`, `not_met` ou `unknown` ;
- sa base : `abstract`, `title`, `saved_claims`, `fulltext` ou `inference` ;
- une note courte ;
- la liste des informations absentes de la notice.

Un critère non établi reste `unknown`. Le profil est persisté dans `assessment.relevance`, avec les signatures de besoin et de notice existantes. Il n'est réévalué que si la notice, les fiches ou le besoin changent ; c'est le mécanisme de dépendances de Codex.

### 3. Classement par niveaux — `research.qualification`, `research.rebalance_ranking`

Le niveau d'un candidat est lu dans le jugement **actuel** de Luna, jamais dans des mots-clés ou dans l'accès au texte :

| Niveau | Condition |
|---|---|
| 0 | contribution directe et tous les critères essentiels `met` |
| 1 | contribution directe, critères essentiels pas tous établis |
| 2 | contribution connexe ou fondamentale |
| 3 | au moins un critère essentiel `not_met` |

Après chaque `record_candidate_decisions` (`toolkit.py`), la règle s'applique ainsi :
- un candidat retenu d'un meilleur niveau remplace le finaliste du plus mauvais niveau ;
- une place vacante prend le meilleur candidat retenu ;
- la liste est ordonnée par niveau, puis selon l'ordre de Luna à l'intérieur d'un niveau.

Garanties :
- pas de remplacement entre candidats de même niveau, donc pas d'agitation du classement ;
- les choix du consultant (acceptation, ordre, mise en attente) priment ;
- un jugement non actuel ne déplace rien ;
- deux versions d'un même travail n'occupent jamais deux places.

Chaque changement est enregistré dans `ranking_changes`, comme un remplacement proposé par Luna. Il contient :
- l'entrant et le sortant ;
- la justification et les critères comparés avec leurs sources ;
- les incertitudes ;
- les dépendances, la signature du besoin et la provenance.

Exemple de justification : « c2 … : entrant met (abstract), sortant not_met (abstract) ».

Le dossier commun aux finalistes et aux concurrents (`context.scientific_dossier`) inclut ce niveau.

### 4. Carte de recherche — `research.planned_families`, `exploration_options`, `save_discovery_review`

- **Premier tour** : la consigne d'exploration demande une recherche par famille du besoin.
- **Exploration** : `exploration_options.planned_families` indique les familles déjà interrogées.
- **Revue de découverte** : elle ne peut pas autoriser la lecture tant qu'une famille planifiée n'a jamais été interrogée, sauf si elle est déclarée `not_relevant`, `unavailable` ou `covered`, avec sa raison.
- **Hors périmètre** : les pistes bibliographiques, la pagination, les voisins et le web restent ceux de Codex, avec leurs plafonds.

### 5. Essai écarté

J'ai essayé de réordonner la présentation au triage selon les mots du besoin. Sur le banc, cela a **baissé** le Recall@20 : 0,32 à 0,34, contre 0,47 pour le tourniquet par famille. L'essai est annulé, et un test garde ce seuil.

## Banc d'évaluation — `tests/ennoscholar_v4/bench/`

**Jugements** (`discovery_judgments.json`) :
- 490 notices jugées de 0 à 3, sur 5 domaines : maillage 3D, débruitage SAR, recyclage du lithium, détection d'anomalies industrielles, contours de chaussées ;
- les corpus viennent de 9 sessions réelles ;
- chaque domaine a une grille écrite à partir de la demande réelle, **avant** la lecture des notices et sans consulter les classements.

**Limite** : un seul évaluateur (l'assistant de développement), sans validation par un expert. La pertinence est jugée sur titre et abstract.

**Métriques** (`metrics.py`) : P@5 et Recall@20 (pertinent = note ≥ 2), NDCG@5 gradué, déduplication par DOI, arXiv ou titre.

**Exécution** : `python -m tests.ennoscholar_v4.bench.run`. Les deux catégories de chiffres ne sont jamais mélangées.

### Mesuré (sorties réelles, jugements indépendants)

| Session | Finalistes réels P@5 / NDCG@5 | Règle appliquée aux décisions réelles | R@20 tri réel / ordre du code |
|---|---|---|---|
| maillage `53292dc4` | 0,80 / 0,89 | **1,00 / 1,00** (2 changements) | 0,43 / 0,24 |
| maillage `04382dc6` | 0,20 / 0,20 | 0,20 / **0,29** (2) | 0,70 / 0,50 |
| maillage `1e5e467e` | 0,40 / 0,51 | 0,40 / **0,55** (0) | 0,50 / 0,75 |
| maillage `00a2e6bb` | 0,60 / 0,49 | inchangé | 0,36 / 0,18 |
| SAR `6728b41d` | 1,00 / 0,93 | inchangé | 0,35 / 0,56 |
| SAR `ee57ed9a` | 1,00 / 0,70 | inchangé | 0,60 / 0,73 |
| batteries `98fddbeb` | 0,80 / 0,80 | inchangé | 0,39 / 0,30 |
| anomalies `e34be359` | 1,00 / 0,65 | inchangé | 0,44 / 0,68 |
| chaussées `ace44bf8` | 1,00 / 0,61 | inchangé (3 changements d'ordre) | 0,28 / 0,33 |

Les décisions enregistrées ne contiennent pas encore de jugements par critère : la règle n'y lit que la contribution. Elle ne dégrade aucun run et en améliore trois.

### Simulé (jugements par critère remplacés par un oracle ; pas une mesure de Luna)

| | Départ réel P@5 | Oracle exact P@5 | Oracle bruité à 25 % P@5 / NDCG@5 |
|---|---|---|---|
| maillage `04382dc6` | 0,20 | 1,00 | 0,80 / 0,76 |
| maillage `1e5e467e` | 0,40 | 0,80 (4 pertinents seulement) | 0,80 / 1,00 |
| maillage `00a2e6bb` | 0,60 | 1,00 | 0,80 / 0,78 |
| SAR `ee57ed9a` | 1,00 | 1,00 | 1,00 / **0,60** (NDCG < départ 0,70) |

Si Luna juge correctement les critères, la règle produit un top 5 pertinent partout où le corpus en contient un. Avec 25 % d'erreurs, la précision reste au niveau du départ, mais l'ordre interne peut se dégrader.

### Efficacité

- **Appels** : la règle n'ajoute aucun appel LLM. Un remplacement que Luna devait auparavant proposer dans une comparaison dédiée devient une opération locale, expliquée.
- **Taille des sorties** : les jugements par critère allongent chaque décision de sélection d'environ 100 tokens par candidat, et le besoin structuré allonge l'appel de compréhension d'environ 400 tokens. Aucune mesure réelle n'est disponible.
- **Coûts historiques** : le banc rapporte les coûts enregistrés des runs (par exemple 0,066 $ pour `53292dc4`). Ce ne sont pas des gains.

## Tests

`tests/ennoscholar_v4/test_discovery_bench.py` (18 tests) comprend :
- **Banc** : couverture des jugements, définitions des métriques, mesures sur runs réels, non-dégradation par la règle, garde sur le Recall@20 du triage, simulations exacte et bruitée.
- **Scénarios adverses** (domaine de la catalyse, pour éviter tout biais vers le maillage) :
  - un sosie qui échoue un critère essentiel ;
  - un article connexe riche en mots-clés ;
  - une stabilité sans agitation ;
  - un abstract incomplet ;
  - le consultant et des jugements périmés ;
  - des titres proches mais travaux distincts, et des versions groupées ;
  - une référence bibliographique qui reste une piste ;
  - une famille absente du premier tour ;
  - la validation et la persistance du besoin ;
  - le contexte transmis ;
  - le passage par l'outil réel, sans appel modèle.
