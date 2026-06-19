# -*- coding: utf-8 -*-
from __future__ import annotations

"""
state_of_art_writer.py — EnnoScholar V2

Génération simple et non-hallucinatoire d’un brouillon d’état de l’art.
Pas de LLM.
Uniquement à partir des articles trouvés.
"""

from typing import Any, Dict, List

from .utils import clean_text


def citation_label(article: Dict[str, Any]) -> str:
    authors = article.get("authors") or []
    year = article.get("year") or "s.d."
    if authors:
        first = str(authors[0]).split()[-1]
        return f"{first} et al., {year}"
    return f"{clean_text(article.get('title'), 40)}, {year}"


def build_state_of_art_section(verrou_result: Dict[str, Any], max_articles: int = 5) -> Dict[str, Any]:
    title = verrou_result.get("verrou_title") or verrou_result.get("scientific_intent", {}).get("verrou_title")
    articles = verrou_result.get("articles") or []

    selected = [a for a in articles if a.get("tag") in {"Direct", "Connexe"}][:max_articles]
    if not selected:
        selected = articles[:max_articles]

    paragraphs = []
    refs = []

    if not selected:
        return {
            "verrou_title": title,
            "draft": "Aucun article suffisamment pertinent n’a été trouvé automatiquement pour rédiger un état de l’art fiable.",
            "references": [],
        }

    paragraphs.append(
        f"Pour le verrou « {title} », la recherche bibliographique automatique a identifié plusieurs travaux "
        "permettant de situer le problème technique dans l’état des connaissances."
    )

    for art in selected:
        label = citation_label(art)
        paragraphs.append(
            f"- {label} : {clean_text(art.get('title'), 180)} "
            f"({art.get('tag')}, score {art.get('relevance_score')})."
        )
        refs.append({
            "label": label,
            "title": art.get("title"),
            "year": art.get("year"),
            "doi": art.get("doi"),
            "url": art.get("url"),
            "source": art.get("source"),
            "tag": art.get("tag"),
            "relevance_score": art.get("relevance_score"),
        })

    return {
        "verrou_title": title,
        "draft": "\n".join(paragraphs),
        "references": refs,
    }
