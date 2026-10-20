# EnnoScholar V4 — conversations liées à EnnoDiagnostic

Implémentation du 17 octobre 2026, dans `C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart`.

## Sauvegarde préalable

- Branche de sauvegarde : `sauv-sans-agent-1`.
- Commit : `7cbcdd9eed0015a3410d8add932d42da29f8a413`.
- Push GitHub réussi. Le SHA de la branche locale, celui de `origin/sauv-sans-agent-1` après fetch explicite et celui renvoyé par `git ls-remote` étaient identiques.
- Le snapshot contient les corrections scientifiques, de tokens et de révision ciblée reçues, avec leur code, leurs tests et leur documentation. Validation avant intégration : **379 tests réussis**.
- Aucun `.env`, secret, base, document client ou stockage runtime privé n’a été ajouté. `.claude/launch.json`, préexistant et local, reste non suivi.
- Développement sur `feature/ennoscholar-v4-diagnostic-link`. Aucun commit de développement : HEAD reste le commit de sauvegarde.

## Parcours consultant

Une nouvelle conversation ouvre « Comment souhaitez-vous commencer ? ». Le consultant choisit Express, qui garde le fonctionnement existant, ou un projet diagnostiqué. Les conversations anciennes sans champ `mode` restent Express ; un ancien `project_id` ne suffit jamais à les convertir.

Le parcours lié présente un seul diagnostic exploitable par projet autorisé, le plus récent, avec organisme, nom, sous-projet éventuel, année CIR, date et statut. Il s’appuie sur les résultats déjà persistés. Il ne lance pas EnnoDiagnostic.

Les décisions `garde` sont lues depuis les lignes métier `Verrou`, sans les remplacer par la décision automatique figurant éventuellement dans un snapshot ancien. Le consultant sélectionne un, plusieurs ou tous les verrous. S’il n’existe aucun « Garder », une sélection explicite de verrous non validés est possible et signalée comme telle. Sans diagnostic exploitable, Express reste proposé.

La confirmation crée et prépare la conversation. **Aucune recherche ni aucun appel LLM ne démarre à la création.** L’accueil affiche les formulations réelles. Le bouton de préparation remplit le message ; son envoi déclenche le fonctionnement scientifique habituel.

Le bandeau affiche le projet et les verrous actifs. « Contexte et preuves » donne accès aux faits, formulations, passages sources, contexte général et changements de périmètre/source. Les détails de provenance sont dépliables. Une option permet de consulter les éléments hors périmètre sans les activer. « Changer le périmètre » active un sous-ensemble de la sélection ; la sélection initiale peut également être revue.

## Adaptateur et persistance

`agents/EnnoScholarV4/diagnostic_link.py` lit uniquement `DiagnosticRun.raw_result_json`, les lignes `Verrou` du diagnostic et les métadonnées du projet. Il réutilise `get_project_for_user` pour les droits du propriétaire, les accès acceptés et les rôles administrateurs. Il n’importe pas le moteur EnnoDiagnostic et ne lit aucun chemin de fichier fourni par le diagnostic.

L’adaptateur reconnaît le snapshot compact courant, les enveloppes V2 `report.diagnostic_v2` / `diagnostic_v2` et leurs `core`, ainsi que les sections canoniques persistées lorsqu’elles sont disponibles. Il conserve les entités, extractions, points de synthèse, méthodes, démarches, paramètres, métriques, résultats, difficultés, sources des verrous et références aux preuves. Il garde les citations, pages, documents, identifiants de passages et indicateurs de vérification disponibles, sans les recréer.

Le JSON existant de `ScholarV4Session.data` reçoit :

```text
mode: express | diagnostic_linked
diagnostic_link:
  snapshot: projet, diagnostic_id, source_version, date/statut,
            locks, synthesis, facts, families, evidence,
            context_sections, rnd_approach, temporal_context
  selected_lock_ids: sélection confirmée
  active_lock_ids: périmètre courant
  revision: compteur pour les modifications concurrentes
  history: changements de périmètre, sélection et source
  source_history: snapshots remplacés explicitement
  scope_work: cadrages et axes de recherche précédents par périmètre
```

`source_version` est un SHA-256 du contenu normalisé, incluant les décisions humaines et les métadonnées de la source. Le client fournit cette version lors de la confirmation : un changement entre aperçu et création/actualisation produit un conflit, sans créer de conversation liée à une sélection périmée.

Aucune table ni migration supplémentaire. Les recherches, articles, analyses, preuves scientifiques, décisions et versions documentaires utilisent les stockages V4 existants. Les mutations de périmètre sont refusées pendant un run et utilisent un contrôle de révision et une mise à jour conditionnelle de la session.

## Périmètre actif et argumentation

L’instruction exacte « Je veux commencer seulement par le premier » active le premier verrou de la sélection affichée, dans l’ordre stable du diagnostic. Le parseur est volontairement limité à des commandes ordinales explicites : une référence au premier article ne change pas le verrou. L’interface permet de choisir directement un verrou si la formulation n’est pas reconnue.

Les associations au verrou reposent sur des relations explicites enregistrées. Une famille, un passage ou une preuve partagés ne suffisent pas à attribuer une méthode ou un résultat au verrou. Les relations explicites `related_lock_ids` priment sur une famille ou une preuve partagée. Un fait sans association prouvée est marqué `project_context_not_linked_to_lock`, et reste un contexte général à interpréter.

Le changement de périmètre conserve les recherches antérieures et leurs sources. Les anciens axes ne sont pas réutilisés automatiquement pour un nouveau verrou ; ils sont mémorisés et restaurables lors du retour au périmètre précédent. Les dates du cadrage déjà renseignées sont conservées lors de l’ouverture d’un nouveau périmètre. Le blueprint courant est marqué à revoir ; il n’est pas effacé, et les brouillons ne sont pas modifiés par ce changement.

Le modèle reçoit les objectifs généraux et un paquet borné des seuls verrous actifs, avec les faits techniques associés ou explicitement généraux. Il doit exploiter les méthodes, conditions, paramètres, résultats et difficultés dans ses requêtes et dans l’évaluation des publications. Les comparaisons et blueprints existants peuvent citer des faits projet du diagnostic, avec une provenance distincte des preuves publiées.

Seuls les passages explicitement classés projet, dont la citation est marquée vérifiée par le diagnostic et qui ne sont pas signalés à revoir, peuvent satisfaire l’exigence de fait projet du blueprint. Les autres éléments restent consultables. Un résultat prévu ou à temporalité incertaine reste une incertitude ; sa provenance et sa temporalité ne sont pas transformées en expérience réalisée. Les citations du diagnostic ne sont jamais ajoutées au registre `evidence` des publications.

Les consignes du mode lié imposent la séparation des résultats projet/littérature, le respect de `source_roles` et `temporal_status`, la comparaison des conditions de validité, la distinction entre publications antérieures et postérieures au démarrage R&D et la validation humaine. L’année CIR ne devient pas automatiquement une date de démarrage. Une recherche vide ne constitue pas une preuve de nouveauté ou d’éligibilité.

## Actualisation et permissions

Une lecture du contexte peut signaler que le diagnostic a changé. Le snapshot de la conversation reste stable jusqu’à une actualisation confirmée, après présentation de la nouvelle version et de ses verrous. L’ancien snapshot et la transition restent enregistrés.

Les sessions restent privées à leur propriétaire. En plus de cette règle, chaque accès à une conversation liée revérifie l’autorisation sur le projet ; une révocation masque aussi sa ligne dans l’historique. Les réponses de runs et leurs annulations passent par cette vérification. Le worker contrôle également l’accès aux frontières d’annulation habituelles. Une révocation ne supprime aucun enregistrement.

Les nouvelles routes appartiennent exclusivement à `/scholar-v4` : liste et aperçu `/diagnostic-projects`, puis lecture et mise à jour `/sessions/{id}/project-context`. Aucun routeur, service ou modèle EnnoDiagnostic n’est modifié.

## Tokens et coûts

Le snapshot complet reste dans la mémoire persistante. Le paquet actif est limité à **4 200 caractères**, ou moins selon le budget du tour ; son contenu est retiré par enregistrements entiers avec indicateurs `partial`, compteurs et outil de récupération. Les citations ne sont pas raccourcies dans le stockage.

`get_project_context(kind, scope, offset, limit, ids)` récupère localement les enregistrements et preuves exacts par pages. `scope=active` vise les relations explicites au verrou ; `scope=general` les informations projet sans attribution ; `scope=all` donne accès au contexte conservé hors périmètre sans modifier la sélection. Cet outil n’existe pas dans les définitions transmises à Express. Dans les phases scientifiques ciblées du mode lié, sa lecture est possible sans valider ni sauter la comparaison ou le blueprint en cours. Le dernier tour réservé à la sauvegarde d’une révision ciblée conserve sa priorité.

Mesure finale hors ligne avec `tiktoken` / `o200k_base`, sur la fixture de test : Express **180 tokens**, contexte lié **827**, contexte lié avec 1 000 faits longs supplémentaires **828** ; outil supplémentaire **260**, consignes supplémentaires **178**. Le snapshot représentait **636 699 tokens**. Les empreintes et dates de la fixture peuvent faire varier ces comptes de quelques tokens.

Ces valeurs représentent un cas synthétique et une estimation de la sérialisation locale, pas la facturation d’un fournisseur. Le surcoût est d’environ **1 100 tokens par appel ordinaire** pour ce cas, sans croissance proportionnelle à la taille du diagnostic. Une récupération ciblée ajoute le contenu demandé et consomme les budgets habituels. Les tests imposent une marge sur le contexte, l’outil et les consignes. Aucun plafond de tours, tokens, contexte ou coût, aucun modèle et aucun tarif n’a été augmenté ou changé.

## Vérifications

- Non-régression finale V4, V3 et V3.1 : **406 tests réussis en 78,29 s**, dont **27 nouveaux tests** dans `tests/ennoscholar_v4/test_diagnostic_link.py`.
- Couverture : Express neuf/ancien, création liée et confirmation, utilisateurs autorisés et administrateur, accès partagé accepté/révoqué, absence de fuite entre clients, dernier diagnostic exploitable sans doublons, décisions Garder réelles, absence de Garder, verrou unique et ordre ordinal, conservation des faits/valeurs/unités/provenances, réouverture, changement/restauration des axes et dates, changement du diagnostic, actualisation explicite, concurrence/run actif, contexte borné et pagination, rôles/temporalité des sources, refus des citations non vérifiées et éléments à revoir, récupération hors périmètre, recherche ciblée simulée et rédaction complète avec récupération pendant le blueprint.
- Révision ciblée liée : une nouvelle version de la section 5 est enregistrée en **un tour simulé**, les six autres sections et toutes les versions précédentes restent identiques.
- Recherche liée : **un appel d’outil de recherche simulé**, puis publication, en **deux tours** ; le modèle factice vérifie la présence des caractéristiques du premier verrou et l’absence des faits propres au second.
- Frontend : `npm run typecheck` et `npm run build` réussissent.
- Navigateur sur serveur local séparé, base temporaire et agent factice : liste du seul projet autorisé, sélection multiple, confirmation, accueil réel, instruction ordinale, changement vers le second verrou avec message conservé, consultation des faits/preuves et création Express indépendante. Les serveurs temporaires ont été arrêtés.
- Les tests bloquent les sockets externes et les appels OpenAI. Aucun test scientifique réel ou payant. Les avertissements de dépréciation existants ne sont pas traités par cette intégration.

Commande de non-régression :

```powershell
D:\EnnoSmart\.venv\Scripts\python.exe -m pytest tests/ennoscholar_v4 tests/ennoscholar_v3 tests/ennoscholar_v31 --import-mode=importlib -q --tb=short --disable-warnings
```

## Limites avant essai réel

La qualité scientifique de la formulation des requêtes, du jugement de pertinence et des comparaisons reste celle du modèle et doit être vérifiée par le consultant. Les clients simulés démontrent le transport du contexte, le contrôle des étapes et les sauvegardes, pas la pertinence d’une recherche scientifique réelle.

Les diagnostics dépourvus de données structurées et de verrous exploitables ne sont pas reconstruits depuis un PDF ou un artefact gzip ; Express est proposé. Les références documentaires sont conservées telles que persistées : cette intégration ne crée pas un visualiseur des documents EnnoDiagnostic ni des liens de téléchargement non autorisés. Les faits dont le lien à un verrou n’a pas été enregistré ne peuvent pas recevoir une association scientifique inventée ; le contexte les signale comme généraux.

Un corpus déjà collecté et les anciennes rédactions restent disponibles après changement de verrou ; le consultant et le modèle doivent réévaluer leur pertinence pour le nouveau périmètre. Aucun transfert ne vaut approbation scientifique, humaine ou CIR. Les budgets finis peuvent toujours arrêter une recherche ou une récupération très longue ; cette intégration ne prétend pas garantir l’exhaustivité.

Avant l’essai réel : redémarrer le backend et le frontend habituels pour charger le code, puis tester un projet autorisé comprenant plusieurs verrous et des preuves structurées. Aucune conversation, fiche, preuve, section ou version de la base existante n’a été modifiée par les vérifications.

## Fichiers du développement

| Périmètre | Fichiers |
|---|---|
| Adaptation, schémas et persistance V4 | `agents/EnnoScholarV4/diagnostic_link.py` (nouveau), `models.py`, `service.py` |
| Contexte, outils et argumentation | `agents/EnnoScholarV4/agent.py`, `context.py`, `toolkit.py`, `argumentation.py`, `writing_context.py` |
| API | `backend_api/routers/scholar_v4.py` |
| Interface et client API | `frontend/components/ennosmart/ennoscholar-v4-chat.tsx`, `scholar-v4-start-dialog.tsx` (nouveau), `scholar-v4-project-context.tsx` (nouveau), `frontend/lib/scholar-v4.ts` |
| Tests et documentation | `tests/ennoscholar_v4/test_diagnostic_link.py` (nouveau), `docs/ennoscholar-v4-diagnostic-link.md` (nouveau), `docs/ennoscholar-v4-etapes-1-8-bilan.md` |

Les scripts de banc d’essai restent dans `.codex_tmp`, ignoré par Git. Les fichiers générés par Next ne font pas partie de la modification. Aucun fichier d’un autre agent ni aucune configuration de modèle ou de tarifs n’est modifié.


## Corrections après audit sur données réelles

La validation complémentaire sur les diagnostics 17 et 48 a corrigé la normalisation des preuves/cartes anciennes, l’attribution par preuve partagée et la suppression des faits projet dans la comparaison bornée. La non-régression passe à **424 tests réussis**, dont 18 nouveaux cas ; les données réelles ont été copiées uniquement dans des bases temporaires. Les identités, sources et décisions humaines de la base active restent intactes.

Le [rapport des corrections et des deux contextes anonymisés](ennoscholar-v4-diagnostic-link-corrections.md) précise les données effectivement transférées, les observations générales non attribuées, les statuts de vérification et les informations absentes. Les mesures synthétiques ci-dessus concernent la première intégration et ne constituent pas une mesure de facturation réelle.
