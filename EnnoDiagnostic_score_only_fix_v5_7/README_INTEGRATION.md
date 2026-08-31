# EnnoDiagnostic — Score Only Fix V5.7

## Cause trouvée

Le troisième verrou est créé par le mécanisme générique `recovered_constraint_*`.
Il est récupéré **après** la constitution des groupes Frascati officiels.

Dans le rapport fourni :
- les deux groupes NLP officiels ont `frascati_score = 0.9`;
- le verrou récupéré a `upstream_frascati_score = 0.0`;
- mais il possède déjà `cluster_role_confidence = 0.82`.

Le `0 %` visible n'indique donc pas que la preuve vaut zéro : il provient de
l'absence d'un assessment Frascati amont pour ce nouveau group_id.

## Correction

Aucune logique de découverte/groupement des verrous n'est modifiée.

Le patch touche uniquement :
- `backend_api/services/diagnostic_service.py`
- `backend_api/services/diagnostic_display_service.py`

Règle :
1. si `score > 0`, on ne change rien;
2. si `score` est nul/absent;
3. et seulement si `group_id` commence par le marqueur générique
   `recovered_constraint_`;
4. alors le score visible/persisté prend `cluster_role_confidence`.

Le score Frascati amont (`upstream_frascati_score`) reste inchangé.

Sur le snapshot fourni, cela donne :
- verrou 1 : 90 % (inchangé)
- verrou 2 : 90 % (inchangé)
- verrou récupéré : 82 % au lieu de 0 %

Ce `82 %` signifie **confiance diagnostique du candidat récupéré**, et non
« 82 % d'éligibilité CIR ».

## Installation

Arrêter backend/worker, puis :

```powershell
cd C:\EnnoSmart

python `
  "C:\EnnoSmart\EnnoDiagnostic_score_only_fix_v5_7\install_score_only_fix_v5_7.py" `
  --repo "C:\EnnoSmart"

python `
  "C:\EnnoSmart\EnnoDiagnostic_score_only_fix_v5_7\verify_score_only_fix_v5_7.py" `
  --repo "C:\EnnoSmart"
```

Puis redémarrer le backend.

Il n'est pas nécessaire de relancer `Préparer les sources`.
Le `diagnostic_display_service` corrige également l'affichage des runs déjà
persistés. Si l'interface conserve une ancienne réponse en cache, rafraîchir la page.
