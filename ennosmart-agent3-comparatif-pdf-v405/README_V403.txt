EnnoSmart - Agent 3 Espace Comparatif V4.03

Code concerne :
- frontend/components/ennosmart/ennoamelioration-page.tsx
  Fenetre Proposition.
- frontend/components/ennosmart/improvement-pdf-comparator.tsx
  Liste des changements + deux PDF.

V4.03 :
- Proposition plein ecran par defaut.
- Bouton Restaurer / Plein ecran.
- Bouton Fermer.
- Comparatif + Sources conserves.
- Sources non modifie.
- Liste Changements retractable.
- Les deux PDF recuperent presque toute la largeur.
- Separateur entre PDF toujours reglable.
- Logique Word/PDF V4.02 inchangee.

Installation :
cd C:\EnnoSmart
.\.venv\Scripts\Activate.ps1

$Fix = Get-ChildItem C:\EnnoSmart -Recurse -Filter METTRE_A_JOUR_AGENT3_V403.ps1 -ErrorAction SilentlyContinue |
    Select-Object -First 1

powershell -ExecutionPolicy Bypass -File $Fix.FullName

Puis actualiser le frontend.
