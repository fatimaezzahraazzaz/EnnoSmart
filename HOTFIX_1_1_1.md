# Hotfix 1.1.1

Corrections :

- Brave Web Search : remplacement de `safesearch=strict` par `moderate`.
- Brave : repli automatique sur une requête minimale si l'API renvoie HTTP 422.
- Brave : suppression de `extra_snippets` pour compatibilité avec tous les plans.
- Brave : quatre variantes de recherche DOI/titre.
- HTTP : gestion de `Retry-After` pour les réponses 429.
- HTTP : absence de retries inutiles sur les erreurs 4xx non transitoires.
- Diagnostic HTTP : le corps de l'erreur est maintenant conservé.
- Version serveur : 1.1.1.
