# EnnoScholar V4 — réparation du parcours découverte → sélection → plan

Corrections du 17 octobre 2026 (P0-1, P0-2, P0-3, P0-4, P1-6 de l'audit), sur `feature/ennoscholar-v4-diagnostic-link`, sans commit.

Contexte :

- Aucun plafond n'est relevé : 80 nouveaux candidats par message, 48 outils, 600 s, 400 000 tokens, 4 $.
- Aucun appel OpenAI et aucune modification de la base SQLite.

## Problèmes réels traités

**Run `d8606428`** (session `ed686234`, dossier CIR LiDAR à quatre verrous) :

- Les 7 premières recherches ont pris les 80 nouveaux candidats ; 8 recherches suivantes, ciblées sur les verrous, ont été refusées.
- Les 6 recherches lancées au même tour sont arrivées au tour suivant avec **une seule notice chacune**. La compaction de l'échange réduisait chaque fenêtre à 4, puis 2, puis 1 notice.
- *Road Boundary Extraction from LiDAR Point Clouds Using Dynamic Programming Algorithm* (4ᵉ de sa recherche) n'a donc jamais été présenté.
- Un identifiant inconnu a fait échouer **tout** le lot de décisions, après en avoir appliqué une partie. Aucune lecture n'a suivi.
- L'étape plan a été forcée sans sélection. Le modèle l'a détournée vers `get_project_context` puis `manage_outline(read)`.
- Le contrôle de tokens comptait la taille de la requête en octets, d'où un faux arrêt `exploration_token_budget`.

**Run `6f5f57f4`** (maillage 3D) : DL-Polycube et DDPM-Polycube étaient collectés, mais seuls leurs titres étaient présentés.

Correction de l'audit : le document précédent plaçait l'article de programmation dynamique en 13ᵉ position. Les données réelles le placent 4ᵉ de sa recherche. La perte venait de la compaction des recherches parallèles, pas du nombre de résumés par fenêtre.

## Corrections

### P0-1 — Décisions de sélection robustes

Fichier : `toolkit.py`, `record_candidate_decisions`.

- **Validation préalable.** Tout le lot est validé avant la moindre écriture.
- **Éléments ignorés et signalés** dans `ignored`, avec leur motif :
  - les identifiants inconnus ;
  - les notices jamais présentées ;
  - les verrous hors du périmètre actif (la décision est gardée, le verrou est retiré) ;
  - les finalistes inconnus, retirés par le consultant, non examinés ou rejetés.
- **Décisions valides enregistrées**, suivies de `recorded_decisions`.
- **Lot sans aucune décision applicable** : il est refusé sans aucune modification de l'état.

### P0-2 — Estimation des tokens

Fichier : `agent.py`, `request_tokens` et `BYTES_PER_TOKEN = 3.0`.

- **Calcul.** La réserve de tokens et la réserve de coût d'un appel utilisent `octets / 3`, au lieu des octets comptés comme des tokens.
- **Mesure de référence.** Sur les runs réels, on observe 3,85 à 4,1 caractères par token.
- **Marge conservée.** L'estimation reste environ 30 % au-dessus de la réalité ; les limites restent donc protectrices.
- **Cas réel.** Le cas de `d8606428` (232 066 tokens utilisés, requête de 131 700 octets) ne provoque plus d'arrêt. L'ancien calcul l'arrêtait.

### P0-3 — Sélection avant le plan, plan non détournable

Fichier : `agent.py`, `finalize`, `balanced`, choix des outils.

- **Sélection préalable.** Pour une demande de plan, si de nouvelles références ont été cherchées sans aucune décision, un point de sélection précède le plan.
  - Il porte sur les candidats de ce message, alternés par verrou.
  - L'outil `record_candidate_decisions` est forcé.
- **Plan forcé.** L'étape plan force `manage_outline` : outil nommé, sans `get_project_context` ni `tool_choice="required"`.
- **Budget d'outils.** La sélection et la sauvegarde du plan peuvent utiliser la réserve d'outils de publication, puisque le plan remplace la publication. Le plafond global est inchangé.
- **Conditions de déclenchement.** La sélection préalable n'est lancée que s'il reste au moins 30 s avant la réserve de publication et deux outils.

### P0-4 — Plafond de 80 réparti, non relevé

Fichiers : `toolkit.py` (`pending_targets` et `search`) et `agent.py`.

- **Part de chaque requête.** Tant que des verrous actifs (mode lié) ou des axes déclarés (Express) n'ont reçu aucune recherche dans ce message, une requête reçoit au plus `reste // (cibles en attente + 1)`, avec un minimum de 8.
- **Exemple.** Quatre verrous reçoivent 20, 20, 20 puis 10 nouveaux candidats, au lieu de 40, 20, 10 et 5.
- **Requête sans verrou.** Elle réserve la place de tous les verrous non cherchés.
- **Axe couvert.** Un axe compte comme cherché seulement si au moins la moitié de ses concepts figure dans une requête.
- **Ordre de stockage.** Quand la part d'une recherche est inférieure à ses résultats, les notices correspondant aux concepts distinctifs de la requête sont enregistrées en premier ; l'ordre des fournisseurs est conservé entre résultats équivalents. La pagination des résultats non enregistrés reprend au premier rang manquant.
- **Recherches refusées.** Celles refusées par la limite n'entraînent aucun appel fournisseur et ne consomment plus le budget d'outils.
- **Aucun quota** sur ce que le modèle retient.

### P1-6 — Présentation des candidats

Fichiers : `store.py` (`lexical_order`, `window`) et `context.py` (`compact_exchange`).

- **Fenêtre de recherche.** Elle présente **tous les résultats**, jusqu'à 24, avec un résumé de 420 caractères marqué `abstract_truncated`, dans une limite inchangée de 20 000 caractères. Auparavant : 8 résumés de 1 600 caractères et des titres seuls pour le reste.
- **Ordre.** Les concepts distinctifs de la requête ordonnent la fenêtre. Ce n'est pas un classement scientifique.
- **Notice complète.** `get_article` rend toujours le résumé complet, dans l'ordre demandé. La recherche locale garde son ordre calculé sur tout le corpus.
- **Compaction.** Elle raccourcit d'abord les résumés (420, puis 220, puis 120 caractères) et ne retire des notices qu'ensuite. Les passages lus et les listes de titres gardent leurs plafonds.

Mesure sur les 13 recherches réelles de `ed686234` :

| Indicateur | Avant | Après |
|---|---|---|
| Notices avec résumé présentées | 92 | 173 |
| Taille cumulée des fenêtres | 209 381 caractères | 192 724 caractères (−8 %) |

## Tests

**Nouveau fichier `tests/ennoscholar_v4/test_selection_pipeline.py`** (10 tests hors ligne) :

- un identifiant inconnu, une notice non présentée ou un finaliste rejeté sont ignorés et signalés, sans application partielle ;
- un lot sans décision applicable ne modifie rien ;
- un verrou inconnu est retiré, la décision est conservée ;
- LiDAR : l'article de programmation dynamique est stocké et présenté en tête avec son résumé ; les parts sont 20, 20, 20 puis 10 ;
- une requête sans verrou garde la place des quatre verrous ;
- maillage : les axes déclarés se partagent la limite (26 au lieu de 40), DL-Polycube et DDPM-Polycube sont présentés en tête avec leur résumé ;
- les recherches refusées ne consomment pas le budget d'outils ;
- **parcours LiDAR complet** :
  1. la demande de plan est suivie de recherches sur les quatre verrous ;
  2. le budget de tours est épuisé sans décision ;
  3. un point de sélection est lancé, alterné par verrou, et contient l'article de programmation dynamique ;
  4. un lot de décisions avec un identifiant erroné est enregistré pour sa partie valide ;
  5. le plan est forcé et le verrou 4 cite l'article ;
- tokens : le faux arrêt de `d8606428` disparaît et l'estimation reste au-dessus du réel mesuré ;
- recherches parallèles : six fenêtres gardent au moins 10 notices chacune, et l'article de programmation dynamique est présenté.

**Tests existants adaptés à l'effet voulu** :

- `test_candidate_visibility.py` : les Polycube sont désormais présentés en tête au lieu d'être indexés par titre ;
- `test_finalization.py` : fenêtre de 24 notices et fenêtres locales de 12 ;
- `test_remaining_defects.py` : les rangs 27, 35, 44 et 65 sont stockés dès la première recherche ;
- `test_turn_goal.py` : outil de plan nommé, et borne de contexte de la session réelle, qui a grandi depuis le run `d8606428`.

**Non-régression V4, V3 et V3.1 : 443 tests réussis.**

## Limites restantes

- **Pas d'essai réel.** La qualité des requêtes et des jugements reste celle du modèle.
- **Résumés abrégés.** À 420 caractères, une décision serrée demande `get_article`.
- **Plafond de 80.** Il est inchangé : dans un message très large (plus de 6 cibles), chaque cible reçoit moins de candidats.
- **Doublons de versions** (DOI arXiv contre DOI éditeur) : ils ne sont pas encore fusionnés (P1-8).
- **Références et citations** (`find_similar_articles`) : toujours sans déclencheur (prochaine étape).
- **Cache des prompts** (P1-5) : non traité.
- **Profondeur de lecture** (6 passages par fenêtre, environ 145 s par sauvegarde) : inchangée.
- **Journalisation des arguments d'outils** : absente, ce qui limite toujours l'audit des traces.
