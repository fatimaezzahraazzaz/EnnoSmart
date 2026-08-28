EnnoDiagnostic Historical Continuity V300
==========================================

Corrige les limites observées sur VECAME 2024/2025 :
- un titre descriptif N-1 comme « Caractérisation de la table » ne devient plus automatiquement une famille de verrou ;
- reconstruction d'une famille scientifique N-1 à partir des verrous + démarches + résultats + limites ;
- plusieurs sous-problèmes N rattachés à la même famille peuvent réellement être fusionnés ;
- le titre final fusionné vient des candidats/preuves N, pas d'un sous-problème étroit ;
- conformité normative seule = contrainte, pas verrou R&D ; un vrai mécanisme scientifique EMC reste conservé ;
- Gap Probe et règle absolue « N-1 n'est jamais une preuve N » conservés.

Pré-requis : V200 déjà intégré dans ennodiagnostic_agent.py.

Installation :
1. Arrêter FastAPI.
2. Décompresser ce pack dans C:\EnnoSmart.
3. Exécuter scripts\INSTALLER_V300.ps1.
4. Exécuter scripts\VERIFY_V300.ps1.
5. Redémarrer FastAPI et relancer EnnoDiagnostic.

Rapport : historical_continuity_report_v300.json
Backups : ennodiagnostic_agent.py.before-historical-v300 ; historical_continuity_reconciler.py.before-v300
