EnnoSmart — Mot de passe oublié direct V1
==========================================

CODE TROUVÉ
-----------

frontend/components/ennosmart/login-page.tsx

Le code actuel possède déjà les modes :
login / register / forgot / reset.

Le bouton « Mot de passe oublié ? » envoyait vers le mode `forgot`,
qui affichait l'écran intermédiaire :
- Adresse e-mail
- Envoyer le lien

Le backend possède déjà :
POST /auth/forgot-password
POST /auth/reset-password

En environnement non production, `/auth/forgot-password` renvoie également
un `preview_token` à usage unique. Le composant savait déjà utiliser ce token.

NOUVEAU COMPORTEMENT
---------------------

1. Le consultant saisit son e-mail sur l'écran de connexion.
2. Il clique « Mot de passe oublié ? ».
3. Le frontend appelle silencieusement `/auth/forgot-password`.
4. En local/dev, il récupère le token temporaire.
5. Il passe DIRECTEMENT à l'écran :

   Nouveau mot de passe
   Mot de passe
   Confirmer le mot de passe
   Enregistrer

L'écran « Envoyer le lien » n'est donc plus affiché en local/dev.

SÉCURITÉ
--------

Je n'ai pas supprimé la vérification par token.

Faire un reset direct en production uniquement à partir de l'adresse e-mail
permettrait à n'importe qui connaissant l'e-mail d'un consultant de changer
son mot de passe.

Le backend actuel protège déjà ce cas : en ENV=prod/production il ne renvoie
pas `preview_token`.

Si l'envoi d'e-mail ne fonctionne pas en production, il faut configurer SMTP.
Le code backend actuel retourne False immédiatement quand SMTP_HOST est vide.

INSTALLATION
------------

Décompresser le ZIP dans C:\EnnoSmart puis :

    cd C:\EnnoSmart
    .\.venv\Scripts\Activate.ps1

    $Fix = Get-ChildItem C:\EnnoSmart -Recurse -Filter INSTALLER_LOGIN_DIRECT_RESET_V1.ps1 -ErrorAction SilentlyContinue |
        Select-Object -First 1

    Write-Host $Fix.FullName
    powershell -ExecutionPolicy Bypass -File $Fix.FullName

Puis actualiser le frontend.

Aucun redémarrage backend n'est requis pour cette correction frontend.

BACKUP
------

login-page.tsx.before-direct-reset-v1
