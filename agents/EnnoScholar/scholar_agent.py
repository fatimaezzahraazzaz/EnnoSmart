# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scholar_agent.py — EnnoScholar V3

Agent 2 complet :
1) Récupère les verrous depuis nlp_result.json.
2) Construit une intention scientifique source-evidence-first.
3) Génère des requêtes scientifiques.
4) Cherche dans Semantic Scholar / OpenAlex / ArXiv.
5) Classe les articles : Direct / Connexe / Fondamental.
6) Valide scientifiquement le verrou.
7) Produit un brouillon template non-LLM.
8) Après sélection consultant, rédige l’état de l’art avec LLM contrôlé.
9) Vérifie les citations pour éviter les hallucinations.

Modes :
- search
- write-selection

Auteur  : EnnoSmart
Version : 3.0.0
"""

import argparse
import json
import os
import re
import socket
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .arxiv_client import ArxivClient
from .openalex_client import OpenAlexClient
from .paper_ranker import rank_papers_for_intent
from .query_builder import attach_queries_to_intent
from .scientific_intent_builder import build_scientific_intent
from .semantic_scholar_client import SemanticScholarClient
from .state_of_art_writer import build_state_of_art_section
from .utils import clean_text, flatten_text, read_json, write_json
from .verrou_scientific_validator import validate_verrou_scientifically
from .verrou_selector import select_scholar_verrous_from_nlp


# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

PACK_KEYS = [
    "objectifs_locaux",
    "verrous_rnd_locaux",
    "methodes_locales",
    "resultats_locaux",
    "limites_locales",
    "contributions_locales",
    "etat_art_local",
    "parametres_locaux",
]

DEFAULT_LLM_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
DEFAULT_LLM_TEMPERATURE = 0.15
DEFAULT_LLM_TIMEOUT = 60

ARTICLE_MAX_ABSTRACT_CHARS = 1800
MAX_SELECTED_ARTICLES_PER_VERROU = 8


# ──────────────────────────────────────────────────────────────────────────────
# Petits utilitaires
# ──────────────────────────────────────────────────────────────────────────────

def _safe_float(x: Any) -> float:
    try:
        return float(x or 0.0)
    except Exception:
        return 0.0


def _first_existing(obj: Dict[str, Any], keys: List[str]) -> Any:
    if not isinstance(obj, dict):
        return None

    for k in keys:
        if obj.get(k):
            return obj.get(k)

    return None


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _paper_stable_key(article: Dict[str, Any]) -> str:
    doi = clean_text(article.get("doi")).lower()
    if doi:
        return "doi:" + doi

    paper_id = clean_text(article.get("paper_id"))
    if paper_id:
        return "id:" + paper_id

    title = clean_text(article.get("title"), 240).lower()
    year = str(article.get("year") or "")
    return f"title:{title}:{year}"


def _citation_label(article: Dict[str, Any]) -> str:
    authors = article.get("authors") or []
    year = article.get("year") or "s.d."

    if authors:
        first_author = str(authors[0] or "").strip()
        if first_author:
            last = first_author.split()[-1]
            return f"{last} et al., {year}"

    title = clean_text(article.get("title"), 50)
    return f"{title}, {year}"


def _make_citation_id(i: int) -> str:
    return f"A{i}"


def _extract_citations(text: str) -> Set[str]:
    return set(re.findall(r"\[A\d+\]", text or ""))


def _strip_citation_brackets(citation_id: str) -> str:
    return citation_id.strip().replace("[", "").replace("]", "")


def _article_is_selected(article: Dict[str, Any]) -> bool:
    if not isinstance(article, dict):
        return False

    if article.get("consultant_selected") is True:
        return True

    if article.get("selected") is True:
        return True

    if article.get("is_selected") is True:
        return True

    return False


def _select_articles_from_verrou_item(
    verrou_item: Dict[str, Any],
    default_tags: Set[str] | None = None,
) -> List[Dict[str, Any]]:
    """
    Récupère les articles sélectionnés dans plusieurs formats possibles :
    - selected_articles
    - articles avec consultant_selected=True
    - fallback optionnel : Direct / Connexe
    """
    default_tags = default_tags or set()

    selected = []

    explicit = verrou_item.get("selected_articles")
    if isinstance(explicit, list):
        selected.extend([a for a in explicit if isinstance(a, dict)])

    articles = verrou_item.get("articles")
    if isinstance(articles, list):
        selected.extend([a for a in articles if isinstance(a, dict) and _article_is_selected(a)])

    if not selected and default_tags and isinstance(articles, list):
        selected.extend([
            a for a in articles
            if isinstance(a, dict) and a.get("tag") in default_tags
        ])

    # Déduplication
    out = []
    seen = set()

    for a in selected:
        k = _paper_stable_key(a)
        if k in seen:
            continue
        seen.add(k)
        out.append(a)

        if len(out) >= MAX_SELECTED_ARTICLES_PER_VERROU:
            break

    return out


# ──────────────────────────────────────────────────────────────────────────────
# Extraction contexte depuis NLP / Diagnostic
# ──────────────────────────────────────────────────────────────────────────────

def _domain_detection(nlp_result: Dict[str, Any]) -> Dict[str, Any]:
    d = nlp_result.get("domain_detection")

    if isinstance(d, dict):
        return d

    for key in ["raw_result", "pre_cir_structured_result", "cir_structured_result"]:
        obj = nlp_result.get(key)
        if isinstance(obj, dict) and isinstance(obj.get("domain_detection"), dict):
            return obj["domain_detection"]

    return {}


def extract_verrous_from_nlp(
    nlp_result: Dict[str, Any],
    max_verrous: int = 8,
) -> Dict[str, Any]:
    """
    Sélectionne les vrais sujets scientifiques à envoyer à EnnoScholar.

    Important :
    On ne prend pas directement tous les items verrous_rnd_locaux.
    On passe par verrou_selector.py pour reconstruire des sujets scientifiques
    à partir des preuves techniques sources.
    """
    return select_scholar_verrous_from_nlp(
        nlp_result,
        max_verrous=max_verrous,
    )


def extract_diagnostic_context(diagnostic_report: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(diagnostic_report, dict):
        return {}

    sections = (
        diagnostic_report.get("sections")
        if isinstance(diagnostic_report.get("sections"), dict)
        else diagnostic_report
    )

    ctx = {
        "synthese": _first_existing(
            sections,
            [
                "resume_strategique",
                "synthese",
                "synthese_projet",
                "project_summary",
                "summary",
            ],
        ),
        "objectif_global": _first_existing(
            sections,
            [
                "objectif_global",
                "objectifs",
                "objective",
                "global_objective",
            ],
        ),
        "verrous_rnd": _first_existing(
            sections,
            [
                "verrous_rnd",
                "verrous",
                "technical_locks",
                "rnd_locks",
            ],
        ),
        "demarche_experimentale": _first_existing(
            sections,
            [
                "demarche_experimentale",
                "demarche",
                "methodes",
                "methods",
                "experimental_approach",
            ],
        ),
        "resultats_metriques": _first_existing(
            sections,
            [
                "resultats_metriques",
                "resultats",
                "metrics",
                "results",
            ],
        ),
        "points_validation": _first_existing(
            sections,
            [
                "points_validation",
                "points_a_valider",
                "validation_points",
                "alertes",
            ],
        ),
    }

    clean = {}

    for k, v in ctx.items():
        txt = flatten_text(v, max_chars=1800)
        if txt:
            clean[k] = txt

    clean["diagnostic_context_text"] = flatten_text(clean, max_chars=3500)

    return clean


# ──────────────────────────────────────────────────────────────────────────────
# Préparation citations et garde-fous
# ──────────────────────────────────────────────────────────────────────────────

def normalize_selected_articles_for_citation(
    selected_articles: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Transforme les articles sélectionnés en sources contrôlées :
    A1, A2, A3...
    """
    normalized = []
    seen = set()

    for article in selected_articles:
        if not isinstance(article, dict):
            continue

        if not clean_text(article.get("title")):
            continue

        key = _paper_stable_key(article)

        if key in seen:
            continue

        seen.add(key)

        idx = len(normalized) + 1
        citation_id = _make_citation_id(idx)

        normalized.append({
            "citation_id": citation_id,
            "citation_token": f"[{citation_id}]",
            "label": _citation_label(article),
            "title": clean_text(article.get("title"), 300),
            "abstract": clean_text(article.get("abstract") or article.get("tldr"), ARTICLE_MAX_ABSTRACT_CHARS),
            "year": article.get("year"),
            "venue": clean_text(article.get("venue"), 180),
            "authors": article.get("authors") or [],
            "doi": clean_text(article.get("doi"), 200),
            "url": clean_text(article.get("url"), 500),
            "source": clean_text(article.get("source"), 120),
            "tag": clean_text(article.get("tag"), 80),
            "relevance_score": article.get("relevance_score"),
            "consultant_note": clean_text(article.get("consultant_note"), 600),
            "reason": clean_text(article.get("reason"), 600),
            "original": article,
        })

        if len(normalized) >= MAX_SELECTED_ARTICLES_PER_VERROU:
            break

    return normalized


def build_references_from_citation_articles(
    citation_articles: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    refs = []

    for a in citation_articles:
        refs.append({
            "citation_id": a["citation_id"],
            "token": a["citation_token"],
            "label": a["label"],
            "title": a["title"],
            "authors": a["authors"],
            "year": a["year"],
            "venue": a["venue"],
            "doi": a["doi"],
            "url": a["url"],
            "source": a["source"],
            "tag": a["tag"],
            "relevance_score": a["relevance_score"],
        })

    return refs


def validate_state_of_art_citations(
    text: str,
    citation_articles: List[Dict[str, Any]],
) -> Dict[str, Any]:
    allowed = {f"[{a['citation_id']}]" for a in citation_articles}
    used = _extract_citations(text)
    unknown = sorted(list(used - allowed))
    unused = sorted(list(allowed - used))

    paragraphs = [
        p.strip()
        for p in re.split(r"\n\s*\n", text or "")
        if len(p.strip()) > 80
    ]

    technical_paragraphs_without_citation = []

    for p in paragraphs:
        # On ne force pas les citations dans les titres ou paragraphes de transition.
        if p.startswith("#"):
            continue

        has_scientific_terms = bool(
            re.search(
                r"\b(article|travaux|littérature|méthode|modèle|résultat|limite|approche|phénomène|performance|incertitude|verrou|expérimental|simulation)\b",
                p,
                flags=re.I,
            )
        )

        if has_scientific_terms and not _extract_citations(p):
            technical_paragraphs_without_citation.append(clean_text(p, 260))

    ok = not unknown

    return {
        "ok": ok,
        "allowed_citations": sorted(list(allowed)),
        "used_citations": sorted(list(used)),
        "unknown_citations": unknown,
        "unused_citations": unused,
        "technical_paragraphs_without_citation": technical_paragraphs_without_citation[:5],
        "warning_count": len(unknown) + len(technical_paragraphs_without_citation),
    }


# ──────────────────────────────────────────────────────────────────────────────
# LLM OpenRouter
# ──────────────────────────────────────────────────────────────────────────────

def _openrouter_api_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "").strip()


def call_openrouter_chat(
    messages: List[Dict[str, str]],
    model: str = DEFAULT_LLM_MODEL,
    temperature: float = DEFAULT_LLM_TEMPERATURE,
    timeout: int = DEFAULT_LLM_TIMEOUT,
) -> Dict[str, Any]:
    """
    Appel OpenRouter minimal sans dépendance externe.

    Requiert :
      OPENROUTER_API_KEY
    """
    api_key = _openrouter_api_key()

    if not api_key:
        return {
            "ok": False,
            "error": "OPENROUTER_API_KEY manquante",
            "content": "",
            "model": model,
        }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    raw = json.dumps(payload).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "HTTP-Referer": "http://localhost/ennosmart",
        "X-Title": "EnnoSmart EnnoScholar",
    }

    try:
        socket.setdefaulttimeout(timeout)

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=raw,
            headers=headers,
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))

        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )

        return {
            "ok": True,
            "error": "",
            "content": content,
            "model": model,
            "raw_usage": data.get("usage"),
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "content": "",
            "model": model,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Writer template sécurisé
# ──────────────────────────────────────────────────────────────────────────────

def build_template_state_of_art_from_selection(
    verrou_item: Dict[str, Any],
    citation_articles: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Fallback sans LLM.
    Rédaction simple, non-hallucinatoire.
    """
    title = (
        verrou_item.get("verrou_title")
        or verrou_item.get("title")
        or verrou_item.get("scientific_intent", {}).get("verrou_title")
        or "Verrou scientifique"
    )

    intent = verrou_item.get("scientific_intent") or {}

    scientific_problem = clean_text(intent.get("scientific_problem"), 600)
    technical_object = clean_text(intent.get("technical_object"), 400)
    phenomenon = clean_text(intent.get("phenomenon"), 400)

    if not citation_articles:
        draft = (
            f"### État de l’art — {title}\n\n"
            "Aucun article n’a été sélectionné par le consultant pour ce verrou. "
            "Il n’est donc pas possible de rédiger un état de l’art fiable sans risque d’hallucination."
        )

        return {
            "mode": "template_no_selection",
            "draft": draft,
            "references": [],
            "citation_guard": {
                "ok": True,
                "warning_count": 0,
                "unknown_citations": [],
            },
            "warnings": ["Aucun article sélectionné."],
        }

    lines = []

    lines.append(f"### État de l’art — {title}")
    lines.append("")

    intro = (
        "Le verrou étudié concerne un problème technique devant être situé "
        "par rapport aux connaissances scientifiques et techniques existantes."
    )

    if scientific_problem:
        intro += f" Le problème scientifique peut être formulé ainsi : {scientific_problem}."

    if technical_object:
        intro += f" L’objet technique concerné est : {technical_object}."

    if phenomenon:
        intro += f" Le phénomène étudié porte notamment sur : {phenomenon}."

    intro += f" {citation_articles[0]['citation_token']}"

    lines.append(intro)
    lines.append("")

    direct = [a for a in citation_articles if a.get("tag") == "Direct"]
    connexe = [a for a in citation_articles if a.get("tag") == "Connexe"]
    fondamental = [a for a in citation_articles if a.get("tag") == "Fondamental"]

    if direct:
        lines.append("#### Travaux directement liés")
        for a in direct:
            lines.append(
                f"- {a['citation_token']} {a['label']} — {a['title']}. "
                f"Cet article est classé comme Direct car il traite un objet ou un phénomène proche du verrou."
            )
        lines.append("")

    if connexe:
        lines.append("#### Travaux connexes")
        for a in connexe:
            lines.append(
                f"- {a['citation_token']} {a['label']} — {a['title']}. "
                f"Cet article apporte un éclairage connexe utile pour positionner le verrou."
            )
        lines.append("")

    if fondamental:
        lines.append("#### Travaux fondamentaux")
        for a in fondamental:
            lines.append(
                f"- {a['citation_token']} {a['label']} — {a['title']}. "
                f"Cette source peut servir à rappeler un principe scientifique ou technique général."
            )
        lines.append("")

    lines.append("#### Limites identifiées dans l’état de l’art")
    lines.append(
        "Les sources sélectionnées permettent de situer le problème dans la littérature, "
        "mais elles ne suffisent pas nécessairement à démontrer que le cas spécifique du projet "
        "est entièrement résolu par les solutions existantes. Cette différence doit être vérifiée "
        "par le consultant à partir des contraintes propres au dossier."
    )
    lines.append("")

    lines.append("#### Gap scientifique pour le dossier CIR")
    lines.append(
        "Au regard des articles sélectionnés, le verrou peut être présenté comme une incertitude "
        "portant sur l’adaptation, la maîtrise ou la validation d’un phénomène technique dans les "
        "conditions spécifiques du projet. La justification finale doit montrer en quoi les solutions "
        "connues ne répondent pas complètement aux contraintes du dossier."
    )

    draft = "\n".join(lines)

    refs = build_references_from_citation_articles(citation_articles)
    guard = validate_state_of_art_citations(draft, citation_articles)

    return {
        "mode": "template",
        "draft": draft,
        "references": refs,
        "citation_guard": guard,
        "warnings": [],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Writer LLM contrôlé
# ──────────────────────────────────────────────────────────────────────────────

def build_llm_prompt_for_state_of_art(
    verrou_item: Dict[str, Any],
    citation_articles: List[Dict[str, Any]],
    project_context: Dict[str, Any] | None = None,
) -> List[Dict[str, str]]:
    title = (
        verrou_item.get("verrou_title")
        or verrou_item.get("title")
        or verrou_item.get("scientific_intent", {}).get("verrou_title")
        or "Verrou scientifique"
    )

    intent = verrou_item.get("scientific_intent") or {}
    context = project_context or verrou_item.get("diagnostic_context") or {}

    context_text = flatten_text(context, max_chars=3000)

    articles_text_parts = []

    for a in citation_articles:
        article_text = (
            f"{a['citation_token']} {a['label']}\n"
            f"Titre : {a['title']}\n"
            f"Année : {a.get('year')}\n"
            f"Tag : {a.get('tag')}\n"
            f"Score : {a.get('relevance_score')}\n"
            f"Résumé : {a.get('abstract') or 'Résumé non disponible.'}\n"
        )

        if a.get("consultant_note"):
            article_text += f"Note consultant : {a['consultant_note']}\n"

        articles_text_parts.append(article_text)

    articles_text = "\n---\n".join(articles_text_parts)

    system = (
        "Tu es un consultant scientifique spécialisé dans la rédaction d'états de l'art "
        "pour des dossiers de Crédit d'Impôt Recherche (CIR).\n\n"
        "Règles obligatoires :\n"
        "1. Tu dois rédiger en français professionnel.\n"
        "2. Tu dois utiliser uniquement les articles fournis par l'utilisateur.\n"
        "3. Tu n'as pas le droit d'inventer une source, un auteur, un DOI, une année ou un résultat.\n"
        "4. Tu dois citer uniquement avec les identifiants fournis : [A1], [A2], [A3], etc.\n"
        "5. Toute affirmation scientifique importante doit être accompagnée d'au moins une citation.\n"
        "6. Si les sources ne permettent pas de conclure, tu dois l'écrire clairement.\n"
        "7. Tu dois distinguer : état de l'art, limites des travaux existants, gap pour le projet.\n"
        "8. Tu ne dois pas écrire que le verrou est définitivement éligible CIR ; tu peux seulement aider à le défendre.\n"
    )

    user = f"""
Rédige un état de l'art contrôlé pour le verrou suivant.

# Verrou
{title}

# Intention scientifique
Problème scientifique :
{clean_text(intent.get("scientific_problem"), 900)}

Objet technique :
{clean_text(intent.get("technical_object"), 500)}

Phénomène :
{clean_text(intent.get("phenomenon"), 500)}

Contraintes :
{json.dumps(intent.get("constraints") or [], ensure_ascii=False)}

Méthodes :
{json.dumps(intent.get("methods") or [], ensure_ascii=False)}

# Contexte projet
{context_text or "Contexte projet non fourni."}

# Articles sélectionnés par le consultant
{articles_text}

# Format demandé

Rédige en Markdown avec exactement les sections suivantes :

### 1. Introduction du verrou scientifique
Présenter le problème et son importance scientifique/technique.

### 2. Travaux directement liés
Synthétiser les articles Direct. Citer chaque idée avec [A1], [A2], etc.

### 3. Travaux connexes et principes utiles
Synthétiser les articles Connexe/Fondamental.

### 4. Limites de l’état de l’art
Dire ce que les articles ne couvrent pas ou ne permettent pas de conclure.

### 5. Gap scientifique pour le projet
Expliquer pourquoi un écart peut subsister entre les connaissances existantes et le cas spécifique du projet.

### 6. Conclusion pour la justification CIR
Formuler une conclusion prudente, sans surpromettre.

Important :
- N'utilise aucune source non listée.
- Ne cite jamais un article absent.
- Utilise les citations sous la forme [A1], [A2].
"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user.strip()},
    ]


def build_llm_state_of_art_from_selection(
    verrou_item: Dict[str, Any],
    citation_articles: List[Dict[str, Any]],
    project_context: Dict[str, Any] | None = None,
    model: str = DEFAULT_LLM_MODEL,
    temperature: float = DEFAULT_LLM_TEMPERATURE,
    writer_mode: str = "auto",
) -> Dict[str, Any]:
    """
    writer_mode:
    - template : pas de LLM
    - llm      : exige LLM, fallback si erreur
    - auto     : LLM si clé disponible, sinon template
    """
    writer_mode = (writer_mode or "auto").lower().strip()

    if writer_mode not in {"template", "llm", "auto"}:
        writer_mode = "auto"

    if writer_mode == "template":
        return build_template_state_of_art_from_selection(
            verrou_item,
            citation_articles,
        )

    if not citation_articles:
        return build_template_state_of_art_from_selection(
            verrou_item,
            citation_articles,
        )

    if not _openrouter_api_key():
        if writer_mode == "llm":
            refs = build_references_from_citation_articles(citation_articles)
            return {
                "mode": "llm_error_no_api_key",
                "llm_used": False,
                "fallback_used": False,
                "draft": "",
                "references": refs,
                "citation_guard": {"ok": False, "warning_count": 1, "unknown_citations": []},
                "warnings": [
                    "OPENROUTER_API_KEY absente : la rédaction LLM est obligatoire, aucun template fallback n'a été utilisé."
                ],
                "llm_error": "OPENROUTER_API_KEY manquante",
            }

        fallback = build_template_state_of_art_from_selection(
            verrou_item,
            citation_articles,
        )
        fallback["mode"] = "template_fallback_no_api_key"
        fallback["llm_used"] = False
        fallback["fallback_used"] = True
        fallback["warnings"] = fallback.get("warnings", []) + [
            "OPENROUTER_API_KEY absente : génération template utilisée."
        ]
        return fallback

    messages = build_llm_prompt_for_state_of_art(
        verrou_item=verrou_item,
        citation_articles=citation_articles,
        project_context=project_context,
    )

    llm_result = call_openrouter_chat(
        messages=messages,
        model=model,
        temperature=temperature,
    )

    if not llm_result.get("ok"):
        if writer_mode == "llm":
            refs = build_references_from_citation_articles(citation_articles)
            return {
                "mode": "llm_error",
                "llm_used": False,
                "fallback_used": False,
                "draft": "",
                "references": refs,
                "citation_guard": {"ok": False, "warning_count": 1, "unknown_citations": []},
                "warnings": [
                    f"Erreur LLM : {llm_result.get('error')}. Aucun template fallback n'a été utilisé."
                ],
                "llm_error": llm_result.get("error"),
                "model": model,
            }

        fallback = build_template_state_of_art_from_selection(
            verrou_item,
            citation_articles,
        )
        fallback["mode"] = "template_fallback_llm_error"
        fallback["llm_used"] = False
        fallback["fallback_used"] = True
        fallback["llm_error"] = llm_result.get("error")
        fallback["warnings"] = fallback.get("warnings", []) + [
            f"Erreur LLM : {llm_result.get('error')}"
        ]
        return fallback

    draft = clean_text(llm_result.get("content"), 20000)

    if not draft:
        if writer_mode == "llm":
            refs = build_references_from_citation_articles(citation_articles)
            return {
                "mode": "llm_error_empty_response",
                "llm_used": False,
                "fallback_used": False,
                "draft": "",
                "references": refs,
                "citation_guard": {"ok": False, "warning_count": 1, "unknown_citations": []},
                "warnings": [
                    "Réponse LLM vide : la rédaction LLM est obligatoire, aucun template fallback n'a été utilisé."
                ],
                "llm_error": "Réponse LLM vide",
                "model": model,
            }

        fallback = build_template_state_of_art_from_selection(
            verrou_item,
            citation_articles,
        )
        fallback["mode"] = "template_fallback_empty_llm"
        fallback["llm_used"] = False
        fallback["fallback_used"] = True
        fallback["warnings"] = fallback.get("warnings", []) + [
            "Réponse LLM vide : génération template utilisée."
        ]
        return fallback

    refs = build_references_from_citation_articles(citation_articles)
    guard = validate_state_of_art_citations(draft, citation_articles)

    warnings = []

    if guard.get("unknown_citations"):
        warnings.append(
            "Le LLM a utilisé des citations inconnues : "
            + ", ".join(guard["unknown_citations"])
        )

    if guard.get("technical_paragraphs_without_citation"):
        warnings.append(
            "Certains paragraphes techniques ne contiennent pas de citation."
        )

    return {
        "mode": "llm",
        "model": llm_result.get("model"),
        "draft": draft,
        "references": refs,
        "citation_guard": guard,
        "warnings": warnings,
        "llm_usage": llm_result.get("raw_usage"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Payload search depuis NLP
# ──────────────────────────────────────────────────────────────────────────────

def build_payload_from_nlp(
    nlp_result_path: str | Path,
    organisme: str = "",
    project: str = "",
    year: str = "",
    diagnostic_report_path: str | Path | None = None,
    max_verrous: int = 8,
) -> Dict[str, Any]:
    nlp = read_json(nlp_result_path, {})
    extracted = extract_verrous_from_nlp(nlp, max_verrous=max_verrous)

    diagnostic_context = {}

    if diagnostic_report_path:
        diagnostic_context = extract_diagnostic_context(
            read_json(diagnostic_report_path, {})
        )

    payload = {
        "organisme": organisme,
        "project": project,
        "year": year,
        "input_nlp_result": str(nlp_result_path),
        "input_diagnostic_report": str(diagnostic_report_path) if diagnostic_report_path else "",
        "domain_detection": extracted.get("domain_detection") or {},
        "diagnostic_context": diagnostic_context,
        "pack_counts": extracted.get("pack_counts"),
        "selector": extracted.get("selector"),
        "verrous": extracted.get("verrous") or [],
    }

    return payload


# ──────────────────────────────────────────────────────────────────────────────
# Payload sélection consultant
# ──────────────────────────────────────────────────────────────────────────────

def build_selection_payload_from_report(
    report: Dict[str, Any],
    default_select_tags: Set[str] | None = None,
) -> Dict[str, Any]:
    """
    Construit un payload de sélection depuis un ennoscholar_report.json.

    Utile pour test sans interface :
    - default_select_tags={"Direct", "Connexe"}
    """
    default_select_tags = default_select_tags or set()

    verrous = []

    for r in report.get("results") or []:
        if not isinstance(r, dict):
            continue

        selected_articles = _select_articles_from_verrou_item(
            r,
            default_tags=default_select_tags,
        )

        verrous.append({
            "verrou_id": r.get("verrou_id"),
            "verrou_title": r.get("verrou_title"),
            "verrou_text": r.get("verrou_text"),
            "scientific_intent": r.get("scientific_intent") or {},
            "decision": r.get("decision"),
            "scientific_support_score": r.get("scientific_support_score"),
            "gap_analysis": r.get("gap_analysis"),
            "selected_articles": selected_articles,
        })

    return {
        "agent": "EnnoScholar",
        "payload_type": "selected_articles_for_state_of_art",
        "generated_at": _now_iso(),
        "organisme": report.get("organisme"),
        "project": report.get("project"),
        "year": report.get("year"),
        "domain_detection": report.get("domain_detection") or {},
        "diagnostic_context": report.get("diagnostic_context") or {},
        "verrous": verrous,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Agent principal
# ──────────────────────────────────────────────────────────────────────────────

class EnnoScholarAgent:
    def __init__(
        self,
        use_semantic_scholar: bool = True,
        use_openalex: bool = True,
        use_arxiv: bool = True,
        limit_per_query: int = 4,
        offline_dry_run: bool = False,
    ):
        self.use_semantic_scholar = use_semantic_scholar
        self.use_openalex = use_openalex
        self.use_arxiv = use_arxiv
        self.limit_per_query = limit_per_query
        self.offline_dry_run = offline_dry_run

        self.semantic_client = SemanticScholarClient()
        self.openalex_client = OpenAlexClient()
        self.arxiv_client = ArxivClient()

    # ──────────────────────────────────────────────────────────────────────
    # Mode 1 : recherche
    # ──────────────────────────────────────────────────────────────────────

    def search_for_verrou(
        self,
        verrou: Dict[str, Any],
        domain_detection: Dict[str, Any],
        diagnostic_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        intent = build_scientific_intent(
            verrou,
            domain_detection=domain_detection,
            diagnostic_context=diagnostic_context,
        )

        intent = attach_queries_to_intent(intent)

        # Les verrous venant du backend EnnoDiagnostic peuvent être enrichis
        # avec des requêtes ciblées. Elles passent avant les requêtes génériques.
        raw_item = verrou.get("raw_item") if isinstance(verrou.get("raw_item"), dict) else {}
        source_json = verrou.get("source_json") if isinstance(verrou.get("source_json"), dict) else {}

        enrichment = (
            source_json.get("scholar_enrichment")
            if isinstance(source_json.get("scholar_enrichment"), dict)
            else {}
        )

        intent["backend_enrichment_profile"] = (
            raw_item.get("enrichment_profile")
            or enrichment.get("profile")
            or verrou.get("enrichment_profile")
            or ""
        )

        intent["original_title"] = (
            verrou.get("original_title")
            or raw_item.get("original_title")
            or ""
        )

        suggested_queries = verrou.get("suggested_queries") or []

        if isinstance(suggested_queries, list) and suggested_queries:
            intent["suggested_queries"] = suggested_queries
            existing = intent.get("search_queries") or []
            merged_queries = []

            for q in suggested_queries:
                query = q.get("query") if isinstance(q, dict) else str(q)
                query = clean_text(query, 220)

                if query and query.lower() not in {
                    x.get("query", "").lower()
                    for x in merged_queries
                    if isinstance(x, dict)
                }:
                    merged_queries.append({
                        "query": query,
                        "kind": "backend_enriched_source_query",
                    })

            for q in existing:
                query = q.get("query") if isinstance(q, dict) else str(q)
                query = clean_text(query, 220)

                if query and query.lower() not in {
                    x.get("query", "").lower()
                    for x in merged_queries
                    if isinstance(x, dict)
                }:
                    merged_queries.append(
                        q if isinstance(q, dict) else {
                            "query": query,
                            "kind": "auto",
                        }
                    )

            intent["search_queries"] = merged_queries[:8]

        queries = intent.get("search_queries") or []
        all_papers = []
        errors = []

        if not self.offline_dry_run:
            for q in queries:
                query = q.get("query") if isinstance(q, dict) else str(q)
                query = clean_text(query, 220)

                if not query:
                    continue

                if self.use_semantic_scholar:
                    res = self.semantic_client.search_papers(
                        query,
                        limit=self.limit_per_query,
                    )
                    for p in res:
                        if p.get("normalized_error"):
                            errors.append(p)
                        else:
                            all_papers.append(p)

                if self.use_openalex:
                    res = self.openalex_client.search_works(
                        query,
                        limit=self.limit_per_query,
                    )
                    for p in res:
                        if p.get("normalized_error"):
                            errors.append(p)
                        else:
                            all_papers.append(p)

                if self.use_arxiv:
                    res = self.arxiv_client.search_papers(
                        query,
                        limit=self.limit_per_query,
                    )
                    for p in res:
                        if p.get("normalized_error"):
                            errors.append(p)
                        else:
                            all_papers.append(p)

                time.sleep(0.05)

        ranked = rank_papers_for_intent(
            all_papers,
            intent,
            top_n=12,
        )

        validation = validate_verrou_scientifically(
            intent,
            ranked,
        )

        result = {
            "verrou_id": verrou.get("verrou_id"),
            "verrou_title": intent.get("verrou_title") or verrou.get("title"),
            "verrou_text": verrou.get("text"),
            "frascati": verrou.get("frascati"),
            "scientific_intent": intent,
            "queries": queries,
            "articles_found": len(ranked),
            "articles": ranked,
            "errors": errors[:10],
            "offline_dry_run": bool(self.offline_dry_run),
            **validation,
        }

        # Preview simple sans LLM
        result["state_of_art_preview"] = build_state_of_art_section(result)

        return result

    def run_search(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        domain_detection = payload.get("domain_detection") or {}
        diagnostic_context = payload.get("diagnostic_context") or {}

        results = []

        for verrou in payload.get("verrous") or []:
            if isinstance(verrou, dict):
                results.append(
                    self.search_for_verrou(
                        verrou,
                        domain_detection,
                        diagnostic_context,
                    )
                )

        decision_counts = {}

        for r in results:
            d = r.get("decision")
            decision_counts[d] = decision_counts.get(d, 0) + 1

        return {
            "agent": "EnnoScholar",
            "version": "v3_search_and_llm_writer",
            "mode": "search",
            "generated_at": _now_iso(),
            "project": payload.get("project"),
            "year": payload.get("year"),
            "organisme": payload.get("organisme"),
            "domain_detection": domain_detection,
            "diagnostic_context": diagnostic_context,
            "diagnostic_context_used": bool(diagnostic_context),
            "verrous_analyzed": len(results),
            "decision_counts": decision_counts,
            "results": results,
        }

    # ──────────────────────────────────────────────────────────────────────
    # Mode 2 : rédaction depuis sélection consultant
    # ──────────────────────────────────────────────────────────────────────

    def write_state_of_art_for_verrou(
        self,
        verrou_item: Dict[str, Any],
        project_context: Dict[str, Any] | None = None,
        writer_mode: str = "auto",
        llm_model: str = DEFAULT_LLM_MODEL,
        llm_temperature: float = DEFAULT_LLM_TEMPERATURE,
    ) -> Dict[str, Any]:
        selected_articles = _select_articles_from_verrou_item(
            verrou_item,
            default_tags=set(),
        )

        citation_articles = normalize_selected_articles_for_citation(
            selected_articles,
        )

        state_of_art = build_llm_state_of_art_from_selection(
            verrou_item=verrou_item,
            citation_articles=citation_articles,
            project_context=project_context,
            model=llm_model,
            temperature=llm_temperature,
            writer_mode=writer_mode,
        )

        return {
            "verrou_id": verrou_item.get("verrou_id"),
            "verrou_title": (
                verrou_item.get("verrou_title")
                or verrou_item.get("title")
                or verrou_item.get("scientific_intent", {}).get("verrou_title")
            ),
            "scientific_intent": verrou_item.get("scientific_intent") or {},
            "selected_articles_count": len(selected_articles),
            "citation_articles": [
                {
                    "citation_id": a["citation_id"],
                    "label": a["label"],
                    "title": a["title"],
                    "year": a["year"],
                    "tag": a["tag"],
                    "relevance_score": a["relevance_score"],
                    "doi": a["doi"],
                    "url": a["url"],
                }
                for a in citation_articles
            ],
            "state_of_art": state_of_art,
        }

    def run_writer_from_selection(
        self,
        selection_payload: Dict[str, Any],
        writer_mode: str = "auto",
        llm_model: str = DEFAULT_LLM_MODEL,
        llm_temperature: float = DEFAULT_LLM_TEMPERATURE,
    ) -> Dict[str, Any]:
        project_context = selection_payload.get("diagnostic_context") or {}

        results = []

        for verrou_item in selection_payload.get("verrous") or []:
            if not isinstance(verrou_item, dict):
                continue

            results.append(
                self.write_state_of_art_for_verrou(
                    verrou_item=verrou_item,
                    project_context=project_context,
                    writer_mode=writer_mode,
                    llm_model=llm_model,
                    llm_temperature=llm_temperature,
                )
            )

        total_warnings = 0
        citation_errors = 0

        for r in results:
            soa = r.get("state_of_art") or {}
            guard = soa.get("citation_guard") or {}

            total_warnings += len(soa.get("warnings") or [])

            if guard.get("unknown_citations"):
                citation_errors += len(guard.get("unknown_citations") or [])

        return {
            "agent": "EnnoScholar",
            "version": "v3_search_and_llm_writer",
            "mode": "write-selection",
            "generated_at": _now_iso(),
            "organisme": selection_payload.get("organisme"),
            "project": selection_payload.get("project"),
            "year": selection_payload.get("year"),
            "writer_mode": writer_mode,
            "llm_model": llm_model,
            "verrous_written": len(results),
            "total_warnings": total_warnings,
            "citation_errors": citation_errors,
            "results": results,
        }

    # Compatibilité ancien appel
    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.run_search(payload)


# ──────────────────────────────────────────────────────────────────────────────
# Fonctions publiques haut niveau
# ──────────────────────────────────────────────────────────────────────────────

def run_ennoscholar_from_nlp(
    nlp_result_path: str | Path,
    organisme: str = "",
    project: str = "",
    year: str = "",
    out_dir: str | Path | None = None,
    diagnostic_report_path: str | Path | None = None,
    max_verrous: int = 5,
    limit_per_query: int = 4,
    use_semantic_scholar: bool = True,
    use_openalex: bool = True,
    use_arxiv: bool = True,
    offline_dry_run: bool = False,
) -> Dict[str, Any]:
    payload = build_payload_from_nlp(
        nlp_result_path=nlp_result_path,
        organisme=organisme,
        project=project,
        year=year,
        diagnostic_report_path=diagnostic_report_path,
        max_verrous=max_verrous,
    )

    if out_dir is None:
        out_dir = Path(nlp_result_path).parent / "ennoscholar"

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        out_dir / "validated_verrous_for_scholar.json",
        payload,
    )

    agent = EnnoScholarAgent(
        use_semantic_scholar=use_semantic_scholar,
        use_openalex=use_openalex,
        use_arxiv=use_arxiv,
        limit_per_query=limit_per_query,
        offline_dry_run=offline_dry_run,
    )

    report = agent.run_search(payload)

    report["input_nlp_result"] = str(nlp_result_path)
    report["input_diagnostic_report"] = str(diagnostic_report_path) if diagnostic_report_path else ""

    report["outputs"] = {
        "payload": str(out_dir / "validated_verrous_for_scholar.json"),
        "report": str(out_dir / "ennoscholar_report.json"),
        "selection_template_direct_connexe": str(out_dir / "selected_articles_template.json"),
    }

    write_json(
        out_dir / "ennoscholar_report.json",
        report,
    )

    # Template pratique pour tester sans frontend :
    # sélectionne automatiquement Direct + Connexe.
    selection_template = build_selection_payload_from_report(
        report,
        default_select_tags={"Direct", "Connexe"},
    )

    write_json(
        out_dir / "selected_articles_template.json",
        selection_template,
    )

    return report


def run_state_of_art_writer_from_selection(
    selection_payload_path: str | Path,
    out_dir: str | Path | None = None,
    writer_mode: str = "auto",
    llm_model: str = DEFAULT_LLM_MODEL,
    llm_temperature: float = DEFAULT_LLM_TEMPERATURE,
) -> Dict[str, Any]:
    selection_payload = read_json(selection_payload_path, {})

    if out_dir is None:
        out_dir = Path(selection_payload_path).parent

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    agent = EnnoScholarAgent(
        use_semantic_scholar=False,
        use_openalex=False,
        use_arxiv=False,
        offline_dry_run=True,
    )

    report = agent.run_writer_from_selection(
        selection_payload=selection_payload,
        writer_mode=writer_mode,
        llm_model=llm_model,
        llm_temperature=llm_temperature,
    )

    report["input_selection_payload"] = str(selection_payload_path)
    report["outputs"] = {
        "state_of_art_report": str(out_dir / "ennoscholar_state_of_art_report.json"),
    }

    write_json(
        out_dir / "ennoscholar_state_of_art_report.json",
        report,
    )

    return report


# ──────────────────────────────────────────────────────────────────────────────
# Preview terminal
# ──────────────────────────────────────────────────────────────────────────────

def print_search_preview(report: Dict[str, Any]) -> None:
    print("OK - EnnoScholar V3 search terminé")
    print("Projet:", report.get("project"))
    print("Année:", report.get("year"))
    print("Verrous analysés:", report.get("verrous_analyzed"))
    print("Décisions:", report.get("decision_counts"))
    print("Payload:", report.get("outputs", {}).get("payload"))
    print("Report:", report.get("outputs", {}).get("report"))
    print("Selection template:", report.get("outputs", {}).get("selection_template_direct_connexe"))

    print("\n--- Aperçu ---")

    for r in (report.get("results") or [])[:5]:
        intent = r.get("scientific_intent") or {}

        print("\n#", r.get("verrou_title"))
        print("Decision:", r.get("decision"))
        print("Scientific support:", r.get("scientific_support_score"))
        print("Intent:", intent.get("scientific_problem"))
        print("Object:", intent.get("technical_object"))
        print("Phenomenon:", intent.get("phenomenon"))
        print("Articles:", r.get("articles_found"))

        print("Queries:")
        for q in (r.get("queries") or [])[:4]:
            print(" -", q.get("kind"), ":", q.get("query"))

        for a in (r.get("articles") or [])[:3]:
            print(
                " Article:",
                a.get("tag"),
                a.get("relevance_score"),
                "-",
                a.get("title"),
                f"({a.get('year')})",
            )


def print_writer_preview(report: Dict[str, Any]) -> None:
    print("OK - EnnoScholar V3 write-selection terminé")
    print("Projet:", report.get("project"))
    print("Année:", report.get("year"))
    print("Verrous rédigés:", report.get("verrous_written"))
    print("Warnings:", report.get("total_warnings"))
    print("Citation errors:", report.get("citation_errors"))
    print("Report:", report.get("outputs", {}).get("state_of_art_report"))

    print("\n--- Aperçu rédaction ---")

    for r in (report.get("results") or [])[:3]:
        print("\n#", r.get("verrou_title"))
        print("Articles sélectionnés:", r.get("selected_articles_count"))

        soa = r.get("state_of_art") or {}
        print("Mode:", soa.get("mode"))

        guard = soa.get("citation_guard") or {}
        print("Citation guard OK:", guard.get("ok"))
        print("Unknown citations:", guard.get("unknown_citations"))

        draft = soa.get("draft") or ""
        print(clean_text(draft, 1000))


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="EnnoScholar V3 — search + write-selection"
    )

    parser.add_argument(
        "--mode",
        choices=["search", "write-selection"],
        default="search",
        help="search = recherche articles ; write-selection = rédaction depuis sélection consultant",
    )

    # Search mode
    parser.add_argument("--organisme", default="")
    parser.add_argument("--project", default="")
    parser.add_argument("--year", default="")
    parser.add_argument("--nlp-result", default="")
    parser.add_argument("--diagnostic-report", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--max-verrous", type=int, default=5)
    parser.add_argument("--limit-per-query", type=int, default=4)

    parser.add_argument("--no-semantic-scholar", action="store_true")
    parser.add_argument("--no-openalex", action="store_true")
    parser.add_argument("--no-arxiv", action="store_true")
    parser.add_argument("--offline-dry-run", action="store_true")

    # Writer mode
    parser.add_argument("--selection-payload", default="")
    parser.add_argument(
        "--writer-mode",
        choices=["auto", "llm", "template"],
        default="auto",
    )
    parser.add_argument("--llm-model", default=DEFAULT_LLM_MODEL)
    parser.add_argument("--llm-temperature", type=float, default=DEFAULT_LLM_TEMPERATURE)

    args = parser.parse_args()

    if args.mode == "search":
        if not args.project:
            raise SystemExit("--project est obligatoire en mode search")

        if not args.nlp_result:
            raise SystemExit("--nlp-result est obligatoire en mode search")

        report = run_ennoscholar_from_nlp(
            nlp_result_path=args.nlp_result,
            organisme=args.organisme,
            project=args.project,
            year=args.year,
            out_dir=args.out_dir or None,
            diagnostic_report_path=args.diagnostic_report or None,
            max_verrous=args.max_verrous,
            limit_per_query=args.limit_per_query,
            use_semantic_scholar=not args.no_semantic_scholar,
            use_openalex=not args.no_openalex,
            use_arxiv=not args.no_arxiv,
            offline_dry_run=args.offline_dry_run,
        )

        print_search_preview(report)
        return

    if args.mode == "write-selection":
        if not args.selection_payload:
            raise SystemExit("--selection-payload est obligatoire en mode write-selection")

        report = run_state_of_art_writer_from_selection(
            selection_payload_path=args.selection_payload,
            out_dir=args.out_dir or None,
            writer_mode=args.writer_mode,
            llm_model=args.llm_model,
            llm_temperature=args.llm_temperature,
        )

        print_writer_preview(report)
        return


if __name__ == "__main__":
    main()