# Patch EnnoDiagnostic + Memory V2

But : brancher la base `experience_memory_v2` dans EnnoDiagnostic, sans toucher au NLP.

Ce patch ajoute :

```txt
EnnoDiagnostic
→ sources RAG du dossier courant = preuves factuelles
→ Memory V2 = projets similaires / continuité / style / anti-faux positifs
→ LLM = reformulation prudente
```

## Fichiers

```txt
memory_v2_retriever.py
style_memory.py
cir_memory_v2_adapter.py
patch_ennodiagnostic_agent.py
```

## Installation

Extrais le zip, puis depuis le dossier extrait :

```powershell
cd C:\EnnoSmart

New-Item -ItemType Directory -Force "C:\EnnoSmart\modules\EXPERIENCE_MEMORY"
Copy-Item ".\memory_v2_retriever.py" "C:\EnnoSmart\modules\EXPERIENCE_MEMORY\memory_v2_retriever.py" -Force

Copy-Item ".\style_memory.py" "C:\EnnoSmart\modules\CIR_STYLE_MEMORY\style_memory.py" -Force

Copy-Item ".\cir_memory_v2_adapter.py" "C:\EnnoSmart\modules\CIR_MEMORY\cir_memory_v2_adapter.py" -Force

.\.venv_memory\Scripts\python.exe .\patch_ennodiagnostic_agent.py
```

Si `patch_ennodiagnostic_agent.py` ne trouve pas ton agent, ouvre le script et ajoute le bon chemin dans `CANDIDATES`.

## Test

Relance le backend ou le script agent, puis lance EnnoDiagnostic.

Dans `ennodiagnostic_report.json`, vérifie :

```txt
memory_v2_report.ok = true
memory_v2_report.similar_projects_count > 0
inputs_status.memory_v2_similar_projects_count > 0
```

## Règle importante

Memory V2 ne remplace jamais les preuves du dossier courant.

```txt
✅ aide à retrouver les projets similaires
✅ aide à comprendre continuité / nouveauté
✅ aide à éviter faux verrous
✅ aide à reformuler en style consultant
❌ ne crée pas un verrou seule
❌ ne sert pas de preuve factuelle du nouveau dossier
```

## Pour l'onglet continuité

Le fichier `cir_memory_v2_adapter.py` expose :

```python
compare_current_with_memory_v2(...)
load_or_create_cir_memory_comparison = compare_current_with_memory_v2
```

Tu peux l'utiliser pour faire évoluer progressivement l'ancien onglet CIR précédent vers Memory V2.
