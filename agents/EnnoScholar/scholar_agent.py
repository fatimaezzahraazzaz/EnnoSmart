# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scholar_agent.py — EnnoScholar V133

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
Version : 3.2.0
"""

import argparse
import importlib
import json
import os
import re
import socket
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .arxiv_client import ArxivClient
from .openalex_client import OpenAlexClient
from .paper_ranker import rank_papers_for_intent
from .query_builder import attach_queries_to_intent, is_query_safe_for_intent, detect_scholar_profile
from .scientific_intent_builder import build_scientific_intent
from .semantic_scholar_client import SemanticScholarClient
from .technical_source_catalog import get_technical_sources_for_intent
from .state_of_art_writer import build_state_of_art_section, build_consultant_state_of_art_context, build_consultant_template_state_of_art
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
MAX_ARTICLES_PER_VERROU = int(os.getenv("ENNOSCHOLAR_MAX_ARTICLES_PER_VERROU", "100"))
MIN_LIMIT_PER_QUERY = int(os.getenv("ENNOSCHOLAR_MIN_LIMIT_PER_QUERY", "10"))


ARXIV_ALLOWED_PROFILES = {
    # ArXiv est très utile pour numérique, IA, maths, physique, électronique.
    "software_ai_data_cyber",
    "signal_image_vision",
    "mathematics_modeling_simulation",
    "automation_robotics_embedded",
    "electronics_telecom_networks",
    "electrical_power_energy",
    "physics_instrumentation",
}


def _scholar_profile(intent: Dict[str, Any] | None) -> str:
    intent = intent or {}
    if isinstance(intent.get("cir_domain_profile"), dict) and intent["cir_domain_profile"].get("profile_id"):
        return str(intent["cir_domain_profile"].get("profile_id"))
    return str(
        intent.get("backend_enrichment_profile")
        or intent.get("enrichment_profile")
        or intent.get("profile")
        or ""
    )


def _use_arxiv_for_profile(profile: str) -> bool:
    # ArXiv génère beaucoup de faux positifs pour bâtiment, santé réglementaire,
    # chimie appliquée, agronomie et SHS. On l'active seulement quand son corpus
    # est naturellement pertinent.
    return profile in ARXIV_ALLOWED_PROFILES


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
# LLM EnnoSmart centralisé
# ──────────────────────────────────────────────────────────────────────────────

def _load_env_for_scholar_writer() -> None:
    """
    Charge le .env pour que le writer EnnoScholar utilise exactement la même
    configuration LLM que le reste du backend EnnoSmart.

    Important : cette fonction ne casse pas si python-dotenv n'est pas installé.
    """
    try:
        from dotenv import load_dotenv
    except Exception:
        return

    candidates = [
        Path.cwd() / ".env",
        Path(r"C:\EnnoSmart\backend_api\.env"),
        Path(r"C:\EnnoSmart\.env"),
        Path(__file__).resolve().parents[2] / ".env",
        Path(__file__).resolve().parents[3] / ".env" if len(Path(__file__).resolve().parents) > 3 else None,
    ]

    seen = set()
    for p in candidates:
        if p is None:
            continue
        try:
            p = p.resolve()
        except Exception:
            pass
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        try:
            if Path(p).exists():
                load_dotenv(Path(p), override=True)
        except Exception:
            # Ne jamais bloquer EnnoScholar à cause d'un .env secondaire.
            pass


def _ensure_llm_import_paths() -> None:
    """
    Rend le module LLM central importable, quelle que soit la manière dont
    EnnoScholar est lancé : FastAPI, CLI, script direct ou test.
    """
    candidates = [
        Path.cwd(),
        Path(r"C:\EnnoSmart\backend_api"),
        Path(r"C:\EnnoSmart"),
        Path(__file__).resolve().parents[2],  # backend_api si fichier dans agents/EnnoScholar
        Path(__file__).resolve().parents[3] if len(Path(__file__).resolve().parents) > 3 else None,
    ]

    for p in candidates:
        if p is None:
            continue
        try:
            sp = str(Path(p).resolve())
            if sp and sp not in sys.path:
                sys.path.insert(0, sp)
        except Exception:
            pass


def _env(name: str, default: str = "") -> str:
    _load_env_for_scholar_writer()
    return str(os.getenv(name, default) or "").strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except Exception:
        return default


def _openrouter_api_key() -> str:
    """
    Conservé pour compatibilité avec build_llm_state_of_art_from_selection().
    La clé est maintenant chargée depuis le .env central.
    """
    return _env("OPENROUTER_API_KEY", "")


def _effective_llm_model(model: str | None = None) -> str:
    """
    Évite de forcer openai/gpt-4o-mini si le .env contient un modèle différent.
    """
    env_model = _env("OPENROUTER_MODEL", "")
    requested = str(model or "").strip()

    if requested and requested != "openai/gpt-4o-mini":
        return requested

    if env_model:
        return env_model

    return requested or DEFAULT_LLM_MODEL


def _load_ennosmart_llm_client():
    """
    Charge le client LLM central du projet.

    Le fichier attendu est généralement :
        backend_api/modules/LLM/llm_client.py

    On teste plusieurs chemins pour rester compatible avec les versions
    précédentes du projet.
    """
    _load_env_for_scholar_writer()
    _ensure_llm_import_paths()

    errors = []

    for module_name in [
        "modules.LLM.llm_client",
        "modules.LLM",
        "modules.llm.llm_client",
        "modules.llm",
        "agents.llm.llm_client",
        "agents.LLM.llm_client",
        "llm.llm_client",
        "llm_client",
    ]:
        try:
            mod = importlib.import_module(module_name)
            client_cls = getattr(mod, "LLMClient")
            return client_cls
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")

    raise RuntimeError(
        "LLMClient EnnoSmart introuvable. Modules testés : "
        + " | ".join(errors)
    )


def _messages_to_single_prompt(messages: List[Dict[str, str]]) -> str:
    """
    Convertit le format chat en prompt unique, car le LLMClient central expose
    generate(prompt=...).
    """
    parts = []

    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user").strip().upper()
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        parts.append(f"[{role}]\n{content}")

    return "\n\n".join(parts).strip()


def call_openrouter_chat(
    messages: List[Dict[str, str]],
    model: str = DEFAULT_LLM_MODEL,
    temperature: float = DEFAULT_LLM_TEMPERATURE,
    timeout: int = DEFAULT_LLM_TIMEOUT,
) -> Dict[str, Any]:
    """
    Appel LLM centralisé pour EnnoScholar.

    Ancien comportement : appel OpenRouter direct avec urllib.
    Nouveau comportement : passage obligatoire par modules.LLM.llm_client.LLMClient.

    Avantages :
    - réutilise ENNOSMART_LLM_PROVIDER ;
    - réutilise OPENROUTER_MODEL ;
    - réutilise OPENROUTER_FALLBACK_MODELS ;
    - réutilise les timeouts ENNOSMART_LLM_CONNECT_TIMEOUT / READ_TIMEOUT ;
    - évite les fallback template alors qu'un module LLM existe déjà.
    """
    _load_env_for_scholar_writer()
    _ensure_llm_import_paths()

    prompt = _messages_to_single_prompt(messages)
    if not prompt:
        return {
            "ok": False,
            "error": "Prompt LLM vide.",
            "content": "",
            "model": model,
            "raw_usage": None,
        }

    effective_model = _effective_llm_model(model)

    try:
        Client = _load_ennosmart_llm_client()
        client = Client(model=effective_model)

        content = client.generate(
            prompt=prompt,
            temperature=temperature,
            max_output_tokens=_env_int("ENNOSCHOLAR_STATE_ART_MAX_OUTPUT_TOKENS", 4200),
            retries=_env_int("ENNOSCHOLAR_STATE_ART_LLM_RETRIES", 1),
        )

        content = str(content or "").strip()
        if not content:
            return {
                "ok": False,
                "error": "Réponse LLM vide.",
                "content": "",
                "model": effective_model,
                "raw_usage": None,
            }

        return {
            "ok": True,
            "error": "",
            "content": content,
            "model": effective_model,
            "raw_usage": None,
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": repr(exc),
            "content": "",
            "model": effective_model,
            "raw_usage": None,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Writer template sécurisé
# ──────────────────────────────────────────────────────────────────────────────

def build_template_state_of_art_from_selection(
    verrou_item: Dict[str, Any],
    citation_articles: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Fallback sans LLM, mais avec logique consultant CIR V134.
    Il ne résume pas article par article : il fusionne connaissances, limites et gap.
    """
    return build_consultant_template_state_of_art(
        verrou_item=verrou_item,
        citation_articles=citation_articles,
        project_context=verrou_item.get("diagnostic_context") or {},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Writer LLM contrôlé
# ──────────────────────────────────────────────────────────────────────────────

def build_llm_prompt_for_state_of_art(
    verrou_item: Dict[str, Any],
    citation_articles: List[Dict[str, Any]],
    project_context: Dict[str, Any] | None = None,
) -> List[Dict[str, str]]:
    """
    V134 : prompt style consultant CIR.
    Le LLM reçoit une matrice structurée : connaissances, limites, gaps.
    Il ne reçoit pas seulement une liste d'articles à résumer.
    """
    writer_context = build_consultant_state_of_art_context(
        verrou_item=verrou_item,
        citation_articles=citation_articles,
        project_context=project_context,
    )

    articles_payload = []
    for a in citation_articles:
        articles_payload.append({
            "citation_token": a.get("citation_token"),
            "label": a.get("label"),
            "title": a.get("title"),
            "year": a.get("year"),
            "tag": a.get("tag"),
            "relevance_score": a.get("relevance_score"),
            "abstract": clean_text(a.get("abstract"), ARTICLE_MAX_ABSTRACT_CHARS),
            "consultant_note": clean_text(a.get("consultant_note"), 600),
            "reason": clean_text(a.get("reason"), 600),
        })

    system = (
        "Tu es un consultant scientifique senior spécialisé dans la rédaction de dossiers CIR.\n"
        "Tu dois rédiger comme un consultant : synthèse progressive, pas résumé article par article.\n\n"
        "Règles obligatoires :\n"
        "1. Rédiger en français professionnel, style rapport CIR.\n"
        "2. Ne jamais inventer une source, un auteur, une année, un résultat ou une donnée.\n"
        "3. Utiliser uniquement les citations autorisées : [A1], [A2], [A3], etc.\n"
        "4. Toute affirmation scientifique issue d'un article doit être citée.\n"
        "5. Ne pas écrire une liste 'Article A dit... Article B dit...' ; fusionner les connaissances.\n"
        "6. Montrer d'abord ce que l'état de l'art établit, puis ses insuffisances.\n"
        "7. Conclure par le gap scientifique du projet, sans décider de l'éligibilité CIR finale.\n"
        "8. Si les sources ne permettent pas de conclure, l'indiquer clairement.\n"
    )

    user = f"""
Rédige l'état de l'art CIR à partir du contexte structuré ci-dessous.

# Contexte structuré produit par EnnoScholarWriter
{json.dumps(writer_context, ensure_ascii=False, indent=2)}

# Articles sélectionnés par le consultant
{json.dumps(articles_payload, ensure_ascii=False, indent=2)}

# Style attendu
Le style doit ressembler à un rapport consultant CIR :
- introduction du domaine et du verrou ;
- présentation synthétique des connaissances existantes ;
- références intégrées naturellement ;
- transition vers les insuffisances ;
- formulation claire des lacunes scientifiques/techniques ;
- conclusion reliant ces lacunes au verrou du projet.

# Format demandé
Rédige en Markdown avec exactement ces sections :

### 1. Analyse de l’état de l’art
Texte rédigé en paragraphes, sans liste article par article.

### 2. Apports des travaux existants
Synthèse fusionnée des connaissances, avec citations.

### 3. Insuffisances identifiées dans la littérature
Présenter les limites et non-couvertures, en lien avec le verrou.

### 4. Écart avec le cas spécifique du projet
Expliquer pourquoi les travaux existants ne suffisent pas à couvrir complètement le cas projet.

### 5. Conclusion pour la justification CIR
Conclusion prudente : le verrou est scientifiquement défendable ou à confirmer, mais la décision finale reste humaine.

Contraintes :
- Pas de sources non listées.
- Pas de citation inconnue.
- Pas de promesse du type "l'entreprise a résolu" si ce n'est pas dans les sources.
- Pas de formulations absolues comme "aucune étude au monde" ; préférer "les sources sélectionnées ne montrent pas".
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
        "mode": "llm_consultant_cir_v134",
        "writer_context": build_consultant_state_of_art_context(verrou_item, citation_articles, project_context),
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
        limit_per_query: int = 12,
        offline_dry_run: bool = False,
        max_articles_per_verrou: int | None = None,
    ):
        self.use_semantic_scholar = use_semantic_scholar
        self.use_openalex = use_openalex
        self.use_arxiv = use_arxiv
        # V133 : même si le backend/front envoie encore 3, on force un minimum
        # pour réellement viser jusqu'à 100 articles par verrou.
        requested_limit = int(os.getenv("ENNOSCHOLAR_LIMIT_PER_QUERY", str(limit_per_query or 12)))
        self.limit_per_query = max(MIN_LIMIT_PER_QUERY, min(requested_limit, 100))
        self.offline_dry_run = offline_dry_run
        self.max_articles_per_verrou = max(1, min(int(max_articles_per_verrou or MAX_ARTICLES_PER_VERROU), 100))

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
            or intent.get("backend_enrichment_profile")
            or intent.get("enrichment_profile")
            or ""
        )
        intent["enrichment_profile"] = intent.get("enrichment_profile") or intent["backend_enrichment_profile"]

        intent["original_title"] = (
            verrou.get("original_title")
            or raw_item.get("original_title")
            or ""
        )

        # V130 : après ajout du contexte backend/source_json, on redétecte le profil et on reconstruit les requêtes.
        intent["backend_enrichment_profile"] = detect_scholar_profile(intent)
        intent["enrichment_profile"] = intent["backend_enrichment_profile"]
        intent = attach_queries_to_intent(intent)

        # V130 : les requêtes générées par profil scientifique restent prioritaires.
        # Les suggested_queries issues des preuves projet sont gardées seulement si elles sont sûres.
        suggested_queries = verrou.get("suggested_queries") or []
        existing = intent.get("search_queries") or []
        merged_queries = []

        def _add_query_item(query_obj: Any, kind: str = "auto", max_items: int = 10):
            query = query_obj.get("query") if isinstance(query_obj, dict) else str(query_obj)
            query = clean_text(query, 220)
            if not query:
                return
            if not is_query_safe_for_intent(query, intent):
                return
            if query.lower() in {
                x.get("query", "").lower()
                for x in merged_queries
                if isinstance(x, dict)
            }:
                return
            if isinstance(query_obj, dict):
                item = dict(query_obj)
                item["query"] = query
                item.setdefault("kind", kind)
            else:
                item = {"query": query, "kind": kind}
            merged_queries.append(item)
            if len(merged_queries) > max_items:
                del merged_queries[max_items:]

        for q in existing:
            _add_query_item(q, kind="domain_profile_query", max_items=12)

        safe_suggested_count = 0
        if isinstance(suggested_queries, list) and suggested_queries:
            intent["suggested_queries"] = suggested_queries
            for q in suggested_queries:
                before = len(merged_queries)
                _add_query_item(q, kind="backend_enriched_source_query_safe", max_items=12)
                if len(merged_queries) > before:
                    safe_suggested_count += 1
                if safe_suggested_count >= 2:
                    break

        intent["search_queries"] = merged_queries[:12]

        queries = intent.get("search_queries") or []
        all_papers = []
        errors = []
        profile = _scholar_profile(intent)
        technical_sources = get_technical_sources_for_intent(intent, max_sources=5)
        # V131 : sources techniques séparées. Elles ne sont plus mélangées aux articles,
        # ne sont plus taguées Direct/Connexe, et ne gonflent plus le score scientifique.
        use_arxiv_for_this_verrou = bool(self.use_arxiv and _use_arxiv_for_profile(profile))

        source_status = {
            "semantic_scholar": {"enabled": bool(self.use_semantic_scholar), "success": 0, "errors": 0, "api_limited": 0},
            "openalex": {"enabled": bool(self.use_openalex), "success": 0, "errors": 0, "api_limited": 0},
            "arxiv": {"enabled": bool(use_arxiv_for_this_verrou), "success": 0, "errors": 0, "api_limited": 0},
        }

        def _record_results(source_name: str, res: List[Dict[str, Any]]) -> None:
            for p in res:
                if p.get("normalized_error"):
                    errors.append(p)
                    source_status[source_name]["errors"] += 1
                    if p.get("api_limited") or p.get("http_status") == 429 or "429" in str(p.get("error") or ""):
                        source_status[source_name]["api_limited"] += 1
                else:
                    all_papers.append(p)
                    source_status[source_name]["success"] += 1

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
                    _record_results("semantic_scholar", res)

                if self.use_openalex:
                    res = self.openalex_client.search_works(
                        query,
                        limit=self.limit_per_query,
                    )
                    _record_results("openalex", res)

                if use_arxiv_for_this_verrou:
                    res = self.arxiv_client.search_papers(
                        query,
                        limit=self.limit_per_query,
                    )
                    _record_results("arxiv", res)

                # petite pause entre requêtes pour limiter les 429
                time.sleep(float(os.getenv("ENNOSCHOLAR_QUERY_SLEEP", "0.8")))

        enabled_sources = [k for k, v in source_status.items() if v.get("enabled")]
        successful_sources = [k for k in enabled_sources if source_status[k].get("success", 0) > 0]
        limited_sources = [k for k in enabled_sources if source_status[k].get("api_limited", 0) > 0]
        search_status = {
            "queries_count": len(queries),
            "limit_per_query": self.limit_per_query,
            "max_articles_per_verrou": self.max_articles_per_verrou,
            "raw_papers_retrieved": len(all_papers),
            "enabled_sources": enabled_sources,
            "successful_sources": successful_sources,
            "limited_sources": limited_sources,
            "api_limited": bool(limited_sources),
            "all_sources_failed": bool(enabled_sources and not successful_sources and errors),
            "source_status": source_status,
        }

        ranked = rank_papers_for_intent(
            all_papers,
            intent,
            top_n=self.max_articles_per_verrou,
        )

        validation = validate_verrou_scientifically(
            intent,
            ranked,
            technical_sources=technical_sources,
            errors=errors,
            search_status=search_status,
        )

        result = {
            "verrou_id": verrou.get("verrou_id"),
            "verrou_title": intent.get("verrou_title") or verrou.get("title"),
            "verrou_text": verrou.get("text"),
            "frascati": verrou.get("frascati"),
            "scientific_intent": intent,
            "queries": queries,
            "articles_found": len(ranked),
            "raw_articles_retrieved": len(all_papers),
            "technical_sources_added": len(technical_sources),
            "articles_limit": self.max_articles_per_verrou,
            "articles": ranked,
            "technical_sources": technical_sources,
            "search_status": search_status,
            "errors": errors[:40],
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
            "version": "v133_title_abstract_strict_direct_force_100",
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
            "version": "v134_consultant_cir_writer_internal_pipeline",
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
    limit_per_query: int = 12,
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
    parser.add_argument("--limit-per-query", type=int, default=12)

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