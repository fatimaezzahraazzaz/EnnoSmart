# EnnoScholar V5 — chaîne scientifique adaptative

Travail du 28 octobre 2026, exclusivement dans le worktree expérimental
`C:\Users\hp\.codex\worktrees\ennoscholar-v5-langgraph\EnnoSmart`, branche
`codex/ennoscholar-v5-langgraph`.

## Périmètre

Sol (`gpt-6.1-sol`, Responses) conserve les décisions scientifiques. Les nœuds
LangGraph existants exécutent ses outils, réconcilient l'état et contrôlent le
budget. Aucun nouvel agent, superviseur LLM, pipeline de rédaction ou ordonnanceur
de lecture exhaustive n'est introduit. La comparaison est proposée lorsque la
demande ou le plan la justifie ; elle ne produit pas toutes les paires du corpus.

Les fonctions V4 sont réutilisées dans l'espace de noms V5 par `reuse.py` et par
le chemin de modules V5 existant. Aucun fichier V4 n'est modifié par ce travail.
Les modifications V4 déjà présentes dans le worktree avant cette intervention
ne sont ni annulées ni attribuées à cette évolution.

## Fichiers et fonctions

| Fichier | Points d'intégration |
| --- | --- |
| `agents/EnnoScholarV5/science.py` (nouveau) | `passage_states`, `reading_status`, `save_review`, `passage_index`, `validate_measurements`, `comparison_work`, `check_comparative_measurements` |
| `agents/EnnoScholarV5/toolkit.py` | Extension des arguments des outils existants ; `ScholarTools.execute` pour index/revue, mesures, récupération et comparaison |
| `agents/EnnoScholarV5/workflow.py` | `acknowledge_inputs`, `compact_known_cards`, `remember_deferred`, `record_delivery`, `section_packet`, `section_science`, `workflow_view` |
| `agents/EnnoScholarV5/writing_context.py` (nouveau) | Surcharge V5 de `section_dossier` / `rank_claims` ; classement lexical des affirmations selon le rôle de leur publication |
| `agents/EnnoScholarV5/runtime.py` | `ScientificAdapter.instructions`, `prepare`, `delivered`, `execute` ; raccordement des informations aux véritables entrées Responses |
| `experiments/ennoscholar_v5/progress.py` | `examined_passages`, `reading_map`, `project`, `model_work_view` |
| `experiments/ennoscholar_v5/policy.py` | `semantic_key` et réutilisation des opérations selon l'état scientifique courant |
| `experiments/ennoscholar_v5/graph.py` | Place réellement disponible pour la vue de travail ; dépendance des revues aux résultats de lecture |
| `experiments/ennoscholar_v5/tests/test_scientific_chain.py` (nouveau) | Scénarios de couverture, mesures, comparaison, dossiers et chaîne complète avec reprise |

Les tests de régression dont les assertions décrivaient l'ancien suivi sont
adaptés dans le répertoire expérimental. Les plafonds, le backend, le frontend,
les exports et les extracteurs multimodaux restent ceux déjà présents.

## Causes corrigées et comportement

### Compréhension et couverture

La présentation d'une preuve constitue un reçu de transmission, pas une analyse.
`examined_passages` ne déduit plus une lecture scientifique du seul registre
`shown_evidence_ids`, ni d'une correspondance approximative sur un préfixe de texte.

Le registre distingue les passages disponibles, présentés, examinés et associés
à une analyse sauvegardée. Dans le chemin actuel, l'analyse devient observable
au moment de sa sauvegarde : les compteurs « analyzed » et « saved » proviennent
donc du même événement vérifiable. Aucune pensée interne de Sol n'est assimilée
à une analyse persistante. Une revue peut examiner une preuve sans produire de
nouvelle affirmation.

Les dimensions reprennent le registre V4 : problème, contribution, méthode,
mécanisme, protocole, paramètres, résultats, métriques, conditions, limites et
comparaisons. « Documenté » signifie présence d'affirmations avec preuves ; cela
ne signifie pas que tous les aspects de la dimension sont suffisamment couverts.

Sol peut enregistrer `analyze_article(action="review", coverage_review=...)`.
Cette revue est liée à la demande courante, à l'article, à l'empreinte de la source
et à celle de sa fiche. Une modification invalide sa suffisance. Les dimensions
essentielles non résolues empêchent une revue déclarée suffisante. Une absence
observée dans quelques passages n'est pas convertie en absence dans tout l'article.
Sol reste responsable de la pertinence des dimensions choisies pour la question.

La vue compacte conserve tous les articles ciblés et tous leurs groupes de
sections. Si leurs libellés occupent trop de place, des indices locaux les
remplacent explicitement ; les noms complets et passages restent accessibles par
l'outil `index`. Aucun numéro de page n'est inventé pour JATS. Les articles hors
périmètre peuvent être omis de cette vue avec un compteur explicite. Si même la
représentation minimale des cibles ne tient pas, une erreur locale explicite
préserve l'état plutôt que d'envoyer silencieusement une seule cible à Sol.

### Lecture ciblée et répétitions

`analyze_article(action="index", section_filter=..., index_offset=...)` expose
les passages non examinés, leurs identifiants, sections, présence de chiffres et
courts aperçus. Ce n'est pas un reçu de lecture. Les passages reportés sont
prioritaires dans l'index, mais Sol choisit lesquels lire. Les index décrivent
l'état courant ; après une lecture ou une sauvegarde, leur pagination doit être
recalculée à partir de cet état.

Les passages reportés faute de contexte conservent leurs identifiants, requête et
curseur dans le registre de lecture existant. Ils ne deviennent présentés que
si leurs preuves complètes figurent effectivement dans une entrée Responses
confirmée. Les fiches déjà transmises sont remplacées dans la mémoire automatique
par des références courtes ; `get_analysis_claims` permet de redemander les
affirmations détaillées. Les lectures ne transportent plus une deuxième copie
de la fiche complète. La finalisation conserve l'accès aux données nécessaires.
Une répétition identique d'une lecture déjà livrée renvoie ses identifiants,
sans recopier le texte ; une relecture explicite reste possible. Le compteur de
stagnation distingue la préparation d'un nouveau résultat de sa répétition :
l'accusé de réception tardif du résultat précédent ne valide pas cette répétition.

### Fiches et mesures

`ArticleAnalyzer.save()` et sa fusion additive sont conservés. Les nouveaux
résultats structurés sont ajoutés sous `analysis.structured_measurements`, indexés
par leur contenu : valeur littérale, unité, conditions expérimentales, méthode de
mesure, origine et identifiants de preuves. Les anciens `Claim` restent inchangés
et ne sont pas convertis automatiquement en mesures.

Une valeur absente des preuves ou une unité sans support littéral est refusée
avant la modification de la fiche. Les cellules de tableaux utilisent le
validateur existant. Ce contrôle garantit la provenance et la présence littérale,
pas l'implication scientifique : le choix de la métrique et l'interprétation de
ses conditions restent le travail de Sol, explicitement indiqué dans les données.
Les mesures sont récupérables par `get_analysis_claims` avec pagination et un
indicateur de correspondance à la source actuellement active. Les dossiers
n'utilisent pas une mesure rattachée uniquement à une ancienne source.

### Comparaisons

`compare_articles`, ses types de relations et ses validateurs V4 sont conservés.
L'agenda V5 signale les groupes utiles du plan ou de la demande, leurs fiches
disponibles et leurs relations encore valides. Il n'impose pas de comparaison
lors d'une simple lecture d'article.

Les relations conservent travaux, preuves, conditions, résultats expérimentaux,
origine et incertitudes. La déduplication existante des relations est complétée
pour éviter d'ajouter un enregistrement historique de comparaison identique.
Une évolution des sources ou fiches invalide la relation pour les dossiers futurs.

Pour les mesures structurées citées de chaque travail, une supériorité numérique
est refusée si métrique, unité, conditions et méthode ne correspondent pas. Ce
contrôle est conservateur : des formulations différentes de protocoles équivalents
peuvent nécessiter une clarification. Il ne prouve pas à lui seul la comparabilité
physique ; les contrôles V4 et le jugement de Sol restent nécessaires.

### Rédaction

`begin_writing`, `next_section`, le plan, `write_section`, les validations de
citations et l'assemblage du manuscrit sont conservés. Les dossiers conservent
des affirmations entières avec leurs passages exacts, en alternant publications
et champs. Le rôle d'une publication influence l'ordre des affirmations à
présenter, sans modifier le plan ni introduire de score scientifique automatique.

`section_packet` ajoute les mesures détaillées et les relations actuellement
valides, y compris leurs résultats expérimentaux et preuves complètes, ainsi que
les lacunes de couverture. Les éléments trop volumineux sont comptés comme
différés avec les outils permettant de les récupérer. Sol peut approfondir un
point essentiel avant de rédiger. Les instructions demandent une argumentation
continue et conditionnelle plutôt qu'une juxtaposition de résumés.

### Correctif du 28 octobre (run réel `f147eded`)

Le run réel a relu les fiches pendant cinq décisions (229 affirmations renvoyées)
sans lecture nouvelle ni sauvegarde, sans que la stagnation se déclenche. Causes :
la clé d'une affirmation dépendait de sa position dans la page (un sous-ensemble de
champs la renumérotait), le crédit « contexte préparé » récompensait toute réponse
contenant des affirmations, et le point de départ d'une décision précédait les
reçus de la décision antérieure. Désormais `workflow.novel_delivery` ne crédite
qu'un élément jamais livré pendant la demande (identité par contenu), et la
signature de départ est prise après ces reçus.

`workflow.working_memory` garde entre les décisions les affirmations déjà
consultées (énoncé abrégé, preuves), les passages reportés et les lectures faites ;
elle prend sa place dans le budget de la mémoire existante (au plus 12 000
caractères, moitié de la mémoire) et n'est pas un reçu de lecture. Rejeu du run :
7 requêtes au lieu de 9, arrêt par stagnation après trois relectures sans apport.

## Persistance et budgets

Aucune migration SQL : les ajouts utilisent le JSON de session V5 déjà persisté,
les fiches V5 et `scientific_relations`. Les revues et lectures différées sont
dans `scientific_reading.tasks`; les reçus de présentation utilisent la mémoire
de demande V5 existante. `ApplicationRepository` conserve état et reçu d'opération
dans la même transaction ; LangGraph référence cette révision. Les tests utilisent
des bases SQLite isolées. Ils ne constituent pas un nouveau test d'exploitation
PostgreSQL.

Le budget global de l'exécution n'est pas changé ni remis à zéro à la reprise.
La vue de travail peut occuper jusqu'à 10 000 caractères au lieu de 5 000, mais
uniquement dans l'espace restant sous les 85 000 caractères déjà configurés.
Le dossier enrichi remplace une part de son enveloppe existante de 40 000
caractères ; il ne s'y ajoute pas. Aucun appel LLM de supervision n'est ajouté.

## Validation

Suite complète hors ligne du 28 octobre 2026 (`experiments/ennoscholar_v5/run_offline.py`) :
**147 réussis, 0 échec, 0 ignoré** (`.artifacts/claude-final2.xml`). Le premier passage
avait révélé 2 échecs (`test_adaptive.py`) : le crédit « contexte préparé » comptait
comme progrès une recherche ne renvoyant que des travaux déjà connus. Correction dans
`graph.py` : les outils de découverte (`search_scientific`, `search_collected_articles`,
`find_similar_articles`) ne progressent que si l'état scientifique change ; le crédit
reste accordé aux lectures, index, fiches et mesures.

Les archives CO₂ de la session `3445749b` et du run `2f26ee71` fournissent des textes
et états réels copiés en lecture seule dans les fixtures. La source n'est jamais
utilisée comme base de test modifiable. Le transport Responses est
`httpx.MockTransport`, avec réponses et décisions scriptées. Les outils, graphes,
transactions, validations et reprises sont exécutés réellement en local.

## Correction du run fd41608c : comparaison enregistrée et mémoire des valeurs

Constat (trois articles CO₂, 28/10) : quatre appels `compare_articles` en lecture
seule, sans relation enregistrée ; chaque reformulation de `research_context`
rendait la même matrice d'environ 33 000 caractères « nouvelle » ; la stabilité de
plus de 14 h à 400 mA cm⁻² de YSZ/CuO, lue dans un passage et présente dans la
fiche, n'a jamais été transmise comme affirmation.

- Nouveauté d'une comparaison : identifiée par les affirmations transmises
  (reçus de contenu) et les relations, plus par le texte de la requête. Une
  fenêtre de matrice déjà reçue dans la demande (mêmes travaux, mêmes curseurs,
  mêmes fiches et relations) reçoit un court avis `matrix_already_delivered`
  avec `next_claim_offsets`, sans recrédit de progrès.
- Lecture et enregistrement distingués : une lecture renvoie `save_with` (schéma
  de relation fondé sur des preuves de chaque publication). Une comparaison
  demandée n'est publiée qu'après une relation enregistrée par les validateurs V4
  (preuves, chiffres présents dans les citations, supériorité seulement à
  protocoles comparables) ou un bilan explicite `relations=[]` avec `assessment`
  (`v5_comparison_decisions`). Pendant une synthèse forcée, `compare_articles` est
  proposé avec `finalize_response`.
- Mémoire de travail : une affirmation de fiche fondée sur un passage lu dans la
  demande est considérée comme consultée (le 14 h et sa limite « ne démontre pas
  une durabilité équivalente à 800–1000 mA cm⁻² ») ; les phrases chiffrées des
  passages lus sans fiche, les relations enregistrées et le curseur de matrice y
  figurent, dans le même budget.
- Une publication refusée faute de relations n'est plus bloquée comme « même
  échec » après l'enregistrement des relations.

## Préparation scientifique avant chaque section (demande directe de rédaction)

Une demande « recherche, propose un plan et rédige un état de l'art complet » ouvre
la rédaction en attente de plan (V4 `begin_writing`). V5 relie désormais :

- plan requis : sans plan enregistré, `finalize_response` est refusé
  (`plan_not_saved`) ; le plan reste soumis à la validation du consultant, rappelée
  dans le manuscrit publié ;
- `section_preparation` (dossier de section et `source_readiness` du plan) : pour
  chaque source, fiche prête (méthodes, résultats, limites documentés par preuves
  exactes, ou revue suffisante de la demande), à analyser depuis le texte local, à
  compléter depuis le texte intégral, fondée sur le seul résumé, ou inaccessible ;
  plus la comparaison des travaux analysés de la section ;
- avant `write_section(save)` : un refus unique par section et par demande
  (`section_preparation_required`) si des actions restent possibles. Sol les
  réalise, ou confirme son texte avec `text=null` sans le renvoyer ; les lacunes
  restantes sont publiées avec la section (« Informations non documentées »,
  limites documentaires) ;
- reprise : « Continue la rédaction » rattache la progression sauvegardée à la
  nouvelle demande ; les fiches, comparaisons et sections déjà enregistrées sont
  réutilisées. Un grand corpus se traite ainsi en plusieurs demandes, chacune dans
  son budget, sans relire ce qui est prêt.

Tests : `test_comparison_memory.py` (rejeu réel fd41608c) et
`test_direct_state_of_art.py` (session sans fiches, demande directe, reprise sous
budget d'outils réduit). Ils ne mesurent pas les choix du Sol réel.

## Correction du run b748605d : source sans fiche et réserve de sauvegarde

Constat (lithium plating, 28/10) : section 1 préparée (deux fiches sur texte
intégral, une relation), puis `write_section` déclarait et citait `[paper_386c…]`,
revue inaccessible sans fiche ; V4 a refusé toute la section. L'appel suivant
demandait 65 322 tokens pour 61 481 restants : `admission_refused`, 0/5 sections.

- Avant V4, V5 sépare les sources sans fiche : citées `[paper_ID]`, refus
  `section_source_not_analysed` qui nomme la source et l'action possible (retirer
  le marqueur si inaccessible, ou analyser la source) ; seulement déclarées, elles
  sont retirées d'`article_ids` (`not_declared_as_analysed`) et restent au plan
  comme limite documentaire. Aucune citation n'est retirée, aucune preuve ajoutée ;
  les validateurs V4 s'appliquent ensuite inchangés.
- Une source dont les téléchargements enregistrés ont échoué est inaccessible pour
  la préparation (nouvel essai seulement avec `candidate_urls`).
- Pendant la rédaction, `RunMeter.admit` garde un appel de la taille de l'appel
  courant (au moins la réserve de synthèse existante) pour enregistrer la section.
  Atteinte, cette réserve transforme l'appel suivant en `write_section` imposé, sans
  nouvelle lecture ni refus de préparation, puis arrête la demande
  (`section_save_reserve`). Le plafond global est inchangé.
- Un texte refusé est conservé comme brouillon provisoire (`v5_pending_sections`,
  même révision du plan), présenté dans le dossier à la reprise et repris par
  `write_section(save, text=null)` ; il n'est jamais publié comme section validée.

Tests : `test_section_save_reserve.py` (rejeu réel b748605d).

## Correction du run 7de2fab6 : taille du contexte mesurée telle qu'envoyée

Constat (lithium plating, reprise de la rédaction, 28/10) : `ValueError('Local
context budget…')` à la décision 76, avant tout appel payant. `Runner.decide`
réservait la place de la vue de travail d'après la taille brute du message de
contrôle, alors que ce message est transmis comme chaîne JSON dans les entrées :
l'échappement ajoutait 1 418 caractères, soit 85 102 pour une limite de 85 000. Une
reprise recalculait le même contexte.

`Runner.assemble` mesure désormais le contrôle tel qu'il est envoyé et, si le
contexte dépasse encore, réduit la seule vue de travail du dépassement ; mémoire,
échange et dossier de section restent intacts. Le refus local demeure si même une
vue minimale ne tient pas, sans omettre aucun travail ciblé.

Tests : `test_context_assembly.py` (état réel persisté et valeurs du checkpoint).

## Correction du run 152fc86a : une section prête doit être rédigée

Constat (lithium plating, plan validé, 0/5 sections, 28/10) : section 1 prête
(deux fiches sur texte intégral, une relation, préparation `ready`), mais Sol n'a
jamais appelé `write_section(save)`. En cinq décisions, trois `write_section(read)`
ont renvoyé les mêmes 16 073 caractères à côté du dossier injecté de 40 000, avec
une consigne V4 « Lis les analyses et leurs passages » ; l'échange (20 488
caractères) était plein, les fiches demandées dans la même réponse étaient
reportées puis redemandées. Le message de contrôle proposait « lecture, analyse,
recherche… ou finalisation » et la vue de travail s'ouvrait sur les analyses
manquantes des quatre sections suivantes. La stagnation, juste dans son constat
(relectures sans apport), arrêtait la demande sans tentative de rédaction.

- `write_section(read)` de la section dont le dossier est déjà fourni renvoie un
  avis court (`dossier_already_in_context`) avec l'état de préparation ; le dossier
  reste l'unique copie.
- `v5_workflow.current_section` ouvre la vue : section, préparation, et action
  suivante (« rédiger et enregistrer » si prête, sinon les actions de préparation) ;
  les comparaisons des sections suivantes attendent leur tour. Pendant la
  rédaction, le message de contrôle nomme cette tâche.
- Une stagnation sur une section prête conduit à son enregistrement par un appel
  `write_section` imposé (comme une stagnation hors rédaction conduit à la
  synthèse) ; une fois la section enregistrée, la rédaction continue. Les
  validateurs V5/V4 restent appliqués ; un refus reste visible et le texte est
  conservé comme brouillon provisoire.

Tests : `test_ready_section_drafting.py` (décisions réelles de 152fc86a, texte
réel de Sol du run b748605d).

## Qualité scientifique des sections (run bcf1d7ad)

« Rédigeable » et « scientifiquement documentée » sont distincts. `section_preparation`
garde `ready` (plus aucune action possible maintenant) et ajoute `basis`
(`fulltext`, `mixed`, `abstract_only`, `none`) et `scientific_state` (`prepared`
seulement si chaque source du plan est analysée sur son texte intégral avec méthodes,
résultats et limites ; sinon `limited`). Une fiche V4 enrichie depuis un PDF reste
« abstract » tant qu'elle garde une preuve du résumé ; V5 la compte comme texte
intégral quand méthodes, résultats et limites sont chacun appuyés par le texte intégral.

- Libellés fondés sur ce qui est su : « texte intégral analysé », « analyse fondée sur
  le résumé ; texte intégral non obtenu », « texte intégral disponible, non analysé »,
  « texte intégral non obtenu ; aucun résultat attribué », « notice seule ».
- Manuscrit : un seul titre par section ; pour chaque section, faits documentés par le
  texte intégral, analyses fondées sur le résumé (non vérifiées), limites documentaires,
  informations non documentées, interprétations (relations comparatives). Une section
  `limited` est marquée « Section provisoire » ; un brouillon n'est complet
  (`coverage_sufficient`) que si toutes ses sections sont `prepared`. Recalculé à
  l'affichage : les textes enregistrés ne sont jamais modifiés ; l'historique de qualité
  de chaque version est conservé (`v5_section_quality`).
- Refus de préparation : uniquement pour une action réellement possible ; jusqu'à deux
  pour un texte local ou une comparaison, un seul pour un téléchargement ; aucun en
  sauvegarde imposée par le budget, la section restant provisoire. Un refus compte
  comme progression du workflow (obligation enregistrée), jamais comme preuve.
- Un PDF ajouté après une fiche fondée sur le résumé rend l'approfondissement de nouveau
  possible ; la section enrichie est une nouvelle version, l'ancienne conservée.

Tests : `test_section_quality.py`.

## Mission de rédaction automatique

Une seule demande (« Rédige l'état de l'art complet… ») ouvre une mission : la tâche
d'écriture automatique de V4 (`writing_job.py`, `job_ledger.py`, pilote thread ou
Celery, reprise par le chien de garde), activée pour V5 et complétée par
`agents/EnnoScholarV5/writing_job.py`. Chaque exécution LangGraph est une
sous-exécution avec son budget propre (400 000 tokens au plus) ; la mission reprend
seule les sections restantes, sans nouveau message du consultant.

- Plafonds : budget en dollars de la mission (autorisé par
  `ENNOSCHOLAR_V5_MISSION_BUDGET_USD`, chaque appel admis contre ce qui reste), tokens de la mission (`ENNOSCHOLAR_V5_MISSION_MAX_TOKENS`,
  2 000 000 par défaut), durée (`ENNOSCHOLAR_V5_MISSION_MAX_SECONDS`, 5 400 s),
  nombre de sous-exécutions (`writing_job_max_segments`). Une sous-exécution ne démarre
  pas sous 140 000 tokens restants (un appel et sa réserve de sauvegarde). Au plafond :
  arrêt, travail conservé, « autorisation supplémentaire nécessaire ».
- Pas de boucle : une sous-exécution sans section ni fiche, preuve ou relation nouvelle
  est sans progrès ; deux de suite arrêtent la mission.
- Plan : aucune section n'est enregistrée avant la validation humaine de la révision
  courante du plan (`plan_not_approved`, y compris pour une sauvegarde imposée par le
  budget ou une stagnation). En attendant, Sol prépare les sources et les comparaisons,
  puis termine par un point sur la préparation. La mission attend « en attente de
  validation » et l'approbation la relance automatiquement.
- Budget autorisé : la mission ne s'ouvre que si `ENNOSCHOLAR_V5_MISSION_BUDGET_USD`
  fixe explicitement un plafond positif ; le défaut de V4 (10 $) n'est jamais retenu.
  Sans autorisation, la demande s'exécute en une seule sous-exécution et la réponse le
  signale ; une valeur nulle, négative ou non numérique est refusée à la configuration.
- Persistance : la mission vit dans la session (identifiant, statut, sous-exécutions,
  dépenses, tokens, erreurs) ; après un redémarrage, le chien de garde lancé avec le
  routeur V5 reprend les missions en attente, sans dupliquer appels ni sections.
- Une sous-exécution ne relit pas les réponses des précédentes (le manuscrit partiel est
  dans les sections et le dossier) ; la réserve de sauvegarde n'impose l'enregistrement
  que d'une section rédigeable, sinon la sous-exécution suivante poursuit la préparation.
- Interface : « Rédaction de l'état de l'art — 2/5 sections terminées. Préparation
  scientifique de la section 3. » ; états en cours, en attente de validation, en pause,
  terminé, terminé avec limites documentaires, suspendu par budget, erreur, annulé.

Tests : `test_drafting_mission.py` (session lithium réelle, plusieurs sous-exécutions,
plafonds, redémarrage, validation du plan).

## Bloc Planification : audit du 2026-10-29 et corrections

Audit hors ligne de 287 scénarios (copies de la session lithium). Corrections dans la
surcouche V5 uniquement (`writing_progress.py`, `runtime.py`, `workflow.py`,
`toolkit.py`, `service.py`) ; V4 inchangé. Tests : `test_planning_block.py`,
`test_plan_edit_scope.py`.

- Modification du plan ou du contenu : une demande sur le plan (titre, intitulé,
  ordre, ajout, suppression, sources d'une section, « dans le plan ») sans demande de
  texte n'ouvre aucune rédaction ; « ne modifie pas le plan », « sans modifier le plan »
  protègent le plan. « Réécris (le contenu de) la section N » est une rédaction ciblée.
- Section nommée : seule elle change, avec les actions demandées (renommer/mettre à
  jour toujours ; déplacer, sources, suppression, ajout, fusion selon la demande).
- Sections citées : motif non gourmand (« Révise la section 2, pas la section 5 » vise
  la 2) ; une liste explicite (« la section 3 puis la section 4 ») révise exactement ces
  sections ; une section inconnue n'ouvre rien.
- Plan approuvé : une demande explicite du consultant est appliquée comme son édition
  (version précédente conservée, approbation retirée) ; la réponse le dit toujours et
  demande la revalidation. Sans demande, Sol ne modifie jamais un plan approuvé.
- Suppressions demandées : la règle V4 de conservation du corpus n'empêche plus de
  supprimer une section, retirer un article ou réduire à N parties.
- Sections rédigées : jamais supprimées sans que le consultant les nomme, jamais
  fusionnées dans une autre (V4 remplacerait le texte de la cible).
- Demande imprécise : après un refus « plan non enregistré », Sol peut publier une
  demande de précision ; la réponse indique « Plan inchangé ». Un nouveau plan doit
  toujours être enregistré.
- Reprise : après une modification du plan, « Continue la rédaction » reprend le plan
  actuel (sections enregistrées conservées) ; une mission en attente de validation
  reprend la même demande sur le plan modifié puis validé.

- « Modifie le plan : supprime la section 4 » : V4 lit « modifie » comme une révision de
  la section 4 et n'autorisait que sa mise à jour ; sa suppression explicitement demandée
  est maintenant appliquée. La section nommée est fixée au début de la demande : après
  la suppression, « section 4 » ne désigne jamais une autre section.
- Demande combinée (« Ajoute une section … puis rédige l'état de l'art complet ») : le
  plan est modifié d'abord (validation retirée ou demandée), rien n'est rédigé et aucune
  mission n'est créée ; l'intention de rédaction est conservée (`v5_pending_draft`).
  Après la validation du consultant, « Continue la rédaction » reprend exactement cette
  rédaction (« rédige-la » vise la section ajoutée ou nommée). Avant la validation, rien
  ne démarre et la réponse le rappelle. Une nouvelle demande de rédaction la remplace.

Limite connue : une réponse identique répétée après un refus est bloquée une fois par
la politique de rejeu commune (un appel de plus, dans la limite des tentatives).

## Limites et protocole de validation réelle, à lancer par le consultant

Les tests scriptés ne démontrent ni la qualité de la prose de Sol réel, ni sa
capacité à choisir spontanément les meilleures dimensions et lectures. Aucun
nouveau traitement d'image, figure, graphique ou PDF payant n'est implémenté.

Après redémarrage manuel de l'application et sélection explicite de V5 :

1. Sur une conversation V5, demander un approfondissement borné des articles CO₂
   déjà conservés : méthodes, résultats chiffrés, conditions et limites. Vérifier
   les neuf cibles, les lectures nouvelles et les revues partielles/suffisantes.
2. Demander la confrontation de deux ou trois travaux choisis, en exigeant les
   conditions et incertitudes. Vérifier les relations persistées et l'absence de
   classement numérique entre protocoles non comparables.
3. Avec un plan explicitement validé, demander une seule section, puis examiner
   les citations, les mesures, les limites documentaires et la continuité de
   l'argumentation. Une reprise doit conserver le plan et les sections sauvegardées.
4. Consulter le journal et le budget du même run : vérifier qu'une reprise ne
   recrée ni les lectures déjà livrées, ni les fiches, ni les sections sauvegardées.

Ce protocole est une proposition manuelle. Aucun serveur ni run scientifique
réel n'est démarré par cette intervention.
