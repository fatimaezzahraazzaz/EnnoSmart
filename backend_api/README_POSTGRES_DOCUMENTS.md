# EnnoSmart V5 — Documents / Upload + PostgreSQL

## 1. Fichiers backend à remplacer/ajouter

Dans :

```text
C:\EnnoSmart\backend_api
```

Remplace :

```text
routers/documents.py
```

Ajoute :

```text
.env.postgres.example
scripts/init_postgres_database.py
scripts/test_database.py
```

## 2. Ce que corrige Documents / Upload

Avant :

```text
Documents = 0
```

même si les fichiers existaient dans :

```text
C:\EnnoSmart\outputs\safe_rag_upload\Girodin\TGM100\2023\uploaded
```

Maintenant tu as deux possibilités :

### A. Upload réel via frontend

La page Upload appelle :

```text
POST /projects/{project_id}/documents/upload
```

### B. Importer les documents déjà présents dans outputs

Le backend ajoute :

```text
POST /projects/{project_id}/documents/import-existing
```

Il scanne :

```text
outputs/safe_rag_upload/{organisme}/{projet}/{annee}/uploaded
outputs/safe_rag_upload/{organisme}/{projet}/{annee}/raw
outputs/safe_rag_upload/{organisme}/{projet}/{annee}/documents
outputs/safe_rag_upload/{organisme}/{projet}/{annee}/input
```

Puis il crée les lignes dans la table `documents`.

## 3. Passer à PostgreSQL

### Étape 1 — Créer la base

Copie `.env.postgres.example` vers `.env` :

```powershell
cd C:\EnnoSmart\backend_api
copy .env.postgres.example .env
```

Puis vérifie dans `.env` :

```env
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/ennosmart
```

Adapte le mot de passe si ton PostgreSQL n’utilise pas `postgres`.

Crée la base :

```powershell
.\.venv\Scripts\activate
python -m scripts.init_postgres_database
```

### Étape 2 — Créer les tables

Lance :

```powershell
python -m scripts.test_database
```

Tu dois voir :

```text
Connexion DB OK
users: 0
projects: 0
documents: 0
...
```

### Étape 3 — Créer ton utilisateur consultant

```powershell
python -m scripts.create_dev_user
```

### Étape 4 — Lancer FastAPI

```powershell
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## 4. Important

Quand tu passes de SQLite à PostgreSQL, c’est normal que les anciens projets disparaissent, car c’est une nouvelle base.

Tu dois refaire :

```text
1. Login
2. Créer projet Girodin / TGM100 / 2023
3. Import diagnostic existant
4. Sync verrous
5. Import scholar existant
6. Sync articles
7. Import documents existants
```

Ensuite le frontend affichera tout depuis PostgreSQL.
