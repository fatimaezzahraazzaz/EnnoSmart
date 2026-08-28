EnnoSmart — Agent 3 V4.05
=========================

Pourquoi V4.04 échouait
-----------------------

V4.04 cherchait une ligne TSX exacte contenant le Badge "Proposition V...".
Après les corrections V4.03, le header local peut avoir une structure ou un
formatage légèrement différent. Le code fonctionnel était présent, mais
l'installateur ne retrouvait pas cette chaîne exacte.

V4.05 est plus robuste :
- ne dépend plus du Badge ;
- repère directement le bouton Plein écran V4.03 ;
- insère juste avant lui les deux petites actions Rejeter / Accepter ;
- retire la grande barre d'actions sous les PDF ;
- préserve le plein écran V4.03 ;
- préserve le comparatif PDF V4.02 ;
- préserve Sources.

Installation
------------

Décompresser ce ZIP dans C:\EnnoSmart puis :

    cd C:\EnnoSmart
    .\.venv\Scripts\Activate.ps1

    $Fix = Get-ChildItem C:\EnnoSmart -Recurse -Filter METTRE_A_JOUR_AGENT3_V405.ps1 -ErrorAction SilentlyContinue |
        Select-Object -First 1

    Write-Host $Fix.FullName
    powershell -ExecutionPolicy Bypass -File $Fix.FullName

Puis actualiser le frontend.

Le précédent échec V4.04 n'a pas écrit le fichier final : il a seulement créé
un backup avant de s'arrêter.
