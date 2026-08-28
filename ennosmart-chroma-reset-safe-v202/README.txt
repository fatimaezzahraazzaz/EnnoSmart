EnnoSmart — Chroma reset Windows safe V202
==============================================

La V201 a échoué pendant l'écriture directe de diagnostic_service.py avec :
OSError: [Errno 22] Invalid argument

La V202 corrige cela avec :
- écriture dans diagnostic_service.py.v202.tmp
- remplacement atomique os.replace()
- 5 tentatives
- suppression de l'attribut ReadOnly si présent

Le correctif fonctionnel reste :
- ne plus supprimer rag/chroma/chroma.sqlite3 pendant prepare-sources
- supprimer seulement rag/chunks.json
- laisser index_nlp_result(..., reset=True) réinitialiser la collection Chroma

INSTALLATION
------------
Arrêter FastAPI avec Ctrl+C, puis :

cd C:\EnnoSmart
.\.venv\Scripts\Activate.ps1

$Fix = Get-ChildItem C:\EnnoSmart -Recurse -Filter INSTALLER_CHROMA_SAFE_V202.ps1 -ErrorAction SilentlyContinue |
    Select-Object -First 1

Write-Host $Fix.FullName
powershell -ExecutionPolicy Bypass -File $Fix.FullName

Si ça échoue encore, lancer DIAGNOSTIC_FICHIER_V202.ps1.
