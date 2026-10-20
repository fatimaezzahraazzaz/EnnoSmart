# EnnoScholar V4 — coûts, budgets et optimisation agentique (audit 128698bb)

19 octobre 2026, branche `feature/ennoscholar-v4-diagnostic-link`, sans commit.

Conditions de travail :

- aucun appel payant ;
- base réelle lue en lecture seule, jamais migrée ;
- modèle principal et plafonds inchangés ;
- contrôles scientifiques et de provenance conservés.

Les montants ci-dessous sont des **recalculs ou simulations au barème du catalogue**, pas des factures.

## Phase 1 — comptabilité et budgets

### 1. Coûts par catégorie de tokens

Fichiers : `pricing.py`, `pricing_catalog.json`, `config.py`, `task_results.py`, `agent.py`.

**Catalogue tarifaire.** Il est versionné et remplaçable (`ENNOSCHOLAR_V4_PRICING_FILE`), par fournisseur, modèle et tier. Pour chaque modèle, il donne quatre prix : entrée ordinaire, cache lu, cache écrit, sortie. Il porte aussi le contexte long et le prix d'une recherche web.

**Une seule formule de coût,** utilisée partout : compteur du run, reçus de tâche, réservation et règlement du registre.

- L'entrée ordinaire vaut entrée totale − cache lu − cache écrit.
- Le raisonnement est déjà inclus dans la sortie : il n'est jamais ajouté une seconde fois.
- Si les compteurs sont incohérents, toute l'entrée est comptée au prix d'entrée le plus élevé, et le montant est marqué « non vérifié ».

**Usage ou tarif inconnu : aucun montant précis (`usd=None`).**

- La réservation reste comptée.
- Un modèle hors catalogue doit fournir ses quatre tarifs, sinon `validate()` refuse le démarrage.

**Réservation avant appel.** Aucun cache lu n'est supposé : toute l'entrée est réservée au prix d'entrée le plus élevé (cache écrit compris), avec toute la sortie autorisée.

**Incohérence de configuration corrigée sans changer de modèle :**

- les anciens défauts 2/10 sont remplacés par le catalogue (12/60 pour `gpt-6.1-sol`) ;
- les 4/20 du profil de lancement de `gpt-5.6-sol` correspondent au catalogue et sont vérifiés ;
- des tarifs d'environnement différents du catalogue ⇒ statut `conflict`, le plus élevé est retenu par catégorie.

**Chaque reçu enregistre :**

- les catégories de tokens ;
- la version tarifaire ;
- le modèle demandé, et le modèle réellement retourné (`response.model`) ;
- le `service_tier`, l'identifiant de réponse, l'empreinte de préfixe et l'état du registre ;
- l'ancien montant (`legacy_estimated_cost_usd`), pour l'audit.

**Rejeu des reçus de l'audit** (`tests/ennoscholar_v4/finance_replay.py`, jeu `fixtures/audit_128698bb_receipts.json`, compteurs seuls) :

| Run | Appels | Ancien compteur | Nouveau calcul | Montant de l'audit | Sous-estimation corrigée |
|---|---:|---:|---:|---:|---:|
| 128698bb (section 7) | 14 | 1,030556 $ | 1,161951 $ | 1,161951 $ | +0,131395 $ |
| 91590987 (13/14) | 21 | 1,959280 $ | 2,262225 $ | 2,262225 $ | +0,302945 $ |
| d46f6f20 (tentative) | 1 | 0,022744 $ | 0,027567 $ | 0,027567 $ | +0,004823 $ |

Les 36 montants sont reproduits appel par appel, à 10⁻⁶ $ près.

- **Nouvelles réservations :** elles couvrent le coût réel des 36 appels.
- **Anciennes réservations :** elles les couvraient aussi, grâce à la réserve de 7 000 tokens de sortie. Le défaut de borne reste démontré par un contre-exemple : 10 000 tokens écrits au cache et 1 000 en sortie donnent 0,06 $ selon l'ancien compteur, contre 0,07 $ réels.

**L'écart avec la baisse de solde de 1,52 $ n'est pas expliqué ici** : il faut l'export de facturation du fournisseur.

### 2. Contrôleur financier persistant

Fichiers : `finance.py`, `service.py`, `agent.py`, migration `backend_api/db/migrations/20261019_ennoscholar_v4_finance.sql`, route `GET /scholar-v4/spending`.

**Hiérarchie des budgets :** organisation → consultant → dossier → tâche → appel. Chaque niveau peut avoir un plafond total, journalier (UTC) et mensuel, ainsi que des dérogations par identité.

**Identités** (fixées par le service, jamais par le navigateur) :

- organisation : configuration serveur (`ENNOSCHOLAR_V4_ORGANIZATION_ID`) ;
- consultant : identifiant authentifié ;
- dossier : `project:<id>` du projet autorisé (`permitted_project`), sinon `conversation:<id>`.

**Tâche.** Sa clé dépend du dossier, du type et d'une cible stable :

- une lecture : œuvre canonique + hash du document lu + schéma de fiche ;
- une section : son titre normalisé ;
- une demande : son texte.

Une relance, un nouveau run ou une nouvelle conversation du même dossier retrouvent donc le **même** budget de tâche.

**Admission** (`MeteredClient` enveloppe le client OpenAI : tous les appels passent par lui) :

1. Estimation prudente de l'appel (`reserve_bound`).
2. Réservation **atomique** dans une transaction. Un incrément conditionnel par budget (`spent + reserved + x ≤ plafond`), évalué par la base sur la ligne verrouillée, dans un ordre de clés stable. Le premier budget insuffisant annule toute la transaction. Les montants sont des entiers en nano-dollars.
3. Refus ⇒ **aucune requête envoyée**. L'arrêt est expliqué : budget refusé, dépensé, réservé, demandé et plafond. Le refus est journalisé.
4. L'appel est exécuté hors de la transaction.
5. Règlement **unique** au coût réel (mise à jour conditionnelle du statut ; un doublon est sans effet), puis libération du reste.
6. **Libération ou maintien de la réservation :**
   - libérée seulement si la non-consommation est certaine : refus HTTP 4xx avant traitement (400, 401, 403, 404, 409, 413, 415, 422, 429) ;
   - conservée (`unknown`) dans les autres cas : interruption du flux, erreur de transport, usage absent ;
   - après un crash, `recover_stale` signale les réservations orphelines, et un administrateur les régularise avec `reconcile`.

**Arrêt propre.** Les brouillons, fiches et preuves déjà sauvegardés sont conservés. La réponse indique le budget en cause.

**Activation, désactivée par défaut.** Les tables ont leurs propres métadonnées, absentes de `create_all` : elles ne sont **jamais** créées au démarrage. L'administrateur doit :

1. Appliquer la migration SQL, ou `finance.migrate(engine)`, sur la base cible.
2. Valider une politique (exemple **non actif** : `budget_policy.example.json`, valeurs proposées par l'audit).
3. Positionner :
   - `ENNOSCHOLAR_V4_BUDGET_POLICY_FILE` ;
   - `ENNOSCHOLAR_V4_ORGANIZATION_ID` ;
   - `ENNOSCHOLAR_V4_FINANCE_ENABLED=true`.

Contrôleur activé sans tables ⇒ refus explicite au démarrage. Sans politique, la dépense est enregistrée mais pas limitée.

**Garantie réelle :** l'admission est contrôlée, et les engagements connus sont suivis de façon conservatrice. Ce n'est **pas** la facture du fournisseur au centime, car le tier, les frais annexes et les tarifs contractuels ne sont pas attestés localement.

## Phase 2 — consommations inutiles

| Correction | Mécanisme | Fichiers |
|---|---|---|
| 3. Lecture unique par document | `reading_identity` (œuvre canonique + hash du texte lu + version du schéma de fiche). Les notices jumelles lisent une seule fois, avec un « lecteur » par groupe ; les autres identifiants restent des alias tracés (`reused_from`, `reading_aliases`). Une section écrite depuis un alias sans fiche utilise la fiche du jumeau. Deux versions de texte, un document privé distinct ou un texte modifié ne sont jamais fusionnés. | `scientific_reading.py`, `writing_context.py` |
| 4. Paquet de section préparé | `retrieval_gaps` est calculé localement avant l'appel : affirmations non encore transmises (par champ), sources sans fiche, fiches sur résumé, dimensions jamais examinées. Le paquet garde ses preuves exactes, protocoles et résultats ; rien n'est retiré pour économiser. | `writing_context.py` |
| 5. Relectures sans apport | Les outils de section suivent les lacunes : `get_analysis_claims` n'est proposé que s'il reste des affirmations non transmises, `analyze_article` et `get_fulltext` seulement en cas de vraie lacune de lecture. Une page déjà transmise reçoit `no_new_evidence`, réponse locale. L'historique des affirmations transmises et des requêtes projet est durable entre runs. | `agent.py` |
| 6. Pas de tour général inutile | Quand les sections restantes attendent des lectures déjà lancées, la boucle prend leurs résultats au lieu de faire un tour général. `set_turn_goal` n'est plus proposé une fois l'objectif déclaré. Un nouveau message passe toujours par son propre appel de compréhension. | `agent.py` |
| 7. Protocole de comparaison | Les outils proposés correspondent exactement à la phase : en phase comparaison, `relations` est obligatoire (liste, éventuellement vide). Si aucune dimension (méthode, protocole, résultats, limites) n'est documentée avec preuves par au moins deux travaux, l'absence de relation est enregistrée localement, sans appel. Aucune comparaison n'est forcée. | `agent.py`, `argumentation.py` |
| 8. Réparation locale | Un refus est diagnostiqué paragraphe par paragraphe (`paragraph_diagnosis`). La réparation envoie `paragraph_patches` pour ces seuls paragraphes. Le reste est conservé à l'identique, avec ses affirmations, chiffres et preuves. La section fusionnée repasse **tous** les contrôles avant sauvegarde. Un texte complet reste possible pour un problème global. | `local_repair.py`, `toolkit.py`, `agent.py` |

**Précision de l'attribution** (correction 8), sans liste blanche. Un nom est accepté s'il figure :

- dans une preuve citée dans la phrase ;
- ou, pour une phrase sans citation, dans une preuve citée dans le même paragraphe ;
- ou s'il a déjà été introduit plus haut avec sa preuve.

Un fait du projet garde sa preuve `[projet:…]` : un terme du projet (« LiDAR », « SegArchA ») est accepté quand la phrase ou son paragraphe cite le passage du diagnostic qui le nomme, et refusé sinon. Une règle « vocabulaire du projet » a été essayée puis retirée : elle acceptait une affirmation sur le projet sans sa preuve, ce que les tests de provenance existants refusent à juste titre.

Sur le refus réel de la section 7 :

- « HiSup » et « Douglas-Peucker » sont acceptés quand leur passage est cité ;
- « Pays-Bas » et « LiDAR » restent refusés tant qu'aucun passage cité ne les nomme. Le refus est alors limité au paragraphe concerné et réparé localement.

**Défaut annexe corrigé.** Une sauvegarde de fiche ou de section déjà reçue est validée localement même si le délai expire entre la réception et la sauvegarde. Avant, ce cas laissait la réponse payée en simple proposition.

## Phase 3 — préparation, sans changement du modèle principal

**9. Cache fournisseur.** Chaque appel enregistre des empreintes du préfixe : instructions, outils, chaque élément d'entrée. Il compare aussi avec l'appel précédent de même `prompt_cache_key` : éléments partagés, premier élément modifié, octets communs. Ce sont des hashes et des tailles, sans texte client. La présence d'une clé ne garantit pas un cache hit. Il n'y a ni préchauffage ni changement de comportement.

**10. Modèle par tâche.** `routing.py` définit 8 étapes : compréhension, sélection, lecture, comparaison, plan, rédaction, réparation locale, revue.

- `ENNOSCHOLAR_V4_TASK_MODELS` est vide par défaut : `config.model` est utilisé partout.
- Toute configuration est validée (étapes connues, tarif attesté).
- Le modèle demandé est enregistré sur chaque reçu.

**11. Bibliothèque publique partagée.** `public_library.py` définit la clé (identité de lecture, schéma, DOI, arXiv) et l'éligibilité :

- éligibles : œuvre publique, texte public, hash exact ;
- exclus : documents déposés et sources privées.

Rien n'est stocké ni partagé : l'activation demande une validation des règles de confidentialité et de licence. Les diagnostics, documents clients, notes et confrontations restent isolés par session et par droits.

## Ce que montrent les rejeux

**Reprise de la section 7 sur l'état réel** (fiches LDPoly et HoliTracer retirées de la copie, modèle simulé) :

- 4 appels : compréhension, LDPoly lu **une seule fois**, HoliTracer, section sauvegardée ;
- aucun tour général ;
- les 13 autres sections sont inchangées.

Le run réel comptait 14 appels, mais le modèle simulé n'a aucun refus d'attribution. **Ce n'est donc pas une mesure d'économie.**

**Appels de l'audit dont la cause est supprimée** (borne haute dans ce scénario, au barème de référence) :

| Appel de l'audit | Coût | Cause supprimée | Statut |
|---|---:|---|---|
| 5 : relecture LDPoly sous alias | 0,080685 $ | lecture unique par document | démontré |
| 4 : tour général après compréhension | 0,132642 $ | attente des lectures en cours | démontré (rejeu) |
| 7 : comparaison demandée en mode lecture | 0,049281 $ | schéma limité à la phase | démontré |
| 8 : sauvegarde d'une comparaison vide | 0,009069 $ | constat local d'absence de dimension commune | conditionnel |
| 10–11 : relectures de fiches sans affirmation nouvelle | 0,205511 $ | outils guidés par `retrieval_gaps` | conditionnel |
| 14 : réparation par régénération complète | 0,135970 $ | correctif par paragraphe | coût de sortie réduit, non mesuré |

Les trois premières lignes totalisent 0,262608 $ (22,6 % de 1,161951 $). Ce **n'est pas** une réduction garantie : la qualité et le nombre de tours réels dépendent du modèle.

**Simulation à huit consultants** (test) : plafond de 0,50 $ par consultant et par jour, 3,00 $ pour l'organisation, appels de 0,10 $.

- 30 appels admis, exactement 3,00 $ dépensés, aucun dépassement à aucun niveau.
- Entre processus, 4 processus et 24 tentatives sur 2,00 $ donnent exactement 20 admis et 4 refusés.

## Protocole du prochain run réel

1. Garder le contrôleur désactivé (ou l'activer sur une **copie** migrée) et le même modèle.
2. Relever, dans `task_results` du run :
   - par appel : `cost` (catégories, montant, version tarifaire), `model_resolved`, `service_tier` ;
   - `cache_probe.compared_with_previous` ;
   - les événements `tools_narrowed`, `awaiting_readings`, `local_repair` et `not_comparable`.
3. Comparer au tableau de l'audit : nombre d'appels par tâche, coût par section acceptée, lectures en double (aucune attendue), tours généraux (aucun attendu en reprise), refus et coût de réparation.
4. Rapprocher les `response_id` avec l'export d'usage du fournisseur couvrant la même fenêtre, avant toute extrapolation pour huit consultants.

## Avant un déploiement à huit consultants

- Faire valider par l'administrateur le catalogue tarifaire (tier, date, contrat) et une politique budgétaire. L'exemple fourni n'est pas actif.
- Appliquer la migration sur la base de production, puis activer `ENNOSCHOLAR_V4_FINANCE_ENABLED`.
- Fixer `ENNOSCHOLAR_V4_ORGANIZATION_ID`.
- Utiliser une clé ou un projet fournisseur dédié à V4 : un autre agent hors registre peut consommer le même solde.
- Mettre en place une procédure de régularisation des appels `unknown` (`reconcile`) et de surveillance de `recover_stale`.
- Le pool du service reste de 4 runs par processus : c'est une limite de charge, pas une répartition équitable entre consultants.

## Tests

- Suite V4 + V3 + V3.1 : **567 réussis, 0 échec** (528 réussis et 1 échec au départ). Cet échec venait de la base réelle, qui contient désormais la section 7 ; le test reprend maintenant la copie sans ce brouillon.
- `test_finance.py` (20 tests) :
  - catégories de tokens, tarifs ou modèle inconnus, incohérence de configuration ;
  - reproduction des 36 reçus, borne de réservation ;
  - huit consultants, appels parallèles, plusieurs processus, dernier solde ;
  - reprise d'une tâche, nouvelle tentative après erreur, crash, usage inconnu, double réception, interruption ;
  - budget parent, arrêt propre de l'agent sans perte de brouillon.
- `test_audit_128698bb.py` (18 tests) :
  - LDPoly lu une seule fois, fenêtres HoliTracer conservées, versions et documents privés distincts ;
  - reprise réelle de la section 7 sans tour général ;
  - lacunes et outils, `no_new_evidence`, objectif conservé ;
  - schéma de comparaison et comparabilité locale ;
  - attribution et réparation par paragraphe ;
  - empreintes de cache, routage neutre, bibliothèque publique.
- **Tests existants adaptés,** parce qu'ils encodaient un comportement corrigé :
  - ancienne formule de coût ;
  - anciens défauts 2/10 ;
  - modèle réel déclaré dans les rejeux ;
  - relecture de fiche par `no_new_evidence` ;
  - retrait de `get_analysis_claims` quand tout a été transmis.
- **Intégrité :**
  - base `ennoscholar_v4_test.db` inchangée (même hash avant et après), aucune table financière créée ;
  - `sauv-sans-agent-1` = HEAD = `7cbcdd9` ;
  - rien n'est commité.
