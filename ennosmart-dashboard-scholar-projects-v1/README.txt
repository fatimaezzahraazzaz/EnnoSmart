EnnoSmart — Dashboard EnnoScholar par projets V1
=================================================

FICHIER CONCERNÉ
----------------

frontend/components/ennosmart/dashboard-page.tsx

Le dashboard calculait déjà `scholarProjects`, c'est-à-dire le nombre de
projets pour lesquels EnnoScholar est disponible, mais la grande carte
affichait `stats.articles`. C'est pour cela que l'écran montrait 203.

CORRECTION
----------

- le grand chiffre EnnoScholar devient `stats.scholarProjects`;
- le texte devient `X projet(s) avec résultats EnnoScholar`;
- la somme globale des articles est supprimée du dashboard;
- dans « Dossiers récents », la colonne EnnoScholar n'affiche plus un
  nombre d'articles : elle affiche `Disponible` ou `Non lancé`;
- le workflow et la vue portefeuille continuent d'utiliser le nombre
  de projets EnnoScholar.

Aucun backend n'est modifié.

INSTALLATION
------------

Décompresser le pack dans C:\EnnoSmart puis :

    cd C:\EnnoSmart
    .\.venv\Scripts\Activate.ps1

    $Fix = Get-ChildItem C:\EnnoSmart -Recurse -Filter INSTALLER_DASHBOARD_SCHOLAR_PROJECTS_V1.ps1 -ErrorAction SilentlyContinue |
        Select-Object -First 1

    Write-Host $Fix.FullName
    powershell -ExecutionPolicy Bypass -File $Fix.FullName

Puis actualiser le frontend.

BACKUP
------

dashboard-page.tsx.before-scholar-projects-v1
