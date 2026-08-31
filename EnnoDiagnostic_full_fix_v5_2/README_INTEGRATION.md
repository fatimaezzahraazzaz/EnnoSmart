# EnnoDiagnostic Full Fix V5.2

Ce paquet contient des FICHIERS COMPLETS, construits à partir du ZIP local fourni après V4.1.

## Correction structurante

1. Les groupes `verrous_rnd_locaux` du NLP restent l'autorité.
2. Les chunks Chroma, titres, tableaux, RQ, méthodes et paramètres ne créent plus automatiquement des verrous.
3. Faux groupes évidents :
   - littérature / survey / articles tiers ;
   - tableau brut ;
   - méthode ou paramètre sans incertitude propre ;
   sont retirés de la vue consultant.
4. Une contrainte forte oubliée peut être récupérée depuis le corpus courant uniquement si elle est explicitement formulée et ancrée dans l'équipe/projet.
5. `etat_art_local` / littérature tierce ne peut plus devenir objectif, démarche, résultat ou paramètre du projet.
6. `scientific_axis_synthesizer` est désactivé par défaut dans le chemin officiel et devient audit-only.
7. Démarche/Frascati : les références externes ne peuvent plus compléter artificiellement la chaîne R&D du projet.
8. Les nombres provenant seulement d'une transcription doivent être présentés comme à confirmer.
9. Aucun nom de projet, modèle, verrou AI-CODE, fichier particulier ou nombre cible de verrous n'est hardcodé.

## Installation

```powershell
cd C:\EnnoSmart

python "C:\EnnoSmart\EnnoDiagnostic_full_fix_v5\install_full_fix_v5.py" `
  --repo "C:\EnnoSmart"
```

Puis :

```powershell
python "C:\EnnoSmart\EnnoDiagnostic_full_fix_v5\verify_full_fix_v5.py" `
  --repo "C:\EnnoSmart"
```

## Test recommandé

Cette V5 change aussi la logique NLP/Frascati (`demarche_legibility.py`, `frascati_assessment.py`).
Pour tester toute la correction proprement :

1. redémarrer backend + worker ;
2. relancer **Préparer les sources UNE FOIS** pour recalculer `nlp_result.json` avec les nouveaux gardes ;
3. lancer **Diagnostic**.

Les runs Diagnostic suivants peuvent réutiliser les sources préparées.

## Correctif V5.1

La V5 pouvait lever :
`TypeError: clean_text() takes 1 positional argument but 2 were given`

La V5.1 rend `clean_text(text, max_chars=None)` rétrocompatible avec les anciens
appels et avec les nouveaux appels de compaction de la récupération des verrous.

## Correctif V5.2

La V5.2 traite les défauts observés après V5.1 :

- les chunks Chroma ne peuvent plus réintroduire une publication déjà exclue du pack NLP courant ;
- les résultats de revue de littérature sont détectés par attribution et répétition de phrase, pas par nom de projet ;
- les méthodes d'articles (« les auteurs... », « ils ont... », « une approche a été proposée... ») sont exclues des travaux projet ;
- une valeur numérique uniquement prononcée dans une transcription et non corroborée par une autre source projet n'est plus publiée comme paramètre acquis ;
- un verrou explicitement placé sous une section « Verrous scientifiques ou techniques » n'est plus supprimé parce que le LLM le formule mal ;
- un titre LLM invalide est récupéré en titre consultant fondé sur le même cluster, au lieu d'afficher « Signal technique à reformuler... ».
