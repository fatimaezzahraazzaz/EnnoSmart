# EnnoSmart Memory V2 FINAL — sans reconstruction de verrous

Correction appliquée :
- La mémoire basée sur un CIR final ne garde que les passages explicitement présents dans le CIR.
- Les verrous reconstruits automatiquement sont supprimés :
  - `implicit`
  - `universal`
  - `verrou_implicite_a_verifier`
  - `universal_theme_reconstruction`
  - `frascati_universal_theme_to_validate`

## Installation

```powershell
Copy-Item .\experience_memory_v2_engine.py C:\EnnoSmart\scripts\experience_memory_v2_engine.py -Force
Copy-Item .\experience_memory_v2_manager_streamlit.py C:\EnnoSmart\scripts\experience_memory_v2_manager_streamlit.py -Force
```

## Nettoyer puis retester

```powershell
Remove-Item "C:\EnnoSmart\storage\experience_memory_v2" -Recurse -Force -ErrorAction SilentlyContinue
```

Puis relance Streamlit :

```powershell
cd C:\EnnoSmart
.\.venv_memory\Scripts\python.exe -m streamlit run scripts\experience_memory_v2_manager_streamlit.py
```

Ajoute le CIR et traite-le.

Dans le nouveau run, tu dois voir le log :

```txt
filter_explicit_cir_final
```

Et les chunks/cards ne doivent plus contenir :

```txt
Verrou implicite possible
```
