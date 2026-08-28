EnnoSmart — Chat documentaire EnnoDiagnostic V5.00
===================================================

FICHIER REMPLACÉ
----------------

frontend/components/ennosmart/diagnostic-rag-chat.tsx

Le composant existant du dépôt est bien celui qui gère :
- Assistant documentaire EnnoDiagnostic ;
- Portée de la recherche ;
- messages RAG ;
- sources/passages ;
- ouverture des preuves documentaires.

NOUVEAU DESIGN
--------------

Le design reprend la maquette validée :

1. Header premium
   - Assistant documentaire
   - EnnoDiagnostic
   - état Prêt
   - réduire / agrandir / fermer
   - téléchargement de l'aperçu sélectionné

2. "Passages et preuves" AU-DESSUS
   - apparaît immédiatement sous le header ;
   - cartes [P1], [P2], [P3] horizontales ;
   - extrait ;
   - fichier ;
   - page/paragraphe ;
   - filtres Tous / Clients / Diagnostic ;
   - sélecteur de portée documentaire ;
   - panneau masquable.

3. Vue principale en plein écran
   - gauche : document réel ;
   - droite : chat ;
   - le document est ouvert via l'endpoint existant :
     POST /projects/{projectId}/source-highlight/preview
   - passage automatiquement surligné ;
   - page automatiquement ouverte dans le lecteur PDF ;
   - figures/tableaux restent dans le document rendu.

4. Chat
   - messages plus aérés ;
   - source principale compacte ;
   - bouton "Voir le passage" ;
   - zone de saisie moderne ;
   - portée documentaire intégrée ;
   - suggestions de démarrage.

5. Comportement
   - ouverture du chat -> plein écran par défaut ;
   - possibilité de réduire ;
   - première source de la réponse ouverte automatiquement ;
   - clic sur une preuve -> le document se positionne dessus.

BACKEND
-------

Aucun nouveau backend n'est ajouté.

Le composant réutilise :
- GET /projects/{projectId}/diagnostic-chat/status
- POST /projects/{projectId}/diagnostic-chat/messages
- POST /projects/{projectId}/source-highlight/preview

Donc le moteur RAG actuel et le renderer Office/PDF actuel sont conservés.

INSTALLATION
------------

Décompresser le ZIP dans C:\EnnoSmart puis :

    cd C:\EnnoSmart
    .\.venv\Scripts\Activate.ps1

    $Installer = Get-ChildItem C:\EnnoSmart -Recurse -Filter INSTALLER_CHAT_DOCUMENTAIRE_V500.ps1 -ErrorAction SilentlyContinue |
        Select-Object -First 1

    Write-Host $Installer.FullName
    powershell -ExecutionPolicy Bypass -File $Installer.FullName

Puis actualiser le frontend.

BACKUP
------

diagnostic-rag-chat.tsx.before-chat-v500
