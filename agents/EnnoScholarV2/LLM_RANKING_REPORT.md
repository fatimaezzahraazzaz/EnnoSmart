# Correction de la sélection scientifique — 10 octobre 2026

La qualification scientifique est désormais séparée de la récupération technique. La recherche fournisseurs, le corpus complet et les traitements des textes restent inchangés. Aucun titre, identifiant ou mot-clé du benchmark n'est utilisé comme règle de production.

## Cause vérifiée avant modification

Le diagnostic a porté sur les notices de la session réelle contenant 1413 articles, puis sur une copie locale de leurs métadonnées et abstracts. Le recalcul avec l'intention et les quinze requêtes retrouvées reproduisait exactement les 1413 scores stockés.

L'ancienne qualification imposait une conjonction lexicale : reconnaître les mots d'un objet et d'une opération dans une fenêtre de dix-huit tokens, puis reconnaître une expression de méthode. Les expressions composées de l'intention étaient trop strictes pour les formulations des notices. Les abstracts étaient bien utilisés ; la condition n'était pas logiquement impossible, mais aucun candidat réel ne satisfaisait la conjonction.

Comptages avant correction : méthode détectée dans 636 notices ; relation principale dans 2 titres et 4 abstracts ; relation secondaire dans 0 titre et 3 abstracts ; relation titre **et** méthode : 0 ; relation abstract **et** méthode : 0, avant même le veto des tâches adjacentes. Résultat : 1413/1413 CONNEXE. Le score lexical pouvait néanmoins placer l'éditorial au rang 5.

| Référence | Rang technique précédent | Présente dans la nouvelle shortlist |
|---|---:|---|
| 3DMeshNet | 926 | Oui |
| DL-Polycube, publication | 138 | Oui |
| DDPM-Polycube | 194 | Oui |
| TetGAN | 282 | Oui |
| MeshingNet | 225 | Oui |
| Survey AI / geometry preparation / mesh generation | 66 | Oui |

## Comportement implémenté

Après une recherche batch, une union diversifiée prélève jusqu'à douze candidats par couple requête/source, par parcours alterné des groupes. Le score de présélection est calculé relativement à chaque requête, et non au vieux classement global. Les meilleurs travaux déjà qualifiés sont conservés. Le plafond de cette shortlist est de 384 travaux ; il ne limite pas le registre backend. L'ordre original de retour des fournisseurs n'étant pas conservé dans l'ancienne session, cette présélection utilise les notices et leur provenance enregistrée.

Le LLM reçoit titre, année, sources, extrait d'abstract, citations, niveau d'accès et provenance. Les abstracts longs sont représentés par leur début et leur fin, jusqu'à environ 1000 caractères ; leurs versions complètes restent backend. Les fulltexts ne sont jamais envoyés à cette qualification.

Le jugement produit `relevance_label`, `relevance_score` sur 100 et une raison. `retrieval_score` reste distinct. DIRECT et FONDAMENTAL précèdent CONNEXE ; leur pertinence scientifique détermine ensuite l'ordre. L'accès fulltext ne donne aucun bonus scientifique. Le prompt distingue production, préparation/adaptation, utilisation d'un objet existant et reconstruction pour un autre usage, sans vocabulaire imposé par le benchmark.

Les réponses de qualification utilisent le format JSON Schema strict de Responses. Chaque identifiant attendu doit apparaître exactement une fois ; un lot invalide n'est pas enregistré. Voir la [documentation officielle des sorties structurées](https://developers.openai.com/api/docs/guides/structured-outputs).

La qualification utilise le même modèle configuré, `gpt-5.6-sol`, sans changement du fichier `.env`. Les lots sont isolés de l'historique principal : au plus 48 notices et un budget estimé de 16000 tokens de notices par lot, avec contrôle du budget total. Jusqu'à trois lots indépendants s'exécutent en parallèle, avec un délai maximal de trente secondes par appel, borné par les échéances existantes. Il n'y a pas de nouvel agent. Les résultats sont réutilisés pour une même demande ; la fenêtre principale contient au plus douze articles actifs.

Le nouvel outil `rank_scientific_articles` permet de requalifier le registre existant sans collecte fournisseur. Après chaque outil, la sélection garde les meilleurs candidats globaux qualifiés pour la demande courante. Si la qualification échoue, une sélection diversifiée explicitement non qualifiée est conservée avec un avertissement. Un résultat entièrement CONNEXE est signalé ; aucune étiquette DIRECT n'est inventée pour masquer cet état.

Publication et preprint forment une projection canonique lorsque leurs identifiants ou titres/auteurs/années permettent le rapprochement. Les sources, URLs et identifiants associés sont exposés ensemble. Aucune notice n'est supprimée : le registre paginé reste complet et les fulltexts restent attachés aux identifiants originaux.

## Validation et limites

La reproduction hors réseau utilise la copie réelle des **1413 notices**, conservées intégralement : **340 travaux shortlistés**, les six références incluses, **8 lots**, puis **12 actifs**. DL-Polycube est représenté comme un travail avec ses deux identifiants et six URLs. Les requêtes isolées mesurent entre **1787 et 15924 tokens estimés**, et entre **6064 et 72556 octets** selon le lot. Les jugements et rangs finaux de cette reproduction sont **simulés** : ils valident le circuit de sélection, pas la pertinence d'un jugement OpenAI réel.

Résultat : **115 tests V2 réussis**, avec un avertissement de dépréciation Starlette/AnyIO ; compilation de **35 fichiers Python** réussie ; `git diff --check` sans erreur. Les tests couvrent corpus mixte, bruit éditorial, candidat sous le rang technique 1000, publication/preprint, accès payant, conservation des textes, échecs de qualification et requalification sans recherche. Les régressions de compaction, contexte borné, batch `get_articles`, limites d'outils, preuves et réponse partielle HTTP 200 restent couvertes.

Les **138 empreintes SHA-256 protégées sont inchangées**, notamment V1, EnnoDiagnostic, recherche fournisseurs, gestion du contexte, fulltext et analyse. Aucun frontend n'a été modifié pour cette correction ; aucun build frontend supplémentaire n'a été lancé. Aucun appel OpenAI réel, aucune nouvelle recherche scientifique et aucun déploiement n'ont été effectués.

La correction est sur disque. Aucun redémarrage, arrêt ou rechargement du backend n'a été demandé par ces commandes. Lors du contrôle final, le processus backend précédemment observé, PID 28192, avait disparu et aucun service n'écoutait sur le port 8000 ; le frontend PID 9900 écoutait encore sur 3000. La session RAM ne peut donc plus être confirmée active. La copie de diagnostic conserve métadonnées et abstracts, **pas les fulltexts**. Aucune restauration de session ni relance de service n'a été effectuée.

## Fichiers et sauvegardes

Sauvegarde initiale avant édition : `D:\EnnoSmart\.ennosmart_patch_backups\scholar_v2_llm_ranking_20261010_211403_058`. Les éditions suivantes ont également leurs copies horodatées dans ses sous-dossiers `revision_*`.

Fichiers existants modifiés pour cette correction :

- `agents/EnnoScholarV2/models.py`
- `agents/EnnoScholarV2/agent.py`
- `agents/EnnoScholarV2/service.py`
- `agents/EnnoScholarV2/prompts.py`
- `agents/EnnoScholarV2/tools/ranking.py`
- `tests/ennoscholar_v2/scientific_fixture.py`

Nouveaux fichiers :

- `agents/EnnoScholarV2/tools/scientific_selection.py`
- `tests/ennoscholar_v2/test_scientific_selection.py`
- `tests/ennoscholar_v2/report_llm_ranking.py`
- `agents/EnnoScholarV2/LLM_RANKING_REPORT.md`

Artefacts locaux de diagnostic, ignorés par Git : `.codex_tmp/scholar-v2-real-ranking-snapshot.json` et `.codex_tmp/scholar-v2-llm-ranking-reproduction.json`. Les autres modifications visibles dans le statut Git précèdent cette correction ; elles n'ont pas été réinitialisées.
