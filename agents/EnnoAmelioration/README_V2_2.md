# EnnoAmelioration V2.2 — Orchestration réelle EnnoDiagnostic → EnnoScholar

Cette version conserve les garde-fous et writers des versions précédentes et corrige la chaîne scientifique.

## Principe

EnnoAmel ne fabrique jamais un verrou ou un domaine scientifique de substitution.

- amélioration purement éditoriale : EnnoAmel writer ;
- amélioration CIR/argumentative/scientifique : réutilisation d'EnnoDiagnostic ;
- si aucun diagnostic n'existe : lancement automatique du vrai `run_ennodiagnostic(db, project)` ;
- nouvelle recherche : le verrou Diagnostic pertinent + `domain_detection` NLP + preuves locales sont transmis au moteur principal EnnoScholar ;
- aucun fallback basé uniquement sur le titre de section n'est accepté pour une recherche scientifique ;
- les nouvelles publications restent candidates jusqu'à validation humaine.

## Flux

`EnnoAmel -> EnnoDiagnostic (reuse/run) -> contexte/verrous/preuves -> EnnoScholar core -> sources candidates -> validation consultant -> rédaction`

## Fichiers principaux modifiés

- `application/diagnostic_orchestration_service.py` (nouveau)
- `application/agent.py`
- `application/research_orchestration_service.py`
- `tests/test_agent_orchestration_v2_2.py`
