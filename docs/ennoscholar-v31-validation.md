# EnnoScholar V3.1 — protocole et validation

## État au 12 octobre 2026

Implémentation indépendante sur `feature/ennoscholar-v3.1`, à partir de `0497f719`.
V3.1 n'est **pas encore validée scientifiquement en conditions réelles**. Aucun appel
OpenAI ou fournisseur payant n'a été exécuté pendant cette mise en œuvre.

Validation technique :

| Contrôle | Résultat |
|---|---|
| Tests V3.1 | 31 réussis, réseau externe et API OpenAI interdits par fixture |
| Tests V2 | 302 réussis, suite exécutée séparément |
| Tests V3 | 32 réussis, suite exécutée séparément |
| Diagnostic V2 + contrôle V2-only | 11 réussis |
| TypeScript | Réussi |
| Build Next.js | Réussi, route `/ennoscholar-v3-1` présente |
| Bibliographie des benchmarks | DOI contrôlés gratuitement auprès de Crossref ; références arXiv héritées du corpus de test vérifié |
| UI avec compte réel | Connexion réussie, création d'une conversation et rechargement de l'historique vérifiés |
| Responsive | Pas de débordement horizontal observé à 375 px et 812 px ; viewport ensuite réinitialisé |
| Benchmark GPT-5.6 Sol | Non exécuté : l'utilisateur a demandé de garder les appels payants désactivés |

Les premières tentatives de lancer toutes les suites dans un seul processus ont échoué
à la collecte en raison de noms de modules identiques et des imports historiques V2.
Les exécutions séparées ci-dessus ont ensuite réussi ; aucun fichier de test V2/V3
n'a été modifié pour contourner ces contraintes.

Le PostgreSQL natif existant a été redémarré sans création/restauration de base.
Le premier lancement utilisait le profil Docker ; le lancement réel utilise `.env.native`
et ses identifiants de base. L'API complète répond sur 8003 et le frontend sur 3002.
La connexion a été vérifiée avec le compte communiqué par l'utilisateur, uniquement
sur cette API réelle. Le serveur de fixture UI est arrêté. Aucun mot de passe n'est
conservé dans le code ou les rapports. La capture locale est `.codex_tmp/v31-ui.png`.
Le chat réel reste bloqué avant tout worker tant que le budget payant n'est pas autorisé.
Après présentation du code et du protocole, l'utilisateur a explicitement choisi de
maintenir les appels payants désactivés. Les benchmarks réels sont donc reportés ;
aucune amélioration scientifique n'est présentée comme démontrée.
223 fichiers suivis des composants protégés ont été contrôlés : aucun changement par
rapport à `0497f719`. Les trois intégrations communes sont additives : route backend,
entrée du shell frontend et helper API authentifié.

## Ce que les tests démontrent

- Un candidat en position 960 parmi 1 000 reste retrouvable par son contenu.
- Deux fenêtres produites dans un même lot d'appels restent complémentaires et cumulatives.
- Présentation et examen sont comptés séparément ; une réponse API interrompue ne valide pas une livraison.
- Une contribution payante découverte tard peut remplacer un finaliste accessible.
- DOI, arXiv et titres exacts restent recherchables, y compris pour revisiter un finaliste.
- Les notices des versions restent distinctes ; seuls des identifiants explicites les relient.
- Les analyses utilisent uniquement des passages présentés, avec pages et empreinte de version.
- Une citation ou page altérée est bloquée par le snapshot source.
- Une association titre–DOI erronée et une DOI inventée sont bloquées.
- Les gaps distinguent absence dans le corpus et inférence comparative étayée.
- BM25 fonctionne sur deux autres domaines simulés. Les embeddings locaux facultatifs
  sont testés avec un encodeur fictif ; cela ne valide pas un modèle multilingue réel.
- L'historique, les sélections, l'isolation par propriétaire, les checkpoints et les baux sont testés.
- Un import PDF ne crée ni DOI ni identité vérifiée et conserve son niveau d'analyse réel.

Ces constats portent sur les mécanismes. Ils ne prouvent ni recall réel, ni precision
scientifique, ni une amélioration par rapport à V2/V3 sur des recherches autonomes.

## Corpus de référence indépendant

`tests/ennoscholar_v31/research/references.json` contient trois cas : maillages en
mécanique (demande principale), structures biomoléculaires et tomographie quantique.
Les titres/DOI ne sont utilisés qu'après une recherche pour calculer les mesures.
`run_live` reçoit uniquement le texte de la demande ; aucun titre, DOI, famille attendue
ou score du benchmark n'entre dans le prompt de production.

Les identités DOI sont accompagnées de notices de registre et de leur date de contrôle.
La pertinence de ces références et des réponses doit encore être annotée par un expert.
Le jeu n'est pas une description exhaustive de chaque domaine.

## Mesures

L'évaluateur produit :

- Recall dans le corpus, exposure, selection et présence dans la réponse.
- Recall@10 strict et sélection par travail canonique lorsque les liens sont établis.
- Pour chaque référence : absent du corpus, présent non exposé, exposé non sélectionné,
  sélectionné non retourné ou retourné ; les indicateurs bruts restent disponibles.
- Precision et coverage seulement avec annotations indépendantes complètes.
- Validité bibliographique constatée par les outils de registre.
- Intégrité des preuves citées comparées aux snapshots ; soutien sémantique indépendant
  laissé non mesuré tant qu'un expert ne l'a pas annoté.
- Durée, consommation connue, réserve pour appels interrompus, statut partiel et cause d'arrêt.

Un score indéfini reste `null`. Les tags DIRECT/CONNEXE du modèle ne sont pas une vérité
de référence. L'absence d'une référence n'est pas attribuée à un fournisseur sans preuve.

## Exécution reproductible

Tests sans dépense, depuis `D:\EnnoSmart` :

```powershell
.venv\Scripts\python.exe -m pytest tests/ennoscholar_v31 -q
.venv\Scripts\python.exe -m pytest tests/ennoscholar_v2 -q
.venv\Scripts\python.exe -m pytest tests/ennoscholar_v3 -q
$env:PYTHONPATH = "D:\EnnoSmart;D:\EnnoSmart\agents\EnnoDiagnostic\V2"
.venv\Scripts\python.exe -m pytest agents/EnnoDiagnostic/V2/tests tests/test_v2_only_cleanup.py -q
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend run build
```

Vérification gratuite des registres, déjà exécutée :

```powershell
.venv\Scripts\python.exe tests/ennoscholar_v31/research/validate_references.py --free-registry-check
```

**Seulement après approbation humaine de 12 USD au maximum**, exemple pour trois cas
avec plafond configuré de 4 USD par tour :

```powershell
.venv\Scripts\python.exe tests/ennoscholar_v31/research/evaluate.py --live --approved-budget-usd 12 --case all --output .codex_tmp/v31-evaluation
```

Chaque cas démarre une conversation et un corpus vides. Les artefacts conservent
requête, configuration de consommation, résultat, activité, état complet et métriques.
Le budget restant déduit aussi les réserves des appels interrompus. Il s'agit d'une
estimation conservatrice et non d'une garantie contractuelle de facturation.

Réévaluation sans nouvelle recherche ni dépense :

```powershell
.venv\Scripts\python.exe tests/ennoscholar_v31/research/evaluate.py --compare-only --case all --output .codex_tmp/v31-evaluation --annotations chemin/annotations.json
```

Format des annotations : objet par cas, puis par `article_id`, avec `relevant` booléen
et `families` liste. La pertinence doit être définie avant annotation ; les labels du
modèle ne doivent pas être recopiés comme annotations indépendantes.

## Limites matérielles et scientifiques

- Les API publiques peuvent limiter la couverture ou les délais ; des requêtes
  complémentaires et les pages suivantes restent à la décision du GPT.
- Corpus plafonné explicitement à 20 000 notices par défaut ; toute perte de capacité
  est signalée. La persistance JSON par session n'est pas un index distribué.
- Embeddings multilingues facultatifs : chemin local à configurer ; aucun téléchargement automatique.
- L'extraction initiale V2 est bornée et signalée. Les PDF conservés peuvent être
  relus par pages. OCR, pagination imprimée et lecture automatique des figures ne sont pas fournis.
- Les relations de versions sans identifiant explicite restent à vérifier.
- Python contrôle les références et citations exactes ; l'entailment scientifique
  des affirmations reste à valider indépendamment.
- Une limite d'article ne démontre pas l'éligibilité CIR d'un projet client.
- Le modèle est configuré selon la documentation officielle ; l'accès réel du compte
  et la qualité de recherche doivent être confirmés dans les essais autorisés.

Le lancement frontend/backend est détaillé dans `agents/EnnoScholarV31/README.md`.
Les comptes et mots de passe ne sont enregistrés dans aucun de ces artefacts.
