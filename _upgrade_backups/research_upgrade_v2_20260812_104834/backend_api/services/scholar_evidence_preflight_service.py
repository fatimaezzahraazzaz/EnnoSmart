# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from db.database import SessionLocal
from db.models import Article, Project, ScholarRun
from services.scholar_direct_fulltext_service import resolve_and_extract_fulltext_for_article


def _bool_env(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _abstract(article: Article) -> str:
    src = article.source_json if isinstance(article.source_json, dict) else {}
    return str(src.get("abstract") or src.get("tldr") or "").strip()


def _classify(article: Article, result: Dict[str, Any]) -> Dict[str, Any]:
    abstract_available = bool(_abstract(article))
    if result.get("full_text_status") == "text_extracted" and result.get("ok"):
        level, ui, label, usable = "FULLTEXT_READY", "fulltext_ready", "Texte intégral extrait", True
    elif abstract_available:
        level, ui, label, usable = (
            "ABSTRACT_READY", "abstract_only",
            "Abstract disponible — texte intégral non trouvé automatiquement", True
        )
    else:
        level, ui, label, usable = (
            "METADATA_ONLY", "restricted_or_unavailable",
            "Texte intégral non trouvé automatiquement — accès potentiellement restreint", False
        )

    return {
        "evidence_status": level,
        "evidence_ui_status": ui,
        "evidence_label": label,
        "evidence_usable": usable,
        "fulltext_ready": level == "FULLTEXT_READY",
        "abstract_ready": level in {"FULLTEXT_READY", "ABSTRACT_READY"},
        "access_check_status": result.get("status"),
        "content_source_kind": result.get("content_source_kind"),
        "extraction_method": result.get("extraction_method"),
        "needs_legal_recovery": bool(result.get("needs_legal_recovery")),
        "likely_restricted_access": result.get("status") in {
            "paywall_blocked", "remote_access_blocked", "direct_known_urls_exhausted"
        },
    }


def _process_one(project_id: int, article_id: int) -> Dict[str, Any]:
    # Important: SQLAlchemy Session is not shared between threads.
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        article = db.get(Article, article_id)
        if project is None or article is None:
            return {"article_id": article_id, "ok": False, "status": "not_found"}

        result = resolve_and_extract_fulltext_for_article(
            db=db, project=project, article_id=article_id,
            refresh_resolution=False, force_reextract=False,
        )

        # The legal MCP is optional during preflight because it can be much
        # slower. Existing post-selection legal recovery remains unchanged.
        if (
            result.get("full_text_status") != "text_extracted"
            and result.get("needs_legal_recovery") is True
            and _bool_env("ENNOSCHOLAR_PREFLIGHT_USE_LEGAL_MCP", False)
        ):
            try:
                from services.scholar_legal_recovery_service import recover_legal_fulltext_for_article
                legal = recover_legal_fulltext_for_article(
                    db, project, article_id, force_refresh=False, search_all=False
                )
                if legal.get("full_text_status") == "text_extracted":
                    result = legal
                else:
                    result["legal_preflight"] = {
                        "status": legal.get("status"),
                        "mcp_called": legal.get("mcp_called"),
                    }
            except Exception as exc:
                result["legal_preflight"] = {"error": f"{type(exc).__name__}: {exc}"}

        classification = _classify(article, result)
        src = dict(article.source_json or {})
        src["evidence_preflight"] = classification
        src["fulltext_preflight_result"] = {
            "ok": bool(result.get("ok")),
            "status": result.get("status"),
            "full_text_status": result.get("full_text_status"),
            "content_source_kind": result.get("content_source_kind"),
            "extraction_method": result.get("extraction_method"),
            "needs_legal_recovery": result.get("needs_legal_recovery"),
        }
        article.source_json = src
        db.add(article)
        db.commit()

        return {"article_id": article_id, "title": article.title, "ok": True, **classification}
    finally:
        db.close()


def preflight_scholar_run(
    db: Session,
    project: Project,
    run: ScholarRun,
    *,
    max_articles: int | None = None,
) -> Dict[str, Any]:
    """Extract/check best results before the consultant sees/selects them."""
    if not _bool_env("ENNOSCHOLAR_PREFLIGHT_ENABLED", True):
        return {"enabled": False, "processed": 0, "results": []}

    limit = max_articles or int(os.getenv("ENNOSCHOLAR_PREFLIGHT_TOP_K", "15"))
    workers = max(1, min(int(os.getenv("ENNOSCHOLAR_PREFLIGHT_WORKERS", "4")), 8))
    rows = (
        db.query(Article)
        .filter(Article.scholar_run_id == run.id)
        .order_by(Article.score.desc().nullslast(), Article.id.asc())
        .limit(max(1, limit))
        .all()
    )
    ids = [int(a.id) for a in rows]

    results: List[Dict[str, Any]] = []
    if not ids:
        return {"enabled": True, "processed": 0, "counts": {}, "results": []}

    with ThreadPoolExecutor(max_workers=min(workers, len(ids))) as executor:
        futures = {executor.submit(_process_one, int(project.id), aid): aid for aid in ids}
        for future in as_completed(futures):
            aid = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({
                    "article_id": aid,
                    "ok": False,
                    "evidence_status": "EXTRACTION_FAILED",
                    "evidence_ui_status": "extraction_failed",
                    "evidence_label": "Vérification automatique échouée",
                    "error": f"{type(exc).__name__}: {exc}",
                })

    counts: Dict[str, int] = {}
    for row in results:
        key = str(row.get("evidence_status") or "ERROR")
        counts[key] = counts.get(key, 0) + 1

    return {
        "enabled": True,
        "processed": len(results),
        "requested_top_k": limit,
        "workers": workers,
        "counts": counts,
        "results": results,
    }
