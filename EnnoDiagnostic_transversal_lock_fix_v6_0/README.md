# EnnoDiagnostic — Transversal Lock Fix V6.0

Objectif : améliorer la remontée d'abstraction des verrous **sans perdre les verrous
atomiques existants et sans modifier les autres sections stabilisées**.

## Modifiés
- `agents/EnnoDiagnostic/scientific_axis_synthesizer.py`
- `agents/EnnoDiagnostic/ennodiagnostic_agent.py`

## Fournis mais volontairement inchangés
- `agents/EnnoDiagnostic/consultant_verrou_synthesizer.py`
- `modules/NLP/semantic_lock_finalizer.py`

## Principe
- Les verrous atomiques NLP/Frascati restent l'autorité.
- Aucun verrou existant n'est supprimé, fusionné ou remplacé.
- Un axe parent transversal peut être **ajouté** uniquement si :
  - il relie au moins deux verrous courants distincts ;
  - chaque verrou apporte une preuve courante ;
  - le mécanisme commun, le titre et l'incertitude sont ancrés dans les preuves ;
  - les gardes scientifiques déjà présentes acceptent la fusion ;
  - l'axe n'est ni un KPI, ni une méthode seule, ni un objectif générique, ni un cas d'usage.
- Les axes singleton ne sont jamais ajoutés.
- En cas d'erreur, le système revient automatiquement aux verrous atomiques inchangés.
- Les sections objectif/démarche/résultats/paramètres/Frascati continuent d'utiliser
  les verrous atomiques, afin de ne pas régresser.

## Activation
Activée par défaut lorsque le projet contient au moins deux verrous atomiques.
Désactivation possible :
`ENNOSMART_DIAG_USE_SCIENTIFIC_AXIS_CONSOLIDATION=0`

## Installation
```powershell
cd C:\EnnoSmart
python "CHEMIN\EnnoDiagnostic_transversal_lock_fix_v6_0\install_transversal_lock_fix_v6_0.py" --repo "C:\EnnoSmart"
python "CHEMIN\EnnoDiagnostic_transversal_lock_fix_v6_0\verify_transversal_lock_fix_v6_0.py" --repo "C:\EnnoSmart"
```

L'installateur sauvegarde automatiquement les deux fichiers remplacés dans un dossier
`EnnoSmart_patch_backups` à côté du dépôt.
