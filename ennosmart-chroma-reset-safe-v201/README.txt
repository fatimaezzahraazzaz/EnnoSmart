EnnoSmart — Chroma reset Windows safe V201
==============================================

PROBLÈME CORRIGÉ
----------------
prepare-sources appelait _reset_generated_diagnostic_artifacts(), qui supprimait
tout le dossier rag/, donc aussi rag/chroma/chroma.sqlite3.

Sous Windows, chroma.sqlite3 peut rester ouvert par chromadb.PersistentClient
dans le processus FastAPI. shutil.rmtree(rag_dir) échoue alors avec :

PermissionError: [WinError 32]
... rag\chroma\chroma.sqlite3

CORRECTION
----------
- rag/chroma n'est plus supprimé physiquement pendant prepare-sources.
- rag/chunks.json est supprimé car il est régénérable.
- les autres artefacts diagnostic/NLP restent nettoyés.
- index_nlp_result(... reset=True) conserve son fonctionnement existant :
  RAGVectorStore.add_chunks(reset=True) appelle reset_collection(), donc la
  collection Chroma courante est supprimée/recréée proprement via l'API Chroma.

Ce correctif ne conserve donc PAS les anciens chunks dans la nouvelle analyse.
Il évite seulement de supprimer le fichier SQLite sous les pieds de Chroma.

INSTALLATION
------------
Décompresser dans C:\EnnoSmart puis :

cd C:\EnnoSmart
.\.venv\Scripts\Activate.ps1

$Fix = Get-ChildItem C:\EnnoSmart -Recurse -Filter INSTALLER_CHROMA_SAFE_V201.ps1 -ErrorAction SilentlyContinue |
    Select-Object -First 1

Write-Host $Fix.FullName
powershell -ExecutionPolicy Bypass -File $Fix.FullName

Puis redémarrer FastAPI.

BACKUP
------
C:\EnnoSmart\backend_api\services\diagnostic_service.py.before-chroma-safe-v201
