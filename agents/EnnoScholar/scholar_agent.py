# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scholar_agent.py — EnnoScholar V145 scientific precision

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
Version : 3.2.0-v146
"""

import argparse
import hashlib
import importlib
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .arxiv_client import ArxivClient
from .openalex_client import OpenAlexClient
from .paper_ranker import rank_papers_for_intent, dedupe_papers
try:
    from .paper_reranker_model import rerank_papers_with_bge
except Exception:
    rerank_papers_with_bge = None
try:
    from .article_summarizer import summarize_candidate_articles
except Exception:
    summarize_candidate_articles = None
from .query_builder import attach_queries_to_intent, is_query_safe_for_intent, detect_scholar_profile, select_best_queries_for_intent
from .scientific_intent_builder import build_scientific_intent
from .semantic_scholar_client import SemanticScholarClient
from .crossref_client import CrossrefClient
from .doaj_client import DoajClient
from .hal_client import HalClient
from .core_client import CoreClient
from .zenodo_client import ZenodoClient
from .europe_pmc_client import EuropePmcClient
from .ieee_client import IeeeClient
from .github_client import GitHubClient
from .huggingface_client import HuggingFaceClient
from .source_router import build_source_plan
from .technical_source_catalog import get_technical_sources_for_intent
try:
    from .scholar_memory_v2 import match_memory_v2_articles
except Exception:
    match_memory_v2_articles = None
from .state_of_art_writer import build_state_of_art_section, build_consultant_state_of_art_context, build_consultant_template_state_of_art
from .utils import clean_text, flatten_text, read_json, write_json
from .verrou_scientific_validator import validate_verrou_scientifically
from .contracts import ContractError, load_confirmed_contract
from .storage_paths import confirmed_verrous_path as default_confirmed_verrous_path
from .verrou_selector import select_confirmed_verrous


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
MAX_ARTICLES_PER_VERROU = int(os.getenv("ENNOSCHOLAR_MAX_ARTICLES_PER_VERROU", "120"))
MIN_LIMIT_PER_QUERY = int(os.getenv("ENNOSCHOLAR_MIN_LIMIT_PER_QUERY", "35"))
MAX_LIMIT_PER_QUERY = int(os.getenv("ENNOSCHOLAR_MAX_LIMIT_PER_QUERY", "100"))
MAX_QUERIES_PER_VERROU = int(os.getenv("ENNOSCHOLAR_MAX_QUERIES_PER_VERROU", "3"))
SOURCE_WORKERS = int(os.getenv("ENNOSCHOLAR_SOURCE_WORKERS", "6"))
# La mémoire de dossiers antérieurs est désactivée par défaut. Elle ne peut être
# activée que volontairement et ses résultats restent de simples candidats à
# reranker ; ils ne constituent jamais des preuves de l'état de l'art.
MEMORY_V2_TOP_K = int(os.getenv("ENNOSCHOLAR_MEMORY_V2_TOP_K", "0"))
SUMMARY_TOP_N = int(os.getenv("ENNOSCHOLAR_SUMMARY_MAX_ARTICLES_PER_VERROU", "0"))

# V141 — cache global de run EnnoScholar.
# Les clients SemanticScholar/OpenAlex/ArXiv ont déjà un cache par requête.
# Ce cache-ci est plus haut niveau : si le payload + la configuration n'ont pas changé,
# on réutilise directement le rapport complet, sans refaire les appels API, le ranking,
# le reranking BGE ni les résumés/abstracts.
RUN_CACHE_VERSION = "v150_problem_evidence_year_cutoff"


def _env_bool_value(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name, "") or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y", "on", "oui"}


# Filtre optionnel articles gratuits / fulltext exploitable.
# Désactivé par défaut : la recherche et le ranking doivent aussi conserver les
# articles payants pertinents. Après sélection, le pipeline direct puis le MCP
# légal cherchent une version gratuite vérifiée du même article.
def _article_has_free_fulltext(article: Dict[str, Any]) -> bool:
    if not isinstance(article, dict):
        return False

    source = clean_text(article.get("source"), 160).lower()

    # arXiv est considéré comme librement accessible.
    if "arxiv" in source:
        return True

    # Les champs sont renseignés par semantic_scholar_client.py, openalex_client.py,
    # arxiv_client.py, et parfois par Memory V2 si l'article avait déjà un PDF.
    for key in [
        "free_fulltext_available",
        "is_open_access",
        "open_access",
        "has_full_text",
        "pdf_available",
    ]:
        if article.get(key) is True:
            return True

    for key in [
        "pdf_url",
        "primary_pdf_url",
        "open_access_pdf_url",
        "fulltext_url",
        "full_text_url",
    ]:
        value = clean_text(article.get(key), 1000)
        if value.startswith("http"):
            return True

    open_access_pdf = article.get("open_access_pdf") or article.get("openAccessPdf")
    if isinstance(open_access_pdf, dict) and clean_text(open_access_pdf.get("url"), 1000).startswith("http"):
        return True

    status = clean_text(article.get("fulltext_access_status"), 200).lower()
    if status in {"open_access_pdf", "open_access_landing", "arxiv_pdf"}:
        return True

    return False


def _filter_free_fulltext_articles(
    articles: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    require_free = _env_bool_value("ENNOSCHOLAR_REQUIRE_FREE_FULLTEXT", False)
    keep_memory_without_pdf = _env_bool_value("ENNOSCHOLAR_KEEP_MEMORY_V2_WITHOUT_PDF", False)

    if not require_free:
        return articles, {
            "enabled": False,
            "reason": "ENNOSCHOLAR_REQUIRE_FREE_FULLTEXT=false",
            "input_count": len(articles or []),
            "kept_count": len(articles or []),
            "removed_count": 0,
        }

    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []

    for article in articles or []:
        if not isinstance(article, dict):
            continue

        source = clean_text(article.get("source"), 160).lower()
        has_free = _article_has_free_fulltext(article)

        # Par défaut, même Memory V2 doit avoir un lien/PDF exploitable, sinon
        # l'article risque de rebloquer la préparation après sélection.
        if has_free or (keep_memory_without_pdf and "memory_v2" in source):
            item = dict(article)
            item.setdefault("free_fulltext_filter", "kept")
            kept.append(item)
        else:
            item = dict(article)
            item["free_fulltext_filter"] = "removed_no_free_pdf_or_oa"
            removed.append(item)

    return kept, {
        "enabled": True,
        "policy": "keep_only_open_access_or_pdf_available",
        "env": {
            "ENNOSCHOLAR_REQUIRE_FREE_FULLTEXT": True,
            "ENNOSCHOLAR_KEEP_MEMORY_V2_WITHOUT_PDF": keep_memory_without_pdf,
        },
        "input_count": len(articles or []),
        "kept_count": len(kept),
        "removed_count": len(removed),
        "removed_examples": [
            {
                "source": clean_text(x.get("source"), 80),
                "title": clean_text(x.get("title"), 220),
                "year": x.get("year"),
                "reason": x.get("free_fulltext_filter"),
            }
            for x in removed[:20]
        ],
    }


def _filter_articles_after_project_year(
    articles: List[Dict[str, Any]],
    project_year: Any,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Un état de l'art d'une année donnée ne peut pas être défendu avec une
    publication postérieure. Les articles sans année restent candidats, mais
    toute année connue strictement supérieure est écartée.
    """
    enabled = _env_bool_value("ENNOSCHOLAR_ENFORCE_PROJECT_YEAR_CUTOFF", True)
    match = re.search(r"\b(19|20)\d{2}\b", str(project_year or ""))
    cutoff = int(match.group(0)) if match else None
    if not enabled or cutoff is None:
        return articles, {
            "enabled": False,
            "cutoff_year": cutoff,
            "input_count": len(articles or []),
            "kept_count": len(articles or []),
            "removed_count": 0,
        }

    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    for article in articles or []:
        if not isinstance(article, dict):
            continue
        raw_year = article.get("year") or article.get("publication_year")
        year_match = re.search(r"\b(19|20)\d{2}\b", str(raw_year or ""))
        article_year = int(year_match.group(0)) if year_match else None
        if article_year is not None and article_year > cutoff:
            removed.append(article)
        else:
            kept.append(article)

    return kept, {
        "enabled": True,
        "cutoff_year": cutoff,
        "input_count": len(articles or []),
        "kept_count": len(kept),
        "removed_count": len(removed),
        "removed_examples": [
            {
                "title": clean_text(item.get("title"), 220),
                "year": item.get("year"),
                "source": clean_text(item.get("source"), 80),
            }
            for item in removed[:10]
        ],
    }


def _cache_root_for_scholar() -> Path:
    root = os.getenv("ENNOSCHOLAR_CACHE_DIR")
    if root:
        return Path(root)
    return Path.cwd() / "storage" / "ennoscholar_cache"


def _stable_json_for_cache(obj: Any) -> str:
    """JSON stable pour construire une clé de cache robuste."""
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return json.dumps(str(obj), ensure_ascii=False, sort_keys=True)


def _run_cache_payload_fingerprint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Empreinte du payload utile. On garde les verrous, domaine et contexte diagnostic,
    car si EnnoDiagnostic change, EnnoScholar doit recalculer.
    """
    payload = payload or {}
    return {
        "organisme": payload.get("organisme") or payload.get("organization") or "",
        "project": payload.get("project") or payload.get("projet") or "",
        "year": payload.get("year") or payload.get("annee") or payload.get("année") or "",
        "domain_detection": payload.get("domain_detection") or {},
        "diagnostic_context": payload.get("diagnostic_context") or {},
        "verrous": payload.get("verrous") or [],
    }


def _run_cache_config_fingerprint(agent: Any) -> Dict[str, Any]:
    """
    Empreinte de configuration. Si tu changes ranking strict, BGE, limites, mémoire,
    summary/no-Gemini, etc., la clé change et le cache ancien n'est pas réutilisé.
    """
    return {
        "run_cache_version": RUN_CACHE_VERSION,
        "version": "v150_problem_evidence_year_cutoff",
        "use_semantic_scholar": bool(getattr(agent, "use_semantic_scholar", False)),
        "use_openalex": bool(getattr(agent, "use_openalex", False)),
        "use_arxiv": bool(getattr(agent, "use_arxiv", False)),
        "offline_dry_run": bool(getattr(agent, "offline_dry_run", False)),
        "limit_per_query": int(getattr(agent, "limit_per_query", 0) or 0),
        "max_articles_per_verrou": int(getattr(agent, "max_articles_per_verrou", 0) or 0),
        "max_queries_per_verrou": int(getattr(agent, "max_queries_per_verrou", 0) or 0),
        "source_workers": int(getattr(agent, "source_workers", 0) or 0),
        "verrou_workers": int(getattr(agent, "verrou_workers", 0) or 0),
        "fast_mode": bool(getattr(agent, "fast_mode", False)),
        "fast_include_secondary_sources": os.getenv("ENNOSCHOLAR_FAST_INCLUDE_SECONDARY_SOURCES", "false"),
        "fast_search_artifacts": os.getenv("ENNOSCHOLAR_FAST_SEARCH_ARTIFACTS", "false"),
        "memory_v2_top_k": int(getattr(agent, "memory_v2_top_k", 0) or 0),
        "summary_top_n": int(SUMMARY_TOP_N),
        "enable_bge_reranker": os.getenv("ENNOSCHOLAR_ENABLE_BGE_RERANKER", ""),
        "reranker_model": os.getenv("ENNOSCHOLAR_RERANKER_MODEL", ""),
        "reranker_top_k_input": os.getenv("ENNOSCHOLAR_RERANKER_TOP_K_INPUT", ""),
        "reranker_weight": os.getenv("ENNOSCHOLAR_RERANKER_WEIGHT", ""),
        "enable_llm_summary": os.getenv("ENNOSCHOLAR_ENABLE_LLM_SUMMARY", ""),
        "summary_provider": os.getenv("ENNOSCHOLAR_SUMMARY_PROVIDER", ""),
        "translate_abstract_fr": os.getenv("ENNOSCHOLAR_TRANSLATE_ABSTRACT_FR", ""),
        "cache_ttl_days": os.getenv("ENNOSCHOLAR_RUN_CACHE_TTL_DAYS", ""),
        "require_free_fulltext": os.getenv("ENNOSCHOLAR_REQUIRE_FREE_FULLTEXT", "false"),
        "enforce_project_year_cutoff": os.getenv("ENNOSCHOLAR_ENFORCE_PROJECT_YEAR_CUTOFF", "true"),
        "keep_memory_v2_without_pdf": os.getenv("ENNOSCHOLAR_KEEP_MEMORY_V2_WITHOUT_PDF", "false"),
        "source_router_version": "v148_fast_tiered_source_router",
        "use_doaj": os.getenv("ENNOSCHOLAR_USE_DOAJ", "true"),
        "use_crossref": os.getenv("ENNOSCHOLAR_USE_CROSSREF", "true"),
        "use_hal": os.getenv("ENNOSCHOLAR_USE_HAL", "true"),
        "use_core": os.getenv("ENNOSCHOLAR_USE_CORE", "true"),
        "use_zenodo": os.getenv("ENNOSCHOLAR_USE_ZENODO", "true"),
        "use_ieee": os.getenv("ENNOSCHOLAR_USE_IEEE", "true"),
        "use_europe_pmc": os.getenv("ENNOSCHOLAR_USE_EUROPE_PMC", "true"),
        "use_github": os.getenv("ENNOSCHOLAR_USE_GITHUB", "true"),
        "use_huggingface": os.getenv("ENNOSCHOLAR_USE_HUGGINGFACE", "true"),
    }


def _run_cache_key(payload: Dict[str, Any], agent: Any) -> str:
    raw = _stable_json_for_cache({
        "payload": _run_cache_payload_fingerprint(payload),
        "config": _run_cache_config_fingerprint(agent),
    })
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:40]


def _run_cache_path(key: str) -> Path:
    return _cache_root_for_scholar() / "runs" / f"{key}.json"


def _read_run_cache(path: Path, ttl_days: int = 30) -> Dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        if ttl_days > 0:
            age = time.time() - path.stat().st_mtime
            if age > ttl_days * 86400:
                return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("report"), dict):
            report = data["report"]
            if isinstance(report.get("results"), list):
                return report
    except Exception:
        return None
    return None


def _write_run_cache(path: Path, key: str, report: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_version": RUN_CACHE_VERSION,
            "cache_key": key,
            "created_at": _now_iso(),
            "report": report,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception:
        # Le cache ne doit jamais casser le run EnnoScholar.
        pass


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




def _verrou_number_from_result(result: Dict[str, Any], fallback_index: int) -> int:
    """Numéro lisible du verrou dans l'interface, sans règle métier."""
    for key in ["verrou_index", "frontend_result_index", "verrou_number", "number", "index"]:
        value = result.get(key)
        try:
            if value is not None and str(value).strip() != "":
                number = int(value)
                # frontend_result_index est parfois 0-based.
                if key == "frontend_result_index" and number >= 0:
                    return number + 1
                if number > 0:
                    return number
        except Exception:
            pass
    return max(1, int(fallback_index or 0) + 1)


def _coverage_identity(info: Dict[str, Any]) -> str:
    """Clé d'un lien article-verrou. Pas de règle domaine : id puis titre."""
    verrou_id = clean_text(info.get("verrou_id"), 120)
    if verrou_id:
        return "id:" + verrou_id
    title = clean_text(info.get("verrou_title"), 260).lower()
    number = str(info.get("verrou_number") or "")
    return f"title:{title}:{number}"


def _merge_coverage_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fusionne les liens article-verrou en gardant le meilleur score par verrou."""
    best: Dict[str, Dict[str, Any]] = {}
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        key = _coverage_identity(item)
        if not key:
            continue
        old = best.get(key)
        if old is None or _safe_float(item.get("relevance_score")) > _safe_float(old.get("relevance_score")):
            best[key] = item

    def sort_key(x: Dict[str, Any]) -> Tuple[Any, ...]:
        try:
            n = int(x.get("verrou_number") or 0)
        except Exception:
            n = 0
        return (0 if n else 1, n, clean_text(x.get("verrou_title"), 260).lower())

    return sorted(best.values(), key=sort_key)


def _article_existing_coverage(article: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Lit les liens déjà présents, au niveau article ou source_json."""
    out: List[Dict[str, Any]] = []
    if isinstance(article.get("covered_verrous"), list):
        out.extend([x for x in article.get("covered_verrous") or [] if isinstance(x, dict)])
    sj = article.get("source_json") if isinstance(article.get("source_json"), dict) else {}
    if isinstance(sj.get("covered_verrous"), list):
        out.extend([x for x in sj.get("covered_verrous") or [] if isinstance(x, dict)])
    return out


def _annotate_multi_verrou_coverage(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    V144 — vérification agent des articles multi-verrous.

    Principe important :
    - on déduplique seulement les doublons internes à un verrou dans paper_ranker ;
    - on ne supprime jamais un article parce qu'il est aussi ressorti pour un autre verrou ;
    - si le même DOI/paper_id/titre est classé dans plusieurs verrous, on l'enrichit avec
      source_json.covered_verrous pour que le frontend affiche « couvre V1, V3, ... » ;
    - aucune règle métier ou hardcoding domaine : la preuve vient uniquement du fait que
      le ranker/reranker l'a retenu dans la liste de chaque verrou.
    """
    if not isinstance(results, list):
        return {"enabled": True, "articles_with_multi_verrou": 0, "links_count": 0}

    coverage_by_paper: Dict[str, List[Dict[str, Any]]] = {}

    for r_idx, result in enumerate(results):
        if not isinstance(result, dict):
            continue

        verrou_id = clean_text(result.get("verrou_id"), 120)
        verrou_title = clean_text(result.get("verrou_title") or result.get("title"), 320)
        verrou_number = _verrou_number_from_result(result, r_idx)

        articles = result.get("articles") or []
        if not isinstance(articles, list):
            continue

        for rank_position, article in enumerate(articles, start=1):
            if not isinstance(article, dict) or not clean_text(article.get("title")):
                continue

            paper_key = _paper_stable_key(article)
            if not paper_key:
                continue

            score = _safe_float(
                article.get("bge_reranker_score")
                or article.get("relevance_score")
                or article.get("score")
            )
            tag = clean_text(article.get("tag") or article.get("tag_article"), 80)

            coverage_by_paper.setdefault(paper_key, []).append({
                "verrou_id": verrou_id,
                "verrou_number": verrou_number,
                "verrou_title": verrou_title,
                "tag": tag,
                "relevance_score": score,
                "rank_position": rank_position,
                "reason": clean_text(article.get("reason"), 700),
                "evidence_source": "ranker_per_verrou",
                "verified_by_agent": True,
            })

    articles_with_multi = 0
    total_links = 0

    for r_idx, result in enumerate(results):
        if not isinstance(result, dict):
            continue

        verrou_id = clean_text(result.get("verrou_id"), 120)
        verrou_title = clean_text(result.get("verrou_title") or result.get("title"), 320)
        verrou_number = _verrou_number_from_result(result, r_idx)

        articles = result.get("articles") or []
        if not isinstance(articles, list):
            continue

        for article in articles:
            if not isinstance(article, dict) or not clean_text(article.get("title")):
                continue

            paper_key = _paper_stable_key(article)
            linked = coverage_by_paper.get(paper_key, [])

            # On ajoute aussi le verrou courant même si le papier n'était pas dans la map
            # pour garantir une structure stable côté frontend.
            current_link = {
                "verrou_id": verrou_id,
                "verrou_number": verrou_number,
                "verrou_title": verrou_title,
                "tag": clean_text(article.get("tag") or article.get("tag_article"), 80),
                "relevance_score": _safe_float(article.get("relevance_score") or article.get("score")),
                "rank_position": int(article.get("rank_position") or 0) or 0,
                "reason": clean_text(article.get("reason"), 700),
                "evidence_source": "ranker_per_verrou",
                "verified_by_agent": True,
            }

            coverage = _merge_coverage_items(_article_existing_coverage(article) + linked + [current_link])
            count = len(coverage)
            total_links += count
            if count > 1:
                articles_with_multi += 1

            article["covered_verrous"] = coverage
            article["multi_verrou_article"] = count > 1
            article["multi_verrou_count"] = count
            article["multi_verrou_policy"] = "kept_per_verrou_not_globally_deduped"
            article["verrou_id"] = article.get("verrou_id") or verrou_id
            article["verrou_title"] = article.get("verrou_title") or verrou_title
            article["verrou_number"] = article.get("verrou_number") or verrou_number

            sj = dict(article.get("source_json")) if isinstance(article.get("source_json"), dict) else {}
            sj["covered_verrous"] = coverage
            sj["multi_verrou_article"] = count > 1
            sj["multi_verrou_count"] = count
            sj["multi_verrou_policy"] = "agent_verified_from_ranker_results"
            sj.setdefault("verrou_id", verrou_id)
            sj.setdefault("verrou_title", verrou_title)
            sj.setdefault("verrou_number", verrou_number)
            article["source_json"] = sj

    return {
        "enabled": True,
        "version": "v146_agent_verified_multi_verrou_coverage",
        "policy": "dedupe_inside_each_verrou_only_keep_same_article_across_verrous",
        "source_of_truth": "ranker_and_reranker_results_per_verrou",
        "no_frontend_inference_required": True,
        "papers_seen": len(coverage_by_paper),
        "articles_with_multi_verrou": articles_with_multi,
        "links_count": total_links,
    }

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
    """
    Décision consultant.

    Important V143 :
    - la décision humaine est prioritaire ;
    - un article taggé "Hors sujet" peut être exploité si le consultant le garde ;
    - on détecte donc aussi consultant_status="garde" / status="garde".
    """
    if not isinstance(article, dict):
        return False

    sj = article.get("source_json") if isinstance(article.get("source_json"), dict) else {}

    for key in [
        "consultant_selected",
        "selected",
        "is_selected",
        "isSelected",
        "keep",
        "kept",
    ]:
        if article.get(key) is True or sj.get(key) is True:
            return True

    raw_status = " ".join([
        str(article.get("consultant_status") or ""),
        str(article.get("consultant_decision") or ""),
        str(article.get("decision_article") or ""),
        str(article.get("selection_status") or ""),
        str(article.get("status_article") or ""),
        str(article.get("status") or ""),
        str(article.get("decision") or ""),
        str(sj.get("consultant_status") or ""),
        str(sj.get("db_consultant_status") or ""),
        str(sj.get("consultant_decision") or ""),
        str(sj.get("decision_article") or ""),
        str(sj.get("selection_status") or ""),
        str(sj.get("status") or ""),
    ])
    status = clean_text(raw_status, 500).lower()

    keep_words = [
        "garde", "gardé", "gardee", "gardée", "garder",
        "retenu", "retenue", "validé", "valide", "validée",
        "selected", "select", "keep", "kept", "accepted",
    ]
    reject_words = [
        "rejete", "rejeté", "rejetee", "rejetée", "reject",
        "rejected", "remove", "removed", "ignore", "ignored",
    ]

    if any(w in status for w in reject_words):
        return False

    return any(w in status for w in keep_words)


def _select_articles_from_verrou_item(
    verrou_item: Dict[str, Any],
    default_tags: Set[str] | None = None,
) -> List[Dict[str, Any]]:
    """
    Récupère les articles sélectionnés dans plusieurs formats possibles :
    - selected_articles
    - articles avec consultant_selected=True
    - fallback optionnel : Direct / Connexe / Fondamental pour générer un template,
      mais jamais en mode rédaction finale sans décision consultant.

    V143 : ne filtre PAS les articles Hors sujet si le consultant les a gardés.
    On ajoute seulement un avertissement pour la traçabilité.
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

        item = dict(a)
        tag = clean_text(item.get("tag") or item.get("tag_article") or "", 80).lower()
        if "hors" in tag:
            item["kept_despite_hors_sujet"] = True
            item.setdefault(
                "consultant_warning",
                "Article gardé par le consultant malgré un tag Hors sujet : exploitable avec vigilance et justification explicite.",
            )

        out.append(item)

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
    Compatibilité historique : l'objet reçu doit désormais être le contrat de
    verrous confirmés. Aucune reconstruction depuis les sorties NLP n'est faite.
    """
    selected = select_confirmed_verrous(nlp_result)
    count = len(selected.get("verrous") or [])
    if max_verrous and max_verrous < count:
        raise ContractError(
            "confirmed_verrous_truncation_forbidden",
            "La liste des verrous confirmés ne peut pas être tronquée.",
            {"confirmed_count": count, "requested_max": max_verrous},
        )
    return selected


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
            "tag": clean_text(article.get("tag") or article.get("tag_article"), 80),
            "relevance_score": article.get("relevance_score"),
            "consultant_note": clean_text(article.get("consultant_note"), 600),
            "reason": clean_text(article.get("reason"), 600),
            "kept_despite_hors_sujet": bool(article.get("kept_despite_hors_sujet")),
            "consultant_warning": clean_text(article.get("consultant_warning"), 600),
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
    confirmed_verrous_path: str | Path | None = None,
) -> Dict[str, Any]:
    legacy_path = Path(nlp_result_path)
    contract_path = (
        Path(confirmed_verrous_path)
        if confirmed_verrous_path
        else default_confirmed_verrous_path(organisme, project, str(year))
    )
    if not contract_path.is_file() and legacy_path.name.lower() == "confirmed_verrous.json":
        contract_path = legacy_path
    if not contract_path.is_file():
        legacy_payload = read_json(legacy_path, {})
        if isinstance(legacy_payload, dict) and (
            isinstance(legacy_payload.get("confirmed_verrous"), list)
            or (
                isinstance(legacy_payload.get("verrous"), list)
                and legacy_payload.get("payload_type") == "ennoscholar_confirmed_verrous_contract_v1"
            )
        ):
            contract_path = legacy_path
        else:
            raise ContractError(
                "confirmed_verrous_missing",
                "EnnoScholar exige confirmed_verrous.json et ne reconstruit plus les verrous depuis le NLP.",
                {
                    "expected_path": str(contract_path),
                    "legacy_nlp_path_ignored": str(legacy_path),
                },
            )

    contract = load_confirmed_contract(contract_path)
    extracted = select_confirmed_verrous(
        {
            **contract,
            "domain_detection": read_json(legacy_path, {}).get("domain_detection", {})
            if legacy_path.is_file()
            else {},
        },
        source_path=str(contract_path),
    )
    count = len(extracted.get("verrous") or [])
    if max_verrous and max_verrous < count:
        raise ContractError(
            "confirmed_verrous_truncation_forbidden",
            "La liste des verrous confirmés ne peut pas être tronquée.",
            {"confirmed_count": count, "requested_max": max_verrous},
        )

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
        "input_confirmed_verrous": str(contract_path),
        "input_diagnostic_report": str(diagnostic_report_path) if diagnostic_report_path else "",
        "domain_detection": extracted.get("domain_detection") or {},
        "diagnostic_context": diagnostic_context,
        "pack_counts": extracted.get("pack_counts"),
        "selector": extracted.get("selector"),
        "verrou_fingerprint": extracted.get("verrou_fingerprint"),
        "confirmed_contract": extracted.get("confirmed_contract"),
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



# V146 — seuls les articles Memory V2 validés par le ranker ET le BGE restent visibles.
def _filter_memory_v2_after_rerank(
    ranked: List[Dict[str, Any]],
    memory_report: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    kept: List[Dict[str, Any]] = []
    accepted = 0
    rejected = 0
    rejected_examples: List[Dict[str, Any]] = []
    for article in ranked or []:
        if not isinstance(article, dict) or not article.get("memory_v2_prior"):
            kept.append(article)
            continue
        ok = article.get("memory_v2_accepted_after_bge") is True
        if ok:
            accepted += 1
            kept.append(article)
        else:
            rejected += 1
            if len(rejected_examples) < 10:
                rejected_examples.append({
                    "title": clean_text(article.get("title"), 240),
                    "tag": article.get("tag"),
                    "bge": article.get("bge_reranker_score"),
                    "reason": article.get("memory_v2_rejection_reason") or "not_validated_after_rerank",
                })
    report = {
        "post_rerank_policy": "keep_memory_only_if_core_support_and_bge_validated",
        "post_rerank_accepted_count": accepted,
        "post_rerank_rejected_count": rejected,
        "post_rerank_rejected_examples": rejected_examples,
    }
    return kept, report


def _select_relevant_articles_for_output(
    ranked: List[Dict[str, Any]],
    top_n: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Conserve le classement interne complet, mais limite la liste affichée aux
    articles utiles pour expliquer et défendre le verrou.
    """
    ranked = [item for item in (ranked or []) if isinstance(item, dict)]
    limit = max(1, int(top_n or len(ranked) or 1))
    limits = {
        "Direct": max(0, int(os.getenv("ENNOSCHOLAR_MAX_DIRECT", str(limit)) or limit)),
        "Connexe": max(0, int(os.getenv("ENNOSCHOLAR_MAX_CONNEXE", str(limit)) or limit)),
        "Fondamental": max(0, int(os.getenv("ENNOSCHOLAR_MAX_FONDAMENTAL", "5") or 5)),
        "Hors sujet": max(0, int(os.getenv("ENNOSCHOLAR_MAX_HORS_SUJET_EXAMPLES", "0") or 0)),
    }

    before = {
        tag: sum(1 for item in ranked if item.get("tag") == tag)
        for tag in limits
    }
    selected: List[Dict[str, Any]] = []
    for tag in ["Direct", "Connexe", "Fondamental", "Hors sujet"]:
        tag_limit = limits[tag]
        if tag_limit > 0:
            selected.extend(
                [item for item in ranked if item.get("tag") == tag][:tag_limit]
            )
    selected = selected[:limit]
    after = {
        tag: sum(1 for item in selected if item.get("tag") == tag)
        for tag in limits
    }
    return selected, {
        "policy": "v149_direct_connexe_first_fundamental_context_only",
        "input_count": len(ranked),
        "output_count": len(selected),
        "limits": limits,
        "counts_before": before,
        "counts_after": after,
    }


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

        # V136 production :
        # - garder un volume élevé d'articles candidats ;
        # - mais limiter le nombre de requêtes et paralléliser les sources ;
        # - utiliser les timeouts courts des clients pour éviter les blocages longs.
        try:
            requested_limit = int(limit_per_query or os.getenv("ENNOSCHOLAR_LIMIT_PER_QUERY", "50"))
        except Exception:
            requested_limit = 50

        self.limit_per_query = max(
            1,
            min(
                max(MIN_LIMIT_PER_QUERY, requested_limit),
                max(1, min(MAX_LIMIT_PER_QUERY, 100)),
            ),
        )
        self.offline_dry_run = offline_dry_run
        self.fast_mode = _env_bool_value("ENNOSCHOLAR_FAST_MODE", True)
        self.max_articles_per_verrou = max(
            1,
            min(int(max_articles_per_verrou or MAX_ARTICLES_PER_VERROU), 200),
        )

        self.max_queries_per_verrou = max(1, min(MAX_QUERIES_PER_VERROU, 6))
        configured_source_workers = int(
            os.getenv("ENNOSCHOLAR_SOURCE_WORKERS", "12" if self.fast_mode else str(SOURCE_WORKERS))
            or ("12" if self.fast_mode else SOURCE_WORKERS)
        )
        self.source_workers = max(1, min(configured_source_workers, 16))
        configured_verrou_workers = int(os.getenv("ENNOSCHOLAR_VERROU_WORKERS", "2") or 2)
        self.verrou_workers = max(1, min(configured_verrou_workers, 4))
        self.memory_v2_top_k = max(0, min(MEMORY_V2_TOP_K, 80))

        api_timeout = int(os.getenv("ENNOSCHOLAR_API_TIMEOUT", "8" if self.fast_mode else "20"))
        api_sleep = float(os.getenv("ENNOSCHOLAR_API_SLEEP", "0.05"))
        api_retries = int(os.getenv("ENNOSCHOLAR_MAX_RETRIES", "1" if self.fast_mode else "3"))

        self.semantic_client = SemanticScholarClient(
            timeout=api_timeout,
            sleep_seconds=api_sleep,
            max_retries=api_retries,
        )
        self.openalex_client = OpenAlexClient(
            timeout=api_timeout,
            sleep_seconds=api_sleep,
            max_retries=api_retries,
        )
        self.arxiv_client = ArxivClient(
            timeout=api_timeout,
            sleep_seconds=api_sleep,
            max_retries=api_retries,
        )
        self.crossref_client = CrossrefClient(timeout=api_timeout, max_retries=api_retries)
        self.doaj_client = DoajClient(timeout=api_timeout, max_retries=api_retries)
        self.hal_client = HalClient(timeout=api_timeout, max_retries=api_retries)
        self.core_client = CoreClient(timeout=max(api_timeout, 10), max_retries=api_retries)
        self.zenodo_client = ZenodoClient(timeout=max(api_timeout, 10), max_retries=api_retries)
        self.europe_pmc_client = EuropePmcClient(timeout=api_timeout, max_retries=api_retries)
        self.ieee_client = IeeeClient(timeout=max(api_timeout, 10), max_retries=api_retries)
        self.github_client = GitHubClient(timeout=api_timeout, max_retries=api_retries)
        self.huggingface_client = HuggingFaceClient(timeout=api_timeout, max_retries=api_retries)

        self.current_payload_meta: Dict[str, Any] = {}
        self._source_locks: Dict[str, threading.Lock] = {}
        self._source_locks_guard = threading.Lock()

    def _source_lock(self, source_name: str) -> threading.Lock:
        """Évite les rafales simultanées vers une même API et donc les 429."""
        with self._source_locks_guard:
            lock = self._source_locks.get(source_name)
            if lock is None:
                lock = threading.Lock()
                self._source_locks[source_name] = lock
            return lock

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

        # V137 : les requêtes doivent être sélectionnées à partir du verrou,
        # mais aussi du contexte EnnoDiagnostic (synthèse, objectif, démarche,
        # résultats, points de validation). On garde ce contexte dans l'intent
        # pour que query_builder puisse scorer les queries sans dépendre d'un
        # domaine codé en dur.
        if isinstance(diagnostic_context, dict):
            intent["diagnostic_context"] = diagnostic_context
            if diagnostic_context.get("diagnostic_context_text"):
                intent["diagnostic_context_text"] = diagnostic_context.get("diagnostic_context_text")

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

        all_queries = intent.get("search_queries") or []

        # V137 : ne plus prendre les N premières queries.
        # Les premières peuvent être des requêtes de domaine trop larges.
        # On sélectionne les meilleures par proximité avec le verrou + contexte
        # EnnoDiagnostic + mots-clés/méthodes/contraintes.
        queries = select_best_queries_for_intent(
            all_queries,
            intent,
            max_queries=self.max_queries_per_verrou,
        )

        all_papers: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        memory_v2_report: Dict[str, Any] = {
            "enabled": bool(match_memory_v2_articles and self.memory_v2_top_k > 0),
            "candidates_count": 0,
            "articles": [],
        }

        profile = _scholar_profile(intent)
        technical_sources = get_technical_sources_for_intent(intent, max_sources=5)

        # V136 : mémoire V2 prioritaire.
        # On injecte les articles déjà utilisés dans des dossiers similaires avant
        # les appels externes. Ces articles sont ensuite rerankés comme les autres.
        if match_memory_v2_articles is not None and self.memory_v2_top_k > 0:
            try:
                memory_v2_report = match_memory_v2_articles(
                    intent,
                    organisme=str(self.current_payload_meta.get("organisme") or ""),
                    project=str(self.current_payload_meta.get("project") or ""),
                    year=str(self.current_payload_meta.get("year") or ""),
                    top_k=self.memory_v2_top_k,
                )
                memory_articles = [
                    a for a in (memory_v2_report.get("articles") or [])
                    if isinstance(a, dict) and a.get("title")
                ]
                all_papers.extend(memory_articles)
            except Exception as exc:
                memory_v2_report = {
                    "enabled": True,
                    "error": str(exc),
                    "candidates_count": 0,
                    "articles": [],
                }
        # V147 — routeur multidomaine : publications et artefacts techniques sont séparés.
        source_plan = build_source_plan(intent)
        scientific_sources = list(source_plan.get("scientific_sources") or [])
        fallback_scientific_sources = list(source_plan.get("fallback_scientific_sources") or [])
        artifact_sources = list(source_plan.get("artifact_sources") or [])

        # Respecte les options historiques du constructeur.
        if not self.use_semantic_scholar:
            scientific_sources = [x for x in scientific_sources if x != "semantic_scholar"]
            fallback_scientific_sources = [x for x in fallback_scientific_sources if x != "semantic_scholar"]
        if not self.use_openalex:
            scientific_sources = [x for x in scientific_sources if x != "openalex"]
            fallback_scientific_sources = [x for x in fallback_scientific_sources if x != "openalex"]
        if not self.use_arxiv:
            scientific_sources = [x for x in scientific_sources if x != "arxiv"]
            fallback_scientific_sources = [x for x in fallback_scientific_sources if x != "arxiv"]

        source_status = {
            name: {"enabled": True, "success": 0, "errors": 0, "api_limited": 0, "skipped": 0}
            for name in scientific_sources + fallback_scientific_sources + artifact_sources
        }
        technical_artifacts: List[Dict[str, Any]] = []

        def _record_results(source_name: str, res: List[Dict[str, Any]], *, artifacts: bool = False) -> None:
            source_status.setdefault(source_name, {"enabled": True, "success": 0, "errors": 0, "api_limited": 0, "skipped": 0})
            for p in res:
                if not isinstance(p, dict):
                    continue
                if p.get("normalized_error"):
                    errors.append(p)
                    if p.get("skipped"):
                        source_status[source_name]["skipped"] += 1
                    else:
                        source_status[source_name]["errors"] += 1
                    if p.get("api_limited") or p.get("http_status") == 429 or "429" in str(p.get("error") or ""):
                        source_status[source_name]["api_limited"] += 1
                elif artifacts:
                    technical_artifacts.append(p)
                    source_status[source_name]["success"] += 1
                else:
                    all_papers.append(p)
                    source_status[source_name]["success"] += 1

        source_functions = {
            "semantic_scholar": self.semantic_client.search_papers,
            "openalex": self.openalex_client.search_works,
            "arxiv": self.arxiv_client.search_papers,
            "crossref": self.crossref_client.search_works,
            "doaj": self.doaj_client.search_articles,
            "hal": self.hal_client.search_works,
            "core": self.core_client.search_works,
            "zenodo": self.zenodo_client.search_records,
            "europe_pmc": self.europe_pmc_client.search_papers,
            "ieee": self.ieee_client.search_papers,
        }
        artifact_functions = {
            "github": self.github_client.search_repositories,
            "huggingface": self.huggingface_client.search_artifacts,
        }

        external_calls_planned = 0
        fallback_calls_planned = 0
        fallback_triggered = False
        unique_candidates_before_fallback = 0
        artifact_calls_planned = 0
        external_elapsed_seconds = 0.0

        if not self.offline_dry_run:
            jobs: List[Tuple[str, str, Any, bool, int]] = []
            for q in queries:
                query = q.get("query") if isinstance(q, dict) else str(q)
                query = clean_text(query, 220)
                if not query:
                    continue
                for source_name in scientific_sources:
                    func = source_functions.get(source_name)
                    if func is not None:
                        jobs.append((source_name, query, func, False, self.limit_per_query))

            # Les artefacts sont recherchés avec une seule requête, pour ne pas surcharger les APIs.
            artifact_query = clean_text((queries[0].get("query") if queries and isinstance(queries[0], dict) else (queries[0] if queries else "")), 220)
            if artifact_query:
                for source_name in artifact_sources:
                    func = artifact_functions.get(source_name)
                    if func is not None:
                        jobs.append((source_name, artifact_query, func, True, int(os.getenv("ENNOSCHOLAR_ARTIFACT_LIMIT", "10") or 10)))

            external_calls_planned = sum(1 for x in jobs if not x[3])
            artifact_calls_planned = sum(1 for x in jobs if x[3])
            started = time.perf_counter()

            def _execute_jobs(batch: List[Tuple[str, str, Any, bool, int]]) -> None:
                if not batch:
                    return
                with ThreadPoolExecutor(max_workers=min(self.source_workers, len(batch))) as executor:
                    def _run_source_job(source_name: str, query: str, func: Any, limit: int) -> Any:
                        with self._source_lock(source_name):
                            return func(query, limit)

                    future_map = {
                        executor.submit(_run_source_job, source_name, query, func, limit): (source_name, query, is_artifact)
                        for source_name, query, func, is_artifact, limit in batch
                    }
                    for future in as_completed(future_map):
                        source_name, query, is_artifact = future_map[future]
                        try:
                            res = future.result()
                            if not isinstance(res, list):
                                res = [{"source": source_name, "query": query, "error": "Client returned non-list result", "normalized_error": True}]
                        except Exception as exc:
                            res = [{"source": source_name, "query": query, "error": str(exc), "normalized_error": True}]
                        _record_results(source_name, res, artifacts=is_artifact)

            _execute_jobs(jobs)

            # Deuxième niveau à la demande : HAL, Zenodo et CORE ne sont appelés
            # que si les sources rapides n'ont pas fourni assez de candidats
            # uniques pour remplir le Top N demandé.
            unique_candidates_before_fallback = len(dedupe_papers(all_papers))
            fallback_target = max(
                1,
                min(
                    self.max_articles_per_verrou,
                    int(os.getenv("ENNOSCHOLAR_FAST_FALLBACK_MIN_CANDIDATES", str(self.max_articles_per_verrou))
                        or self.max_articles_per_verrou),
                ),
            )
            if (
                fallback_scientific_sources
                and unique_candidates_before_fallback < fallback_target
                and queries
            ):
                fallback_triggered = True
                fallback_query = clean_text(
                    queries[0].get("query") if isinstance(queries[0], dict) else queries[0],
                    220,
                )
                fallback_jobs: List[Tuple[str, str, Any, bool, int]] = []
                for source_name in fallback_scientific_sources:
                    func = source_functions.get(source_name)
                    if func is not None and fallback_query:
                        fallback_jobs.append(
                            (source_name, fallback_query, func, False, self.limit_per_query)
                        )
                fallback_calls_planned = len(fallback_jobs)
                external_calls_planned += fallback_calls_planned
                _execute_jobs(fallback_jobs)

            external_elapsed_seconds = round(time.perf_counter() - started, 3)

        # Filtre OA optionnel avant ranking. Il reste désactivé dans le flux
        # canonique : un article payant Direct doit rester sélectionnable afin
        # que le pipeline direct puis le MCP légal puissent retrouver sa copie.
        all_papers_before_free_filter = len(all_papers)
        all_papers, free_fulltext_filter_report = _filter_free_fulltext_articles(all_papers)
        all_papers, project_year_filter_report = _filter_articles_after_project_year(
            all_papers,
            self.current_payload_meta.get("year"),
        )

        all_scientific_sources = list(dict.fromkeys(scientific_sources + fallback_scientific_sources))
        enabled_sources = [k for k in all_scientific_sources if source_status.get(k, {}).get("enabled")]
        successful_sources = [k for k in enabled_sources if source_status.get(k, {}).get("success", 0) > 0]
        limited_sources = [k for k in enabled_sources if source_status.get(k, {}).get("api_limited", 0) > 0]
        enabled_artifact_sources = [k for k in artifact_sources if source_status.get(k, {}).get("enabled")]
        successful_artifact_sources = [k for k in enabled_artifact_sources if source_status.get(k, {}).get("success", 0) > 0]
        search_status = {
            "queries_count": len(queries),
            "queries_generated_count": len(all_queries),
            "query_selection_version": intent.get("query_builder_version") or "unknown",
            "max_queries_per_verrou": self.max_queries_per_verrou,
            "limit_per_query": self.limit_per_query,
            "max_articles_per_verrou": self.max_articles_per_verrou,
            "raw_papers_retrieved_before_free_filter": all_papers_before_free_filter,
            "raw_papers_retrieved": len(all_papers),
            "free_fulltext_filter": free_fulltext_filter_report,
            "project_year_filter": project_year_filter_report,
            "memory_v2_candidates": int(memory_v2_report.get("candidates_count") or 0),
            "external_calls_planned": external_calls_planned,
            "fallback_calls_planned": fallback_calls_planned,
            "fallback_triggered": fallback_triggered,
            "unique_candidates_before_fallback": unique_candidates_before_fallback,
            "artifact_calls_planned": artifact_calls_planned,
            "source_plan": source_plan,
            "technical_artifacts_count": len(technical_artifacts),
            "external_elapsed_seconds": external_elapsed_seconds,
            "enabled_sources": enabled_sources,
            "successful_sources": successful_sources,
            "limited_sources": limited_sources,
            "enabled_artifact_sources": enabled_artifact_sources,
            "successful_artifact_sources": successful_artifact_sources,
            "api_limited": bool(limited_sources),
            "all_sources_failed": bool(enabled_sources and not successful_sources and errors),
            "source_status": source_status,
        }

        # 1) Ranker déterministe : tags Direct / Connexe / Fondamental + score explicable.
        deterministic_ranked = rank_papers_for_intent(
            all_papers,
            intent,
            top_n=self.max_articles_per_verrou,
        )

        # 2) Reranker local BGE : réordonne les meilleurs articles selon
        #    verrou + contexte diagnostic VS titre + abstract.
        reranker_report = {
            "enabled": False,
            "used": False,
            "error": "paper_reranker_model indisponible",
        }
        ranked = deterministic_ranked
        if rerank_papers_with_bge is not None:
            ranked, reranker_report = rerank_papers_with_bge(
                deterministic_ranked,
                intent,
                top_n=self.max_articles_per_verrou,
            )

        # V146 : Memory V2 n'est conservée que si le ranker ET le BGE ont validé
        # un concept coeur et un rôle méthodologique/phénoménologique.
        ranked, memory_post_report = _filter_memory_v2_after_rerank(ranked, memory_v2_report)
        memory_v2_report.update(memory_post_report)
        memory_v2_report["accepted_count"] = int(memory_post_report.get("post_rerank_accepted_count") or 0)

        ranked, relevance_output_report = _select_relevant_articles_for_output(
            ranked,
            self.max_articles_per_verrou,
        )

        precision_counts = {
            "Direct": sum(1 for a in ranked if a.get("tag") == "Direct"),
            "Connexe": sum(1 for a in ranked if a.get("tag") == "Connexe"),
            "Fondamental": sum(1 for a in ranked if a.get("tag") == "Fondamental"),
            "Hors sujet": sum(1 for a in ranked if a.get("tag") == "Hors sujet"),
        }

        # 3) Résumé court des Top N articles pour aider le consultant à sélectionner.
        #    Le module utilise un cache et fallback sans LLM si Gemini/OpenRouter est indisponible.
        summary_report = {
            "enabled": False,
            "summarized_count": 0,
            "error": "article_summarizer indisponible",
        }
        summary_enabled = _env_bool_value("ENNOSCHOLAR_SUMMARIZE_DURING_SEARCH", False) and SUMMARY_TOP_N > 0
        if summarize_candidate_articles is not None and summary_enabled:
            ranked, summary_report = summarize_candidate_articles(
                ranked,
                intent,
                top_n=max(0, min(SUMMARY_TOP_N, len(ranked))),
            )
        else:
            summary_report = {
                "enabled": False,
                "summarized_count": 0,
                "mode": "on_demand_after_consultant_selection",
                "reason": "ENNOSCHOLAR_SUMMARIZE_DURING_SEARCH=false or top_n=0",
            }

        search_status["reranker"] = reranker_report
        search_status["relevance_output_filter"] = relevance_output_report
        search_status["precision_tag_counts"] = precision_counts
        search_status["article_summaries"] = summary_report

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
            "queries_generated": all_queries,
            "articles_found": len(ranked),
            "raw_articles_retrieved": len(all_papers),
            "memory_v2": {k: v for k, v in memory_v2_report.items() if k != "articles"},
            "memory_v2_articles_retrieved": int(memory_v2_report.get("accepted_count") or 0),
            "technical_sources_added": len(technical_sources),
            "articles_limit": self.max_articles_per_verrou,
            "reranking": reranker_report,
            "article_summary_report": summary_report,
            "articles": ranked,
            "technical_sources": technical_sources,
            "technical_artifacts": technical_artifacts,
            "source_plan": source_plan,
            "search_status": search_status,
            "errors": errors[:40],
            "offline_dry_run": bool(self.offline_dry_run),
            **validation,
        }

        # Preview simple sans LLM
        result["state_of_art_preview"] = build_state_of_art_section(result)

        return result

    def run_search(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = payload or {}
        domain_detection = payload.get("domain_detection") or {}
        diagnostic_context = payload.get("diagnostic_context") or {}

        self.current_payload_meta = {
            "organisme": payload.get("organisme") or payload.get("organization") or "",
            "project": payload.get("project") or payload.get("projet") or "",
            "year": payload.get("year") or payload.get("annee") or payload.get("année") or "",
        }

        # V141 — cache global de run complet.
        # À la différence du cache par requête des clients, ce cache évite tout recalcul
        # si le même projet/verrous/contexte/configuration est relancé.
        run_cache_enabled = _env_bool_value("ENNOSCHOLAR_RUN_CACHE_ENABLED", True)
        force_refresh = bool(
            _env_bool_value("ENNOSCHOLAR_FORCE_REFRESH", False)
            or payload.get("force_refresh")
            or payload.get("refresh")
            or payload.get("ignore_cache")
        )
        run_cache_ttl_days = int(os.getenv("ENNOSCHOLAR_RUN_CACHE_TTL_DAYS", "30") or "30")
        run_cache_key = _run_cache_key(payload, self)
        run_cache_path = _run_cache_path(run_cache_key)

        if run_cache_enabled and not force_refresh:
            cached_report = _read_run_cache(run_cache_path, ttl_days=run_cache_ttl_days)
            if cached_report is not None:
                cached_report = dict(cached_report)
                cached_report["cache"] = {
                    "enabled": True,
                    "used": True,
                    "level": "run",
                    "key": run_cache_key,
                    "path": str(run_cache_path),
                    "ttl_days": run_cache_ttl_days,
                    "message": "Rapport EnnoScholar repris depuis le cache global : aucune nouvelle recherche API ni reranking BGE.",
                }
                cached_report["generated_at_from_cache"] = cached_report.get("generated_at")
                cached_report["served_at"] = _now_iso()
                return cached_report

        verrou_items = [
            verrou for verrou in (payload.get("verrous") or [])
            if isinstance(verrou, dict)
        ]
        results: List[Dict[str, Any]] = []
        search_started = time.perf_counter()

        # Les verrous sont indépendants. En mode rapide, deux verrous peuvent
        # chercher en parallèle ; les appels vers une même API restent régulés
        # par _source_lock pour éviter les limitations 429.
        effective_verrou_workers = min(self.verrou_workers, len(verrou_items))
        if effective_verrou_workers <= 1:
            results = [
                self.search_for_verrou(verrou, domain_detection, diagnostic_context)
                for verrou in verrou_items
            ]
        else:
            ordered_results: List[Dict[str, Any] | None] = [None] * len(verrou_items)
            with ThreadPoolExecutor(max_workers=effective_verrou_workers) as executor:
                future_map = {
                    executor.submit(
                        self.search_for_verrou,
                        verrou,
                        domain_detection,
                        diagnostic_context,
                    ): index
                    for index, verrou in enumerate(verrou_items)
                }
                for future in as_completed(future_map):
                    ordered_results[future_map[future]] = future.result()
            results = [item for item in ordered_results if isinstance(item, dict)]

        search_elapsed_seconds = round(time.perf_counter() - search_started, 3)

        multi_verrou_coverage_report = _annotate_multi_verrou_coverage(results)

        decision_counts = {}

        for r in results:
            d = r.get("decision")
            decision_counts[d] = decision_counts.get(d, 0) + 1

        report = {
            "agent": "EnnoScholar",
            "version": "v150_problem_evidence_year_cutoff",
            "mode": "search",
            "generated_at": _now_iso(),
            "project": payload.get("project"),
            "year": payload.get("year"),
            "organisme": payload.get("organisme"),
            "domain_detection": domain_detection,
            "diagnostic_context": diagnostic_context,
            "diagnostic_context_used": bool(diagnostic_context),
            "verrous_analyzed": len(results),
            "search_elapsed_seconds": search_elapsed_seconds,
            "verrou_workers": effective_verrou_workers,
            "decision_counts": decision_counts,
            "multi_verrou_coverage": multi_verrou_coverage_report,
            "cache": {
                "enabled": bool(run_cache_enabled),
                "used": False,
                "level": "run",
                "key": run_cache_key,
                "path": str(run_cache_path),
                "ttl_days": run_cache_ttl_days,
                "force_refresh": bool(force_refresh),
            },
            "results": results,
        }

        if run_cache_enabled and not force_refresh:
            _write_run_cache(run_cache_path, run_cache_key, report)
            report["cache"]["written"] = True

        return report

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
        del project_context, writer_mode, llm_model, llm_temperature
        return {
            "ok": False,
            "status": "legacy_per_verrou_writer_disabled",
            "message": (
                "La rédaction par verrou est désactivée. Utiliser la Phase 4.7 "
                "puis run_phase_5_state_of_art_writer pour le document global."
            ),
            "verrou_id": verrou_item.get("verrou_id"),
            "verrou_title": (
                verrou_item.get("verrou_title")
                or verrou_item.get("title")
                or verrou_item.get("scientific_intent", {}).get("verrou_title")
            ),
        }

    def run_writer_from_selection(
        self,
        selection_payload: Dict[str, Any],
        writer_mode: str = "auto",
        llm_model: str = DEFAULT_LLM_MODEL,
        llm_temperature: float = DEFAULT_LLM_TEMPERATURE,
    ) -> Dict[str, Any]:
        del writer_mode, llm_temperature
        return {
            "ok": False,
            "status": "legacy_per_verrou_writer_disabled",
            "message": (
                "Le mode write-selection historique ne produit plus de texte. "
                "La rédaction canonique exige les Phases 4.7 et 5, les Article Cards "
                "et, en mode chat, le plan consultant autorisé."
            ),
            "agent": "EnnoScholar",
            "version": "2.0.0",
            "mode": "write-selection",
            "generated_at": _now_iso(),
            "organisme": selection_payload.get("organisme"),
            "project": selection_payload.get("project"),
            "year": selection_payload.get("year"),
            "llm_model": llm_model,
            "verrous_written": 0,
            "results": [],
            "canonical_writer": "state_of_art.phase_5_state_of_art_writer_service.run_phase_5_state_of_art_writer",
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
    confirmed_verrous_path: str | Path | None = None,
) -> Dict[str, Any]:
    payload = build_payload_from_nlp(
        nlp_result_path=nlp_result_path,
        organisme=organisme,
        project=project,
        year=year,
        diagnostic_report_path=diagnostic_report_path,
        max_verrous=max_verrous,
        confirmed_verrous_path=confirmed_verrous_path,
    )

    if out_dir is None:
        out_dir = Path(nlp_result_path).parent / "ennoscholar"

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        out_dir / "confirmed_verrous_for_scholar.json",
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
        "payload": str(out_dir / "confirmed_verrous_for_scholar.json"),
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
