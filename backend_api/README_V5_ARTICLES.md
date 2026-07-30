# V5 - Extraction robuste des articles EnnoScholar

Cette version corrige `sync-articles` quand `GET /articles` ne retourne rien.

## Fichier à remplacer

```text
services/scholar_service.py
```

## Redémarrage

```powershell
CTRL + C
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## Debug optionnel

```powershell
python -m scripts.debug_scholar_report
```

## Synchroniser les articles

Avec ton projet actuel :

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/projects/2/scholar/1/sync-articles" -Headers @{ Authorization = "Bearer $token" }
```

Puis :

```powershell
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/projects/2/articles" -Headers @{ Authorization = "Bearer $token" }
```
