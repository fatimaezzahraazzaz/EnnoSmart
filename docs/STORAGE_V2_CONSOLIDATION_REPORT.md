# Storage V2 — rapport de consolidation

Date : 15 septembre 2026

## Résultat

- CIR réellement indexés : **626/626** dans Storage V2.
- Emplacements de bibliothèque sans fichier : **104** (les 730 lignes du catalogue ne représentent donc pas 730 documents).
- Documents projet : **26/26** dans Storage V2.
- Vérification finale : **652 migrés / 652 vérifiés**, zéro erreur, SHA-256 identiques.
- Téléchargement réel après nettoyage : HTTP 200, 1 634 712 octets, SHA-256 `d4890beae88e41895df1a0f26c95cd173845694bfba7e2fb68f31bdc28f723b7`.

## Organisation finale

Racine permanente et runtime unique :

`C:\EnnoSmartData\object_storage_v2`

- `organismes/.../sha256/...` : objets permanents adressés par hash.
- `runtime/organismes` : artefacts opérationnels encore lus par l'application.
- `runtime/experience_memory_v2` : catalogue, cartes, NLP, chunks et rapports nécessaires.
- `runtime/power_automate_import` : métadonnées d'audit, jamais la source OneDrive.
- caches et previews : TTL de 24 heures.

Les deux anciennes racines n'existent plus :

- `C:\EnnoSmart\storage`
- `C:\EnnoSmartData\storage`

## Nettoyage exécuté

- Temporaires/staging : **9 291 fichiers, 18 245 088 860 octets**.
- Anciennes copies `power_automate_import`, `experience_memory_v2` et `cir_index_preparation` : **41 889 fichiers, 26 420 753 283 octets**.
- Archives, sauvegardes, corbeille, ancien stockage projet et anciens Chroma locaux : **12 085 fichiers, 11 162 600 195 octets**.
- Total supprimé : **63 265 fichiers, 55 828 442 338 octets (51,99 Gio)**.

Nettoyage complémentaire du dépôt et des anciens runtimes : anciens jeux de
données d'entraînement, pages Streamlit, scripts ponctuels, modules inactifs,
cache Hugging Face, cache `backend_api/storage`, annotations, journaux, sorties
et rapports intermédiaires. `.venv` a été reconstruit et validé ;
`.venv_py314` et `.venv-mcp` sont conservés en attente d'une éventuelle
suppression explicite.

Le dernier résidu hors Storage V2 a également été retiré : 18 rapports/logs
(79 976 140 octets), une ancienne sortie (29 103 octets) et un cache vide.
Les variables encore dirigées vers `C:\EnnoSmart\storage` ont été redirigées
vers `object_storage_v2\runtime`; un redémarrage complet confirme que l'ancien
dossier n'est plus recréé.

OneDrive/SharePoint n'a été ni modifié ni supprimé. Il reste la source externe en lecture seule.

## Inventaire final `C:\EnnoSmartData`

- `object_storage_v2` : 36 414 fichiers, 6 397 659 823 octets (5,958 Gio).
- Aucun autre dossier à la racine.
- `C:\EnnoSmart\storage` : absent après redémarrage.
- `C:\EnnoSmartData\storage` : absent.

## État des services

- Backend : HTTP 200 avec le `.venv` principal reconstruit.
- PostgreSQL : 26 documents et 626 CIR avec métadonnées Storage V2.
- Chroma : l'application est configurée exclusivement en mode HTTP ; les anciennes bases locales ont été retirées. Le service central doit être démarré via son environnement Docker pour les fonctions RAG. Lors du dernier contrôle, Docker/Chroma et le frontend étaient arrêtés indépendamment du nettoyage.

## Preuves

- Apply final sans erreur : `storage_v2_reports/storage_v2_migration_20260914_205003_apply.json`.
- Nettoyage 24 h : `storage_v2_reports/storage_v2_cleanup_20260914_205928_apply.json`.
- Verify final après consolidation : `storage_v2_reports/storage_v2_migration_20260915_093427_verify.json`.
