# V31.1 — Vérification stricte payload enrichi

Si tu obtiens :

```text
original_title: None
profile: None
suggested_queries: vide
```

alors le backend n'utilise pas encore le fichier `scholar_service.py` V31.

## Copie obligatoire

```text
backend_api/services/scholar_service.py
agents/EnnoScholar/scholar_agent.py
test_backend_ennoscholar_enriched_payload.py
verify_scholar_service_v31.py
```

vers :

```text
C:\EnnoSmart\backend_api\services\scholar_service.py
C:\EnnoSmart\agents\EnnoScholar\scholar_agent.py
C:\EnnoSmart\backend_api\test_backend_ennoscholar_enriched_payload.py
C:\EnnoSmart\backend_api\verify_scholar_service_v31.py
```

## Vérification locale

```powershell
cd C:\EnnoSmart\backend_api
.\.venv\Scripts\activate
python verify_scholar_service_v31.py
```

Tout doit être ✅.

## Important

Arrête complètement uvicorn puis relance-le. Ne laisse pas l'ancien serveur tourner.

```powershell
CTRL + C
uvicorn main:app --host 127.0.0.1 --port 8000
```

## Vérification API

```powershell
python test_backend_ennoscholar_enriched_payload.py
```

Résultat attendu :

```text
original_title: Fiabilité, usure...
enriched_title: Maîtrise du soufflage carter...
profile: blowby_segments_crankcase
suggested_queries:
- reciprocating compressor piston rings blow-by leakage crankcase pressure
```
