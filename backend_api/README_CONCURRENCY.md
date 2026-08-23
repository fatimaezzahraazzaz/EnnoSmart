# Concurrence multi-utilisateur EnnoSmart

La configuration cible 20 utilisateurs ou davantage :

- authentification JWT sans session serveur globale ;
- pool PostgreSQL de 20 connexions + 20 connexions temporaires ;
- 80 threads FastAPI pour que les lectures restent disponibles pendant les tâches longues ;
- 8 appels LLM simultanés au maximum, partagés entre FastAPI et Celery via Redis ;
- 4 tâches par worker Scholar et 4 tâches par worker CIR ;
- une seule mutation à la fois pour une conversation donnée, sans bloquer les autres conversations.

## Services requis

PostgreSQL est requis en production. SQLite reste adapté uniquement aux tests locaux, car ses écritures sont sérialisées.

Installer d'abord les dépendances du backend dans l'environnement Python utilisé par FastAPI et Celery :

```powershell
python -m pip install -r C:\EnnoSmart\backend_api\requirements.txt
```

Redis doit être actif pour la limite LLM distribuée, les verrous multi-processus et les tâches Celery :

```powershell
docker compose -f C:\EnnoSmart\docker-compose.cir-workers.yml up -d redis
```

Puis lancer les deux familles de workers :

```powershell
powershell -ExecutionPolicy Bypass -File C:\EnnoSmart\start_celery_worker.ps1
powershell -ExecutionPolicy Bypass -File C:\EnnoSmart\start_cir_worker_windows.ps1
```

Le endpoint `GET /health` expose les compteurs de capacité du pool web, de la base et du LLM, sans exposer d'URL ni de secret de base de données.

## Réglages principaux

```dotenv
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=20
WEB_THREAD_LIMIT=80
ENNOSMART_LLM_MAX_CONCURRENCY=8
ENNOSMART_LLM_QUEUE_TIMEOUT_SECONDS=900
ENNOSCHOLAR_CELERY_CONCURRENCY=4
ENNOSMART_CIR_WORKER_CONCURRENCY=4
```

Augmenter la concurrence LLM uniquement si le quota du fournisseur et la capacité PostgreSQL le permettent. La valeur est globale à l'application lorsque Redis est disponible, pas une valeur par utilisateur.
