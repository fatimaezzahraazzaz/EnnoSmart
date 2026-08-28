IMPORTANT : voir README_V401.txt pour le correctif timeout/concurrence LibreOffice.

EnnoSmart — Agent 3 Comparatif PDF V4.00
========================================

La fenêtre Proposition garde seulement 2 onglets visibles :
- Comparatif
- Sources

L'onglet Sources actuel n'est pas réécrit.

Comparatif :
- liste uniquement les modifications ;
- original réel à gauche, PDF ;
- proposition à droite, PDF ;
- suppression/remplacement rouge côté original ;
- ajout/modification vert côté proposition ;
- clic sur une modification : les deux PDF se positionnent automatiquement ;
- séparateur entre les deux PDF déplaçable ;
- panneau Proposition élargi et redimensionnable.

DOCX :
- copie du document source, jamais modification de l'original ;
- tentative d'injection des couples before/after ;
- figures et tableaux restent dans la copie ;
- LibreOffice convertit ensuite la copie en PDF ;
- si une modification n'est pas réinjectable proprement, annotation verte
  de revue au bon emplacement au lieu d'inventer ou casser la mise en page.

Installation :
1. Décompresser le ZIP dans C:\EnnoSmart.
2. PowerShell :

   cd C:\EnnoSmart
   .\.venv\Scripts\Activate.ps1

   $Installer = Get-ChildItem C:\EnnoSmart -Recurse -Filter INSTALLER_AGENT3_PDF_V400.ps1 -ErrorAction SilentlyContinue |
       Select-Object -First 1

   powershell -ExecutionPolicy Bypass -File $Installer.FullName

3. Redémarrer FastAPI :

   cd C:\EnnoSmart
   .\.venv\Scripts\Activate.ps1
   $env:LIBREOFFICE_BIN="C:\Program Files\LibreOffice\program\soffice.exe"

   python -m uvicorn main:app `
       --app-dir "C:\EnnoSmart\backend_api" `
       --host 127.0.0.1 `
       --port 8002

4. Actualiser le frontend.

Pas besoin de relancer une Proposition V2/V3 déjà calculée si elle possède déjà
ses changements.

Fichiers ajoutés :
- frontend/components/ennosmart/improvement-pdf-comparator.tsx
- backend_api/services/improvement_comparison_service.py

Fichiers patchés localement :
- frontend/components/ennosmart/ennoamelioration-page.tsx
- backend_api/routers/improvement.py

Backups automatiques :
- ennoamelioration-page.tsx.before-agent3-pdf-v400
- improvement.py.before-agent3-pdf-v400
- source_highlight.py.before-agent3-pdf-v400 uniquement si nécessaire
