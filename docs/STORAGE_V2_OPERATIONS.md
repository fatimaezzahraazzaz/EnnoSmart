# Exploitation Storage V2

## Activation OVH

Le déploiement standard utilise le volume persistant OVH monté dans le
conteneur :

```dotenv
ENNOSMART_STORAGE_V2_ENABLED=true
ENNOSMART_OBJECT_STORAGE_PROVIDER=local
ENNOSMART_STORAGE_V2_LOCAL_ROOT=/var/lib/ennosmart/object_storage_v2
ENNOSMART_STORAGE_ROOT=/var/lib/ennosmart/object_storage_v2/runtime
```

S3 reste disponible en remplaçant le provider et en renseignant :

```dotenv
ENNOSMART_OBJECT_STORAGE_PROVIDER=s3
ENNOSMART_S3_ENDPOINT=https://<endpoint-s3>
ENNOSMART_S3_REGION=<region>
ENNOSMART_S3_BUCKET=<bucket>
ENNOSMART_S3_ACCESS_KEY=<secret>
ENNOSMART_S3_SECRET_KEY=<secret>
ENNOSMART_S3_SECURE=true
```

Le bucket et les identifiants doivent être testés avec un objet non métier
avant toute migration S3.

## Séquence de migration

Depuis `C:\EnnoSmart` et l'environnement Python du backend :

```powershell
python backend_api/scripts/migrate_storage_v2.py --provider s3 --report-dir storage_v2_reports
python backend_api/scripts/migrate_storage_v2.py --apply --provider s3 --report-dir storage_v2_reports
python backend_api/scripts/migrate_storage_v2.py --verify --provider s3 --report-dir storage_v2_reports
```

La première commande ne modifie rien. `--apply` copie et enregistre les métadonnées, mais ne supprime pas les sources. `--verify` doit terminer avec `verified_equals_total_migrated: true` et zéro erreur réelle.

## Nettoyage

Prévisualiser uniquement :

```powershell
python backend_api/scripts/storage_v2_cleanup.py --report-dir storage_v2_reports
```

La suppression TTL explicite est disponible avec `--apply`, mais elle ne doit être utilisée qu'après validation des rapports de migration, PostgreSQL, Chroma et récupération des documents. Les entrées `NEEDS_REVIEW` ne sont jamais supprimées.

## Retour arrière

Après la consolidation du 14 septembre 2026, les anciennes copies locales ont
été supprimées. Ne pas désactiver Storage V2 : un retour arrière nécessite une
restauration externe préalable. Les objets Storage V2 et PostgreSQL doivent
toujours être sauvegardés ensemble.
