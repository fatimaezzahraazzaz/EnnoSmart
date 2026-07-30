# -*- coding: utf-8 -*-
from __future__ import annotations

"""
article_summarizer.py — EnnoScholar V140 no Gemini

Objectif : ne plus appeler Gemini/OpenRouter pour les résumés articles.
On conserve l'abstract complet fourni par ArXiv / Semantic Scholar / OpenAlex / Mémoire V2.
L'interface peut afficher :
- article.source_json.abstract ou source_json.article_summary.abstract_original ;
- source_json.abstract_fr si une traduction FR est ajoutée plus tard.
"""

import json
import os
import re
import time
import urllib.request
from typing import Any, Dict, List, Tuple



def _env_bool(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name, "") or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y", "on", "oui"}


def _translate_fr_with_ollama(text: str) -> str:
    """Traduction optionnelle locale, sans Gemini. Retourne "" si indisponible."""
    if not text or not _env_bool("ENNOSCHOLAR_TRANSLATE_ABSTRACT_FR", False):
        return ""
    if _looks_french(text):
        return text

    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("ENNOSCHOLAR_TRANSLATE_MODEL") or os.getenv("OLLAMA_MODEL") or "qwen2.5:3b-instruct"
    timeout = int(os.getenv("ENNOSCHOLAR_TRANSLATE_TIMEOUT", "60") or 60)

    prompt = (
        "Traduis en français le résumé scientifique suivant. "
        "Garde les sigles techniques, ne résume pas, ne rajoute aucune information.\n\n"
        + text[:12000]
    )
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    try:
        req = urllib.request.Request(
            base_url + "/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return _clean(data.get("response"), 20000)
    except Exception:
        return ""


def _clean(text: Any, max_chars: int = 20000) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:max_chars].strip()


def _looks_french(text: str) -> bool:
    n = text.lower()
    fr_hits = sum(1 for w in [" le ", " la ", " les ", " des ", " une ", " cette ", " dans ", " données", " apprentissage", " méthode", " résultats"] if w in f" {n} ")
    accents = any(c in text for c in "éèêàùçîïô")
    return accents or fr_hits >= 3


def _article_abstract(article: Dict[str, Any]) -> str:
    tldr = article.get("tldr")
    if isinstance(tldr, dict):
        tldr = tldr.get("text")
    return _clean(
        article.get("abstract")
        or article.get("summary")
        or tldr
        or "",
        20000,
    )


def _paper_title(article: Dict[str, Any]) -> str:
    return _clean(article.get("title"), 360)


def _passthrough_summary(article: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    abstract = _article_abstract(article)
    title = _paper_title(article)
    reason = _clean(article.get("reason"), 1000)
    tag = article.get("tag") or article.get("tag_article") or "Fondamental"
    is_fr = _looks_french(abstract)

    if not abstract:
        abstract = "Résumé non disponible dans les métadonnées récupérées. Le consultant doit consulter la source originale."

    abstract_fr = abstract if is_fr else _translate_fr_with_ollama(abstract)

    return {
        "resume_court": abstract_fr or abstract,
        "abstract_original": abstract,
        "abstract_fr": abstract_fr,
        "abstract_language": "fr" if abstract_fr else "unknown_or_en",
        "apport_scientifique": f"Résumé brut fourni par la source pour l'article « {title} ». Aucun résumé LLM n'a été généré.",
        "lien_avec_verrou": reason or "Lien proposé par le classement EnnoScholar ; à vérifier par le consultant.",
        "limite_pour_le_projet": "La transposition au projet doit être vérifiée par le consultant à partir de l'article complet.",
        "tag_recommande": tag,
        "confidence": 0.5,
        "summary_mode": "abstract_passthrough_no_llm",
    }


def summarize_candidate_articles(
    articles: List[Dict[str, Any]],
    intent: Dict[str, Any],
    top_n: int | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    API compatible V139, mais sans aucun appel externe.
    """
    started = time.perf_counter()
    items = [dict(a) for a in (articles or []) if isinstance(a, dict)]
    limit = int(top_n if top_n is not None else os.getenv("ENNOSCHOLAR_SUMMARY_MAX_ARTICLES_PER_VERROU", "10") or 10)
    limit = max(0, min(limit, len(items)))

    for idx in range(limit):
        items[idx]["article_summary"] = _passthrough_summary(items[idx], intent or {})
        items[idx]["article_summary_cache_hit"] = False

    report = {
        "enabled": True,
        "provider": "none",
        "model": "abstract_passthrough_no_gemini",
        "translation_provider": "ollama_optional" if _env_bool("ENNOSCHOLAR_TRANSLATE_ABSTRACT_FR", False) else "none",
        "top_n": limit,
        "input_count": len(items),
        "summarized_count": limit,
        "llm_used_count": 0,
        "cache_hits": 0,
        "fallback_count": 0,
        "errors": [],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }

    return items, report
