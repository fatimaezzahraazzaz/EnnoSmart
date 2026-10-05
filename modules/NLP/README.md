# Composants partagés encore utilisés

Ce dossier n'est pas le moteur Diagnostic V1. Le diagnostic principal utilise
`agents/EnnoDiagnostic/V2/ennodiagnostic_v2`.

Les composants NLP conservés servent toujours à :

- extraire le texte des documents pour la V2 et la comparaison documentaire ;
- indexer la mémoire des CIR précédents (`cir_pipeline`, `pipeline_route`,
  `CIR`, et leurs dépendances) ;
- détecter le domaine et utiliser FastJudge dans EnnoAmélioration ;
- charger le modèle NLI requis par la mémoire CIR et les contrôles de sources.

`project_fact_gate.py` a été déplacé depuis l'ancien agent Diagnostic sans
modifier son contenu, pour préserver les règles des consommateurs partagés.

Ne pas supprimer ce dossier ni ses modèles sans migrer ces consommateurs.
