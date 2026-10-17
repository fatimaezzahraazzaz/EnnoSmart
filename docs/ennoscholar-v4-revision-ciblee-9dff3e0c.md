# Diagnostic de la révision ciblée — run 9dff3e0c

Vérification hors ligne du 17 octobre 2026, sans appel API ni recherche réseau. Worktree : `C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart`.

## Traces et périmètre

- Run : `9dff3e0ce918436b973c37c4060ad5a2` ; session : `4a7314e41b904abb93026346d4068cb1`.
- Demande : réviser uniquement la section 5, vérifier méthodes, auteurs et résultats avec les quatre publications retenues, conserver les autres sections.
- Source : `ennoscholar_v4_test.db`, tables `scholar_v4_runs` et `scholar_v4_sessions`, ouvertes en SQLite `mode=ro` et `query_only`. Les relectures et rejeux utilisent une copie JSON en mémoire ; aucune correction du document réel n'a été enregistrée.
- Les empreintes des neuf modules attestés au démarrage du backend correspondent aux fichiers présents au début de cette revue. Il ne s'agit donc pas d'un diagnostic fondé sur une autre copie du code.
- Les sept sections ont chacune un brouillon avant et après l'exécution échouée ; la section 5 conserve le brouillon `f42e3744d1574ac28b4504fa87d30630`. Huit versions documentaires sont conservées.

## Consommation réelle des tours

Tous les appels `write_section` ci-dessous ont `action=read`. Aucun `save` n'a été demandé.

| Tour LLM | Caractères d'entrée | Tokens d'entrée | Tokens de sortie | `write_section(read)` | `get_analysis_claims` |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 17 616 | 8 868 | 351 | 1 | 4 |
| 2 | 77 660 | 25 929 | 251 | 0 | 4 |
| 3 | 74 009 | 23 904 | 250 | 0 | 4 |
| 4 | 73 430 | 24 058 | 326 | 1 | 4 |
| 5 | 73 864 | 24 354 | 369 | 1 | 4 |
| 6 | 70 189 | 23 952 | 254 | 0 | 4 |
| 7 | 73 731 | 23 778 | 359 | 1 | 3 |
| 8 | 81 761 | 26 198 | 283 | 0 | 4 |
| 9 | 69 827 | 22 704 | 219 | 1 | 0 |
| 10 | 42 741 | 15 061 | 299 | 0 | 4 |
| 11 | 73 961 | 24 299 | 274 | 0 | 4 |
| 12 | 73 172 | 23 687 | 230 | 0 | 4 |
| 13 | 65 522 | 21 964 | 213 | 0 | 4 |
| Total | 867 483 | 288 756 | 3 678 | 5 | 47 |

Répartition des récupérations de fiches : DL-Polycube 11 ; NeurFrame 12 ; publication de Pelissier et al. 12 ; publication de Qian et al. 12. Les 52 outils ont terminé sans erreur. Aucun `analyze_article`, `get_fulltext`, `compare_articles`, `build_argument_blueprint`, recherche ou validation bibliographique distante n'a été appelé. Il n'y a eu aucune tentative de validation/sauvegarde du nouveau texte, donc aucun refus d'attribution ou numérique ayant empêché une sauvegarde. La liste `writing_progress.errors` est vide.

L'arrêt est exactement `exploration_round_budget` : six tours de base plus l'allocation existante de sept tours pour une section (`1 × 3 + 4`). Les 52 outils restent sous la limite effective existante de 53 pour cette demande (`48 + 1 × 3 + 2`). Les plafonds de tokens, coût, durée et contexte n'ont pas déclenché cet arrêt.

**Limite des traces historiques :** les événements de ce run enregistrent l'article récupéré, mais pas `fields`, `offset`, `next_offset` ou le contenu des appels LLM. On peut établir les répétitions par article et par tour ; on ne peut pas affirmer que les 47 appels portent sur des pages strictement identiques, ni restituer les arguments exacts de chacun. Les nouveaux événements conservent désormais ces champs et le nombre d'affirmations retournées.

## Causes constatées

1. **Le texte à réviser n'était pas fourni intégralement.** `section_projection` transmet un aperçu du texte rendu. `writing_context` peut ensuite remplacer toute cette projection par l'identifiant, le titre et les articles quand les fiches prennent la place. La reconstruction du premier paquet réel ne contient plus aucun brouillon, alors que le texte brut de la section 5 compte 5 892 caractères. `write_section(read)` ne retournait pas non plus un champ indépendant contenant ce texte brut complet.
2. **La réduction privilégiait les premières fiches.** Pour les quatre publications réelles, le paquet reconstruit ne conserve que la fiche DL-Polycube. Il transmet trois preuves, dont aucune preuve de Qian. Le rédacteur n'a donc pas simultanément le texte à corriger et les éléments de toutes les publications nécessaires à la correction.
3. **La boucle n'avait pas de reçu de progression visible ni de tour réservé à l'enregistrement.** Les réponses de fiches gonflent les requêtes suivantes jusqu'à 81 761 caractères, puis l'échange précédent est remplacé. Le contexte n'indique pas de façon durable au modèle les pages déjà reçues. La borne sur les lectures strictement identiques ne réserve pas une tentative de sauvegarde avant le treizième tour.
4. **Le faux positif GCN-Polycube est un problème de portée de citation.** Dans la section 2, le nom figure dans la première phrase d'un paragraphe explicitement consacré à DL-Polycube. La preuve `ev_3ddd7dd349501e6be8d9a089`, citée à la fin du même paragraphe, décrit exactement le modèle ; la fiche le relie également à cette preuve. Le contrôle limité à la phrase ignorait ce lien.

L'audit global est effectué **après** la boucle, sans appel LLM. Ses quatre alertes historiques (une dans la section 2 et trois dans la section 5) n'ont consommé aucun des treize tours et n'ont pas déclenché une nouvelle argumentation. Il n'est pas la cause de l'absence de sauvegarde.

## Corrections limitées

- `writing_context.py` : paquet propre à la révision d'une section existante, avec texte brut intégral, identifiant de version, identités et affirmation de méthode de chacune des publications, propriétaires réels des preuves, résultats numériques conservés dans le schéma d'entrée et passages exacts prioritaires. Les références secondaires sont signalées distinctement des contributions propres. Aucune autre section n'est transmise comme texte à réviser.
- `context.py` : le même budget mémoire est consacré à ce paquet ; les aperçus globaux et répétitions de métadonnées sont retirés avant le texte à réviser. Les affirmations et citations restent entières. Un texte essentiel qui ne peut pas être transmis provoque un arrêt explicite, jamais une troncature silencieuse.
- `toolkit.py` : `write_section(read)` retourne également `current_draft.raw_text` complet. Aucun brouillon n'est modifié par cette lecture.
- `agent.py` : une révision ciblée écarte les anciens focus de lecture/reprise ; les reçus des pages déjà récupérées sont fournis au modèle. Le dernier tour déjà disponible propose uniquement `write_section(save)`, avec les mêmes vérifications de provenance, attribution et chiffres. Les lectures distinctes nécessaires restent disponibles avant ce dernier tour. Les événements de récupération incluent maintenant champs, positions et complétude.
- `attribution.py` : une citation à la fin du même paragraphe peut étayer un composant d'une publication explicitement nommée dans la phrase, uniquement si sa propre fiche non `background` relie ce composant aux preuves exactes du paragraphe. Cette exception n'autorise ni les auteurs cités dans les travaux antérieurs, ni un nom trouvé ailleurs dans le texte intégral. Elle ne traverse pas les paragraphes.

Aucun nom d'article, d'auteur ou de modèle n'a été ajouté aux règles de production. Kong/Qian et MeshGraphNet/AdaptNet restent détectés par les tests existants ; l'audit de la copie réelle ne signale plus GCN-Polycube, mais conserve les trois alertes de la section 5.

## Coût et objectifs P1

- 13 appels LLM, modèle réel `gpt-5.6-sol` inchangé ; 52,487 secondes cumulées entre événements de début/fin LLM, sur 68,797 secondes d'exécution totale.
- 288 756 tokens d'entrée, dont 106 380 en cache ; 3 678 tokens de sortie, dont 442 de raisonnement. Les tokens en cache et de raisonnement sont des sous-ensembles, pas des quantités à additionner aux totaux.
- Coût **estimé par l'application** : **1,228584 USD**, soit `(288756 × 4 + 3678 × 20) / 1e6` avec les tarifs locaux déjà configurés. Le calcul conservateur ne déduit pas de remise pour le cache ; ce montant n'est pas une facture vérifiée. Aucun tarif ni algorithme de facturation n'a été changé.
- Les consignes réduites à 3 961 caractères étaient bien actives. Néanmoins, une révision sans sauvegarde a consommé davantage de tokens d'entrée que le run précédent ayant enregistré cinq sections (278 869). La répétition des paquets de lecture annule donc l'objectif P1 sur ce cas. Les cinq lectures de contexte n'ont pas fourni le brouillon intégral demandé.

## Validation hors ligne

Les rejeux utilisent uniquement un client simulé et la copie JSON de la session. Le rejeu avant correction reproduit 13 tours, 47 récupérations et 5 lectures de section, sans sauvegarde. Il reproduit la **structure des appels** ; ses champs/positions sont simulés, puisque les traces réelles ne les conservent pas.

Après correction, la copie de cette session reçoit les 5 892 caractères du brouillon sans troncature, les quatre publications, et les passages exacts prioritaires de Qian et d'AdaptNet. Le rédacteur simulé enregistre une nouvelle version de la seule section 5 en **un tour et un appel `write_section(save)`**, avec une entrée de **17 956 caractères** et sans relecture de fiches ni comparaison. Les six autres sections, tous les anciens brouillons, les huit versions documentaires antérieures, les fiches et les preuves sont inchangés. Ce résultat démontre le chemin d'enregistrement et la disponibilité des sources, pas la qualité d'un véritable modèle.

Les tests déterministes de `test_targeted_revision.py` couvrent aussi la conservation des versions, la lecture exacte, les focus périmés, l'audit hors cible, le dernier tour réservé, le maintien du refus des preuves inconnues, GCN-Polycube et des noms alternatifs. Les tests précédents de pagination distincte et d'attribution restent requis.

Résultat final : **379 tests réussis**, aucun échec (V4, V3 et V3.1 ; sept nouveaux cas ; 64,90 s). Commande :

```powershell
D:\EnnoSmart\.venv\Scripts\python.exe -m pytest tests/ennoscholar_v4 tests/ennoscholar_v3 tests/ennoscholar_v31 --import-mode=importlib -q --tb=short --disable-warnings
```

`git diff --check` passe avec `core.whitespace=cr-at-eol`. L'empreinte SHA-256 de la base réelle est identique au début et à la fin : `5617ae04487486aec4c2b6247fc8824f09ee566a8c55fc8f706583778f0f7313`.

## Limites restantes et prochain essai

- Le sens scientifique et l'exhaustivité d'une correction restent des jugements du modèle et du consultant. L'exception de citation par paragraphe repose sur une fiche déjà étayée ; elle ne valide pas automatiquement tous les faits d'un paragraphe.
- Le dernier tour garantit une tentative d'enregistrement demandée au modèle, pas l'acceptation d'un texte invalide. Un refus conserve les versions et produit un état partiel explicite.
- Une section très volumineuse peut exiger une révision par paragraphes ; les plafonds actuels ne sont pas augmentés.
- Les arguments historiques non journalisés et le montant facturé réel ne peuvent pas être reconstitués sans données supplémentaires. Aucun appel au fournisseur n'a été effectué pour les obtenir.
- Le backend déjà démarré conserve son code chargé en mémoire. Il doit être redémarré pour le prochain test réel ; cette revue ne l'a ni redémarré ni utilisé pour lancer une demande payante.
