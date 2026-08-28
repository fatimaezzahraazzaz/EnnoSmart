EnnoSmart V172 — auto-positionnement des deux documents A/B
===========================================================

Problème corrigé
----------------
Le passage était bien surligné dans le PDF, mais le consultant devait
faire défiler manuellement les documents pour le retrouver.

Nouveau comportement
--------------------
1. Le consultant clique sur un passage dans la liste de comparaison.
2. Le backend crée les deux previews PDF surlignées.
3. Il renvoie X-EnnoSmart-Highlight-Page pour A et pour B.
4. Le frontend lit cette page.
5. Les deux iframes sont rechargées avec :
       #page=<page>&zoom=page-width
6. Document A et Document B s'ouvrent automatiquement sur leur passage.

HTML fallback
-------------
Le fallback HTML possédait déjà un scroll automatique sur
#selected-passage. Il est conservé.

Installation
------------
Depuis le dossier décompressé :

  powershell -ExecutionPolicy Bypass -File .\scripts\INSTALLER_V172.ps1

Puis redémarrer FastAPI et actualiser Next.js.

Il n'est pas nécessaire de relancer EnnoDiagnostic.
