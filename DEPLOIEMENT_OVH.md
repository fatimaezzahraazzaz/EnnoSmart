# Déploiement OVH d’EnnoSmart

Ce guide déploie le code dans des images Docker et conserve PostgreSQL, Chroma,
les sources, les JSON NLP et les sorties validées hors du dépôt Git.

## 1. Ce qui doit être poussé dans Git

À pousser :

- `agents/`, `backend_api/`, `modules/`, `mcp_servers/` et `frontend/` ;
- `requirements.txt`, `requirements-optional.txt` et `pyproject.toml` ;
- `frontend/package.json` et `frontend/package-lock.json` ;
- `models/fastjudge/fastjudge_linearsvc_C025.joblib` ;
- `.env.example`, `.env.windows.example`, `.dockerignore`, `.gitignore` ;
- `docker-compose.ovh.yml`, `docker-compose.windows.yml`, `deploy/ovh/` et
  `scripts/deployment/`.

À ne jamais pousser :

- `.env` et `backend_api/.env` ;
- `storage/`, `outputs/`, Chroma, les JSON NLP et les documents clients ;
- les sauvegardes PostgreSQL (`*.dump`) et les archives de migration ;
- `.venv*`, `node_modules`, `.next`, les caches Hugging Face et les modèles
  téléchargés ;
- les clés API, mots de passe, certificats et journaux.

Contrôle local avant le commit, dans PowerShell :

```powershell
cd C:\EnnoSmart
git check-ignore -v .env backend_api/.env storage outputs
git status --short
git add -A
git diff --cached --stat
git diff --cached --name-only |
  Select-String -Pattern '(^|/)(\.env$|storage/|outputs/|chroma/)|\.dump$|\.sqlite3$'
```

La dernière commande ne doit afficher aucun secret, document client, Chroma ou
dump. Si le contrôle est propre :

```powershell
git commit -m "Prepare EnnoSmart OVH persistent deployment"
git push origin NOM_DE_LA_BRANCHE
```

`git add -A` inclut également les suppressions de l’ancien nettoyage. Vérifier
le résumé avant de valider le commit.

## Test complet après clonage sur un autre PC Windows

Un clone Git seul démarre une application vide : Git contient le code, mais
jamais la base client, Chroma, les JSON NLP ni les secrets. Pour retrouver les
projets existants, transmettre séparément `ennosmart.dump` et
`ennosmart-data.tgz`.

Installer Git et Docker Desktop avec le moteur WSL 2, puis dans PowerShell :

```powershell
git clone URL_DU_DEPOT C:\EnnoSmart
cd C:\EnnoSmart
Copy-Item .env.windows.example .env
notepad .env
New-Item -ItemType Directory -Force C:\EnnoSmartData
```

Dans `.env`, remplacer au minimum `SECRET_KEY`, `POSTGRES_PASSWORD`,
`OPENAI_API_KEY` et les adresses e-mail des fournisseurs scientifiques. Ne
jamais renvoyer ce fichier dans Git.

Extraire ensuite les données transmises dans le dossier externe :

```powershell
tar.exe -xzf C:\CHEMIN\ennosmart-data.tgz -C C:\EnnoSmartData
```

Construire les images et démarrer uniquement les services techniques :

```powershell
docker compose -f docker-compose.ovh.yml -f docker-compose.windows.yml build
docker compose -f docker-compose.ovh.yml -f docker-compose.windows.yml `
  up -d postgres redis grobid
```

Restaurer la base avant le premier démarrage de l’API :

```powershell
$pg = docker compose -f docker-compose.ovh.yml -f docker-compose.windows.yml ps -q postgres
docker cp C:\CHEMIN\ennosmart.dump "${pg}:/tmp/ennosmart.dump"
docker compose -f docker-compose.ovh.yml -f docker-compose.windows.yml `
  exec postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl /tmp/ennosmart.dump'
```

Valider les chemins et Chroma, puis lancer tout le projet :

```powershell
docker compose -f docker-compose.ovh.yml -f docker-compose.windows.yml `
  run --rm api python /opt/ennosmart/scripts/deployment/normalize_database_paths.py
docker compose -f docker-compose.ovh.yml -f docker-compose.windows.yml `
  run --rm api python /opt/ennosmart/scripts/deployment/normalize_database_paths.py --apply
docker compose -f docker-compose.ovh.yml -f docker-compose.windows.yml `
  run --rm api python /opt/ennosmart/scripts/deployment/verify_runtime_paths.py
docker compose -f docker-compose.ovh.yml -f docker-compose.windows.yml up -d
```

Ouvrir `http://127.0.0.1:3000`. Sans dump et sans l’archive de données,
l’interface peut démarrer mais elle ne retrouvera pas les anciens projets ni
leurs sources. Les volumes Docker nommés de PostgreSQL et Redis survivent aux
redémarrages ; ne pas lancer `docker compose down -v`.

## 2. Données à transférer séparément de Git

Trois éléments doivent être sauvegardés et envoyés au serveur par SCP, SFTP,
rsync ou OVH Object Storage :

1. un dump PostgreSQL au format custom ;
2. `C:\EnnoSmartData\storage` en entier, qui contient notamment Chroma,
   `nlp_result.json`, `chunks.json`, les sources et les mémoires ;
3. `C:\EnnoSmartData\outputs` si des sorties historiques y sont encore utiles.

Ne jamais transférer uniquement `chroma.sqlite3`. Un répertoire Chroma comprend
aussi des sous-répertoires UUID indispensables. Il faut copier l’arborescence
Chroma complète, services arrêtés.

## 3. Sauvegarde sur la machine Windows actuelle

Arrêter FastAPI, Next.js et les workers Celery avant la copie. PostgreSQL peut
rester démarré pendant `pg_dump`, mais aucun agent ne doit modifier Chroma.

Créer un dossier de migration hors du dépôt :

```powershell
New-Item -ItemType Directory -Force C:\EnnoSmartMigration
```

Sauvegarder PostgreSQL avec les outils PostgreSQL installés sur Windows :

```powershell
$env:PGPASSWORD = "MOT_DE_PASSE_POSTGRES_LOCAL"
& "C:\Program Files\PostgreSQL\17\bin\pg_dump.exe" `
  -h 127.0.0.1 -p 5432 -U UTILISATEUR_LOCAL `
  -d NOM_BASE_LOCALE -Fc --no-owner --no-acl `
  -f C:\EnnoSmartMigration\ennosmart.dump
Remove-Item Env:PGPASSWORD
```

Archiver le stockage externe déjà préparé :

```powershell
tar.exe -czf C:\EnnoSmartMigration\ennosmart-data.tgz `
  -C C:\EnnoSmartData storage outputs
```

Si Windows Defender bloque un ancien cache HTML, exclure uniquement les caches
HTML régénérables ; ne pas exclure `documents`, `nlp`, `rag`, `chroma`,
`experience_memory_v2` ou les PDF gardés.

Calculer les empreintes avant le transfert :

```powershell
Get-FileHash C:\EnnoSmartMigration\ennosmart.dump -Algorithm SHA256
Get-FileHash C:\EnnoSmartMigration\ennosmart-data.tgz -Algorithm SHA256
```

## 4. Préparer le serveur OVH

Configuration conseillée : Ubuntu 24.04 LTS, au moins 4 vCPU, 16 Go de RAM et
un volume persistant dimensionné selon le corpus. Le stockage local actuel
dépassant déjà 25 Go avec les mémoires, prévoir au moins 100 Go. Debian 12
convient aussi, mais il faut alors suivre la variante Debian du dépôt Docker
officiel.

Installer les outils de l’hôte :

```bash
sudo apt update
sudo apt install -y ca-certificates curl git nginx certbot python3-certbot-nginx
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker nginx
sudo docker run --rm hello-world
sudo docker compose version
sudo usermod -aG docker "$USER"
```

Se déconnecter puis se reconnecter après l’ajout au groupe Docker.

### Volume persistant OVH

Attacher un volume Block Storage OVH. Identifier soigneusement le nouveau
périphérique avec `lsblk -f`. Ne jamais formater un disque contenant déjà des
données. Pour un volume neuf seulement :

```bash
sudo mkfs.ext4 /dev/NOUVEAU_VOLUME
sudo mkdir -p /mnt/ennosmart-data
sudo mount /dev/NOUVEAU_VOLUME /mnt/ennosmart-data
sudo blkid /dev/NOUVEAU_VOLUME
```

Ajouter son UUID dans `/etc/fstab`, puis créer l’arborescence :

```bash
sudo mkdir -p /mnt/ennosmart-data/{storage,outputs,cache,logs,postgres,redis}
sudo chown -R 10001:10001 /mnt/ennosmart-data/{storage,outputs,cache,logs}
sudo chmod -R 750 /mnt/ennosmart-data/{storage,outputs,cache,logs}
sudo chown 70:70 /mnt/ennosmart-data/postgres
sudo chmod 700 /mnt/ennosmart-data/postgres
sudo chown 999:999 /mnt/ennosmart-data/redis
sudo chmod 750 /mnt/ennosmart-data/redis
```

L’UID `10001` correspond à l’utilisateur non privilégié de l’image backend.
Les UID `70` et `999` correspondent respectivement aux images Alpine de
PostgreSQL et Redis utilisées par ce compose. Ne pas appliquer un `chown -R
10001` sur les dossiers `postgres` et `redis`.

## 5. Cloner le code et créer le `.env`

```bash
sudo mkdir -p /opt/ennosmart
sudo chown "$USER":"$USER" /opt/ennosmart
git clone URL_DU_DEPOT /opt/ennosmart
cd /opt/ennosmart
cp .env.example .env
chmod 600 .env
nano .env
```

Le vrai `.env` reste uniquement sur OVH. Renseigner au minimum :

```dotenv
ENV=production
ENNOSMART_ROOT=/opt/ennosmart
ENNOSMART_BASE_DIR=/opt/ennosmart
ENNOSMART_DATA_ROOT=/var/lib/ennosmart
ENNOSMART_STORAGE_ROOT=/var/lib/ennosmart/storage
UPLOAD_ROOT=/var/lib/ennosmart/storage/uploads
AI_OUTPUT_ROOT=/var/lib/ennosmart/outputs/safe_rag_upload
ENNOSMART_EXPERIENCE_MEMORY_V2_DIR=/var/lib/ennosmart/storage/experience_memory_v2
ENNOSMART_MEMORY_V2_ROOT=/var/lib/ennosmart/storage/organismes
ENNOSMART_LOG_ROOT=/var/lib/ennosmart/logs
ENNOSMART_CACHE_ROOT=/var/lib/ennosmart/cache
ENNOSMART_HOST_DATA_DIR=/mnt/ennosmart-data
ENNOSMART_POSTGRES_DATA_DIR=/mnt/ennosmart-data/postgres
ENNOSMART_REDIS_DATA_DIR=/mnt/ennosmart-data/redis

POSTGRES_DB=ennosmart
POSTGRES_USER=ennosmart
POSTGRES_PASSWORD=SECRET_HEXADECIMAL_SANS_CARACTERE_URL_SPECIAL
SECRET_KEY=SECRET_LONG_ALEATOIRE

FRONTEND_URL=https://app.votre-domaine.fr
CORS_ORIGINS=https://app.votre-domaine.fr
NEXT_PUBLIC_API_URL=https://app.votre-domaine.fr/api

OPENAI_API_KEY=VOTRE_CLE
UNPAYWALL_EMAIL=adresse@entreprise.fr
CROSSREF_MAILTO=adresse@entreprise.fr
```

Générer des secrets sans caractère problématique dans une URL :

```bash
openssl rand -hex 32
openssl rand -hex 48
```

Compléter ensuite les fournisseurs réellement utilisés dans `.env.example`.

## 6. Transférer et restaurer Chroma et les JSON NLP

Depuis Windows :

```powershell
scp C:\EnnoSmartMigration\ennosmart.dump UTILISATEUR@SERVEUR:/tmp/
scp C:\EnnoSmartMigration\ennosmart-data.tgz UTILISATEUR@SERVEUR:/tmp/
```

Sur OVH, vérifier les SHA-256 puis extraire :

```bash
sha256sum /tmp/ennosmart.dump /tmp/ennosmart-data.tgz
sudo tar -xzf /tmp/ennosmart-data.tgz -C /mnt/ennosmart-data
sudo chown -R 10001:10001 /mnt/ennosmart-data/{storage,outputs,cache,logs}
sudo chmod -R u+rwX,g+rX,o-rwx /mnt/ennosmart-data/{storage,outputs,cache,logs}
```

Ne pas démarrer les workers avant le contrôle Chroma.

## 7. Construire les images et restaurer PostgreSQL

```bash
cd /opt/ennosmart
docker compose -f docker-compose.ovh.yml build
docker compose -f docker-compose.ovh.yml up -d postgres redis grobid
docker compose -f docker-compose.ovh.yml ps
```

Copier le dump dans PostgreSQL puis restaurer la base vide :

```bash
POSTGRES_CONTAINER=$(docker compose -f docker-compose.ovh.yml ps -q postgres)
docker cp /tmp/ennosmart.dump "$POSTGRES_CONTAINER:/tmp/ennosmart.dump"
docker compose -f docker-compose.ovh.yml exec postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl /tmp/ennosmart.dump'
```

Contrôler les données :

```bash
docker compose -f docker-compose.ovh.yml exec postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT COUNT(*) AS projects FROM projects; SELECT COUNT(*) AS documents FROM documents; SELECT COUNT(*) AS articles FROM articles;"'
```

Normaliser d’abord les anciens chemins Windows en mode lecture seule :

```bash
docker compose -f docker-compose.ovh.yml run --rm api \
  python /opt/ennosmart/scripts/deployment/normalize_database_paths.py
```

Lire le résultat, puis appliquer :

```bash
docker compose -f docker-compose.ovh.yml run --rm api \
  python /opt/ennosmart/scripts/deployment/normalize_database_paths.py --apply
```

Le script ne modifie pas les chemins OneDrive externes. Il normalise uniquement
les chemins applicatifs absolus de `storage`, `outputs` et `ai_folder`.

## 8. Vérifier Chroma et les chemins persistants

```bash
docker compose -f docker-compose.ovh.yml run --rm api \
  python /opt/ennosmart/scripts/deployment/verify_runtime_paths.py
```

Le résultat doit finir par `RUNTIME_STORAGE_OK`. Le script vérifie que les
sorties sont hors du code, que le volume est inscriptible, compte les JSON NLP
et exécute `PRAGMA quick_check` en lecture seule sur chaque `chroma.sqlite3`.

## 9. Démarrer EnnoSmart

```bash
docker compose -f docker-compose.ovh.yml up -d
docker compose -f docker-compose.ovh.yml ps
docker compose -f docker-compose.ovh.yml logs --tail=200 api scholar-worker cir-worker legal-mcp
curl http://127.0.0.1:8000/health
curl -I http://127.0.0.1:3000
```

Le premier traitement peut télécharger les modèles Hugging Face dans
`/var/lib/ennosmart/cache/huggingface`. Ce cache reste sur le volume et ne sera
pas retéléchargé après chaque déploiement.

## 10. Nginx et HTTPS

Exemple `/etc/nginx/sites-available/ennosmart` :

```nginx
server {
    listen 80;
    server_name app.votre-domaine.fr;

    client_max_body_size 100m;

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/ennosmart /etc/nginx/sites-enabled/ennosmart
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d app.votre-domaine.fr
```

N’exposer publiquement que les ports 22, 80 et 443. PostgreSQL, Redis, GROBID,
l’API et le frontend restent sur le réseau Docker ou `127.0.0.1`.

## 11. Dépendances complètes

Le déploiement Docker installe automatiquement :

- Python 3.12 et toutes les dépendances de `requirements.txt` ;
- FastAPI/Uvicorn, SQLAlchemy, psycopg/psycopg2, Celery et Redis ;
- LangGraph et ses checkpoints PostgreSQL/Redis ;
- Chroma, sentence-transformers, PyTorch, Transformers et FastJudge ;
- Pydantic AI Slim avec le fournisseur OpenAI, utilisé par la conclusion
  structurée EnnoDiagnostic ;
- PyMuPDF, pypdf, pdfplumber, Pillow, CairoSVG et les lecteurs Office ;
- Tesseract français/anglais, Poppler, LibreOffice, FFmpeg, Java et les polices ;
- le serveur MCP légal et ses clients HTTP ;
- Node.js 22 et exactement les paquets verrouillés par
  `frontend/package-lock.json` ;
- PostgreSQL 17, Redis 7 et GROBID via leurs images séparées.

`requirements-optional.txt` n’est pas installé par défaut. Il contient Surya,
Pix2Tex, WhisperX, Qwen Vision, GLiNER et les outils d’entraînement. Ne
l’installer que si ces fonctions lourdes sont réellement activées et si le
serveur possède le GPU/la mémoire nécessaires :

```bash
docker compose -f docker-compose.ovh.yml run --rm api \
  python -m pip install -r /opt/ennosmart/requirements-optional.txt
```

Cette installation faite dans un conteneur temporaire ne persiste pas. Pour une
production utilisant ces options, créer une image dédiée qui installe le fichier
optionnel pendant le build.

## 12. Sauvegardes et mises à jour

Sauvegarder quotidiennement PostgreSQL et régulièrement le volume persistant.
Pour une copie cohérente de Chroma, arrêter temporairement les services qui
écrivent :

```bash
docker compose -f docker-compose.ovh.yml stop api scholar-worker cir-worker legal-mcp
```

Effectuer la sauvegarde/snapshot, puis :

```bash
docker compose -f docker-compose.ovh.yml start api scholar-worker cir-worker legal-mcp
```

Pour mettre à jour uniquement le code :

```bash
cd /opt/ennosmart
git pull --ff-only
docker compose -f docker-compose.ovh.yml build
docker compose -f docker-compose.ovh.yml up -d
```

Le volume `/mnt/ennosmart-data` contient les données applicatives, PostgreSQL
et Redis ; il survit aux reconstructions et remplacements des conteneurs. Ne
jamais supprimer ou reformater ce point de montage pendant une mise à jour.
