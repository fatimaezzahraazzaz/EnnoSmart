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
   - `ennosmart-data.tgz` : données d'exécution actives, notamment les sources,
     JSON NLP, chunks, mémoires et répertoires Chroma complets ;
   - `SHA256SUMS.txt` : empreintes des deux fichiers.

Le vrai fichier `.env` ne doit jamais être ajouté à Git. Le déployeur copie
`.env.example` vers `.env`, puis renseigne les secrets reçus séparément
(gestionnaire de mots de passe, téléphone ou message chiffré).

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

Le clone Git seul lance une application vide. Pour retrouver le corpus et
Chroma, il faut aussi extraire `ennosmart-data.tgz` dans
`C:\EnnoSmartData`, puis restaurer `ennosmart.dump` comme expliqué dans le
guide complet.

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
