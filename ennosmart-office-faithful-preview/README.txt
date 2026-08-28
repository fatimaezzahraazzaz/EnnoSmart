EnnoSmart — affichage fidèle DOCX/PPTX/XLSX/PDF
=================================================

FICHIERS DU PACK
----------------

backend_api/routers/source_highlight.py
    Backend complet corrigé.
    Les fichiers Office ne sont plus reconstruits comme simple texte HTML.

frontend/components/ennosmart/diagnosis-page.tsx
    Version complète avec Document A / Document B côte à côte.

scripts/check_windows.ps1
    Vérifie LibreOffice et le backend sur Windows.

scripts/install_libreoffice_ovh.sh
    Installe LibreOffice + polices de base sur Ubuntu/OVH.

requirements-office-preview.txt
    Dépendances Python utiles.


PRINCIPE
--------

DOC / DOCX / DOCM
PPT / PPTX / PPTM
XLS / XLSX / XLSM
      |
      v
LibreOffice headless
      |
      v
PDF fidèle en cache
      |
      v
PyMuPDF
      |
      v
passage surligné
      |
      v
iframe A et B côte à côte

Le PDF converti conserve beaucoup mieux :
- figures
- images
- tableaux
- graphiques
- pagination
- en-têtes / pieds de page
- mise en page Word / PowerPoint


INSTALLATION LOCALE WINDOWS
---------------------------

1. Sauvegarde :

   C:\EnnoSmart\backend_api\routers\source_highlight.py
   C:\EnnoSmart\frontend\components\ennosmart\diagnosis-page.tsx

2. Copie les deux fichiers du pack aux mêmes emplacements.

3. Installe LibreOffice si nécessaire.

4. Dans PowerShell :

   cd C:\EnnoSmart
   .\.venv\Scripts\Activate.ps1

   pip install pymupdf python-docx openpyxl extract-msg

   powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1

5. Redémarre FastAPI.

6. Redémarre / actualise Next.js.


TEST BACKEND
------------

Une fois connecté dans l'app, le endpoint :

   GET /projects/<ID>/source-highlight/health

doit retourner notamment :

   "pymupdf_ok": true
   "libreoffice_ok": true


INSTALLATION OVH
----------------

   chmod +x scripts/install_libreoffice_ovh.sh
   ./scripts/install_libreoffice_ovh.sh

puis dans le venv :

   pip install pymupdf python-docx openpyxl extract-msg


CACHE
-----

Les PDF Office convertis sont mis en cache sous :

   storage/previews/source_highlight/office_pdf/

Donc le même DOCX/PPTX n'est pas reconverti à chaque sélection de passage.


IMPORTANT SUR LA FIDÉLITÉ
-------------------------

Cette solution affiche le document via un PDF rendu par LibreOffice.
C'est bien plus fidèle que python-docx -> texte -> HTML.

Une différence minime de pagination/police peut exister par rapport à
Microsoft Word si les polices originales ne sont pas installées sur OVH.

Pour améliorer encore la fidélité sur OVH, installer les polices utilisées
par les documents d'entreprise.
