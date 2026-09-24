# EnnoDiagnostic V2 — architecture de stabilisation

Cette V2 propose une architecture plus simple pour EnnoDiagnostic et fonctionne
avec **documents bruts, pré-CIR, CIR structurés et plusieurs documents ensemble**.

## Pipeline

```text
DOCUMENT(S)
    ↓
Structure légère + chunks avec contexte
    ↓
LLM = extracteur sémantique principal
    ↓
Extraction multi-label par chunk
- objectifs
- verrous potentiels
- méthodes
- paramètres
- résultats
- preuves exactes
    ↓
FastJudge = signal secondaire, jamais un veto
    ↓
Fusion inter-chunks / inter-documents
    ↓
Qualification Frascati
    ↓
Validation consultant
    ↓
Pack traçable pour UI / RAG
```

## Principes

- Tous les documents utilisent le **même moteur sémantique**.
- Le type RAW / pré-CIR / CIR sert seulement de contexte.
- Un passage peut être à la fois `résultat` et preuve d'un `verrou`.
- FastJudge ne supprime jamais une extraction du LLM.
- Les regex servent au nettoyage et à la structure, pas à comprendre le sens.
- Frascati est appliqué **après** l'extraction.
- Une preuve doit être une citation du passage source.
- Aucun pourcentage global n'est produit si la qualification n'est pas fiable.

## Important

C'est une base V2 propre et exécutable, pas une promesse de perfection.
Avant de remplacer la version déployée, fais tourner V1 et V2 en parallèle sur
un benchmark manuel d'environ 10 dossiers.

Commence par :
1. `integration/MIGRATION_FROM_CURRENT.md`
2. `benchmark/README.md`
3. `examples/example_run.py`

Tests :
```bash
python -m pytest -q
```
