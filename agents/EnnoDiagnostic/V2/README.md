# EnnoDiagnostic V2 — moteur actif

Le backend Diagnostic et le diagnostic ciblé d’EnnoAmélioration utilisent V2.
L’agent Diagnostic V1 a été retiré. Le dossier `modules/NLP` conserve uniquement
les composants encore partagés avec l’extraction documentaire, la mémoire CIR
et le routage d’EnnoAmélioration.

## Flux actuel

```text
documents → extraction texte déterministe → fulltext + index RAG
    → Global Fast Path : dossier global ou lots selon le préflight
    → validation des faits et preuves + audit de complétude
    → consolidation des verrous → qualification
    → familles + synthèse projet + démarche R&D + Frascati
    → scoring Python → PostgreSQL → UI
```

Le fallback d’extraction sémantique par chunks est un chemin **V2**, indépendant
et conservé. Il ne réactive aucun code Diagnostic V1.

La revue globale peut enrichir la vue des faits selon les incertitudes détectées.
Les preuves et identifiants sources restent conservés. La consolidation traite
les relations contradictoires localement, sans abandonner les composants
cohérents ni modifier les règles d’incertitude autonome.

## Configuration

- `ENNOSMART_DIAGNOSTIC_GLOBAL_FAST_PATH=1`
- `ENNOSMART_DIAGNOSTIC_GLOBAL_REASONER_PROVIDER=openai`
- `ENNOSMART_DIAGNOSTIC_GLOBAL_REASONER_MODEL=gpt-5`
- `ENNOSMART_DIAGNOSTIC_GLOBAL_FAST_EFFORT=medium`
- `ENNOSMART_DIAGNOSTIC_GLOBAL_REASONER_EFFORT=high`

Le modèle global est indépendant du modèle choisi pour les autres agents.
Le cache global est stocké dans `analysis_cache/global_fast` du projet.
Les chunks RAG restent disponibles pour le chat et les citations.

Voir `docs/UPGRADE_DIAGNOSTIC_V2.md` pour le remplacement de l’application serveur.
