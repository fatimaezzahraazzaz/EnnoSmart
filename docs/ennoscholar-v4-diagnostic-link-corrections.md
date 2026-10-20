# Liaison EnnoDiagnostic → EnnoScholar : corrections après audit réel

17 octobre 2026. Branche `feature/ennoscholar-v4-diagnostic-link`, sans commit.

L’absence de diagnostic dans la SQLite active est normale avant le premier lancement d’EnnoDiagnostic. Aucune donnée n’a été importée dans cette base pour contourner cette situation.

## Corrections

### Normalisation des formats existants

L’adaptateur utilise les entités/extractions V2, les points de synthèse, les cartes `diagnostic_cards` et les sections canoniques anciennes. Il conserve le texte et les sources, sans exécuter EnnoDiagnostic, consulter un RAG ou lire les documents clients. Les sections par clé sont fusionnées avec les sections canoniques, dont la formulation reste prioritaire. Les objectifs et sous-objectifs ne sont pas inventés lorsqu’ils manquent.

Une preuve peut porter `quote`, `excerpt` ou `text`, avec `evidence_id`, `passage_id`, `chunk_id` ou `rag_chunk_id`. La normalisation garde les champs originaux et ajoute les alias utiles, les chemins JSON d’origine et le statut de vérification. Elle n’invente ni document, page, position ni vérification. Un `quote_verified` absent reste inconnu ; des indicateurs contradictoires ne produisent pas une preuve vérifiée.

Les identifiants locaux répétés tels que `E1` sont désambiguïsés par leur passage, leur document, leurs positions et leur texte, avec un identifiant stable limité au diagnostic. L’identifiant original est conservé. Une référence ambiguë sans passage permettant de la résoudre est signalée, jamais associée arbitrairement. La récupération par ancien identifiant peut retourner plusieurs candidats ; les références normalisées permettent de les distinguer.

Les cartes et paragraphes anciens deviennent des **observations du diagnostic à confirmer**. Leurs méthodes, paramètres et résultats rapportés sont récupérables pour la comparaison, mais ne satisfont pas automatiquement l’exigence de fait projet vérifié d’un blueprint. « Garder » ne vérifie aucune citation et ne remplace pas un rôle documentaire projet/littérature manquant.

### Périmètre scientifique

Les décisions humaines viennent toujours de `verrous.consultant_status`, et les qualifications automatiques restent distinctes.

Seules des relations explicites peuvent attribuer une méthode ou un résultat au verrou : identifiant de verrou déclaré, appartenance enregistrée sur le verrou lui-même, ou lien explicite transmis par les identifiants d’items d’un même fait. Les familles, preuves communes, passages communs et items utilisés pour dériver la formulation d’un verrou ne suffisent pas. Les extractions de verrous secondaires ne sont pas réintroduites comme contexte général du verrou actif.

- `scope=active` : données explicitement liées au verrou actif.
- `scope=general` : informations projet sans attribution établie, accessibles sans activer un autre verrou.
- `scope=all` : accès ciblé aux autres éléments conservés, y compris les relations non résolues.

Le contexte de recherche garde les objectifs et le contexte technique général ; il ne transmet pas automatiquement les méthodes/résultats généraux comme travaux du verrou sélectionné. Le contexte de comparaison peut présenter ces observations générales dans une catégorie séparée, avec `lock_ids: []` et leur statut de vérification/temporalité.

### Comparaison scientifique

Le paquet argumentatif contient maintenant une vue `project_context` concise, protégée lors de la réduction du contexte. Les catégories sont entrelacées pour éviter que les objectifs ne prennent toute la place avant les méthodes, paramètres et résultats. Les enregistrements restent entiers ; les références aux preuves sont limitées avec un compteur et une récupération ciblée.

La liste complète des faits projet peut être différée, mais les références compactes aux faits effectivement présentés restent dans `project_facts`. Les citations complètes et les métadonnées de provenance se relisent avec `get_project_context`. Les observations non vérifiées restent visibles dans `project_context` sans identifiant de fait projet admissible inventé. Les faits ajoutés ensuite par le consultant sont conservés.

Une étape `project_confrontation` utilisant un fait projet général doit expliciter l’incertitude sur son rattachement au verrou. Les preuves des publications restent dans leur registre scientifique propre ; une preuve de diagnostic ne devient jamais une preuve publiée. Les interprétations du modèle conservent les contrats V4 existants et ne valent pas validation humaine ou CIR.

Le paquet projet est limité à 3 400 caractères avant réduction éventuelle dans la fenêtre existante. Aucune hausse de budget, de modèle, de tarif ou de nombre de tours. Les contrats Express restent inchangés et ne reçoivent pas ce nouveau paquet projet.

## Validation sur les diagnostics réels 17 et 48

Source : archive locale `D:\EnnoSmart-backups\ovh-history-deploy-20261014\remote-backups\postgres-before-contextual.dump`.

Le décodeur PostgreSQL a uniquement lu les tables demandées vers stdout avec `--file=-`. Aucun serveur PostgreSQL n’a été démarré et aucune restauration n’a été effectuée. Les seules copies de lignes réelles ont été placées dans des SQLite temporaires isolées, créées par les tests. Aucun JSON client n’est ajouté aux fixtures versionnées ; les tests lisent l’archive locale lorsqu’elle est disponible. Son empreinte est vérifiée avant et après.

| Observation | Diagnostic ancien 17 | Diagnostic V2 48 |
|---|---|---|
| Décision humaine | 35, 36, 37 sont `garde` | 140–143 restent `en_attente` |
| Choix utilisé hors ligne | 35 seul parmi les trois conservés | 140 seul, choix non validé explicitement autorisé dans l’environnement temporaire |
| Preuves normalisées | 100 entrées, dont plusieurs représentations de passages ; 45 passages attachés au verrou 35 | 124 références |
| Vérification | 100 statuts non établis ; aucune promotion automatique | 124 indicateurs de citation vérifiée repris tels que persistés |
| Faits attribués dans le périmètre actif | 45 passages anciens attachés au verrou, sans classification expérimentale inventée | Une seule entité canonique du verrou 140 |
| Méthodes/paramètres/résultats | Cartes et paragraphes rapportés, généraux et à confirmer | Accessibles avec citations ; généraux en l’absence de relation explicite au verrou |
| Extrait odeurs/fumées du verrou secondaire | Sans objet | L’item `it_fast_24_p254` est exclu des faits du verrou 140 |
| Contexte de comparaison borné | Paramètre, résultat indisponible signalé, démarche rapportée, objectif et passage | Méthode, paramètre, résultat et métrique avec références utilisables |

Les 100 entrées anciennes ne signifient pas 100 preuves expérimentales indépendantes : les cartes et verrous peuvent conserver des extraits ou des positions différents d’un même passage. Aucune déduplication fondée sur le seul identifiant ancien n’écrase ces représentations.

Les exemples ci-dessous sont des extraits anonymisés du contexte effectivement **préparé par les fonctions de production**, dans l’environnement temporaire. Aucun contexte n’a été envoyé à un LLM. Les catégories et statuts sont ceux des données ; les extraits n’impliquent aucune validation scientifique du dossier.

### Exemple ancien : verrou 35

Le verrou porte sur les sorties vectorielles topologiquement valides issues de LiDAR clairsemé. Dans le paquet de comparaison :

```json
{
  "active_lock_ids": ["35"],
  "main_objective": "L'objectif global n'est pas suffisamment stabilisé dans les preuves qualifiées du projet courant ; une validation consultant est requise.",
  "observations": [
    {
      "source_fact_id": "diagnostic_card_7_0",
      "category": "parameter",
      "scope": "project_context_not_linked_to_lock",
      "lock_ids": [],
      "verification_status": "not_established",
      "project_fact_ids": [],
      "diagnostic_evidence_ids": ["diag_ev_17_69b2c4420f8d48bf26b3f39a"]
    },
    {
      "source_fact_id": "diagnostic_card_6_0",
      "category": "result",
      "statement": "Aucun résultat suffisamment contextualisé et attribuable au projet courant n'a pu être publié automatiquement.",
      "scope": "project_context_not_linked_to_lock",
      "lock_ids": [],
      "verification_status": "not_established",
      "project_fact_ids": []
    }
  ],
  "partial": true
}
```

La formulation complète du paramètre transmise au modèle décrit **5 points-clés** pour un bloc de triangles de présignalisation. Elle est disponible avec sa preuve, mais son rapport au verrou LiDAR n’est pas établi. Elle ne doit donc pas orienter automatiquement la recherche du verrou. La démarche détaillée, un objectif stabilisé et des résultats vérifiés demeurent indisponibles dans ce diagnostic ; la correction ne les invente pas.

### Exemple V2 : verrou 140

Le verrou actif concerne le durcissement d’une résine au contact de l’air. Exemple de paramètre général présent dans le paquet de comparaison :

```json
{
  "active_lock_ids": ["140"],
  "observations": [
    {
      "source_fact_id": "ent_369dda7cd58c",
      "category": "parameter",
      "statement": "Réglage d’intensité de la couleur noire en 2e couche à 0,15 puis 0,5 (V2-1 et V2-1+), charge Mussel-S.",
      "scope": "project_context_not_linked_to_lock",
      "lock_ids": [],
      "verification_status": "verified_quote",
      "temporal_status": "unclear",
      "diagnostic_evidence_ids": ["ev_fast_1539400b9bc09a62"]
    }
  ],
  "partial": true
}
```

Le même paquet présente une méthode d’application d’une couche d’adhésion et le résultat rapporté « Ok Good contrast » pour une configuration de couches et charges. Ces observations sont documentées dans le projet ; elles **ne sont pas attribuées au durcissement à l’air**. `verified_quote` qualifie le statut de la citation persistée, pas une validation humaine de l’interprétation ni une relation au verrou. Les indications de temporalité sont conservées lorsqu’elles existent ; les entités canoniques peuvent rester `unclear`, et les extractions détaillées restent récupérables.

Les deux projections complètes anonymisées sont conservées localement dans `.codex_tmp/diagnostic-link-transfer-validation/anonymized-prepared-contexts.json`, hors versionnement. Les identités projet/organisme sont masquées et les termes techniques conservés.

## Tests et protections

- **424 tests réussis** : V4, V3 et V3.1, dont 18 nouveaux cas pour cette correction.
- **7 cas sur l’archive réelle** : décisions humaines, collisions et provenance des preuves anciennes, cartes/paragraphes, filtrage du V2, récupération ciblée, survie du contexte projet dans une comparaison bornée et coexistence avec les preuves des publications.
- **11 cas déterministes complémentaires** : alias de citation, collisions non résolubles, relations explicites/absentes/non résolues, citations contradictoires, comparaison avec faits généraux, incertitude du blueprint, faits ultérieurs du consultant et contrat Express.
- Les publications des tests de comparaison sont des fixtures scientifiques hors ligne, pas de nouvelles publications collectées. Les lignes projet sont celles de l’archive réelle.
- Les tests bloquent les connexions externes et les appels payants. Aucun changement d’EnnoDiagnostic ou de l’interface.
- L’empreinte SHA-256 de la SQLite active est identique avant et après les vérifications ; `config.py` et la référence Git `sauv-sans-agent-1` sont également inchangés.

Fichiers de cette correction : `agents/EnnoScholarV4/diagnostic_link.py`, `agents/EnnoScholarV4/argumentation.py`, `agents/EnnoScholarV4/toolkit.py`, `backend_api/routers/scholar_v4.py`, trois fichiers de tests et la documentation V4. Les autres changements locaux de l’intégration précédente sont préservés.

## Premier essai réel

Le code est prêt pour lancer un nouveau diagnostic, puis essayer le parcours lié. Le backend actif étant un processus sans rechargement automatique, il faut le redémarrer avec son lanceur habituel avant l’essai ; aucune restauration ni modification de configuration de base n’est nécessaire.

Vérifier dans l’essai : décisions « Garder », choix d’un seul verrou, requêtes centrées sur ce verrou, distinction entre général et attribué, récupération de citations et confrontation aux articles. La qualité sémantique des nouvelles sorties et des comparaisons du modèle reste à valider humainement. Les tests ne constituent pas un essai LLM réel ni une garantie de pertinence scientifique ou d’éligibilité CIR. Un snapshot lié antérieur à cette correction conserve sa source ; son actualisation reste explicite.
