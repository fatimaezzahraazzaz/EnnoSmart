# EnnoDiagnostic — Correctif narratif V5.3

## Portée volontairement limitée

Cette version corrige uniquement la publication des faits dans :

- Synthèse stratégique
- Objectif global
- Démarche
- Résultats / métriques
- Paramètres / contraintes
- Chaîne R&D utilisée par la lecture Frascati

Elle NE remplace PAS :

- `consultant_verrou_synthesizer.py`
- `scientific_axis_synthesizer.py`
- `structured_eligibility_writer.py`

La détection/récupération des verrous dans `ennodiagnostic_agent.py` est protégée
par une garde de hash AST pendant l'installation. Si une fonction verrou change,
l'installation échoue et restaure les fichiers.

## Règle centrale

`role NLP` n'est plus suffisant pour publier un fait.

Pour être publié :
- objectif = finalité explicite + acteur projet ;
- démarche = action réellement exécutée + acteur projet ;
- résultat = observation/mesure projet ou document de résultats ;
- paramètre = contrainte/paramètre projet ; un nombre issu seulement d'une
  transcription est bloqué ;
- littérature, questions de réunion, consignes, listes de fichiers et méthodes
  tierces restent dans l'audit mais ne deviennent jamais des faits projet.

## Installation

ARRÊTER backend et worker avant l'installation.

```powershell
cd C:\EnnoSmart

python `
  "C:\EnnoSmart\EnnoDiagnostic_narrative_fix_v5_3\install_narrative_fix_v5_3.py" `
  --repo "C:\EnnoSmart"
```

Puis :

```powershell
python `
  "C:\EnnoSmart\EnnoDiagnostic_narrative_fix_v5_3\verify_narrative_fix_v5_3.py" `
  --repo "C:\EnnoSmart"
```

## Test

Comme `demarche_legibility.py` et `frascati_assessment.py` changent :

1. redémarrer backend + worker ;
2. relancer **Préparer les sources une fois** ;
3. lancer **Diagnostic**.

Le correctif ne fixe aucun nombre de verrous. Il conserve exactement le chemin
V5.2 de détection/synthèse ; il ne doit donc ni forcer 3 verrous, ni en recréer 15.
