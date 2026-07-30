# EnnoSmart Backend API

Backend FastAPI avec :

- Authentification JWT
- Consultants / utilisateurs
- Projets liés à chaque consultant
- Upload sécurisé de documents
- Lecture des résultats IA depuis `outputs/safe_rag_upload`
- Endpoints EnnoDiagnostic
- Endpoints EnnoScholar
- PostgreSQL possible
- SQLite possible pour tester rapidement

## 1. Installation

Dans PowerShell :

```powershell
cd C:\EnnoSmart
mkdir backend_api
```

Copie tous les fichiers de ce dossier dans :

```text
C:\EnnoSmart\backend_api
```

Puis :

```powershell
cd C:\EnnoSmart\backend_api
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

## 2. Lancer rapidement avec SQLite

Par défaut `.env.example` utilise :

```text
DATABASE_URL=sqlite:///./ennosmart_dev.db
```

Donc tu peux lancer directement :

```powershell
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Ouvre :

```text
http://127.0.0.1:8000/docs
```

## 3. Utiliser PostgreSQL

Dans `.env`, remplace :

```text
DATABASE_URL=sqlite:///./ennosmart_dev.db
```

par :

```text
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/ennosmart
```

Assure-toi que la base `ennosmart` existe dans PostgreSQL.

## 4. Test rapide

Créer un consultant :

```http
POST /auth/register
```

Body :

```json
{
  "full_name": "Sophie Consultant",
  "email": "sophie@ennosmart.local",
  "password": "password123"
}
```

Login :

```http
POST /auth/login
```

Body :

```json
{
  "email": "sophie@ennosmart.local",
  "password": "password123"
}
```

Copie `access_token`, puis dans Swagger clique sur `Authorize` :

```text
Bearer TON_TOKEN
```

## 5. Créer un projet

```http
POST /projects
```

Body :

```json
{
  "organisme": "Girodin",
  "project_name": "TGM100",
  "year": "2023",
  "domain_label": "Génie mécanique"
}
```

## 6. Lire les résultats IA

Si tu as déjà ce dossier :

```text
C:\EnnoSmart\outputs\safe_rag_upload\Girodin\TGM100\2023
```

Alors l’endpoint suivant peut lire les JSON :

```http
GET /projects/{project_id}/diagnostic/latest
GET /projects/{project_id}/scholar/latest
```

## 7. Sécurité importante

Chaque projet possède un `consultant_id`.

Le backend vérifie à chaque route :

```text
project.consultant_id == current_user.id
```

Donc un consultant ne voit pas les projets d’un autre consultant.

## 8. Connexion frontend

Le frontend Next.js devra appeler :

```text
POST http://127.0.0.1:8000/auth/login
GET  http://127.0.0.1:8000/projects
GET  http://127.0.0.1:8000/projects/{id}/diagnostic/latest
```

avec le header :

```text
Authorization: Bearer TON_TOKEN
```


## Correction importante

Tu peux créer l'utilisateur de test avec :

```powershell
python scripts/create_dev_user.py
```

ou :

```powershell
python -m scripts.create_dev_user
```

Dans Swagger, après `/auth/login`, copie seulement `access_token`.
Clique sur `Authorize`, colle uniquement le token, sans écrire `Bearer`.


## Fix bcrypt / passlib

Si tu vois :

```text
ValueError: password cannot be longer than 72 bytes
```

ou :

```text
error reading bcrypt version
```

corrige avec :

```powershell
pip uninstall -y bcrypt
pip install bcrypt==4.0.1
python -m scripts.create_dev_user
```

Ce n'est pas le mot de passe qui est faux. C'est une incompatibilité de version entre `passlib` et `bcrypt`.
