# EnnoDiagnostic Balanced/Fast V5.4

Cette version a été construite à partir du ZIP V5.3 exact fourni par l'utilisateur.
Elle corrige les sections narratives et le temps de Diagnostic **sans modifier la mécanique des verrous**.

## Périmètre du correctif

Seulement 3 fichiers sont remplacés :

- `agents/EnnoDiagnostic/ennodiagnostic_agent.py`
- `agents/EnnoDiagnostic/diagnostic_static_presenter.py`
- `agents/EnnoDiagnostic/project_fact_gate.py`

Les fichiers suivants ne sont PAS contenus dans le patch et ne sont PAS remplacés :

- `consultant_verrou_synthesizer.py`
- `scientific_axis_synthesizer.py`

L'installateur protège également par hash les quatre fonctions de l'agent qui chargent, récupèrent, reformulent et enrichissent les verrous. Il refuse l'installation si la base locale n'est plus celle attendue.

## Corrections fonctionnelles

- Filtrage des faits projet avant troncature du pack NLP : les vrais faits situés après le top-30 brut ne sont plus perdus.
- Objectif : accepte un objectif exprimé par l'équipe même dans une transcription, mais bloque questions, administratif et littérature.
- Démarche : accepte les actions réellement exécutées par l'équipe ; bloque méthodes d'articles.
- Résultats : accepte les observations et tableaux de résultats du projet ; bloque cible, question, trace administrative et littérature.
- Paramètres : conserve seulement paramètres/contraintes attribuables au projet ; les chiffres de transcription non corroborés restent exclus.
- Les validations héritées ne peuvent plus annuler une décision positive du gate de provenance.
- Démarche/Résultats ne sont plus remplacés par les éléments de preuve Frascati officiels.
- Synthèse stratégique construite à partir des faits propres et des verrous finaux, pas du contexte global brut.
- Minimum dynamique d'items : une section avec un seul fait sûr n'est plus forcée à inventer un deuxième item.
- Les paragraphes peuvent récupérer leurs evidence IDs depuis le texte, ce qui évite des retries de grounding inutiles.

## Accélération

Après `Préparer les sources`, `nlp_result.json` est déjà l'autorité structurée. V5.4 active par défaut :

`ENNOSMART_DIAG_FAST_NLP_AUTHORITY=1`

Le chemin normal de `Diagnostic` réutilise directement le NLP courant et évite les recherches Chroma répétées, notamment la recherche verrou très large. L'ancien chemin RAG reste disponible avec :

`ENNOSMART_DIAG_FAST_NLP_AUTHORITY=0`

L'analyse LLM Pydantic de Frascati est aussi désactivée par défaut parce que le run fourni échouait systématiquement avant de retomber sur le fallback déterministe :

`ENNOSMART_DIAG_FRASCATI_USE_LLM=0`

Elle peut être réactivée explicitement avec `1`.

## Installation

Arrêter backend et worker avant installation.

```powershell
cd C:\EnnoSmart

python `
  "C:\EnnoSmart\EnnoDiagnostic_balanced_fast_fix_v5_4\install_balanced_fast_fix_v5_4.py" `
  --repo "C:\EnnoSmart"
```

Puis :

```powershell
python `
  "C:\EnnoSmart\EnnoDiagnostic_balanced_fast_fix_v5_4\verify_balanced_fast_fix_v5_4.py" `
  --repo "C:\EnnoSmart"
```

## Premier test

Parce que `project_fact_gate.py` est utilisé aussi pendant la préparation NLP/Frascati :

1. redémarrer backend + worker ;
2. lancer **Préparer les sources une seule fois** ;
3. lancer **Diagnostic**.

Les Diagnostics suivants peuvent être relancés sans préparer les sources si les documents n'ont pas changé.

## Logs attendus

Le Diagnostic doit afficher notamment :

- `[EnnoDiagnostic][FAST_NLP_AUTHORITY] chroma=0 ...`
- `[EnnoDiagnostic][CURRENT_NLP_PACK] ...`
- plus d'erreur Pydantic Frascati par défaut ;
- `[EnnoDiagnostic][PERF] ...` avec le détail des étapes.

Le nombre de verrous n'est jamais hardcodé. Sur le snapshot fourni, la logique verrou elle-même reste strictement celle qui produisait 3 verrous.
