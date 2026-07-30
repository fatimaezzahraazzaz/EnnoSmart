# -*- coding: utf-8 -*-
from __future__ import annotations

"""Writer historique générique conservé pour compatibilité.

Le writer canonique est la Phase 5. Ce module ne contient plus d'ontologie
bâtiment, matériau ou autre domaine. Il produit uniquement un aperçu fondé sur
les métadonnées et résumés réellement fournis.
"""

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from .utils import clean_text, flatten_text, norm, tokenize


def _citation_label(article: Dict[str, Any], index: int = 1) -> str:
    raw = (
        article.get("citation_label")
        or article.get("citation")
        or article.get("label")
        or article.get("citation_token")
        or f"A{index}"
    )
    match = re.search(r"\bA(\d+)\b", str(raw), flags=re.I)
    return f"A{match.group(1)}" if match else f"A{index}"


def _citation_token(article: Dict[str, Any], index: int = 1) -> str:
    return f"[{_citation_label(article, index)}]"


def _abstract(article: Dict[str, Any]) -> str:
    return clean_text(
        article.get("abstract")
        or article.get("tldr")
        or article.get("summary")
        or article.get("resume"),
        1800,
    )


def _sentences(text: str) -> List[str]:
    return [
        clean_text(item, 700)
        for item in re.split(r"(?<=[.!?])\s+|\n+", text or "")
        if len(clean_text(item, 700)) >= 30
    ]


def _article_terms(article: Dict[str, Any]) -> List[str]:
    text = " ".join(
        [
            clean_text(article.get("title"), 400),
            _abstract(article),
            " ".join(map(str, article.get("keywords") or [])),
        ]
    )
    stop = {
        "avec", "dans", "pour", "sans", "entre", "cette", "article", "étude",
        "study", "paper", "using", "based", "from", "that", "this", "the",
        "and", "des", "les", "une", "sur", "par", "que", "qui", "est",
        "sont", "method", "méthode", "result", "résultat",
    }
    return [
        token
        for token in tokenize(norm(text))
        if len(token) >= 4 and token not in stop and not token.isdigit()
    ]


def build_article_insights(citation_articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    insights: List[Dict[str, Any]] = []
    for index, article in enumerate(citation_articles or [], 1):
        abstract = _abstract(article)
        contribution = _sentences(abstract)[:2]
        limitations: List[str] = []
        if not abstract:
            limitations.append("Résumé scientifique absent : utilisation limitée aux métadonnées.")
        tag = clean_text(article.get("tag"), 60)
        if tag.lower() in {"connexe", "related"}:
            limitations.append("Source connexe : la transposition au verrou doit rester explicitement justifiée.")
        elif tag.lower() in {"fondamental", "fundamental", "background"}:
            limitations.append("Source de cadrage : elle ne constitue pas à elle seule une preuve directe.")
        insights.append(
            {
                "citation_token": _citation_token(article, index),
                "label": _citation_label(article, index),
                "title": clean_text(article.get("title"), 320),
                "year": article.get("year"),
                "tag": tag,
                "relevance_score": article.get("relevance_score"),
                "contribution_sentences": contribution,
                "limitations": limitations,
                "terms": _article_terms(article)[:18],
            }
        )
    return insights


def build_knowledge_map(citation_articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    insights = build_article_insights(citation_articles)
    frequencies = Counter(
        term
        for insight in insights
        for term in insight.get("terms") or []
    )
    dominant_terms = [term for term, _ in frequencies.most_common(20)]
    return {
        "insights": insights,
        "dominant_terms": dominant_terms,
        "articles_with_abstract": sum(
            1 for article in citation_articles or [] if _abstract(article)
        ),
    }


def build_gap_analysis_from_articles(
    verrou_item: Dict[str, Any],
    citation_articles: List[Dict[str, Any]],
    project_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    explicit: List[str] = []
    for article in citation_articles or []:
        for key in ("limitations", "limits", "research_gap", "gap", "open_questions"):
            value = article.get(key)
            if isinstance(value, list):
                explicit.extend(clean_text(item, 650) for item in value)
            else:
                text = clean_text(value, 650)
                if text:
                    explicit.append(text)
    existing = clean_text(verrou_item.get("gap_analysis"), 1200)
    if existing:
        explicit.insert(0, existing)
    return {
        "gaps": list(dict.fromkeys(item for item in explicit if item))[:12],
        "gap_status": "documented" if explicit else "not_explicitly_documented",
    }


def build_consultant_state_of_art_context(
    verrou_item: Dict[str, Any],
    citation_articles: List[Dict[str, Any]],
    project_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    intent = verrou_item.get("scientific_intent")
    intent = intent if isinstance(intent, dict) else {}
    title = clean_text(
        verrou_item.get("verrou_title")
        or verrou_item.get("title")
        or intent.get("verrou_title"),
        500,
    )
    if not title:
        raise ValueError("verrou_title manquant : aucun titre automatique n'est créé.")
    knowledge = build_knowledge_map(citation_articles)
    gaps = build_gap_analysis_from_articles(verrou_item, citation_articles, project_context)
    return {
        "writer_version": "generic_evidence_only_v1",
        "verrou_id": clean_text(verrou_item.get("verrou_id") or verrou_item.get("id"), 120),
        "verrou_title": title,
        "scientific_problem": clean_text(intent.get("scientific_problem"), 1200),
        "technical_object": clean_text(intent.get("technical_object"), 600),
        "phenomenon": clean_text(intent.get("phenomenon"), 600),
        "constraints": intent.get("constraints") or [],
        "methods": intent.get("methods") or [],
        "project_context_text": flatten_text(project_context or {}, max_chars=3000),
        "selected_articles_count": len(citation_articles or []),
        **knowledge,
        **gaps,
    }


def _references(citation_articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    for index, article in enumerate(citation_articles or [], 1):
        refs.append(
            {
                "label": _citation_label(article, index),
                "title": clean_text(article.get("title"), 400),
                "authors": article.get("authors") or [],
                "year": article.get("year"),
                "venue": article.get("venue"),
                "doi": article.get("doi"),
                "url": article.get("url") or article.get("open_access_url"),
            }
        )
    return refs


def build_consultant_template_state_of_art(
    verrou_item: Dict[str, Any],
    citation_articles: List[Dict[str, Any]],
    project_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    context = build_consultant_state_of_art_context(
        verrou_item,
        citation_articles,
        project_context,
    )
    if not citation_articles:
        return {
            "ok": False,
            "mode": "blocked_no_evidence",
            "writer_context": context,
            "draft": "",
            "references": [],
            "citation_guard": {"ok": False, "unknown_citations": []},
            "warnings": ["Aucun article sélectionné : rédaction scientifique bloquée."],
        }

    paragraphs: List[str] = [
        f"### Analyse de l’état de l’art — {context['verrou_title']}",
    ]
    for insight in context.get("insights") or []:
        sentences = insight.get("contribution_sentences") or []
        if not sentences:
            continue
        paragraphs.append(
            " ".join(sentences)
            + " "
            + str(insight.get("citation_token"))
        )
    for gap in context.get("gaps") or []:
        paragraphs.append(f"La littérature sélectionnée signale également la limite suivante : {gap}")
    if len(paragraphs) == 1:
        return {
            "ok": False,
            "mode": "blocked_missing_article_content",
            "writer_context": context,
            "draft": "",
            "references": _references(citation_articles),
            "citation_guard": {"ok": False, "unknown_citations": []},
            "warnings": ["Les articles sélectionnés ne contiennent aucun résumé exploitable."],
        }

    draft = "\n\n".join(paragraphs)
    allowed = {_citation_label(article, index) for index, article in enumerate(citation_articles, 1)}
    detected = set(re.findall(r"\[(A\d+)\]", draft, flags=re.I))
    unknown = sorted(label.upper() for label in detected if label.upper() not in allowed)
    return {
        "ok": not unknown,
        "mode": "generic_evidence_only_template",
        "writer_context": context,
        "draft": draft,
        "references": _references(citation_articles),
        "citation_guard": {
            "ok": not unknown,
            "unknown_citations": unknown,
            "allowed_citations": sorted(allowed),
        },
        "warnings": [],
    }


def build_state_of_art_section(
    verrou_result: Dict[str, Any],
    max_articles: int = 5,
) -> Dict[str, Any]:
    articles = [
        article
        for article in verrou_result.get("articles") or []
        if isinstance(article, dict)
        and clean_text(article.get("tag"), 40).lower() not in {"hors sujet", "out_of_scope"}
    ][: max(1, int(max_articles))]
    result = build_consultant_template_state_of_art(verrou_result, articles)
    return {
        "verrou_id": verrou_result.get("verrou_id"),
        "verrou_title": verrou_result.get("verrou_title"),
        "draft": result.get("draft", ""),
        "references": result.get("references", []),
        "status": "ok" if result.get("ok") else result.get("mode"),
        "warnings": result.get("warnings", []),
    }

