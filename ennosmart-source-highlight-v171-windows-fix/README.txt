EnnoSmart — Source Highlight V171
=================================

Cette version corrige l'erreur Windows :

  Could not find platform independent libraries <prefix>
  SfxBaseModel::impl_store ... failed: 0x11b

CAUSES TRAITÉES
---------------
1. LibreOffice héritait des variables PYTHONHOME / PYTHONPATH du venv.
2. LibreOffice écrivait directement dans storage/previews/... ;
   sous Windows cela peut provoquer un Abort I/O / fichier verrouillé.
3. Un cache PDF incomplet pouvait rester après un échec.
4. Le fichier source pouvait provenir d'un emplacement synchronisé /
   matérialisé et être moins robuste à ouvrir directement.

V171 FAIT DONC
--------------
- copie le DOCX/PPTX/XLSX dans %TEMP%;
- crée un profil LibreOffice isolé;
- convertit dans un dossier %TEMP% séparé;
- retire PYTHONHOME/PYTHONPATH de l'environnement du sous-processus;
- vérifie que le fichier produit commence par %PDF-;
- publie le PDF dans le cache uniquement après succès;
- remplace le cache de façon atomique;
- continue à utiliser PyMuPDF pour le surlignage.

INSTALLATION
------------
Décompresser le ZIP, puis PowerShell :

  cd <dossier-du-pack>
  powershell -ExecutionPolicy Bypass -File .\scripts\INSTALLER_V171.ps1

Puis :

  powershell -ExecutionPolicy Bypass -File .\scripts\test_libreoffice_windows.ps1

Ensuite redémarrer FastAPI.

IMPORTANT
---------
Il n'est pas nécessaire de relancer EnnoDiagnostic.
