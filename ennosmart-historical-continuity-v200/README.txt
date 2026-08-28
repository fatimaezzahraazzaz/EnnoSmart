EnnoSmart - EnnoDiagnostic Historical Continuity Reconciler V200
=================================================================

OBJECTIF
--------
Ce pack ajoute la logique longitudinale N / N-1 demandee pour EnnoDiagnostic,
sans contaminer la premiere detection par l'historique.

Architecture :
1. PASS 1 - EnnoDiagnostic analyse uniquement les documents de l'annee N.
2. PASS 2 - le reconciler charge le vrai CIR precedent du meme organisme / projet
   / sous-projet via CIR_MEMORY (N-1, sinon N-2/N-3).
3. Il relie chaque verrou historique a ses methodes, resultats, limites,
   contributions et parametres historiques afin de reconstruire l'histoire R&D.
4. Il classe les verrous N : continued, refined, sub_lock, partially_lifted,
   extended_scope, new, uncertain.
5. Plusieurs sous-problemes N peuvent etre regroupes dans une meme famille
   scientifique historique uniquement si les ancres lexicales et la confiance
   sont suffisantes.
6. GAP PROBE : si un verrou N-1 semble avoir disparu, l'historique sert seulement
   a lancer une recherche ciblee dans les sources de l'annee N. Un candidat n'est
   recupere que si des PREUVES N existent et si la porte de securite est satisfaite.
7. Le rapport final garde l'historique dans historical_continuity, marque comme
   non-preuve du projet courant.

REGLE DE NON-HALLUCINATION
--------------------------
- CIR N-1 = contexte historique / hypothese de continuite / declencheur de recherche.
- CIR N-1 n'est JAMAIS une preuve factuelle de l'annee N.
- Un verrou recupere par gap probe doit contenir des preuves de l'annee N.
- Aucun score Frascati n'est recalcule par V200.
- En cas d'erreur V200, le diagnostic independant de l'annee N est conserve.

FICHIERS DU PACK
----------------
agents/EnnoDiagnostic/historical_continuity_reconciler.py
    Nouveau moteur de reconciliation historique.

scripts/apply_v200_patch.py
    Patch cible de ennodiagnostic_agent.py. Il ne remplace pas tout le fichier.
    Il cree ennodiagnostic_agent.py.before-v200 avant modification.

scripts/INSTALLER_V200.ps1
    Installation Windows automatique, ASCII-only.

scripts/VERIFY_V200.ps1
    Compilation + tests.

scripts/UNINSTALL_V200.ps1
    Restauration de la sauvegarde precedente.

tests/test_historical_continuity_reconciler_v200.py
    Tests dont un cas VECAME-like : barre de poussee / paliers / bain d'huile
    regroupes dans la meme famille N-1 + recuperation stricte d'un verrou oublie.

INSTALLATION WINDOWS
--------------------
1. Extraire le ZIP, par exemple dans :
   C:\EnnoSmart\ennosmart-historical-continuity-v200

2. Ouvrir PowerShell puis lancer :

   powershell -ExecutionPolicy Bypass -File "C:\EnnoSmart\ennosmart-historical-continuity-v200\scripts\INSTALLER_V200.ps1"

3. Verification facultative :

   powershell -ExecutionPolicy Bypass -File "C:\EnnoSmart\ennosmart-historical-continuity-v200\scripts\VERIFY_V200.ps1"

4. Redemarrer le backend EnnoSmart avec votre commande habituelle et relancer
   EnnoDiagnostic sur un projet qui possede un CIR precedent dans Memory V2.

SORTIES AJOUTEES
----------------
Le rapport EnnoDiagnostic contient maintenant :
- historical_continuity_report
- inputs_status.historical_continuity_available
- inputs_status.historical_gap_recovery_count
- telemetry.main_verrous_before_historical_reconciliation
- telemetry.historical_reconciliation_merged_groups
- telemetry.historical_reconciliation_gap_recovered
- telemetry.historical_reconciliation_history_is_current_proof = false

Le moteur sauvegarde aussi :
  <dossier projet>/ennodiagnostic/historical_continuity_report_v200.json

VERSION DU RAPPORT
------------------
ennodiagnostic_v200_historical_continuity_reconciliation

PARAMETRES ENVIRONNEMENT OPTIONNELS
-----------------------------------
ENNOSMART_HISTORICAL_RECONCILIATION_USE_LLM=1
ENNOSMART_HISTORICAL_RECONCILIATION_TEMPERATURE=0.02
ENNOSMART_HISTORICAL_RECONCILIATION_MAX_TOKENS=2200
ENNOSMART_HISTORICAL_RECONCILIATION_RETRIES=1
ENNOSMART_HISTORICAL_MERGE_MIN_CONFIDENCE=0.66
ENNOSMART_HISTORICAL_GAP_TRIGGER_SCORE=0.48
ENNOSMART_HISTORICAL_GAP_MAX_FAMILIES=10
ENNOSMART_HISTORICAL_GAP_RECOVERY=1
ENNOSMART_HISTORICAL_GAP_MIN_CONFIDENCE=0.76

DESINSTALLATION
---------------
powershell -ExecutionPolicy Bypass -File "C:\EnnoSmart\ennosmart-historical-continuity-v200\scripts\UNINSTALL_V200.ps1"

SECURITE D'INTEGRATION
----------------------
Le patch est volontairement cible : il ne remplace pas tout ennodiagnostic_agent.py.
C'est important si le fichier local contient des corrections plus recentes que la
branche GitHub. Si le code attendu n'est pas retrouve exactement, l'installation
s'arrete avec une erreur et ne reecrit pas l'agent.
