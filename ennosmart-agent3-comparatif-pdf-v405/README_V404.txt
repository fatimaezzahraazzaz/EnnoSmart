EnnoSmart - Agent 3 V4.04

Correction :
- suppression des deux grands boutons Rejeter / Accepter sous les PDF ;
- les deux actions deviennent deux petites icones 32x32 dans le header ;
- aucune hauteur supplementaire sous les PDF ;
- plein ecran V4.03 conserve ;
- liste Changements retractable conservee ;
- Sources inchange ;
- logique PDF / Word V4.02 inchangee.

Installation :

cd C:\EnnoSmart
.\.venv\Scripts\Activate.ps1

$Fix = Get-ChildItem C:\EnnoSmart -Recurse -Filter METTRE_A_JOUR_AGENT3_V404.ps1 -ErrorAction SilentlyContinue |
    Select-Object -First 1

powershell -ExecutionPolicy Bypass -File $Fix.FullName

Puis actualiser le frontend.

Aucun redemarrage backend et aucun rerun Agent 3.
