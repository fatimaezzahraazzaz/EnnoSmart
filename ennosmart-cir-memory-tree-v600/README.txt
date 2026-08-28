EnnoSmart — CIR Memory V6.00
============================

Page trouvée dans le dépôt :
frontend/components/ennosmart/cir-memory-page.tsx

Le catalogue actuel contient déjà :
organisme / project / subproject / year / indexed_file_name / source_files.

La V6.00 remplace uniquement la représentation plate par :

Organisme
  Projet
    Sous-projet (uniquement s'il existe)
      Année
        CIR final
    Année (si aucun sous-projet)
      CIR final

Le panneau droit affiche le chemin logique :
Organisme > Projet > Sous-projet éventuel > Année

et le fichier CIR final avec :
- Ouvrir le CIR
- Original

Ouverture :
- PDF : affiché directement.
- DOC/DOCX/DOCM : converti en PDF via le renderer Office EnnoSmart.
- TXT/MD : affiché comme texte.
- "Original" télécharge toujours le fichier non transformé.

Routes ajoutées :
GET /cir-memory/v2/projects/{memory_id}/source-preview
GET /cir-memory/v2/projects/{memory_id}/source-download

Sécurité :
le frontend n'envoie jamais le file_path. Il envoie uniquement memory_id.
Le serveur retrouve lui-même le fichier dans son catalogue Memory V2.

Installation :

cd C:\EnnoSmart
.\.venv\Scripts\Activate.ps1

$Installer = Get-ChildItem C:\EnnoSmart -Recurse -Filter INSTALLER_CIR_MEMORY_TREE_V600.ps1 -ErrorAction SilentlyContinue |
    Select-Object -First 1

Write-Host $Installer.FullName
powershell -ExecutionPolicy Bypass -File $Installer.FullName

Puis redémarrer FastAPI :

cd C:\EnnoSmart
.\.venv\Scripts\Activate.ps1
$env:LIBREOFFICE_BIN="C:\Program Files\LibreOffice\program\soffice.com"

python -m uvicorn main:app `
    --app-dir "C:\EnnoSmart\backend_api" `
    --host 127.0.0.1 `
    --port 8002

Puis actualiser le frontend.

Backups :
cir-memory-page.tsx.before-cir-memory-tree-v600
cir_memory.py.before-cir-memory-tree-v600
cir_memory_source_preview_service.py.before-cir-memory-tree-v600
