EnnoSmart — Agent 3 Comparatif PDF V4.01
========================================

PROBLÈME CORRIGÉ
----------------

Les logs montraient deux conversions LibreOffice simultanées du même DOCX :

- side=original
- side=proposed

Chaque processus soffice.exe arrivait au timeout de 120 secondes.

V4.01 corrige les deux causes :

1. Backend
   - timeout Office par défaut : 420 secondes ;
   - verrou fichier atomique par PDF source ;
   - un seul LibreOffice convertit le même DOCX ;
   - le second appel attend puis réutilise le cache ;
   - compatible threads, React Strict Mode et plusieurs workers/processus ;
   - verrou stale nettoyé automatiquement en cas de crash.

2. Frontend
   - original puis proposition, séquentiellement ;
   - plus de Promise.allSettled qui déclenchait les deux conversions lourdes
     au même instant.

Le document original n'est jamais modifié.

INSTALLATION SI V4.00 EST DÉJÀ INSTALLÉE
-----------------------------------------

Décompresser le ZIP dans C:\EnnoSmart puis :

    cd C:\EnnoSmart
    .\.venv\Scripts\Activate.ps1

    $Fix = Get-ChildItem C:\EnnoSmart -Recurse -Filter METTRE_A_JOUR_AGENT3_V401.ps1 -ErrorAction SilentlyContinue |
        Select-Object -First 1

    powershell -ExecutionPolicy Bypass -File $Fix.FullName

Puis redémarrer le backend :

    cd C:\EnnoSmart
    .\.venv\Scripts\Activate.ps1
    $env:LIBREOFFICE_BIN="C:\Program Files\LibreOffice\program\soffice.exe"
    $env:ENNOSMART_OFFICE_CONVERT_TIMEOUT="420"

    python -m uvicorn main:app `
        --app-dir "C:\EnnoSmart\backend_api" `
        --host 127.0.0.1 `
        --port 8002

Actualiser ensuite le frontend.

PAS BESOIN DE RELANCER L'AMÉLIORATION
--------------------------------------

Le correctif concerne uniquement la visualisation/conversion PDF.
La Proposition V2/V3 existante reste réutilisable.

BACKUPS
-------

source_highlight.py.before-agent3-v401
improvement-pdf-comparator.tsx.before-agent3-v401
