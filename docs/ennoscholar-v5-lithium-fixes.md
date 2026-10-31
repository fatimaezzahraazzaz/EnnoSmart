# V5 — corrections du run lithium f2d6207d

Les modifications sont limitées au worktree expérimental. La session réelle 21ce7ee2 et ses six runs sont lus avec SQLite mode=ro, copiés dans une fixture de rejeu, et ne sont jamais utilisés comme base modifiable.

## P0 — mission, reprise, sauvegarde

- startup.configure_local_v4 transmet uniquement les trois paramètres documentés de mission V5. La variable fournie explicitement dans le shell garde la priorité. Les valeurs non positives/non finies sont refusées avant mutation de l’environnement.
- La surcharge V5 writing_progress réutilise le registre et les sauvegardes V4. Les demandes Terminer/Achever/Finaliser reprennent les sections restantes ; une négation ne déclenche pas de rédaction.
- Le but draft sauvegardé par Sol peut ouvrir le suivi sans dépendre d’un modèle de phrase. Le service rattache ce premier run à une mission, conserve les coûts de son registre et borne ce premier run au budget autorisé dès le premier appel.
- Les missions conservent leur conducteur, leurs contrôles de portée, l’approbation humaine, les réservations financières et leurs limites de reprises.
- La dernière section d’une demande de manuscrit contient une Conclusion générale avec citations. Elle est contrôlée par write_section avec tout le texte, sauvegardée dans ce même brouillon et référencée depuis document.conclusion. Aucun ajout au plan approuvé. Une conclusion absente reste du travail à terminer. Les brouillons antérieurs ne sont pas réécrits.

## P1 — préparation scientifique

- search_needed=False concerne la découverte ; il ne désactive pas get_fulltext pour les travaux déjà sélectionnés.
- Le dossier distingue local_text, not_requested et retrieval_failed, avec tentatives, passages présentés et preuves sauvegardées.
- Les textes jamais récupérés et les textes locaux non analysés doivent être préparés. Les fiches déjà fondées sur des lectures intégrales mais partielles peuvent encore être confirmées comme limitées après les refus bornés existants ; leur sauvegarde ne valide pas les dimensions manquantes. Un échec d’accès enregistré permet une rédaction limitée ; il ne démontre pas l’absence de toute version ouverte.
- Sol choisit toujours les passages, les conclusions scientifiques et les comparaisons. Aucun nouveau scientifique LLM ni agent n’est ajouté.

## P2 — références et qualité

- La normalisation V5 rapproche les formules à indices séparés comme Li3 PO4 et Li₃PO₄, sans modifier les règles V4 ni les identités DOI.
- Le test utilise le reçu Crossref réel conservé et la notice Semantic Scholar réelle. La revalidation utilise un registre simulé et conserve l’historique du conflit ; elle ne crée pas un reçu de vérification sur les données réelles.
- Les dossiers exposent un index des valeurs littérales des preuves, notamment les températures de Folkson, sans convertir les anciens textes en mesures réputées vérifiées.
- Les contrôles d’attribution, de provenance numérique, de citations et de sauvegarde V4 sont conservés. Les sections limitées ne deviennent pas scientifiquement complètes par leur seule sauvegarde.

## P3 — contexte et répétitions

- Une formulation différente de write_section(read) ne change pas l’identité scientifique du dossier. Après transmission confirmée, un doublon renvoie une référence courte ; une modification de fiche invalide la réutilisation.
- Un revisit répété pour la même question ne réinjecte pas les mêmes passages. Une autre question ou une raison explicite permet la relecture.
- Les reçus de lecture et de consultation sont conservés lorsque les sous-exécutions poursuivent le même message du consultant. Une nouvelle question possède sa propre identité de lecture et peut redemander les passages nécessaires.
- Les index, fiches, preuves et mémoires de travail existants restent récupérables. Le contexte global et les plafonds ne sont pas augmentés.

## Fichiers et fonctions modifiés

| Priorité | Fichier | Fonctions |
| --- | --- | --- |
| P0 | agents/EnnoScholarV5/startup.py | configure_local_v4 : liste autorisée, validation des montants, diagnostic non secret |
| P0 | agents/EnnoScholarV5/config.py | mission_budget : montant positif fini |
| P0 | agents/EnnoScholarV5/writing_progress.py (nouveau) | begin_writing, refresh_writing, conclusion_pending : surcharge V5 du registre V4 |
| P0 | agents/EnnoScholarV5/service.py | open_run, execute, after_run : missions identifiées à l'ouverture ou par la décision de Sol, conservation du premier coût |
| P0/P1/P2 | agents/EnnoScholarV5/runtime.py | instructions, workflow_checks, after_workflow : raccordement du but draft, validations et sauvegarde de conclusion |
| P0/P1/P2 | agents/EnnoScholarV5/workflow.py | reconcile_draft_goal, section_packet, section_preparation, preparation_refusal, section_science |
| P2 | agents/EnnoScholarV5/sources.py | title_key : normalisation des formules et signes |
| P3 | experiments/ennoscholar_v5/policy.py | semantic_key, reusable : dossier stable et reprise d'une lecture sans recopie |

Tests : nouveau test_lithium_fixes.py ; scénarios CIR et test de qualité adaptés aux lectures supplémentaires nécessaires. La fixture real-lithium-session.json.gz est une copie de la session réelle en lecture seule. Aucun changement SQL, frontend, extraction multimodale, export ou module V4.

## Validation

Résultat final : **204 tests réussis, 0 échec, 0 erreur**, en 801,44 secondes. Les 188 tests existants et 16 nouveaux cas sont couverts. Le rapport JUnit est `experiments/ennoscholar_v5/.artifacts/lithium-complete.xml`.

La première suite intermédiaire a relevé sept échecs, corrigés avant la livraison : préconditions scientifiques plus strictes dans les modèles scriptés, calcul du nombre d'appels de préparation et place conservée pour les relations dans le dossier. Une passe de 203 tests a ensuite réussi ; le contrôle des reçus entre segments a ajouté une correction et un cas supplémentaire, puis la suite complète de 204 tests a réussi.

Les nouveaux tests vérifient le profil local, cinq formulations de reprise, le but draft sans modèle de phrase, la mission sur la copie lithium avec deux brouillons conservés, la conclusion sauvegardée, les coûts du premier segment, la demande négative, le texte jamais recherché, la lecture locale, la tentative simulée de récupération via LangGraph, Li₃PO₄ et les conflits réels, Folkson, les relectures et la mémoire entre segments. Les contrôles de dollars, tokens, durée, approbation humaine, reprise et stagnation existants sont également exécutés.

Les décisions Sol sont scriptées via le vrai SDK Responses avec transport simulé. Les bases et checkpoints de tests sont isolés et le réseau est bloqué par le lanceur hors ligne. Les modèles scriptés des scénarios CIR demandent maintenant les lectures utiles au lieu de confirmer aveuglément un texte alors qu'une source locale n'a pas été analysée.

Vérification d'intégrité : 58 fichiers V4 identiques à leur empreinte au début de cette intervention ; JSON de la session lithium et de ses six runs inchangés. Résultat : `experiments/ennoscholar_v5/.artifacts/lithium-preservation-final.json`.

Le contrôle local réel `start_ennoscholar_v5.py --profile local-v4 --env-file D:\EnnoSmart\.env --check-config` a retourné `mission_enabled=true` et `mission_budget_usd=3.0`, sans importer le backend ni démarrer de serveur. Aucun appel API payant, téléchargement réel, commit, push ou déploiement.

Commande exacte de reproduction hors ligne, depuis le worktree V5 :

```powershell
& 'D:\EnnoSmart\.venv\Scripts\python.exe' -B experiments/ennoscholar_v5/run_offline.py --disable-warnings --basetemp=experiments/ennoscholar_v5/.artifacts/pytest-lithium-complete --junitxml=experiments/ennoscholar_v5/.artifacts/lithium-complete.xml
```

## Limites de portée

Le plafond de mission borne la mission créée et ses sous-exécutions. Les anciens runs de la session, exécutés sans mission, restent dans leurs registres historiques ; ils ne sont pas rétroactivement réaffectés à une mission. Aucune mission existante n'est rechargée par une nouvelle sous-exécution.

La qualité des choix scientifiques et de la prose de Sol réel reste à démontrer. Les scénarios utilisent des décisions scriptées, de vrais outils et sauvegardes, des sources archivées ou simulées, et un transport Responses simulé. Les textes inaccessibles peuvent laisser un manuscrit complet avec limites documentaires, sans couverture scientifique suffisante. Les formulations reconnues ouvrent la mission dès la demande ; une intention identifiée seulement par Sol peut être rattachée après le premier segment, avec son coût conservé.

## Validation réelle minimale (à lancer par le consultant)

Après redémarrage manuel avec le même profil local-v4, contrôler que la mission affiche le budget autorisé de 3 dollars, puis envoyer une seule fois la demande Terminer la rédaction sur la session lithium existante. Vérifier la conservation des deux premiers brouillons, la tentative d’accès à une source de section 3, son enrichissement si possible, les sauvegardes restantes et la conclusion. Un arrêt du budget par segment peut relancer un segment dans les limites de la même mission ; le plafond global ne peut pas être dépassé. Aucun run réel n’est lancé par cette intervention.
