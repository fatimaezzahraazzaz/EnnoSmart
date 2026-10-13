# Corrections EnnoScholar 2.0.0

## Flux canonique

```text
confirmed_verrous.json (EnnoDiagnostic)
    → recherche et sélection consultant
    → Article Cards
    → Phase 4 / 4.5 / 4.6
    → Phase 4.7 : histoire scientifique globale
    → plan consultant approuvé + ordre de rédaction (si flux chat)
    → Phase 5 : état de l’art global evidence-first
```

## Corrections fonctionnelles

- `contracts.py` : contrat strict pour les verrous et le plan consultant.
- `verrou_selector.py` et `scholar_agent.py` : aucune reconstruction depuis le
  NLP, aucun identifiant `scholar_topic_*`, aucune troncature de verrous.
- `scientific_intent_builder.py`, `paper_ranker.py` et `query_builder.py` :
  logique générique construite depuis le dossier courant.
- `state_of_art_writer.py` : ancien fallback remplacé par un composant générique
  evidence-only.
- Phases 4, 4.6 et 4.7 : contrôle exact des identifiants, titres et ordre.
- `consultant_plan_service.py` : proposition, modification, approbation,
  empreinte d’intégrité et ordre explicite de rédaction.
- Phase 5 : writer global unique ; titres consultant conservés ; tous les
  verrous restent présents ; citations limitées aux Article Cards ; blocage en
  l’absence de preuves.
- Ancien writer `write-selection` par verrou : désactivé comme writer final.
- Modèles GPT-5 : absence de `temperature` non standard et utilisation de
  `max_completion_tokens` avec l’API OpenAI.
- Mémoire V2 : désactivée par défaut et jamais utilisée comme preuve.
- Clients HTTP : suppression des modifications globales de timeout.
- Chemins de stockage : surcharge possible par variables d’environnement.
- Installeur : copie récursive de l’agent complet, sauvegarde préalable et
  exécution automatique des tests.
- Dépendances et documentation d’installation ajoutées.

## Validation

`check_ennoscholar_complete.py` compile tous les modules, importe le paquet et
exécute les tests. Les tests couvrent notamment :

- immutabilité des verrous ;
- aliases configurables ;
- refus du NLP comme source de verrous ;
- validation et autorisation du plan ;
- refus d’un verrou inventé dans le plan ;
- blocage sans Article Cards ;
- absence de contamination entre projets ;
- désactivation de la mémoire antérieure ;
- désactivation de l’ancien writer concurrent ;
- compatibilité GPT-5.
