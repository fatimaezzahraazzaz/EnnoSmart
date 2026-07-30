# Fix import modules

Cette version corrige l'erreur :

```txt
No module named 'modules'
```

Installation :

```powershell
Copy-Item .\experience_memory_v2.py C:\EnnoSmart\scripts\experience_memory_v2.py -Force
```

Puis relance :

```powershell
cd C:\EnnoSmart
.\.venv_memory\Scripts\python.exe scripts\experience_memory_v2.py --build --reset-chroma
```

Si l'erreur continue, ça veut dire que ton dossier `modules` n'est pas dans `C:\EnnoSmart`, `C:\EnnoSmart\ai`, `C:\EnnoSmart\backend_api` ou `C:\EnnoSmart\backend_api\ai`.
Dans ce cas, cherche-le avec :

```powershell
Get-ChildItem C:\EnnoSmart -Directory -Recurse -Filter modules
```
