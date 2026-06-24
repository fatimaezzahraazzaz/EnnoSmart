# V4 - Correction des champs verrous

Cette version corrige la synchronisation des verrous.

Avant, les champs suivants restaient vides :

- tag_cir
- score
- justification

alors que les informations existaient dans :

- source_json.manual_cir_tag
- source_json.manual_justification

## Installation rapide

Remplace ces fichiers dans ton backend :

```text
services/diagnostic_service.py
scripts/backfill_verrou_fields.py
```

Puis redémarre FastAPI.

## Corriger les verrous déjà synchronisés

Comme tu as déjà synchronisé les verrous, lance :

```powershell
python -m scripts.backfill_verrou_fields
```

Puis vérifie :

```powershell
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/projects/2/verrous" -Headers @{ Authorization = "Bearer $token" }
```
