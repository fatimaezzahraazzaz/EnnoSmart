# Architecture détaillée

## 1. Documents
Tous les documents deviennent des `DocumentInput`.
Le mode `raw`, `pre_cir` ou `cir` ne change pas le moteur d'analyse.

## 2. Chunking
Le découpage conserve paragraphes, puces, titres et provenance.
Les petites phrases ne sont pas supprimées arbitrairement.
Le contexte avant/après est fourni au LLM, mais la preuve finale doit venir du
passage source.

## 3. Extraction sémantique
Le LLM retourne du JSON multi-label.
Un même chunk peut contenir :
- objectif ;
- verrou ;
- méthode ;
- paramètre ;
- résultat.

Pour un verrou :
- explicite ou implicite ;
- formulation courte ;
- question technique non résolue ;
- citation exacte ;
- justification.

## 4. FastJudge
FastJudge annote les sorties.
S'il n'est pas d'accord avec le LLM, on marque `needs_review=True`.
Il ne supprime rien.

## 5. Fusion
Les formulations proches sont fusionnées entre chunks et entre documents.
Toutes les preuves restent conservées.

## 6. Frascati
La qualification est indépendante de la détection.
Statuts :
- supported
- partial
- insufficient
- not_evaluated

Pas de score global 0–100 par défaut.

## 7. Consultant
Tous les verrous peuvent être envoyés dans une file :
- pending
- accepted
- edited
- rejected
