EnnoSmart — Agent 3 Comparatif documentaire V4.02
=================================================

CORRECTIONS
-----------

1. Le comparatif part directement de la conversation :
      ImprovementSession.source_document_id
              ↓
      Document.id exact du CIR sélectionné
              ↓
      Document.file_data / PostgreSQL

   Aucun scan par nom de fichier.

2. CIR PDF :
      PDF source
        ↓
      utilisé directement
        ↓
      aucune conversion LibreOffice / Word

   Original : suppression/remplacement en rouge.
   Proposition : ajout/modification en vert sous forme de revue visuelle.

3. CIR Word DOC/DOCX/DOCM :
   Sous Windows :
      Microsoft Word COM -> PDF en priorité
      LibreOffice -> fallback

   Cela évite de dépendre uniquement de LibreOffice pour les gros DOCX
   qui peuvent rester bloqués en mode headless.

4. Cache stable :
   document_id / file_sha256
   et non le chemin d'un fichier temporaire.

5. Pour un DOCX modifiable :
   copie DOCX -> before/after -> conversion PDF.
   Le CIR original stocké en base n'est jamais modifié.

INSTALLATION
------------

Si V4.00 ou V4.01 est déjà installée :

    cd C:\EnnoSmart
    .\.venv\Scripts\Activate.ps1

    $Fix = Get-ChildItem C:\EnnoSmart -Recurse -Filter METTRE_A_JOUR_AGENT3_V402.ps1 -ErrorAction SilentlyContinue |
        Select-Object -First 1

    Write-Host $Fix.FullName

    powershell -ExecutionPolicy Bypass -File $Fix.FullName

Puis redémarrer FastAPI :

    cd C:\EnnoSmart
    .\.venv\Scripts\Activate.ps1

    $env:LIBREOFFICE_BIN="C:\Program Files\LibreOffice\program\soffice.com"
    $env:ENNOSMART_OFFICE_CONVERT_TIMEOUT="420"
    $env:ENNOSMART_WORD_CONVERT_TIMEOUT="240"

    python -m uvicorn main:app `
        --app-dir "C:\EnnoSmart\backend_api" `
        --host 127.0.0.1 `
        --port 8002

Puis actualiser le frontend.

Pas besoin de relancer la proposition Agent 3.
