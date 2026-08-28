EnnoSmart - Agent 3 V4.06

Apres Acceptation :
- la proposition acceptee devient la version active et reste a gauche ;
- Modifications devient vide ;
- Nouvelle version devient vide jusqu'a une nouvelle amelioration.

Apres Rejet :
- la version active precedente reste a gauche ;
- la proposition rejetee disparait ;
- Modifications et Nouvelle version restent vides.

A la prochaine amelioration, le comparatif se remplit a nouveau.

Installation :
cd C:\EnnoSmart
.\.venv\Scripts\Activate.ps1
$Fix = Get-ChildItem C:\EnnoSmart -Recurse -Filter METTRE_A_JOUR_AGENT3_V406.ps1 -ErrorAction SilentlyContinue | Select-Object -First 1
powershell -ExecutionPolicy Bypass -File $Fix.FullName

Puis redemarrer FastAPI et actualiser le frontend.
