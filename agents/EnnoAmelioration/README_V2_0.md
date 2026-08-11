# EnnoAmelioration V2.0 — Precise EnnoScholar Core

Cette version conserve toutes les corrections V1.1 → V1.9 et remplace uniquement le moteur utilisé lors d'une **nouvelle recherche scientifique ciblée**.

## Architecture

- EnnoAmel détecte la demande et demande confirmation si nécessaire.
- Guided Research conserve la session, l'onglet Sources et la validation humaine.
- La récupération scientifique n'utilise plus `WebResearchService` générique.
- Elle appelle le moteur principal `EnnoScholarAgent.run_search`, donc :
  `scientific_intent_builder → query_builder → paper_ranker → BGE reranker`.
- Une demande de **nouvelle** recherche force `force_refresh=True`.
- Les publications postérieures à l'année CIR sont exclues si cette année est disponible dans le projet.
- Les sources déjà explicitement gardées/rejetées ne sont pas reproposées comme nouvelles.
- Les candidats restent `proposed` jusqu'à validation du consultant.
- Aucun fallback silencieux vers une recherche générique n'est autorisé si le coeur EnnoScholar est indisponible.

## Installation

Remplacer le dossier actuel `agents/EnnoAmelioration` par le contenu de cette archive puis redémarrer le backend.

Aucune modification du dossier EnnoScholar n'est nécessaire.
