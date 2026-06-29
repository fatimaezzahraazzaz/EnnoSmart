# -*- coding: utf-8 -*-
from __future__ import annotations

"""
state_of_art_writer.py — EnnoScholar V134

Rédaction état de l'art style consultant CIR.

Objectif : ne pas faire un résumé article-par-article.
Le writer construit d'abord une matrice de connaissances :
- contexte scientifique du verrou ;
- apports établis par les articles ;
- limites / non-couvertures ;
- gap avec le cas projet ;
- sources techniques à consulter séparément.

Cette étape reste dans un seul agent EnnoScholar : pipeline interne, pas nouveaux agents visibles.
"""

import re
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from .utils import clean_text, flatten_text, norm


# ──────────────────────────────────────────────────────────────────────────────
# Helpers communs
# ──────────────────────────────────────────────────────────────────────────────


def _is_technical_source(article: Dict[str, Any]) -> bool:
    return (
        article.get("source") == "technical_catalog"
        or article.get("source_type") == "technical_reference"
        or article.get("tag") == "Technique"
    )


def _citation_token(article: Dict[str, Any], fallback_index: int = 1) -> str:
    token = article.get("citation_token") or article.get("token")
    if token:
        return str(token)
    cid = article.get("citation_id") or f"A{fallback_index}"
    cid = str(cid).strip().replace("[", "").replace("]", "")
    return f"[{cid}]"


def citation_label(article: Dict[str, Any]) -> str:
    authors = article.get("authors") or []
    year = article.get("year") or "s.d."
    if authors:
        first = str(authors[0] or "").strip()
        if first:
            return f"{first.split()[-1]} et al., {year}"
    return f"{clean_text(article.get('title'), 40)}, {year}"


def _paper_text(article: Dict[str, Any]) -> str:
    return " ".join([
        str(article.get("title") or ""),
        str(article.get("abstract") or article.get("tldr") or ""),
        str(article.get("venue") or ""),
        " ".join(map(str, article.get("fields_of_study") or [])),
    ])


def _has_any(text: str, terms: List[str]) -> bool:
    n = norm(text)
    return any(norm(t) in n for t in terms)


def _short_sentence(text: Any, max_chars: int = 420) -> str:
    s = clean_text(text, 1600)
    if len(s) <= max_chars:
        return s
    parts = re.split(r"(?<=[.!?])\s+", s)
    out = ""
    for p in parts:
        if len(out) + len(p) + 1 > max_chars:
            break
        out = (out + " " + p).strip()
    return out or s[:max_chars].rstrip() + "…"


# ──────────────────────────────────────────────────────────────────────────────
# Analyse interne style consultant : articles -> connaissances -> limites
# ──────────────────────────────────────────────────────────────────────────────


THEME_RULES: List[Tuple[str, List[str]]] = [
    ("matériaux biosourcés et propriétés intrinsèques", ["bio-based", "biosour", "hemp", "chanvre", "straw", "paille", "wood", "timber", "natural fibre", "vegetal", "plant-based"]),
    ("performance thermique, inertie, diffusivité et effusivité", ["thermal", "inertia", "diffusivity", "effusivity", "phase shift", "summer comfort", "overheating", "heat storage", "conductivity"]),
    ("comportement hygrothermique, humidité et risque fongique", ["hygrothermal", "moisture", "vapour", "vapor", "humidity", "mould", "mold", "fungal", "condensation", "WUFI"]),
    ("tenue au feu et réaction au feu", ["fire", "charring", "char", "smouldering", "smoldering", "reaction to fire", "fire resistance", "REI", "heat flux"]),
    ("stabilité mécanique, assemblages et comportement structurel", ["timber-concrete", "wood-concrete", "connector", "shear", "ductility", "seismic", "cyclic", "diaphragm", "composite floor"]),
    ("durabilité, vieillissement et comportement long terme", ["durability", "ageing", "aging", "long-term", "weathering", "service life", "degradation"]),
    ("évaluation environnementale et carbone", ["life cycle", "LCA", "embodied carbon", "carbon", "environmental", "RE2020"]),
    ("modélisation, simulation et validation expérimentale", ["model", "simulation", "numerical", "experimental", "test", "measurement", "validation", "benchmark"]),
]


GAP_RULES: List[Tuple[str, List[str], str]] = [
    (
        "transposabilité limitée aux systèmes constructifs réels",
        ["material", "properties", "characterization", "sample", "specimen", "laboratory"],
        "les travaux caractérisent souvent les matériaux ou éprouvettes dans des conditions contrôlées, mais documentent moins leur transposition à l'échelle d'un système constructif complet soumis aux contraintes du projet.",
    ),
    (
        "manque de données long terme en service",
        ["hygrothermal", "moisture", "mould", "durability", "long-term", "service life"],
        "les sources situent les phénomènes d'humidité, de vieillissement ou de risque biologique, mais les protocoles de suivi en ouvrage et les seuils de criticité ne sont pas toujours établis pour le cas spécifique du projet.",
    ),
    (
        "validation simultanée de contraintes contradictoires",
        ["fire", "thermal", "acoustic", "structural", "seismic", "comfort", "performance"],
        "les publications traitent souvent une famille de performance isolée, alors que le dossier impose de satisfaire simultanément plusieurs exigences techniques.",
    ),
    (
        "absence de preuve directe dans la configuration projet",
        ["timber frame", "wall", "assembly", "connector", "composite", "bio-based"],
        "les articles proches ne démontrent pas nécessairement que la solution est directement applicable à la configuration, aux matériaux et aux conditions d'usage du projet.",
    ),
]


def build_article_insights(citation_articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extraction déterministe d'insights exploitables, sans halluciner."""
    insights: List[Dict[str, Any]] = []
    for idx, a in enumerate(citation_articles, start=1):
        text = _paper_text(a)
        themes = [label for label, terms in THEME_RULES if _has_any(text, terms)]
        if not themes:
            themes = ["connaissance scientifique générale liée au verrou"]

        contribution = ""
        abstract = clean_text(a.get("abstract") or a.get("tldr"), 1400)
        if abstract:
            contribution = _short_sentence(abstract, 360)
        else:
            contribution = f"L'article intitulé « {clean_text(a.get('title'), 180)} » est associé au verrou par son titre et ses métadonnées, mais son résumé n'est pas disponible."

        limitations = []
        ntext = norm(text)
        if "review" in ntext or "state of the art" in ntext:
            limitations.append("source utile pour cadrer le domaine, mais moins probante qu'une validation expérimentale directement comparable au cas projet")
        if not abstract:
            limitations.append("résumé non disponible : exploitation à vérifier manuellement")
        if a.get("tag") == "Fondamental":
            limitations.append("source principalement fondamentale : à utiliser pour le contexte, pas comme preuve directe de solution")
        if a.get("tag") == "Connexe":
            limitations.append("source connexe : transposition au cas projet à argumenter")

        insights.append({
            "citation_token": _citation_token(a, idx),
            "label": citation_label(a),
            "title": clean_text(a.get("title"), 260),
            "year": a.get("year"),
            "tag": a.get("tag"),
            "relevance_score": a.get("relevance_score"),
            "themes": themes[:4],
            "contribution": contribution,
            "limitations": limitations[:3],
        })
    return insights


def build_knowledge_map(citation_articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    insights = build_article_insights(citation_articles)
    by_theme: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ins in insights:
        for theme in ins.get("themes") or []:
            by_theme[theme].append(ins)

    knowledge = []
    for theme, items in by_theme.items():
        direct = [x for x in items if x.get("tag") == "Direct"]
        connexe = [x for x in items if x.get("tag") == "Connexe"]
        fond = [x for x in items if x.get("tag") == "Fondamental"]
        tokens = [_citation_token({"citation_token": x.get("citation_token")}, i + 1) for i, x in enumerate(items[:5])]
        knowledge.append({
            "theme": theme,
            "articles_count": len(items),
            "direct_count": len(direct),
            "connexe_count": len(connexe),
            "fondamental_count": len(fond),
            "citation_tokens": tokens,
            "representative_articles": [
                {"token": x.get("citation_token"), "label": x.get("label"), "title": x.get("title"), "tag": x.get("tag")}
                for x in items[:4]
            ],
        })

    # Priorité : thèmes avec Direct puis Connexe.
    knowledge.sort(key=lambda x: (x["direct_count"], x["connexe_count"], x["articles_count"]), reverse=True)
    return {"insights": insights, "knowledge_themes": knowledge}


def build_gap_analysis_from_articles(verrou_item: Dict[str, Any], citation_articles: List[Dict[str, Any]], project_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    all_text = " ".join(_paper_text(a) for a in citation_articles)
    detected = []
    for label, terms, explanation in GAP_RULES:
        if _has_any(all_text, terms):
            detected.append({"gap_type": label, "explanation": explanation})

    if not detected:
        detected.append({
            "gap_type": "limite de couverture documentaire",
            "explanation": "les sources sélectionnées situent le domaine scientifique, mais leur couverture exacte du verrou doit être vérifiée au regard des contraintes propres au dossier.",
        })

    existing_gap = clean_text(verrou_item.get("gap_analysis"), 900)
    if existing_gap:
        detected.insert(0, {"gap_type": "analyse EnnoScholar existante", "explanation": existing_gap})

    return {"gaps": detected[:5]}


def build_consultant_state_of_art_context(
    verrou_item: Dict[str, Any],
    citation_articles: List[Dict[str, Any]],
    project_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Contexte structuré à donner au LLM writer."""
    intent = verrou_item.get("scientific_intent") or {}
    title = (
        verrou_item.get("verrou_title")
        or verrou_item.get("title")
        or intent.get("verrou_title")
        or "Verrou scientifique"
    )
    knowledge = build_knowledge_map(citation_articles)
    gaps = build_gap_analysis_from_articles(verrou_item, citation_articles, project_context)

    return {
        "writer_version": "v134_consultant_cir_internal_pipeline",
        "verrou_title": title,
        "scientific_problem": clean_text(intent.get("scientific_problem"), 900),
        "technical_object": clean_text(intent.get("technical_object"), 500),
        "phenomenon": clean_text(intent.get("phenomenon"), 500),
        "constraints": intent.get("constraints") or [],
        "methods": intent.get("methods") or [],
        "project_context_text": flatten_text(project_context or verrou_item.get("diagnostic_context") or {}, max_chars=3000),
        "selected_articles_count": len(citation_articles),
        "direct_count": len([a for a in citation_articles if a.get("tag") == "Direct"]),
        "connexe_count": len([a for a in citation_articles if a.get("tag") == "Connexe"]),
        "fondamental_count": len([a for a in citation_articles if a.get("tag") == "Fondamental"]),
        **knowledge,
        **gaps,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Template fallback style consultant CIR
# ──────────────────────────────────────────────────────────────────────────────


def build_consultant_template_state_of_art(
    verrou_item: Dict[str, Any],
    citation_articles: List[Dict[str, Any]],
    project_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    from .scholar_agent import build_references_from_citation_articles, validate_state_of_art_citations

    ctx = build_consultant_state_of_art_context(verrou_item, citation_articles, project_context)
    title = ctx["verrou_title"]
    refs = build_references_from_citation_articles(citation_articles)

    if not citation_articles:
        draft = (
            f"### Analyse de l’état de l’art — {title}\n\n"
            "Aucun article n’a été sélectionné par le consultant pour ce verrou. "
            "La rédaction automatique est donc bloquée afin d’éviter toute hallucination."
        )
        return {
            "mode": "template_consultant_no_articles",
            "writer_context": ctx,
            "draft": draft,
            "references": refs,
            "citation_guard": validate_state_of_art_citations(draft, citation_articles),
            "warnings": ["Aucun article sélectionné."],
        }

    lines: List[str] = []
    lines.append(f"### Analyse de l’état de l’art — {title}")
    lines.append("")

    intro_problem = ctx.get("scientific_problem") or title
    lines.append(
        "Nous présentons dans cette section une synthèse de l’analyse bibliographique menée afin de situer "
        f"le verrou relatif à « {title} ». Le problème scientifique porte sur {intro_problem}. "
        "L’objectif n’est pas de résumer séparément chaque publication, mais d’identifier ce que les connaissances existantes permettent déjà d’établir, puis les limites qui subsistent au regard du cas étudié."
    )
    lines.append("")

    themes = ctx.get("knowledge_themes") or []
    if themes:
        lines.append("Les travaux recensés permettent d’abord de cadrer plusieurs familles de connaissances pertinentes.")
        for theme in themes[:4]:
            reps = theme.get("representative_articles") or []
            tokens = [r.get("token") for r in reps if r.get("token")]
            token_txt = ", ".join(tokens[:4])
            if token_txt:
                lines.append(
                    f"Ils documentent notamment {theme.get('theme')}, à partir de sources classées Directes ou Connexes lorsque leur contenu se rapproche du verrou ({token_txt})."
                )
            else:
                lines.append(f"Ils documentent notamment {theme.get('theme')}.")
        lines.append("")

    direct_articles = [a for a in citation_articles if a.get("tag") == "Direct"]
    connexe_articles = [a for a in citation_articles if a.get("tag") == "Connexe"]
    fond_articles = [a for a in citation_articles if a.get("tag") == "Fondamental"]

    if direct_articles:
        tokens = ", ".join(_citation_token(a, i + 1) for i, a in enumerate(direct_articles[:5]))
        lines.append(
            "Les articles les plus directement liés au verrou apportent des éléments expérimentaux, numériques ou bibliographiques utiles pour situer la difficulté technique. "
            f"Ils constituent la base principale de l’analyse, notamment {tokens}."
        )
        lines.append("")

    if connexe_articles or fond_articles:
        tokens = ", ".join(_citation_token(a, i + 1) for i, a in enumerate((connexe_articles + fond_articles)[:5]))
        lines.append(
            "Les travaux connexes et fondamentaux complètent cette lecture en apportant des principes, des méthodes ou des résultats de contexte. "
            f"Ils doivent toutefois être mobilisés avec prudence, car leur transposition au cas du projet n’est pas toujours directe ({tokens})."
        )
        lines.append("")

    gaps = ctx.get("gaps") or []
    lines.append("Toutefois, l’analyse met en évidence plusieurs insuffisances au regard du verrou étudié.")
    for g in gaps[:4]:
        lines.append(clean_text(g.get("explanation"), 600))
    lines.append("")

    lines.append(
        "Ainsi, les connaissances disponibles permettent de situer le verrou dans un champ scientifique documenté, "
        "mais elles ne suffisent pas à démontrer que les solutions existantes couvrent entièrement la configuration, "
        "les contraintes et les conditions d’usage propres au projet. Cette limite justifie la poursuite d’une analyse expérimentale, numérique ou documentaire ciblée dans le cadre du dossier CIR."
    )

    draft = "\n\n".join([x for x in lines if x is not None])
    guard = validate_state_of_art_citations(draft, citation_articles)

    return {
        "mode": "template_consultant_cir_v134",
        "writer_context": ctx,
        "draft": draft,
        "references": refs,
        "citation_guard": guard,
        "warnings": [],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Preview recherche non-LLM
# ──────────────────────────────────────────────────────────────────────────────


def build_state_of_art_section(verrou_result: Dict[str, Any], max_articles: int = 5) -> Dict[str, Any]:
    title = verrou_result.get("verrou_title") or verrou_result.get("scientific_intent", {}).get("verrou_title")
    articles = [a for a in (verrou_result.get("articles") or []) if isinstance(a, dict)]
    technical_sources = [a for a in (verrou_result.get("technical_sources") or []) if isinstance(a, dict)]
    technical_sources += [a for a in articles if _is_technical_source(a)]

    academic = [a for a in articles if not _is_technical_source(a)]
    selected = [a for a in academic if a.get("tag") in {"Direct", "Connexe"}][:max_articles]
    if not selected:
        selected = academic[:max_articles]

    paragraphs = []
    refs = []

    if not selected:
        return {
            "verrou_title": title,
            "draft": "Aucun article académique suffisamment pertinent n’a été trouvé automatiquement pour rédiger un état de l’art fiable. Les sources techniques proposées doivent être vérifiées manuellement.",
            "references": [],
            "technical_references": [
                {
                    "title": t.get("title"),
                    "url": t.get("url"),
                    "venue": t.get("venue"),
                    "source_kind": t.get("source_kind"),
                }
                for t in technical_sources[:6]
            ],
        }

    paragraphs.append(
        f"Pour le verrou « {title} », la recherche bibliographique automatique a identifié des articles académiques permettant de situer le problème technique dans l’état des connaissances."
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

    tech_refs = []
    if technical_sources:
        paragraphs.append("")
        paragraphs.append("Sources techniques reconnues à consulter en complément :")
        seen = set()
        for t in technical_sources[:6]:
            key = (t.get("title"), t.get("url"))
            if key in seen:
                continue
            seen.add(key)
            paragraphs.append(f"- {clean_text(t.get('title'), 160)} — {clean_text(t.get('venue'), 80)}.")
            tech_refs.append({
                "title": t.get("title"),
                "url": t.get("url"),
                "venue": t.get("venue"),
                "source_kind": t.get("source_kind"),
                "catalog_score": t.get("catalog_score"),
            })

    return {
        "verrou_title": title,
        "draft": "\n".join(paragraphs),
        "references": refs,
        "technical_references": tech_refs,
    }
