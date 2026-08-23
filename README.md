# EnnoSmart — branche de déploiement

Cette branche contient l’application de production : API FastAPI, interface
Next.js, agents EnnoDiagnostic/EnnoScholar/EnnoAmelioration, workers Celery,
RAG/Chroma et serveur MCP légal de récupération d’articles.

Elle ne contient pas les bases PostgreSQL, les index Chroma, les documents
clients, les sorties IA, les secrets, les caches de modèles, les tests, les
intégrations temporaires ni les sauvegardes. Ces données doivent être créées ou
restaurées directement sur le serveur.

## Versions recommandées

- Ubuntu 24.04 LTS x86_64 ;
- Python 3.12 ;
- Node.js 20.9 ou supérieur et npm 10+ ;
- Docker Engine avec le plugin Compose ;
- au moins 16 Go de RAM pour le profil CPU de base (davantage si les modèles
  lourds sont activés).

Python 3.14 n’est pas la cible de déploiement : plusieurs bibliothèques ML/OCR
n’y publient pas encore toutes leurs roues binaires.

## Installation système (Ubuntu)

```bash
sudo apt update
sudo apt install -y \
  python3.12 python3.12-venv python3.12-dev build-essential libpq-dev \
  tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng \
  ffmpeg libreoffice poppler-utils default-jre \
  libcairo2 libpango-1.0-0 libgdk-pixbuf-2.0-0
```

Docker fournit PostgreSQL, Redis et GROBID avec
`docker-compose.research.yml`. Node.js doit être installé depuis une source qui
fournit une version compatible avec `frontend/package-lock.json`.

## Installation Python complète

Depuis la racine clonée :

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

`requirements.txt` est la source canonique utilisée aussi par les fichiers
`backend_api/requirements.txt` et
`mcp_servers/legal_fulltext_mcp/requirements.txt`.

Le profil par défaut inclut l’API, PostgreSQL, Redis/Celery, Chroma, les
embeddings, les formats bureautiques, Tesseract, faster-whisper, Ollama et le
MCP. Les capacités lourdes ou hors service web principal (Surya,
WhisperX/diarisation, Pix2Tex, Pydantic AI, vision Qwen locale, anciennes pages
Streamlit et outils d’entraînement) sont isolées :

```bash
.venv/bin/python -m pip install -r requirements-optional.txt
```

Pour une machine NVIDIA, installer d’abord la version de PyTorch correspondant
exactement au pilote/CUDA du serveur, puis relancer les requirements.

## Configuration

```bash
cp .env.example .env
chmod 600 .env
mkdir -p storage/uploads outputs/safe_rag_upload logs
```

Pour un lancement manuel dans un shell, charger les variables avant les
commandes ci-dessous :

```bash
set -a
. ./.env
set +a
```

Avec systemd, utiliser plutôt `EnvironmentFile=/opt/ennosmart/.env` dans chaque
unité de service.

Modifier au minimum dans `.env` :

- `SECRET_KEY` ;
- `POSTGRES_PASSWORD` et le même mot de passe dans `DATABASE_URL` ;
- `FRONTEND_URL` et `CORS_ORIGINS` ;
- le fournisseur LLM choisi et sa clé ;
- `UNPAYWALL_EMAIL`/`CROSSREF_MAILTO` pour la recherche scientifique ;
- les chemins `/opt/ennosmart` si le clone est installé ailleurs.

Le frontend reçoit son URL d’API au moment du build :

```bash
cp frontend/.env.production.example frontend/.env.production
```

Remplacer ensuite `NEXT_PUBLIC_API_URL` par l’URL HTTPS publique de l’API.

## PostgreSQL, Redis et GROBID

```bash
docker compose -f docker-compose.research.yml up -d
docker compose -f docker-compose.research.yml ps
```

Les volumes Docker `ennosmart_postgres_data` et `ennosmart_redis_data` restent
sur le serveur et ne sont jamais envoyés dans Git. Au premier démarrage, l’API
crée les tables absentes. Pour une restauration, importer le dump PostgreSQL
avant de lancer l’API.

## Modèles et Chroma

Le modèle applicatif FastJudge requis est versionné sous :

```text
models/fastjudge/fastjudge_linearsvc_C025.joblib
```

Les modèles publics volumineux ne sont pas versionnés. Avec
`ENNOSMART_EMBEDDING_OFFLINE=0`, ils sont téléchargés dans `HF_HOME` au premier
usage. Il est préférable de les précharger pendant le déploiement, puis de
passer `ENNOSMART_EMBEDDING_OFFLINE=1` si le serveur doit fonctionner sans
accès réseau.

Les index Chroma sont recréés sous `storage/**/chroma/`. Pour migrer un serveur
existant, copier les documents et sorties utiles puis reconstruire les index ;
ne pas committer `chroma.sqlite3`.

## Build du frontend

```bash
cd frontend
npm ci
npm run build
cd ..
```

Le dépôt utilise `package-lock.json` comme lockfile de production.

## Commandes de lancement

Les processus Python ont besoin de la racine et de `backend_api` dans le
`PYTHONPATH`. Ces commandes sont prévues pour être reprises dans systemd,
Supervisor ou un autre gestionnaire de processus.

API :

```bash
cd /opt/ennosmart
PYTHONPATH=/opt/ennosmart:/opt/ennosmart/backend_api \
  .venv/bin/uvicorn backend_api.main:app \
  --host 127.0.0.1 --port 8000 --workers 1
```

Worker EnnoScholar :

```bash
cd /opt/ennosmart
PYTHONPATH=/opt/ennosmart:/opt/ennosmart/backend_api \
  .venv/bin/celery -A backend_api.worker.celery_app:celery_app worker \
  --loglevel=INFO --pool=threads --concurrency=4
```

Worker CIR :

```bash
cd /opt/ennosmart
PYTHONPATH=/opt/ennosmart:/opt/ennosmart/backend_api \
  .venv/bin/celery -A backend_api.workers.celery_app:celery_app worker \
  --loglevel=INFO --pool=threads --concurrency=4 -Q ennosmart.cir
```

MCP légal :

```bash
cd /opt/ennosmart
PYTHONPATH=/opt/ennosmart:/opt/ennosmart/backend_api \
  .venv/bin/python -m mcp_servers.legal_fulltext_mcp.server
```

Frontend :

```bash
cd /opt/ennosmart/frontend
npm start -- --hostname 127.0.0.1 --port 3000
```

Nginx (ou le proxy OVH) doit terminer TLS et router le domaine applicatif vers
le port 3000 et le domaine API vers le port 8000. Les ports PostgreSQL, Redis,
GROBID et MCP sont liés à `127.0.0.1` et ne doivent pas être exposés au public.

## Contrôles après démarrage

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8010/health
curl --fail http://127.0.0.1:3000/
docker compose -f docker-compose.research.yml ps
```

La documentation OpenAPI est disponible sur `http://127.0.0.1:8000/docs` si
elle n’est pas filtrée par le proxy.

## Données à sauvegarder hors Git

- dump PostgreSQL ;
- `storage/` (documents, Chroma, mémoire, caches et modèles téléchargés) ;
- `outputs/` si les artefacts générés doivent être conservés ;
- le vrai `.env`, dans un coffre de secrets ;
- les éventuels modèles privés supplémentaires.

Ne jamais sauvegarder une base active en copiant seulement son répertoire :
utiliser `pg_dump` pour PostgreSQL et arrêter les processus avant toute copie
brute d’un index Chroma.
