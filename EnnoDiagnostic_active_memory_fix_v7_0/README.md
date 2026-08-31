# EnnoDiagnostic — Active Scientific Memory V7.0

## But

Faire du CIR précédent du **même projet** une mémoire scientifique active :

- il aide à retrouver un verrou de continuité qu'un dossier N incomplet ou un premier passage NLP a pu manquer ;
- il aide à chercher les démarches, résultats, paramètres et objectifs de continuité dans les documents N ;
- il aide `Objectif global` et `Synthèse stratégique` à rester au bon niveau d'abstraction et à ne pas confondre un cas d'étude local avec l'objectif global ;
- il ne devient **jamais** une preuve factuelle de l'année N à lui seul.

La règle reste :

`CIR N-1 -> hypothèse de continuité -> recherche dans les documents N -> preuve N -> affichage dans N`

## Fichiers remplacés

1. `agents/EnnoDiagnostic/historical_continuity_reconciler.py`
2. `agents/EnnoDiagnostic/scientific_axis_synthesizer.py`
3. `agents/EnnoDiagnostic/diagnostic_static_presenter.py`
4. `agents/EnnoDiagnostic/ennodiagnostic_agent.py`

`modules/NLP/evidence_graph.py` est fourni pour référence seulement et n'est pas remplacé.

## Corrections

### Verrous historiques
- Les passages historiques classés `limite` ne sont plus perdus dès qu'un `verrou` explicite existe.
- Un matching lexical moyen ne suffit plus à empêcher prématurément le `gap probe`.
- Le `gap probe` cherche plusieurs facettes du verrou historique : verrou/limite, méthode, résultat, paramètre, objectif, contribution.
- Une continuité peut être confirmée par **plusieurs indices N complémentaires** au lieu d'exiger qu'un seul passage ressemble textuellement au titre du CIR N-1.
- Un verrou récupéré apparaît avec les verrous N uniquement s'il possède des preuves N.
- Les verrous atomiques déjà détectés restent conservés.

### Objectif et synthèse
- La mémoire N-1 peut orienter le niveau d'abstraction lorsque les documents N confirment la trajectoire.
- Un équipement, une campagne ou une configuration locale ne remplace pas automatiquement l'objectif global.
- Aucune donnée historique seule n'est présentée comme un fait N.

### Démarches / résultats / paramètres
- Les contextes historiques déjà produits par le synthétiseur sont maintenant réellement branchés jusqu'au presenter.
- La mémoire sert à retrouver des indices N ; les affirmations courantes restent ancrées dans N.
- Un résultat N-1 n'est jamais recopié comme résultat N sans résultat courant.

### Préservation V6.0
- Les verrous atomiques restent intacts.
- Les axes transversaux V6 restent additifs.
- Les axes singleton restent rejetés.

## Non modifié

- Frascati et ses scores
- V5.7 score handling
- chat / RAG
- frontend
- extraction documentaire
- provenance
- règles de non-hallucination
- nombre cible de verrous
- aucune règle spécifique à VECAME, AI-CODE ou à un domaine technique

## Installation

Décompresser le dossier `EnnoDiagnostic_active_memory_fix_v7_0` dans `C:\EnnoSmart`, puis :

```powershell
cd C:\EnnoSmart

python `
  ".\EnnoDiagnostic_active_memory_fix_v7_0\install_active_memory_fix_v7_0.py" `
  --repo "C:\EnnoSmart"

python `
  ".\EnnoDiagnostic_active_memory_fix_v7_0\verify_active_memory_fix_v7_0.py" `
  --repo "C:\EnnoSmart"
```

L'installateur crée automatiquement une sauvegarde des quatre fichiers remplacés dans
`C:\EnnoSmart_patch_backups\active_memory_v7_0_<timestamp>` (à côté du dépôt).

## Premier test recommandé

Relancer le même projet avec **exactement les mêmes documents N** afin d'isoler l'effet du code.

À contrôler :

1. les verrous déjà présents avant V7 sont toujours là ;
2. un verrou historique supplémentaire n'apparaît que si des indices N convergents existent ;
3. l'objectif/synthèse deviennent moins locaux lorsqu'une continuité historique du même projet est réellement disponible ;
4. aucune démarche/résultat historique n'est présenté comme exécuté en N sans preuve N.

Si aucun CIR N-1 du même projet n'est présent/indexé dans la mémoire, V7 ne peut évidemment pas inventer cette continuité : le diagnostic courant continue alors de fonctionner normalement.
