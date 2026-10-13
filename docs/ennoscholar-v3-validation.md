# Évaluation réelle d’EnnoScholar V3 — 11 octobre 2026

**V3 n'est pas validée scientifiquement.** Les outils, la connexion et la persistance fonctionnent, mais la couverture, l'achèvement des analyses et le classement restent insuffisants. Aucun article du benchmark n'a été transmis à l'agent. Les essais sont des appels réellement exécutés, avec leurs échecs conservés.

## Protocole et provenance

La demande exacte sur les maillages 3D en mécanique a été exécutée sur des instances V3 sans corpus initial, puis depuis le frontend avec le compte habituel et PostgreSQL. Les dix références ne sont lues que par l'évaluateur après la recherche. Leur identité bibliographique a été vérifiée par Crossref, DataCite/arXiv et les pages originales disponibles ; cette vérification n'est pas une annotation humaine de pertinence.

Les premiers essais ont révélé saturation du contexte, pannes temporaires de résolution DOI, sorties d'analyse tronquées, difficultés de délais et coupures OpenAI. Les corrections limitent les retours d'outils, préservent les analyses et l'index des candidats, retentent les registres, utilisent des analyses concises et gardent une réserve de synthèse. Une analyse tronquée ou sans preuve n'est jamais validée. Les lectures et les réponses interrompues conservent un statut partiel et une estimation de consommation incertaine.

La sélection ci-dessous a été enregistrée par le véritable outil `evaluate_research`, après des tours de reprise dans la conversation du frontend. La rédaction finale du modèle a ensuite subi un timeout : **le classement sauvegardé est réel, mais la réponse finale est partielle**. Les reprises demandaient de travailler sur le corpus existant ; elles ne fournissaient aucun titre ou DOI du benchmark.

## Classement effectivement enregistré par V3

Le tableau reproduit le classement de l'agent ; il n'a pas été réordonné manuellement pour augmenter le score.

| Rang | Publication | Base réellement disponible/analysée | Accès éditeur observé |
|---:|---|---|---|
| 1 | [A survey of AI methods for geometry preparation and mesh generation in engineering simulation](https://doi.org/10.1007/s00366-026-02379-1) | Texte complet analysé, passages sélectionnés | open_access |
| 2 | [DDPM-Polycube: a denoising diffusion probabilistic model for polycube-based hexahedral mesh generation and volumetric spline construction](https://doi.org/10.1007/s00366-025-02248-3) | Texte complet analysé, passages sélectionnés | open_access |
| 3 | [Patient-Specific Heart Geometry Modeling for Solid Biomechanics Using Deep Learning](https://doi.org/10.1109/tmi.2023.3294128) | Abstract analysé | paywalled |
| 4 | [DL-Polycube: Deep learning enhanced polycube method for high-quality hexahedral mesh generation and volumetric spline construction](https://doi.org/10.48550/arxiv.2410.18852) | Texte récupéré, non analysé | open_access |
| 5 | [AMBER: Adaptive Mesh Generation by Iterative Mesh Resolution Prediction](https://doi.org/10.48550/arxiv.2505.23663) | Texte récupéré, non analysé | unknown |
| 6 | [Neural Volumetric Mesh Generator](https://arxiv.org/abs/2210.03158) | Texte récupéré, non analysé | open_access |
| 7 | [NeurFrame: Learning Continuous Frame Fields for Structured Mesh Generation](https://doi.org/10.48550/arxiv.2603.12820) | Texte récupéré, non analysé | open_access |
| 8 | [Reinforcement Learning for Block Decomposition of CAD Models](https://doi.org/10.48550/arxiv.2302.11066) | Abstract disponible, non analysé | open_access |
| 9 | [Graph Neural Networks for Mesh Generation and Adaptation in Structural and Fluid Mechanics](https://doi.org/10.3390/math12182933) | Abstract disponible, non analysé | open_access |
| 10 | [A Machine Learning Approach to Predict Near-optimal Meshes for Turbulent Compressible Flow Simulations](https://doi.org/10.1080/10618562.2024.2306941) | Abstract disponible, non analysé | open_access |

`open_access` et `paywalled` viennent des notices disponibles ; `unknown` signifie non établi. Ils sont indépendants de l'accès à une prépublication. Un texte téléchargé n'a pas nécessairement été analysé. Pour les deux analyses intégrales, l'extraction est bornée : V3 a utilisé des passages avec pages/sections et n'a pas lu chaque mot de la publication. DDPM-Polycube est daté de 2026 dans Crossref, malgré son DOI contenant 2025.

## Benchmark et comparaison

| Mesure | V2, demande initiale, un tour | V3, classement après reprises |
|---|---:|---:|
| Recall@5 strict | 0,20 | 0,10 |
| Recall@10 strict | 0,20 | 0,30 |
| Références du benchmark découvertes dans le corpus | 8/10 | 5/10 |
| Textes complets analysés avec preuves V3 | Contrat non fourni par V2 ; aucune analyse dans cet essai | 2 |
| Precision@10 indépendante | Non annotée | Non annotée |

La comparaison n'est pas équivalente en budget : V3 a utilisé plusieurs tours de reprise, alors que V2 a reçu la demande une seule fois. Le classement V2 est reconstruit à partir de l'ordre des références effectivement présentées dans sa réponse, plutôt qu'à partir de l'ordre de collecte interne. V2 a présenté neuf références ; son absence de contrat `verified` ne signifie pas que ces références sont fausses.

Les trois identifiants strictement retrouvés dans le top dix V3 sont DDPM-Polycube, NeurFrame et l'article GNN en mécanique. DL-Polycube figure au rang 4 sous la DOI de sa prépublication arXiv ; la DOI de la version revue du benchmark est bien découverte, mais elle n'est pas la notice classée. AMBER 2025 au rang 5 n'est pas la référence arXiv 2406.14161 du benchmark : les deux restent distinctes.

Sur l'ensemble des essais V3, huit références du benchmark ont été découvertes et vérifiées au moins une fois. **Cela ne constitue pas un Recall@10 de 0,8** : ces résultats proviennent de plusieurs exécutions, dont certaines se sont interrompues sans classement.

## Références absentes du classement strict final

| Référence | Constat |
|---|---|
| [3DMeshNet](https://doi.org/10.1016/j.gmod.2025.101257) | Jamais découvert dans les essais V3 |
| [DL-Polycube](https://doi.org/10.1007/s00366-025-02161-9) | Découvert dans la conversation, absent du classement strict |
| [MeshingNet3D](https://doi.org/10.1016/j.advengsoft.2021.103021) | Découvert dans un autre essai V3 |
| [AMBER](https://arxiv.org/abs/2406.14161) | Découvert dans la conversation, absent du classement strict |
| [Learned Adaptive Mesh Generation](https://arxiv.org/abs/2505.20457) | Jamais découvert dans les essais V3 |
| [Faster and efficient tetrahedral mesh generation using generator neural networks for 2D and 3D geometries](https://doi.org/10.1007/s00521-023-09119-2) | Découvert dans un autre essai V3 |
| [Meshing using neural networks for improving the efficiency of computer modelling](https://doi.org/10.1007/s00366-023-01812-z) | Découvert dans un autre essai V3 |

Deux références n'ont jamais été découvertes dans les essais V3 : 3DMeshNet et Learned Adaptive Mesh Generation. Les causes exactes de non-découverte ne sont pas établies. Les erreurs et limites de recherche sont observées ; attribuer précisément chaque absence à une requête ou à un fournisseur serait une hypothèse.

## Évaluation scientifique critique

Le rang 1 attribué à une revue montre que le classement ne privilégie pas assez les contributions méthodologiques directes. La biomécanique au rang 3 fournit un cas de génération volumique pour FEM, mais sa généralisation aux géométries industrielles n'est pas démontrée par l'abstract analysé. Les familles polycube/diffusion, champs de taille, champs de repères et génération volumique sont présentes ; la couverture des méthodes tétraédriques apprises reste lacunaire.

Quatre textes complets du classement sont récupérés mais non analysés. Les notices des autres articles ne permettent pas de confirmer leurs résultats quantitatifs, conditions d'échec, robustesse et généralisation. Les tags produits par le modèle sont des jugements provisoires : par exemple la revue a été marquée DIRECT. Il serait incorrect d'utiliser ces tags pour calculer une précision scientifique indépendante.

Un article payant est effectivement conservé au rang 3, analysé au niveau abstract. Les interruptions OpenAI et les délais des fournisseurs ont empêché une validation complète. Le dernier tour dure 118 secondes et rapporte 0,153 USD de consommation confirmée par les réponses terminées, plus une réserve conservatrice de 1,745 USD pour des réponses interrompues dont la consommation n'a pas été retournée. Ces nombres ne sont pas une facture. Le coût V2 n'était pas instrumenté dans son contrat et reste inconnu.

## Corrections nécessaires, par priorité

1. **Terminer l'analyse scientifique des candidats prioritaires.** Quatre textes du top dix sont déjà récupérés mais non analysés. L'évaluation doit distinguer méthodes directes, préparation géométrique, champs de taille/repères et synthèses bibliographiques, avec preuves sur éléments produits, validité des maillages, qualité, coût et généralisation. Le classement doit privilégier la réponse à la demande et les preuves disponibles ; la revue au rang 1 révèle une pondération insuffisante.
2. **Améliorer la couverture sans connaître le benchmark.** Planifier plusieurs familles de requêtes conceptuelles, puis exploiter citations et articles voisins des publications découvertes. La recherche doit couvrir notamment génération volumique tétraédrique, hexaédrique, adaptation et méthodes hybrides. Les corrections de requêtes arXiv et de conservation du contexte sont implémentées, mais leur effet sur un nouvel essai complet n'a pas été validé.
3. **Consolider les identités bibliographiques.** Relier prépublication et version revue avec une provenance vérifiée, tout en conservant leurs identifiants distincts. DL-Polycube montre une fragmentation de version ; les deux références AMBER doivent rester distinguées. Une similarité de titre ne suffit pas pour déclarer une correspondance au benchmark.
4. **Fiabiliser délais et reprise.** Les erreurs de connexion et timeouts sont observés ; leur origine exacte n'est pas établie. Ajuster les délais de lecture aux appels longs, maintenir une échéance globale, sauvegarder chaque étape et permettre une reprise contrôlée avec plafond de coût. Le dernier classement a été conservé, mais la synthèse finale a échoué. Les appels interrompus doivent garder une consommation incertaine, sans être comptés gratuitement.
5. **Ajouter une évaluation indépendante de pertinence.** Faire annoter les résultats par un spécialiste et comparer V2/V3 avec une demande et un budget équivalents. La précision scientifique n'est actuellement pas mesurée ; les jugements du modèle ne peuvent pas servir de vérité de référence.

Ces points constituent le travail restant, sans nouveau test payé exécuté pour ce rapport. À la demande de l'utilisateur, les appels OpenAI, reconstructions et relances du benchmark sont arrêtés. Les chiffres ci-dessus proviennent exclusivement des sorties déjà enregistrées. Les vérifications techniques citées ci-dessous ont été effectuées auparavant ; elles ne remplacent pas l'évaluation scientifique.

## Frontend, backend et V2

La page de test est `http://localhost:3001/ennoscholar-v3`, reliée au backend complet mis à jour sur 8002 et à la base PostgreSQL habituelle. Les serveurs antérieurs 3000/8000 sont restés ouverts pour préserver les sessions V2 en mémoire. Les sources de production enregistrent les routes V3 dans l'application principale ; le serveur 3000 déjà démarré ne recharge pas automatiquement son ancien build.

La connexion réelle, création de conversation, recherche asynchrone, annulation, rechargement de l'historique et conservation d'un article ont été testés dans l'interface. Le classement est stocké côté serveur et réapparaît au rechargement. Les candidats non classés sont accessibles séparément. Le compte et son mot de passe ne sont pas inclus dans les artefacts.

Validation technique : 32 tests V3, 284 tests V2/Diagnostic, vérification TypeScript et build Next.js réussis. Les empreintes des 170 fichiers protégés V2/V1/Diagnostic sont inchangées. Les changements antérieurs de l'utilisateur dans le dépôt ont été conservés.

Les preuves synthétiques, identifiants, classements, statuts d'accès et activités natives figurent dans `tests/ennoscholar_v3/research/observed_results_2026-10-11.json`. Les sorties brutes restent dans `.codex_tmp/scholar-v3-live-*` et ne sont pas simulées. L'implémentation et la reproduction des essais sont décrites dans `agents/EnnoScholarV3/README.md`.

Les modifications, les sources V2 déjà présentes et les preuves synthétiques sont sauvegardées sur la branche `codex/ennoscholar-v3`. Une copie locale des sorties brutes, de la comparaison V2 et des empreintes protégées est conservée dans `.codex_tmp/ennoscholar-v3-recorded-evidence-2026-10-11.zip` (hors Git). Aucun nouveau test OpenAI ni aucune reconstruction n'ont été effectués pour terminer ce rapport.
