# EnnoScholar V4 — réparations fiables, rédaction automatique complète et coûts

Corrections du 2026-10-20, à partir du run `95f3507d6bdc4a6f9e6e12e399d392b4`
(« rédige toutes les sections restantes de l'état de l'art »).

## 1. Sauvegardes et réparations

**Défaut observé.** La section 3.1 citait `[ev_c9e88fa7c429da9f7]`, forme tronquée de la
preuve déclarée `ev_c9e88fa7c429da9f7f90cf10`. Le refus disait seulement « Référence ou preuve
non déclarée », le diagnostic local ne contrôlait pas les marqueurs, et la réparation ne
renvoyait au modèle que son dernier correctif (`text: null`). Le modèle a retiré trois fois
le « 3.1 » du titre ; le texte, inchangé, a été refusé trois fois.

**Corrections.**

| Fichier | Changement |
|---|---|
| `local_repair.py` | `marker_issues` : chaque marqueur `[paper_…]`/`[ev_…]` refusé, avec son paragraphe, son extrait, son type (`truncated_identifier`, `undeclared_evidence`, `unknown_identifier`, `unread_evidence`, `evidence_outside_cited_articles`, `evidence_integrity`) et la preuve qu'il désigne. Intégré à `diagnose`. |
| `local_repair.py` | `correct_marker_arguments` : complète un identifiant **seulement** s'il est un préfixe d'au moins 12 caractères d'**une seule** preuve admissible (déclarée, ou montrée pour une publication déclarée), ou déclare une preuve valide d'une publication déclarée citée sans déclaration. Une publication n'est jamais ajoutée à la bibliographie automatiquement. |
| `toolkit.py`, `agent.py` | La correction est appliquée avant la validation ; **tous** les contrôles (affirmations, attribution, valeurs chiffrées, bibliographie, liens, intégrité des preuves) s'exécutent ensuite sur le texte corrigé. Les corrections sont tracées (`marker_corrections`). |
| `publication.py` | Les refus nomment les marqueurs (`unknown_markers`) et la preuve en cause (`invalid_evidence_ids`, cause exacte). Aucun contrôle n'est assoupli. |
| `task_results.py` | Une réparation de section renvoie la **proposition fusionnée complète** (texte validé, publications et preuves déclarées, consigne), sans les champs dupliqués (`claims`, `argument_summary`, chiffres) qui restent dans la base de fusion. |

Sur la session réelle (copie en mémoire), la proposition refusée trois fois est désormais
enregistrée sans aucun appel au modèle.

## 2. Rédaction automatique complète (`writing_job.py`, `job_ledger.py`, `service.py`)

Une demande de rédaction de l'état de l'art complet (ou d'une plage de sections) ouvre une
**tâche de rédaction persistante**. Chaque segment est un run V4 ordinaire ; les sections
enregistrées ne sont jamais régénérées.

**Dépenses durables (audit Codex P1‑1, P1‑2, P1‑6).** Chaque appel fournisseur d'un segment,
le premier compris, est **réservé dans la ligne de son run avant d'être envoyé** (montant
protecteur), puis réglé (coût calculé), libéré (non traité, certain) ou marqué **incertain**
(peut avoir été traité : la réservation reste comptée). Un run interrompu avant `finish()`
garde ses écritures ; une réservation encore ouverte à la fin d'un run ou après expiration de
son bail devient incertaine. Le solde du manuscrit est reconstruit à partir de ces écritures
(confirmé / réservé / incertain), jamais à partir du résumé final du run. L'admission refuse
un appel si confirmé + réservé + incertain + la nouvelle réservation dépasse le budget, ou si
le segment ne détient plus la conversation (worker obsolète) : rien n'est envoyé.

**Atomicité (P1‑3).** Toute lecture‑écriture du JSON de session ou de run (points de
sauvegarde, fin de run, transitions de tâche, annulation, sélection, documents, écritures du
registre) se fait sous le verrou de ligne (`session.lock_rows` : verrou de ligne PostgreSQL,
verrou d'écriture SQLite), session puis run. Une évaluation de fin de segment n'a lieu qu'une
fois ; un état périmé ne peut plus être réécrit.

**Périmètre (P1‑4).** Une continuation reprend la progression ouverte par le premier segment,
**par son identifiant** : mêmes sections ciblées (par ex. 3 à 5), mêmes sections mises de côté,
même révision de plan. Un plan ou un périmètre modifié arrête la tâche avant tout appel.

**Demande et consignes (P1‑5).** Aucune continuation n'est enregistrée comme message du
consultant. Chaque segment répond à la demande d'origine (`instructions.current_request`) avec
le but déclaré au premier segment (aucun nouvel appel de compréhension) ; ses consignes
ponctuelles (« En anglais », « Ne modifie pas le plan ») restent en vigueur. Un message du
consultant pendant la tâche la **met en pause** ; elle ne reprend que sur demande
(`POST …/writing-job/resume`), avec le même périmètre et le même budget.

**Persistance (P1‑5, P1‑6).**
- `ENNOSCHOLAR_V4_WRITING_JOB_EXECUTOR=thread` (défaut) : le backend exécute les segments.
- `=celery` : avant toute ouverture de tâche, le backend envoie la tâche
  `ennosmart.scholar_v4.worker_check` au worker et compare sa configuration (modèle, version des
  prix, mode de cache, clé API et base de données, par empreintes à sens unique). Broker,
  worker ou configuration indisponibles : la demande est refusée (503) ; pendant la tâche :
  arrêt `executor_unavailable`. **Aucun repli vers les threads.**
- `=auto` : Celery si la vérification réussit, sinon le backend (choix enregistré dans la tâche).
- Chaque tâche enregistre l'empreinte de configuration de son ouverture : un processus
  différent (par exemple un worker qui lirait le `.env` du worktree) l'arrête en
  `configuration_mismatch` sans aucun appel.
- Reprise : au démarrage, puis toutes les 60 s par une surveillance du backend (sans
  navigateur) ; un pilote qui trouve un segment au bail encore valide programme une nouvelle
  tentative après son expiration (Celery : `retry`, sinon minuterie) au lieu de s'arrêter.

**Revue Codex n° 2.**
- Chaque segment transmet au modèle la demande d'origine **complète** (`context.model_context`),
  jamais la dernière réponse automatique ; les réponses partielles postérieures ne sont pas
  envoyées comme demande.
- Un premier run interrompu avant l'ouverture de la progression (processus perdu, erreur
  fournisseur) ne rend plus la tâche `not_applicable` : la même tâche, avec le même budget et
  ses dépenses, repart de la demande d'origine (`initializing`), dans la limite de 2 essais sans
  progression. Seul un run terminé où le modèle n'a pas ouvert de rédaction donne `not_applicable`.
- La surveillance démarre avec FastAPI (`routers/scholar_v4.py`, événement `startup`), pas au
  premier appel du service ; premier passage 10 s après le démarrage, puis toutes les 60 s.
- Elle redécouvre les tâches en cours ou en attente directement dans la base toutes les 5 min
  (toutes au démarrage) ; un droit de distribution par processus, écrit sous verrou et expirant
  après 120 s, évite que plusieurs processus relancent la même tâche.
- L'empreinte de configuration couvre aussi le modèle effectif de chaque étape
  (`task_models`) et le contenu du catalogue de prix (et les prix imposés par l'environnement).

**Arrêts explicites**, avec un message dans la conversation : terminé, annulé, budget atteint,
aucune nouvelle section sur 2 segments, 12 segments, section refusée 6 fois (mise de côté, les
autres continuent), plan ou périmètre modifié, exécuteur indisponible, configuration différente.

**API** : `GET /scholar-v4/sessions/{id}/writing-job`, `POST …/writing-job/cancel`,
`POST …/writing-job/resume`. L'interface suit les segments, affiche la dépense (confirmée,
incertaine), la pause et les boutons d'arrêt et de reprise.

Lancer le worker avec **les mêmes** `DATABASE_URL` (chemin SQLite absolu), clé et modèle que le
backend (sinon il est refusé) :

```
cd backend_api
celery -A worker.celery_app worker --loglevel=info --pool=solo
```

## 3. Coûts

- **Catalogue** (`pricing_catalog.json`, pages officielles relues le 2026-10-20) : GPT‑5.6 Sol
  conforme (4 / 0,40 / 5 / 20 $ par million : entrée, lecture cache, écriture cache, sortie).
  GPT‑6.1 Sol corrigé : **2 / 0,10 / 2,50 / 10** (le catalogue indiquait 12 / 0,60 / 15 / 60).
- **Modèle (2026-10-21)** : `gpt-6-luna` exclusivement, pour tous les appels V4. Les anciens modèles restent tarifés uniquement pour les reçus historiques. Les sélecteurs hérités et le routage par tâche sont neutralisés par `model_policy.py`.
- **Cache** (`prompt_cache.py`) : en mode implicite, GPT‑5.6 écrit tout le préfixe de chaque
  requête (×1,25 du prix d'entrée) ; les requêtes de section ne se répétant pas, le run
  95f3507d écrivait ≈ 16 000 tokens et n'en relisait aucun. En mode explicite (défaut des
  déploiements, `ENNOSCHOLAR_V4_PROMPT_CACHE_MODE=explicit|implicit|off`), seules les
  instructions et les outils sont éligibles à une écriture puis à une relecture (sans garantie de hit) ; le reste est facturé au prix
  d'entrée ordinaire. Rien n'est retiré de la requête ; la réservation est calculée sur la
  requête de l'agent. Estimation sur une requête de section réelle : −32 % du coût d'entrée.
- **Réparations** : la correction déterministe évite l'appel au modèle pour les identifiants
  résolus sans ambiguïté ; l'écho de réparation retire les champs dupliqués (jusqu'à ≈ 6 500
  caractères par réparation).

## Configuration du modèle

Depuis la migration du 2026-10-21, le modèle est centralisé dans `model_policy.py`.
Le `.env` du worktree, le profil local et le lanceur ont été alignés sur `gpt-6-luna`.
Le lanceur garde sa clé locale distincte, mais ne remplace plus les tarifs du catalogue.
Les autres agents ne sont pas modifiés. Les nouveaux processus backend et worker doivent
partager le même code, la même clé, la base et le catalogue ; un ancien job attend explicitement
sur `configuration_mismatch` plutôt que de mélanger ses modèles. Aucun historique n'est réécrit.
Aucun service n'a été redémarré pendant cette intervention.

## Points à vérifier lors d'un test réel

1. Le compte accepte `prompt_cache_options` / `prompt_cache_breakpoint` (sinon
   `ENNOSCHOLAR_V4_PROMPT_CACHE_MODE=implicit`) ; vérifier `cache_write_tokens` puis
   `cached_tokens` sur deux sections successives.
2. Mode d'exécution : en mode `thread`, une panne du backend est reprise à son redémarrage
   suivant (surveillance au démarrage puis toutes les 60 s).
3. Le budget manuscrit (10 $ par défaut) convient au nombre de sections.

## 4. Recherche scientifique autonome et persistante (`research.py`)

Cause du run 3D `94599ea8` : une seule vague de 4 recherches, une sélection, 268 s de lecture
sur 439 s, arrêt sur la limite de durée avec 10 publications retenues dont 5 analysées.

- **Tâche de recherche persistante** : une demande de recherche de publications ouvre la même
  tâche durable que la rédaction (`writing_job`, `kind = "research"`) : segments automatiques,
  registre financier par appel (confirmé / réservé / incertain), bail, reprise après panne,
  surveillance, annulation et reprise. Budget propre (`ENNOSCHOLAR_V4_RESEARCH_JOB_BUDGET_USD`,
  4 $ ; 6 segments). Une seule tâche par conversation : une nouvelle demande explicite de
  rédaction ou de recherche remplace une tâche en attente ou en pause (historique conservé) ;
  une autre question la met en pause.
- **Objectif** : un nombre demandé (« les 10 articles ») est atteint uniquement par des fiches
  vérifiées, analysées, avec leurs propres preuves exactes, sans recommandation en attente.
  Sans nombre, la revue de couverture courante du modèle (`review_research_coverage`) décide ;
  elle ne peut déclarer ni succès ni épuisement non établis.
- **Continuation** : un segment reprend la demande, le but et les décisions déjà prises ; il lit
  d'abord les publications retenues non analysées lorsqu'elles suffisent à l'objectif, sans
  nouvelle sélection ni nouvelle recherche ; aucune fiche existante n'est refaite.
- **Exploration adaptative** (tous domaines) : quand la couverture reste insuffisante, le modèle
  reçoit l'état de l'objectif et des options concrètes tirées des recherches enregistrées : pages
  suivantes possibles, sources indisponibles (la recherche web les complète), familles déjà
  interrogées, requêtes collectées. Les pages déjà collectées et les sources en attente après
  une erreur (429, `Retry-After`) ne sont jamais redemandées. Références et citations restent
  accessibles par `find_similar_articles`.
- **Abstracts complets** : en recherche, un candidat n'est rejeté qu'après transmission de son
  abstract complet au modèle ; sinon la décision reste « en attente ».
- **Versions ouvertes** : une prépublication n'est utilisée comme source alternative que si
  auteurs, titre et période correspondent et que son identité est vérifiée ; aucune fusion.
- **Texte intégral** : préchargement borné (3 au plus), temps partagé entre les URL candidates
  (25 s au plus par URL tant que d'autres restent, 5 s au moins pour une alternative), dans le
  délai propre à l'article ; une lecture interrompue est retentée deux fois au plus.
- **Temps** : estimation des lectures sur les durées mesurées (20 à 120 s) et réserve de
  publication propre à la recherche (75 s).
- **Contexte** : la progression de l'objectif et l'exploration adaptative sont conservées même
  quand le contexte doit être réduit.

## 5. Qualité des états de l'art (`editorial.py`)

- Chaque section reçoit une coordination bornée avec les autres sections (question, affirmations
  établies, valeurs, preuves déjà développées ailleurs), retirée en premier si la place manque.
- Le plan proposé est analysé (rôle des sections, recouvrements de questions et de sources) ;
  le modèle ne modifie jamais un plan approuvé : il propose ; la modification du consultant
  s'applique et retire l'approbation.
- La revue déterministe signale les preuves partagées et les résultats chiffrés répétés entre
  sections, sans rien réécrire ni bloquer l'approbation.
- « Brouillon complet 11/11 » est distingué de la couverture scientifique (publications du plan
  sans fiche, revue de couverture) et de l'état de la revue scientifique ; aucune approbation
  automatique.
