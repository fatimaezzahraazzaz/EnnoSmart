# EnnoSmart

Ce dépôt contient uniquement le code et la configuration reproductible de
l'application. Les données d'exécution sont volontairement séparées du dépôt :
base PostgreSQL, corpus clients, Chroma, JSON NLP, documents et secrets.

## Transfert le plus simple

Le responsable du déploiement doit recevoir **deux liens** et les secrets par
un canal séparé :

1. le lien de ce dépôt GitHub privé ;
2. un lien privé OneDrive ou OVH Object Storage vers un dossier contenant :
   - `ennosmart.dump` : base PostgreSQL propre de déploiement ;
   - `ennosmart-data.tgz` : mémoire CIR globale uniquement, avec sa collection
     Chroma et les index JSON indispensables à la recherche des CIR précédents ;
   - `SHA256SUMS.txt` : empreintes des deux fichiers.

Le vrai fichier `.env` ne doit jamais être ajouté à Git. Le déployeur copie
`.env.example` vers `.env`, puis renseigne les secrets reçus séparément
(gestionnaire de mots de passe, téléphone ou message chiffré).

Le paquet de déploiement propre ne contient aucun projet, document importé,
résultat NLP, extraction ou sortie d'agent provenant du développement. La base
applicative démarre vide, avec uniquement les deux utilisateurs initiaux. Les
fichiers `chunks`, `cards`, `runs`, `relations` et `catalog_v2.json` présents
dans l'archive sont des index internes de la mémoire CIR globale ; ils ne créent
pas de projets dans l'interface.

## Prérequis et dépendances

### Installation recommandée : Docker

Sur OVH, il suffit d'installer sur l'hôte :

- Git ;
- Docker Engine ;
- le plugin Docker Compose.

Il ne faut pas installer Python, Node.js, PostgreSQL, Redis, GROBID, Tesseract
ou LibreOffice manuellement sur l'hôte. La construction Docker s'en charge :

- `requirements.txt` contient les dépendances Python de production de l'API,
  des agents, des workers, du serveur MCP, de Chroma et des traitements NLP ;
- `deploy/ovh/Dockerfile.backend` utilise Python 3.12 et installe aussi les
  paquets système nécessaires : Java, FFmpeg, LibreOffice, Poppler, Tesseract
  français/anglais, PostgreSQL client, Cairo, polices et bibliothèques images ;
- `frontend/package-lock.json` verrouille les dépendances du frontend ;
  `deploy/ovh/Dockerfile.frontend` utilise Node.js 22 et exécute `npm ci` ;
- `docker-compose.ovh.yml` démarre PostgreSQL 17, Redis 7 et GROBID, puis l'API,
  les deux workers, le serveur MCP et le frontend.

`requirements-optional.txt` contient uniquement les fonctions lourdes non
nécessaires au fonctionnement normal : OCR Surya, OCR de formules Pix2Tex,
diarisation WhisperX, vision Qwen et outils historiques. Ne pas l'installer sur
le serveur sauf si l'une de ces fonctions est explicitement activée et que les
ressources CPU/GPU correspondantes sont disponibles.

### Installation manuelle sur Windows

Docker Desktop reste la méthode la plus fiable. Pour une installation Python
manuelle, utiliser impérativement Python 3.12 et recréer l'environnement :

```powershell
cd C:\EnnoSmart
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

cd frontend
npm ci
```

Cette variante exige aussi Node.js 22, PostgreSQL 17, Redis 7, Tesseract avec
les langues française et anglaise, Poppler, LibreOffice, FFmpeg et Java. Pour
éviter ces installations séparées, utiliser les fichiers Docker fournis.

## Installation OVH en bref

Sur le serveur Linux, après installation de Git et Docker :

```bash
git clone URL_DU_DEPOT /opt/ennosmart
cd /opt/ennosmart
cp .env.example .env
chmod 600 .env
nano .env

sudo mkdir -p /mnt/ennosmart-data
sudo tar -xzf /chemin/ennosmart-data.tgz -C /mnt/ennosmart-data

docker compose -f docker-compose.ovh.yml build
docker compose -f docker-compose.ovh.yml up -d postgres redis grobid
```

Restaurer ensuite la base avant de démarrer l'API :

```bash
docker compose -f docker-compose.ovh.yml cp \
  /chemin/ennosmart.dump postgres:/tmp/ennosmart.dump

docker compose -f docker-compose.ovh.yml exec postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-acl /tmp/ennosmart.dump'

docker compose -f docker-compose.ovh.yml run --rm api \
  python /opt/ennosmart/scripts/deployment/normalize_database_paths.py --apply

docker compose -f docker-compose.ovh.yml run --rm api \
  python /opt/ennosmart/scripts/deployment/verify_runtime_paths.py

docker compose -f docker-compose.ovh.yml up -d
```

L'application est ensuite disponible sur le port configuré par le déploiement.
Changer immédiatement les mots de passe initiaux des comptes administrateurs.

## Installation sur un autre PC Windows

Le clone Git seul lance une application vide sans mémoire CIR. Pour conserver
uniquement cette mémoire globale, il faut aussi extraire
`ennosmart-data.tgz` dans `C:\EnnoSmartData`, puis restaurer
`ennosmart.dump` comme expliqué dans le guide complet.

La procédure détaillée, les prérequis, les permissions du volume persistant,
la sauvegarde et la restauration sont dans
[DEPLOIEMENT_OVH.md](DEPLOIEMENT_OVH.md).

## Contenu interdit dans Git

- `.env`, clés API, mots de passe, certificats et jetons ;
- dumps PostgreSQL et bases SQLite ;
- Chroma, sources clients, PDF, JSON NLP et sorties des agents ;
- environnements virtuels, caches, modèles téléchargés et fichiers temporaires.

Ces éléments sont déjà protégés par `.gitignore`. Avant chaque envoi :

```powershell
git status --short
git diff --cached --name-only |
  Select-String -Pattern '(^|/)(\.env$|storage/|outputs/|chroma/)|\.dump$|\.sqlite3$'
```

La seconde commande ne doit afficher aucun fichier.
