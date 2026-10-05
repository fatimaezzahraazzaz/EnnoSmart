# Mise à jour de l’application existante vers EnnoDiagnostic V2

Le paquet `ennosmart-v2-source.tgz` contient le backend actuel, le frontend actuel,
les autres agents et le moteur Diagnostic V2. Il inclut les nouveaux fichiers V2
même s’ils n’ont pas encore été ajoutés à Git.

Il exclut les secrets, données client, diagnostics, traces, sauvegardes, modèles
téléchargés et exemples locaux. Le modèle FastJudge partagé reste inclus.

## Préparation locale

```powershell
.\.venv\Scripts\python.exe -B scripts/deployment/package_release.py
```

Le paquet est accompagné d’un SHA-256 et d’un manifeste des fichiers sources.
Les tests ne sont pas inclus dans l’image de production.

## Sur le serveur existant

Vérifier d’abord le dossier de l’application, les services en cours et les chemins
des volumes. Sauvegarder le code, le `.env`, les identifiants des images actuelles
et la base PostgreSQL avant le remplacement. Conserver ces sauvegardes hors du
dossier remplacé, accessibles uniquement à l’administrateur.

Décompresser le paquet dans un nouveau dossier de release. Conserver le `.env`
du serveur et ses URLs publiques ; ne pas le remplacer par celui du PC Windows.
Les variables V2 de `.env.example` rendent explicites les valeurs par défaut du
nouveau moteur (`gpt-5`, effort Fast `medium`, reviewer `high`).

Le dossier de release doit être le contexte de construction Docker. Construire
les images avant de remplacer les conteneurs :

```bash
docker compose -f docker-compose.ovh.yml build api frontend
docker compose -f docker-compose.ovh.yml up -d --no-deps api scholar-worker cir-worker legal-mcp frontend
```

L’API et les workers doivent utiliser la même nouvelle image backend. Le serveur
garde ses volumes PostgreSQL, Redis, Chroma et stockage documentaire existants.
Cette mise à jour ne nécessite pas de migration de schéma ni de diagnostic LLM.
Ne pas exécuter la restauration de la base vide décrite pour une première
installation, ni une suppression des volumes.

Après démarrage, vérifier `/health` (service `v151_ennodiagnostic_v2_only`), le
chargement du frontend et l’accès aux projets existants. Vérifier dans le
conteneur que l’agent V1 n’est plus importable et que les modules V2 le sont.
Conserver les anciennes images et sources jusqu’à validation de l’application.

## Composants partagés conservés

`modules/NLP` reste requis pour le chargement des documents, la mémoire CIR et
certains classifieurs d’EnnoAmélioration. Ses dépendances ont été contrôlées.
`project_fact_gate` a été déplacé dans ce dossier sans modifier ses règles.
Le fallback sémantique de Diagnostic V2 est conservé ; ce n’est pas l’agent V1.
