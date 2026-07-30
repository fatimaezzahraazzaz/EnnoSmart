# Rapport de validation — EnnoScholar Legal Fulltext MCP v1.1

Date de validation : 2026-07-18

## Vérifications réalisées

- Compilation Python complète : réussie.
- Tests unitaires : **8/8 réussis**.
- Validation DOI identique : réussie.
- Rejet DOI différent : réussi.
- Validation par titre/auteurs/année : réussie.
- Cache SQLite TTL : réussi.
- Ranking des candidats : réussi.
- Classification page universitaire : `public_author_copy` réussie.
- Classification dépôt HAL : `repository_copy` réussie.
- Blocage des domaines de redistribution non autorisée : réussi.

## Providers présents

- existing_url
- unpaywall
- core
- openalex
- semantic_scholar
- hal
- arxiv
- europe_pmc
- zenodo
- public_web (Brave Search, optionnel)

## Limitations connues

- Les tests automatisés n'appellent pas les API externes afin de rester reproductibles.
- Les providers nécessitant une clé sont désactivés si la variable correspondante est vide.
- Une URL publique dont la licence n'est pas déclarée est marquée `publicly_accessible_license_unknown`, et non comme licence Open Access explicite.
- Le provider `public_web` exige `BRAVE_SEARCH_API_KEY`.
- Le PDF est vérifié par signature/contenu HTTP, mais l'extraction scientifique reste assurée par `scholar_pdf_direct_extractor.py`.
