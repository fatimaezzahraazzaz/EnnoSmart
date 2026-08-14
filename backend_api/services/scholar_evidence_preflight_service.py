# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import logging
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List

from sqlalchemy.orm import Session
from db.database import SessionLocal
from db.models import Article, Project, ScholarRun
from services.scholar_direct_fulltext_service import resolve_and_extract_fulltext_for_article

TARGETED_LEGAL_CONCURRENCY = max(
    1,
    min(int(os.getenv("ENNOSCHOLAR_PREFLIGHT_LEGAL_WORKERS", "6")), 10),
)
BROAD_LEGAL_CONCURRENCY = max(
    1,
    min(int(os.getenv("ENNOSCHOLAR_PREFLIGHT_BROAD_LEGAL_WORKERS", "6")), 8),
)
# Alias conservé pour compatibilité avec les fonctions legacy du fichier.
LEGAL_CONCURRENCY = TARGETED_LEGAL_CONCURRENCY
_TARGETED_LEGAL_SEMAPHORE = threading.BoundedSemaphore(TARGETED_LEGAL_CONCURRENCY)
_BROAD_LEGAL_SEMAPHORE = threading.BoundedSemaphore(BROAD_LEGAL_CONCURRENCY)
LOGGER = logging.getLogger("ennoscholar.preflight")

def _bool_env(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _safe_db_worker_count(requested: int) -> int:
    """Reserve les connexions necessaires a la session pilote et aux erreurs."""
    try:
        from db.database import engine

        pool_size = int(engine.pool.size())
        max_overflow = max(0, int(getattr(engine.pool, "_max_overflow", 0)))
        available = max(1, pool_size + max_overflow - 2)
        return max(1, min(int(requested), available))
    except Exception:
        return max(1, int(requested))

def _abstract(article: Article) -> str:
    src = article.source_json if isinstance(article.source_json, dict) else {}
    tldr = src.get("tldr")
    if isinstance(tldr, dict):
        tldr = tldr.get("text")
    return str(
        src.get("abstract")
        or src.get("abstract_original")
        or src.get("summary")
        or tldr
        or ""
    ).strip()


def _article_identity_keys(article: Article) -> set[str]:
    source = article.source_json if isinstance(article.source_json, dict) else {}
    keys: set[str] = set()
    title = re.sub(
        r"[\W_]+",
        " ",
        str(article.title or source.get("title") or "").casefold(),
        flags=re.UNICODE,
    ).strip()
    if title:
        keys.add(f"title:{title}")
    doi = str(article.doi or source.get("doi") or "").strip().lower()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi).strip()
    if doi:
        keys.add(f"doi:{doi}")
    paper_id = str(
        source.get("paper_id")
        or source.get("paperId")
        or source.get("openalex_id")
        or ""
    ).strip().lower()
    if paper_id:
        keys.add(f"paper:{paper_id}")
    return keys


def _dedupe_article_rows(rows: Iterable[Article]) -> List[Article]:
    """Evite de payer deux fois extraction/MCP pour le meme article."""
    unique: List[Article] = []
    by_identity: Dict[str, Article] = {}
    for article in rows:
        keys = _article_identity_keys(article)
        if any(key in by_identity for key in keys):
            continue
        unique.append(article)
        for key in keys:
            by_identity[key] = article
    return unique

def _result_reason(result: Dict[str, Any]) -> Dict[str, Any]:
    status = str(result.get("status") or "not_checked").strip().lower()
    message = str(result.get("message") or result.get("error") or "").strip()

    if status in {"paywall_blocked", "subscription_required", "payment_required"}:
        return {
            "reason_code": "PAYWALL_BLOCKED",
            "reason_detail": "Accès payant ou abonnement requis pour obtenir le texte intégral.",
            "recommended_action": "Fournir le PDF si une licence autorise son utilisation, ou rechercher une copie ouverte.",
            "access_kind": "paid",
        }
    if status in {"antibot_blocked", "remote_access_blocked", "publisher_interstitial"}:
        return {
            "reason_code": "AUTOMATED_ACCESS_BLOCKED",
            "reason_detail": "Le site éditeur bloque la lecture automatique (anti-robot ou page intermédiaire).",
            "recommended_action": "Ouvrir la notice manuellement ou fournir le PDF autorisé.",
            "access_kind": "blocked",
        }
    if status in {"pdf_identity_mismatch", "wrong_document_rejected"}:
        return {
            "reason_code": "DOCUMENT_IDENTITY_MISMATCH",
            "reason_detail": "Un document a été trouvé, mais il ne correspond pas assez sûrement à cet article.",
            "recommended_action": "Vérifier le DOI et téléverser le PDF exact si disponible.",
            "access_kind": "identity_mismatch",
        }
    if status in {"remote_copy_unavailable", "not_found", "http_404"}:
        return {
            "reason_code": "REMOTE_COPY_UNAVAILABLE",
            "reason_detail": "La copie distante référencée est introuvable ou n’est plus disponible.",
            "recommended_action": "Chercher une autre copie légale ou fournir le document.",
            "access_kind": "unavailable",
        }
    if status in {
        "direct_known_urls_exhausted", "no_legal_copy_found", "missing_legal_fulltext",
        "missing_or_blocked_fulltext", "no_candidate_url",
    }:
        return {
            "reason_code": "NO_LEGAL_FULLTEXT_FOUND",
            "reason_detail": "Aucune copie légale exploitable du texte intégral n’a été trouvée automatiquement.",
            "recommended_action": "Fournir le PDF ou vérifier la référence auprès d’une bibliothèque documentaire.",
            "access_kind": "unavailable",
        }
    if status in {
        "pdf_text_insufficient", "html_text_insufficient", "xml_text_insufficient",
        "unsupported_remote_content", "extraction_error", "legal_recovery_error",
    } or status.endswith("_exception"):
        return {
            "reason_code": "EXTRACTION_FAILED",
            "reason_detail": "Le document a été atteint, mais son contenu n’a pas pu être extrait de façon fiable.",
            "recommended_action": "Fournir une version PDF lisible ou relancer l’extraction/OCR.",
            "access_kind": "extraction_failed",
        }
    if status in {"not_checked", "queued", "extraction_queued", "extraction_running"}:
        return {
            "reason_code": "EXTRACTION_PENDING",
            "reason_detail": "La récupération et l’extraction automatiques sont en cours.",
            "recommended_action": "Attendre la fin de la pré-vérification automatique.",
            "access_kind": "pending",
        }
    return {
        "reason_code": "FULLTEXT_UNAVAILABLE",
        "reason_detail": message or "Le texte intégral n’est pas disponible automatiquement.",
        "recommended_action": "Vérifier la notice ou fournir une copie autorisée du document.",
        "access_kind": "unknown",
    }

def _classify(article: Article, result: Dict[str, Any]) -> Dict[str, Any]:
    abstract_available = bool(_abstract(article))
    reason = _result_reason(result)

    if result.get("full_text_status") == "text_extracted" and result.get("ok"):
        level = "FULLTEXT_READY"
        ui = "fulltext_ready"
        label = "Texte intégral extrait"
        usable = True
        candidate_only = False
        reason = {
            "reason_code": "FULLTEXT_EXTRACTED",
            "reason_detail": "Le texte intégral a été récupéré, vérifié et extrait.",
            "recommended_action": "L’article peut être évalué comme preuve scientifique complète.",
            "access_kind": "fulltext",
        }
    elif abstract_available:
        level = "ABSTRACT_READY"
        ui = "abstract_only"
        label = f"Abstract uniquement — {reason['reason_detail']}"
        usable = False
        candidate_only = True
    else:
        level = "METADATA_ONLY"
        ui = "restricted_or_unavailable"
        label = reason["reason_detail"]
        usable = False
        candidate_only = True

    return {
        "evidence_status": level,
        "evidence_ui_status": ui,
        "evidence_label": label,
        "evidence_usable": usable,
        "candidate_only": candidate_only,
        "fulltext_ready": level == "FULLTEXT_READY",
        "abstract_ready": abstract_available,
        "access_check_status": result.get("status"),
        "content_source_kind": result.get("content_source_kind"),
        "extraction_method": result.get("extraction_method"),
        "needs_legal_recovery": bool(result.get("needs_legal_recovery")),
        "likely_restricted_access": str(result.get("status") or "").lower() in {
            "paywall_blocked",
            "antibot_blocked",
            "remote_access_blocked",
            "publisher_interstitial",
            "direct_known_urls_exhausted",
            "no_legal_copy_found",
        },
        **reason,
    }

def _set_article_evidence(
    db: Session,
    article: Article,
    evidence: Dict[str, Any],
    result: Dict[str, Any] | None = None,
) -> None:
    src = dict(article.source_json or {})
    src["evidence_preflight"] = dict(evidence)
    if isinstance(result, dict):
        src["fulltext_preflight_result"] = {
            "ok": bool(result.get("ok")),
            "status": result.get("status"),
            "full_text_status": result.get("full_text_status"),
            "content_source_kind": result.get("content_source_kind"),
            "extraction_method": result.get("extraction_method"),
            "needs_legal_recovery": result.get("needs_legal_recovery"),
            "message": result.get("message"),
            "error": result.get("error"),
            "legal_attempted": bool(result.get("legal_attempted")),
            "legal_status": result.get("legal_status"),
        }
    article.source_json = src
    db.add(article)

def _legal_recovery(
    db: Session,
    project: Project,
    article_id: int,
    *,
    search_all: bool = False,
    expand_on_failure: bool = True,
):
    if not _bool_env("ENNOSCHOLAR_PREFLIGHT_USE_LEGAL_MCP", False):
        return None
    try:
        from services.scholar_legal_recovery_service import recover_legal_fulltext_for_article
    except Exception:
        return None

    semaphore = _BROAD_LEGAL_SEMAPHORE if search_all else _TARGETED_LEGAL_SEMAPHORE
    with semaphore:
        try:
            return recover_legal_fulltext_for_article(
                db,
                project,
                article_id,
                force_refresh=bool(search_all),
                search_all=bool(search_all),
                expand_on_failure=bool(expand_on_failure),
            )
        except Exception as exc:
            return {
                "ok": False,
                "status": "legal_recovery_error",
                "error": f"{type(exc).__name__}: {exc}",
            }

def _process_one(
    project_id: int,
    article_id: int,
    *,
    allow_legal_recovery: bool = True,
) -> Dict[str, Any]:
    db = SessionLocal()
    started = time.perf_counter()
    try:
        project = db.get(Project, int(project_id))
        article = db.get(Article, int(article_id))
        if project is None or article is None:
            return {
                "article_id": article_id,
                "ok": False,
                "evidence_status": "EXTRACTION_FAILED",
                "evidence_usable": False,
                "status": "not_found",
            }

        current_evidence = (
            dict((article.source_json or {}).get("evidence_preflight") or {})
            if isinstance(article.source_json, dict)
            else {}
        )
        current_evidence.update({
            "evidence_status": "EXTRACTION_RUNNING",
            "evidence_ui_status": "running",
            "evidence_label": "Récupération et extraction en cours",
            "evidence_usable": False,
            "candidate_only": True,
            "fulltext_ready": False,
            "reason_code": "EXTRACTION_PENDING",
            "reason_detail": "La récupération et l'extraction automatiques sont en cours.",
            "recommended_action": "Attendre la fin du traitement automatique.",
            "access_kind": "pending",
        })
        _set_article_evidence(db, article, current_evidence)
        db.commit()
        LOGGER.info(
            "[EnnoScholar extraction] article=%s démarré — %s",
            article_id,
            str(article.title or "Sans titre")[:120],
        )

        try:
            direct = resolve_and_extract_fulltext_for_article(
                db=db,
                project=project,
                article_id=int(article_id),
                refresh_resolution=False,
                force_reextract=False,
            )
        except Exception as exc:
            result = {
                "ok": False,
                "status": "direct_extraction_exception",
                "full_text_status": "missing_or_blocked_fulltext",
                "needs_legal_recovery": True,
                "error": f"{type(exc).__name__}: {exc}",
            }
        else:
            result = dict(direct or {})

        legal_result = None
        if allow_legal_recovery and result.get("full_text_status") != "text_extracted":
            legal_result = _legal_recovery(db, project, int(article_id))
            if (
                isinstance(legal_result, dict)
                and legal_result.get("full_text_status") == "text_extracted"
                and legal_result.get("ok")
            ):
                result = dict(legal_result)

        result["legal_attempted"] = isinstance(legal_result, dict)
        result["legal_status"] = (
            legal_result.get("status")
            if isinstance(legal_result, dict)
            else None
        )

        if result.get("ok") and result.get("full_text_status") == "text_extracted":
            try:
                from services.scholar_fulltext_cache_service import store_cached_fulltext
                cache_row = store_cached_fulltext(db, article, result)
                if cache_row is not None:
                    source_json = dict(article.source_json or {})
                    source_json["fulltext_cache_id"] = int(cache_row.id)
                    source_json["fulltext_cache_key"] = cache_row.cache_key
                    article.source_json = source_json
            except Exception:
                LOGGER.exception(
                    "[EnnoScholar extraction] article=%s mise en cache impossible",
                    article_id,
                )

        classification = _classify(article, result)
        _set_article_evidence(db, article, classification, result)
        db.commit()

        LOGGER.info(
            "[EnnoScholar extraction] article=%s terminé statut=%s cause=%s durée=%.1fs",
            article_id,
            classification.get("evidence_status"),
            classification.get("reason_code"),
            time.perf_counter() - started,
        )

        return {
            "article_id": int(article_id),
            "verrou_id": article.verrou_id,
            "title": article.title,
            "ok": True,
            **classification,
        }
    finally:
        db.close()


def _running_evidence(article: Article, label: str) -> Dict[str, Any]:
    current = (
        dict((article.source_json or {}).get("evidence_preflight") or {})
        if isinstance(article.source_json, dict)
        else {}
    )
    current.update({
        "evidence_status": "EXTRACTION_RUNNING",
        "evidence_ui_status": "running",
        "evidence_label": label,
        "evidence_usable": False,
        "candidate_only": True,
        "fulltext_ready": False,
        "reason_code": "EXTRACTION_PENDING",
        "reason_detail": label,
        "recommended_action": "Attendre la fin du traitement automatique.",
        "access_kind": "pending",
    })
    return current


def _queued_evidence(article: Article, result: Dict[str, Any], label: str) -> Dict[str, Any]:
    evidence = _classify(article, result)
    evidence.update({
        "evidence_status": "EXTRACTION_QUEUED",
        "evidence_ui_status": "queued",
        "evidence_label": label,
        "evidence_usable": False,
        "candidate_only": True,
        "fulltext_ready": False,
        "reason_code": "EXTRACTION_PENDING",
        "reason_detail": label,
        "recommended_action": "Attendre la passe automatique suivante.",
        "access_kind": "pending",
    })
    return evidence


def _existing_fulltext_result(article: Article) -> Dict[str, Any] | None:
    evidence = (
        (article.source_json or {}).get("evidence_preflight")
        if isinstance(article.source_json, dict)
        else None
    )
    if not isinstance(evidence, dict) or evidence.get("evidence_status") != "FULLTEXT_READY":
        return None
    return {
        "article_id": int(article.id),
        "verrou_id": article.verrou_id,
        "title": article.title,
        "ok": True,
        "terminal": True,
        "reused": True,
        **evidence,
    }


def _process_cache_stage(project_id: int, article_id: int) -> Dict[str, Any]:
    """Passe 0 : réutilise PostgreSQL avant tout appel réseau."""
    del project_id
    db = SessionLocal()
    try:
        article = db.get(Article, int(article_id))
        if article is None:
            raise ValueError("article_not_found")

        existing = _existing_fulltext_result(article)
        if existing is not None:
            return existing

        from services.scholar_fulltext_cache_service import get_cached_fulltext

        cached = get_cached_fulltext(db, article)
        if cached is not None:
            evidence = _classify(article, cached)
            source_json = dict(article.source_json or {})
            source_json["fulltext_cache_id"] = cached.get("fulltext_cache_id")
            source_json["fulltext_cache_key"] = cached.get("fulltext_cache_key")
            article.source_json = source_json
            _set_article_evidence(db, article, evidence, cached)
            db.commit()
            LOGGER.info(
                "[EnnoScholar extraction][CACHE] article=%s texte réutilisé depuis PostgreSQL",
                article_id,
            )
            return {
                "article_id": int(article.id),
                "verrou_id": article.verrou_id,
                "title": article.title,
                "ok": True,
                "terminal": True,
                "reused": True,
                **evidence,
            }

        pending_result = {
            "ok": False,
            "status": "oa_discovery_queued",
            "full_text_status": "not_checked",
        }
        evidence = _queued_evidence(
            article,
            pending_result,
            "Cache absent ; résolution Open Access groupée planifiée.",
        )
        _set_article_evidence(db, article, evidence, pending_result)
        db.commit()
        return {
            "article_id": int(article.id),
            "verrou_id": article.verrou_id,
            "title": article.title,
            "ok": True,
            "terminal": False,
            "needs_discovery": True,
            **evidence,
        }
    finally:
        db.close()


def _process_cache_stage_batch(
    db: Session,
    articles: Iterable[Article],
) -> List[Dict[str, Any]]:
    """Passe cache groupee : une lecture et une transaction pour le catalogue."""
    article_rows = list(articles)
    from services.scholar_fulltext_cache_service import get_cached_fulltexts

    cached_by_id = get_cached_fulltexts(db, article_rows)
    stage_rows: List[Dict[str, Any]] = []
    reused_count = 0
    already_ready_count = 0

    for article in article_rows:
        existing = _existing_fulltext_result(article)
        if existing is not None:
            already_ready_count += 1
            stage_rows.append(existing)
            continue

        cached = cached_by_id.get(int(article.id))
        if cached is not None:
            evidence = _classify(article, cached)
            source_json = dict(article.source_json or {})
            source_json["fulltext_cache_id"] = cached.get("fulltext_cache_id")
            source_json["fulltext_cache_key"] = cached.get("fulltext_cache_key")
            article.source_json = source_json
            _set_article_evidence(db, article, evidence, cached)
            reused_count += 1
            stage_rows.append({
                "article_id": int(article.id),
                "verrou_id": article.verrou_id,
                "title": article.title,
                "ok": True,
                "terminal": True,
                "reused": True,
                **evidence,
            })
            continue

        pending_result = {
            "ok": False,
            "status": "oa_discovery_queued",
            "full_text_status": "not_checked",
        }
        evidence = _queued_evidence(
            article,
            pending_result,
            "Cache absent ; resolution Open Access groupee planifiee.",
        )
        _set_article_evidence(db, article, evidence, pending_result)
        stage_rows.append({
            "article_id": int(article.id),
            "verrou_id": article.verrou_id,
            "title": article.title,
            "ok": True,
            "terminal": False,
            "needs_discovery": True,
            **evidence,
        })

    db.commit()
    LOGGER.info(
        "[EnnoScholar extraction][CACHE] lecture groupee terminee "
        "articles=%s deja_prets=%s reutilises=%s absents=%s",
        len(article_rows),
        already_ready_count,
        reused_count,
        len(article_rows) - already_ready_count - reused_count,
    )
    return stage_rows


def _access_failure_details(result: Dict[str, Any]) -> Dict[str, Any]:
    """Traduit le diagnostic technique en cause courte pour le consultant."""
    attempts = result.get("attempts") if isinstance(result.get("attempts"), list) else []
    candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
    diagnostic = " ".join(
        str(value or "")
        for item in [result, *attempts]
        if isinstance(item, dict)
        for value in (
            item.get("status"),
            item.get("reason"),
            item.get("message"),
            item.get("http_status"),
        )
    ).casefold()

    if "paywall" in diagnostic or "subscribe" in diagnostic or "login" in diagnostic:
        return {
            "reason_code": "PAYWALL_BLOCKED",
            "reason_detail": "Le texte integral est derriere un acces payant ou une connexion.",
            "access_kind": "paid",
        }
    if any(
        token in diagnostic
        for token in (
            "antibot", "anti-bot", "challenge", "cloudflare", "akamai",
            "bm-verify", "provider=interstitial", " 403",
        )
    ):
        public_pdf = next(
            (
                candidate
                for candidate in candidates
                if isinstance(candidate, dict)
                and candidate.get("legal_access") is True
                and str(candidate.get("kind") or "").casefold() == "pdf"
                and str(candidate.get("url") or "").startswith(("http://", "https://"))
            ),
            None,
        )
        if public_pdf:
            return {
                "reason_code": "PUBLIC_PDF_BROWSER_ONLY",
                "reason_detail": (
                    "Une copie PDF publique officielle a ete trouvee, mais le site "
                    "bloque son telechargement automatique par une protection anti-robot."
                ),
                "access_kind": "public_browser_only",
                "browser_download_url": str(public_pdf.get("url") or ""),
            }
        return {
            "reason_code": "ANTIBOT_BLOCKED",
            "reason_detail": "Le site refuse la verification automatique (protection anti-robot).",
            "access_kind": "blocked",
        }
    if "tls" in diagnostic or "certificate" in diagnostic:
        return {
            "reason_code": "REMOTE_TLS_ERROR",
            "reason_detail": "Le certificat du serveur distant ne permet pas une verification sure.",
            "access_kind": "technical_error",
        }
    if "404" in diagnostic:
        return {
            "reason_code": "REMOTE_COPY_UNAVAILABLE",
            "reason_detail": "La copie distante referencee n'existe plus (HTTP 404).",
            "access_kind": "not_found",
        }
    if not result.get("candidates"):
        return {
            "reason_code": "NO_FULLTEXT_URL",
            "reason_detail": "Aucune URL de texte integral ou de PDF n'a ete trouvee.",
            "access_kind": "not_found",
        }
    return {
        "reason_code": "NO_ACCESSIBLE_FULLTEXT",
        "reason_detail": "Aucune copie publique exploitable n'a ete trouvee automatiquement.",
        "access_kind": "not_found",
    }


def _access_evidence(article: Article, result: Dict[str, Any]) -> Dict[str, Any]:
    if result.get("ok") and result.get("full_text_status") == "text_extracted":
        return _classify(article, result)

    available = bool(
        result.get("ok")
        and result.get("full_text_status") in {
            "pdf_url_available",
            "html_fulltext_available",
            "xml_fulltext_available",
        }
    )
    if available:
        kind = str(result.get("content_source_kind") or "pdf").upper()
        return {
            "evidence_status": "ACCESS_AVAILABLE",
            "evidence_ui_status": "available_on_click",
            "evidence_label": f"Texte integral {kind} accessible",
            "evidence_usable": False,
            "candidate_only": True,
            "fulltext_ready": False,
            "abstract_ready": bool(_abstract(article)),
            "access_check_status": result.get("status"),
            "content_source_kind": str(result.get("content_source_kind") or "pdf").lower(),
            "extraction_method": None,
            "needs_legal_recovery": False,
            "likely_restricted_access": False,
            "reason_code": "FULLTEXT_ACCESSIBLE",
            "reason_detail": "Une copie publique est accessible ; le texte n'est pas encore extrait.",
            "recommended_action": "Cliquer sur l'article pour lancer son extraction.",
            "access_kind": "fulltext_url",
        }

    failure = _access_failure_details(result)
    browser_only = failure.get("reason_code") == "PUBLIC_PDF_BROWSER_ONLY"
    return {
        "evidence_status": (
            "BROWSER_DOWNLOAD_REQUIRED" if browser_only else "ACCESS_UNAVAILABLE"
        ),
        "evidence_ui_status": (
            "browser_download_then_upload" if browser_only else "upload_required"
        ),
        "evidence_label": failure["reason_detail"],
        "evidence_usable": False,
        "candidate_only": True,
        "fulltext_ready": False,
        "abstract_ready": bool(_abstract(article)),
        "access_check_status": result.get("status"),
        "content_source_kind": None,
        "extraction_method": None,
        "needs_legal_recovery": False,
        "likely_restricted_access": failure["access_kind"] in {
            "paid", "blocked", "public_browser_only",
        },
        **failure,
        "recommended_action": (
            "Telecharger le PDF public dans le navigateur, puis l'importer ici pour activer Garder et Rejeter."
            if browser_only
            else "Importer le PDF autorise pour activer Garder et Rejeter."
        ),
        "needs_consultant_upload": True,
    }


def _process_access_probe(project_id: int, article_id: int) -> Dict[str, Any]:
    """Verifie l'acces au texte sans lancer PDF parsing, OCR, GROBID ou MCP."""
    db = SessionLocal()
    started = time.perf_counter()
    try:
        project = db.get(Project, int(project_id))
        article = db.get(Article, int(article_id))
        if project is None or article is None:
            raise ValueError("project_or_article_not_found")

        existing = _existing_fulltext_result(article)
        if existing is not None:
            return existing

        from services.scholar_fulltext_cache_service import get_cached_fulltext
        cached = get_cached_fulltext(db, article)
        if cached is not None:
            evidence = _classify(article, cached)
            source_json = dict(article.source_json or {})
            source_json["fulltext_cache_id"] = cached.get("fulltext_cache_id")
            source_json["fulltext_cache_key"] = cached.get("fulltext_cache_key")
            article.source_json = source_json
            _set_article_evidence(db, article, evidence, cached)
            db.commit()
            return {
                "article_id": int(article.id),
                "verrou_id": article.verrou_id,
                "title": article.title,
                "ok": True,
                "reused": True,
                **evidence,
            }

        from services.scholar_fulltext_fetcher import fetch_fulltext_pdf_for_article
        result = dict(fetch_fulltext_pdf_for_article(
            db=db,
            project=project,
            article_id=int(article.id),
            force=_bool_env("ENNOSCHOLAR_ACCESS_FORCE_REFRESH", True),
        ) or {})
        evidence = _access_evidence(article, result)
        source_json = dict(article.source_json or {})
        source_json["access_probe_result"] = {
            "status": result.get("status"),
            "full_text_status": result.get("full_text_status"),
            "content_source_kind": result.get("content_source_kind"),
            "pdf_source_url": result.get("pdf_source_url"),
            "pdf_final_url": result.get("pdf_final_url"),
            "fulltext_source_url": result.get("fulltext_source_url"),
            "fulltext_final_url": result.get("fulltext_final_url"),
            "browser_download_url": evidence.get("browser_download_url"),
            "reason_code": evidence.get("reason_code"),
            "needs_consultant_upload": bool(evidence.get("needs_consultant_upload")),
        }
        if evidence.get("evidence_status") == "BROWSER_DOWNLOAD_REQUIRED":
            # Un ancien diagnostic MCP ne doit pas masquer le fait que le PDF
            # officiel est bien connu et ouvrable dans un navigateur.
            source_json.pop("mcp_access_diagnostic", None)
            source_json.pop("mcp_fulltext_candidates", None)
        article.source_json = source_json
        if evidence.get("evidence_status") in {
            "ACCESS_UNAVAILABLE", "BROWSER_DOWNLOAD_REQUIRED",
        }:
            article.consultant_status = "en_attente"
        _set_article_evidence(db, article, evidence, result)
        db.commit()
        LOGGER.info(
            "[EnnoScholar acces] article=%s statut=%s cause=%s duree=%.1fs",
            article.id,
            evidence.get("evidence_status"),
            evidence.get("reason_code"),
            time.perf_counter() - started,
        )
        return {
            "article_id": int(article.id),
            "verrou_id": article.verrou_id,
            "title": article.title,
            "ok": True,
            **evidence,
        }
    finally:
        db.close()


def _verified_mcp_access_candidates(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_candidates: List[Dict[str, Any]] = []
    best = payload.get("best_candidate")
    if isinstance(best, dict):
        raw_candidates.append(best)
    raw_candidates.extend(
        item for item in (payload.get("locations") or []) if isinstance(item, dict)
    )

    verified: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        raw_metadata = candidate.get("raw_metadata") if isinstance(candidate.get("raw_metadata"), dict) else {}
        verified_html = bool(raw_metadata.get("verified_html_fulltext") is True)
        if (
            candidate.get("legal_access") is not True
            or candidate.get("same_article") is not True
            or not (candidate.get("verified_pdf") is True or verified_html)
        ):
            continue
        url = str(candidate.get("final_url") or candidate.get("pdf_url") or "").strip()
        if not url.startswith(("http://", "https://")) or url.casefold() in seen:
            continue
        seen.add(url.casefold())
        verified.append({
            "url": url,
            "pdf_url": candidate.get("pdf_url"),
            "final_url": candidate.get("final_url") or url,
            "kind": "landing" if verified_html and candidate.get("verified_pdf") is not True else "pdf",
            "source": f"legal_mcp:{str(candidate.get('provider') or 'unknown')}",
            "provider": candidate.get("provider"),
            "legal_access": True,
            "same_article": True,
            "verified_pdf": bool(candidate.get("verified_pdf")),
            "verified_html_fulltext": verified_html,
            "license": candidate.get("license"),
            "version": candidate.get("version"),
            "host_type": candidate.get("host_type"),
            "access_type": candidate.get("access_type"),
            "rights_status": candidate.get("rights_status"),
            "source_domain": candidate.get("source_domain"),
            "discovered_via": candidate.get("discovered_via"),
            "identity_score": candidate.get("identity_score"),
            "identity_method": candidate.get("identity_method"),
            "retrieval_stage": "legal_mcp_access_probe",
        })
    return verified


def _process_mcp_access_probe(project_id: int, article_id: int) -> Dict[str, Any]:
    """Dernier contrôle : MCP exhaustif de résolution, toujours sans extraction."""
    db = SessionLocal()
    started = time.perf_counter()
    try:
        article = db.get(Article, int(article_id))
        project = db.get(Project, int(project_id))
        if article is None or project is None:
            raise ValueError("project_or_article_not_found")

        previous_evidence = dict((article.source_json or {}).get("evidence_preflight") or {})
        current = dict(previous_evidence)
        current.update({
            "evidence_status": "MCP_SEARCHING",
            "evidence_ui_status": "mcp_searching",
            "evidence_label": "Recherche MCP des copies légales restantes",
            "evidence_usable": False,
            "candidate_only": True,
            "fulltext_ready": False,
            "reason_code": "MCP_SEARCH_PENDING",
            "reason_detail": "Les liens connus et les fournisseurs OA directs n'ont rien trouvé ; le MCP vérifie les autres sources légales.",
            "recommended_action": "Attendre la conclusion du MCP avant la décision consultant.",
            "access_kind": "pending",
        })
        _set_article_evidence(db, article, current)
        db.commit()

        from services.scholar_legal_fulltext_bridge import (
            build_mcp_diagnostic,
            resolve_mcp_for_article,
        )
        mcp_result = dict(resolve_mcp_for_article(
            article,
            force=_bool_env("ENNOSCHOLAR_ACCESS_MCP_FORCE_REFRESH", True),
            search_all=True,
            retry_deterministic_providers=False,
        ) or {})
        candidates = _verified_mcp_access_candidates(mcp_result)
        diagnostic = build_mcp_diagnostic(mcp_result, max_attempts=40, max_locations=60)

        source_json = dict(article.source_json or {})
        source_json["mcp_access_diagnostic"] = diagnostic
        source_json["mcp_fulltext_candidates"] = candidates
        article.source_json = source_json

        if candidates:
            first = candidates[0]
            access_result = {
                "ok": True,
                "status": "mcp_verified_fulltext_available",
                "full_text_status": (
                    "html_fulltext_available"
                    if first.get("verified_html_fulltext")
                    else "pdf_url_available"
                ),
                "content_source_kind": "html" if first.get("verified_html_fulltext") else "pdf",
                "fulltext_source_url": first.get("url"),
                "fulltext_final_url": first.get("final_url"),
                "pdf_source_url": first.get("pdf_url"),
                "pdf_final_url": first.get("final_url"),
                "mcp_called": True,
                "mcp_status": mcp_result.get("status"),
                "mcp_verified_candidates_count": len(candidates),
                "needs_consultant_upload": False,
            }
            evidence = _access_evidence(article, access_result)
            evidence.update({
                "reason_code": "MCP_VERIFIED_FULLTEXT_ACCESSIBLE",
                "reason_detail": "Le MCP a trouvé et vérifié une copie légale correspondant au même article.",
                "recommended_action": "Cliquer sur l'article pour lancer son extraction.",
                "access_kind": "legal_mcp_fulltext_url",
            })
        else:
            transient = bool(mcp_result.get("retry_recommended")) or str(
                mcp_result.get("status") or ""
            ) in {"mcp_unavailable", "mcp_client_error", "provider_temporarily_unavailable"}
            previous_reason = str(previous_evidence.get("reason_detail") or "").strip()
            if transient:
                evidence = {
                    "evidence_status": "ACCESS_UNCONFIRMED",
                    "evidence_ui_status": "mcp_unavailable",
                    "evidence_label": "Le MCP n'a pas pu terminer la vérification",
                    "evidence_usable": False,
                    "candidate_only": True,
                    "fulltext_ready": False,
                    "abstract_ready": bool(_abstract(article)),
                    "access_check_status": mcp_result.get("status"),
                    "needs_legal_recovery": True,
                    "likely_restricted_access": False,
                    "reason_code": "MCP_TEMPORARILY_UNAVAILABLE",
                    "reason_detail": "La disponibilité du texte ne peut pas encore être confirmée car le MCP est indisponible.",
                    "recommended_action": "Relancer la vérification MCP avant de conclure ou d'importer un PDF.",
                    "access_kind": "technical_error",
                    "needs_consultant_upload": False,
                }
            else:
                browser_only = previous_evidence.get("reason_code") == "PUBLIC_PDF_BROWSER_ONLY"
                evidence = {
                    "evidence_status": (
                        "BROWSER_DOWNLOAD_REQUIRED"
                        if browser_only
                        else "ACCESS_UNAVAILABLE"
                    ),
                    "evidence_ui_status": "upload_required",
                    "evidence_label": (
                        "PDF public bloque pour les clients automatiques"
                        if browser_only
                        else "Aucune copie légale vérifiée par le MCP"
                    ),
                    "evidence_usable": False,
                    "candidate_only": True,
                    "fulltext_ready": False,
                    "abstract_ready": bool(_abstract(article)),
                    "access_check_status": mcp_result.get("status"),
                    "needs_legal_recovery": False,
                    "likely_restricted_access": bool(previous_evidence.get("likely_restricted_access")),
                    "reason_code": previous_evidence.get("reason_code") or "MCP_NO_LEGAL_COPY_FOUND",
                    "reason_detail": (
                        f"{previous_reason} " if previous_reason else ""
                    ) + "Le MCP a ensuite recherche les endpoints editeur et les autres copies legales, sans autre candidat automatiquement exploitable.",
                    "recommended_action": (
                        "Telecharger le PDF public dans le navigateur, puis l'importer ici."
                        if browser_only
                        else "Importer une copie PDF autorisee pour activer Garder et Rejeter."
                    ),
                    "access_kind": previous_evidence.get("access_kind") or "not_found",
                    "browser_download_url": previous_evidence.get("browser_download_url"),
                    "needs_consultant_upload": True,
                }
                article.consultant_status = "en_attente"
            access_result = {
                "ok": False,
                "status": mcp_result.get("status") or "mcp_no_verified_candidate",
                "full_text_status": "missing_legal_fulltext",
                "mcp_called": True,
                "mcp_failure_code": mcp_result.get("failure_code"),
                "needs_consultant_upload": bool(evidence.get("needs_consultant_upload")),
            }

        _set_article_evidence(db, article, evidence, access_result)
        db.commit()
        LOGGER.info(
            "[EnnoScholar acces][MCP] article=%s statut=%s candidats=%s duree=%.1fs",
            article.id,
            evidence.get("evidence_status"),
            len(candidates),
            time.perf_counter() - started,
        )
        return {
            "article_id": int(article.id),
            "verrou_id": article.verrou_id,
            "title": article.title,
            "ok": True,
            "mcp_called": True,
            **evidence,
        }
    finally:
        db.close()


def inspect_scholar_run_access(
    db: Session,
    project: Project,
    run: ScholarRun,
) -> Dict[str, Any]:
    """Controle leger du catalogue ; aucune extraction lourde n'est lancee."""
    started = time.perf_counter()
    configured_workers = max(
        1,
        min(int(os.getenv("ENNOSCHOLAR_ACCESS_WORKERS", "12")), 16),
    )
    workers = _safe_db_worker_count(configured_workers)
    rows = (
        db.query(Article)
        .filter(Article.scholar_run_id == int(run.id))
        .order_by(Article.score.desc().nullslast(), Article.id.asc())
        .all()
    )
    rows = _dedupe_article_rows(rows)
    if not rows:
        return {
            "enabled": True,
            "mode": "on_demand_access_preflight_with_mcp_v2",
            "processed": 0,
            "available_candidates": 0,
            "counts": {},
            "results": [],
        }

    pending_rows = [row for row in rows if _existing_fulltext_result(row) is None]
    for article in pending_rows:
        evidence = dict((article.source_json or {}).get("evidence_preflight") or {})
        evidence.update({
            "evidence_status": "ACCESS_CHECKING",
            "evidence_ui_status": "checking_access",
            "evidence_label": "Recherche d'une copie publique accessible",
            "evidence_usable": False,
            "candidate_only": True,
            "fulltext_ready": False,
            "reason_code": "ACCESS_CHECK_PENDING",
            "reason_detail": "Verification legere des URL de texte integral en cours.",
            "recommended_action": "Les articles classes sont deja affiches ; attendre le statut d'acces.",
            "access_kind": "pending",
        })
        _set_article_evidence(db, article, evidence)
    db.commit()

    discovery_summary: Dict[str, Any] = {"skipped": not pending_rows}
    if pending_rows:
        try:
            from services.scholar_deterministic_oa_service import enrich_articles_with_deterministic_oa
            discovery_summary = enrich_articles_with_deterministic_oa(db, pending_rows)
        except Exception as exc:
            db.rollback()
            discovery_summary = {"error": f"{type(exc).__name__}: {exc}"}
            LOGGER.exception("[EnnoScholar acces][OA_DISCOVERY] echec")

    article_ids = [int(article.id) for article in rows]
    results: List[Dict[str, Any]] = []
    LOGGER.info(
        "[EnnoScholar acces] run=%s articles=%s workers=%s extraction=on_click",
        run.id,
        len(article_ids),
        workers,
    )
    with ThreadPoolExecutor(max_workers=min(workers, len(article_ids))) as executor:
        futures = {
            executor.submit(_process_access_probe, int(project.id), article_id): article_id
            for article_id in article_ids
        }
        for completed_count, future in enumerate(as_completed(futures), start=1):
            article_id = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = _persist_unhandled_failure(int(project.id), article_id, exc)
            results.append(result)
            if completed_count % 10 == 0 or completed_count == len(article_ids):
                LOGGER.info(
                    "[EnnoScholar acces] progression run=%s %s/%s",
                    run.id,
                    completed_count,
                    len(article_ids),
                )

    # Le consultant ne reçoit pas encore la conclusion "introuvable" : tous
    # les échecs directs/OA passent d'abord par le MCP légal exhaustif. Le MCP
    # résout et vérifie les candidats, mais n'extrait aucun texte ici.
    mcp_ids = [
        int(item["article_id"])
        for item in results
        if item.get("article_id") is not None
        and item.get("evidence_status") in {
            "ACCESS_UNAVAILABLE",
            "BROWSER_DOWNLOAD_REQUIRED",
            "EXTRACTION_FAILED",
            "METADATA_ONLY",
            "ABSTRACT_READY",
        }
    ]
    mcp_results: List[Dict[str, Any]] = []
    if mcp_ids:
        LOGGER.info(
            "[EnnoScholar acces][MCP] démarrage articles=%s workers=%s mode=deep_fast_stop_first",
            len(mcp_ids),
            BROAD_LEGAL_CONCURRENCY,
        )
        with ThreadPoolExecutor(
            max_workers=min(BROAD_LEGAL_CONCURRENCY, len(mcp_ids))
        ) as executor:
            futures = {
                executor.submit(_process_mcp_access_probe, int(project.id), article_id): article_id
                for article_id in mcp_ids
            }
            for future in as_completed(futures):
                article_id = futures[future]
                try:
                    mcp_results.append(future.result())
                except Exception as exc:
                    mcp_results.append(
                        _persist_unhandled_failure(int(project.id), article_id, exc)
                    )

        replacement = {
            int(item["article_id"]): item
            for item in mcp_results
            if item.get("article_id") is not None
        }
        results = [
            replacement.get(int(item.get("article_id") or 0), item)
            for item in results
        ]
        LOGGER.info(
            "[EnnoScholar acces][MCP] terminé articles=%s accessibles=%s introuvables=%s non_confirmes=%s",
            len(mcp_results),
            sum(1 for item in mcp_results if item.get("evidence_status") == "ACCESS_AVAILABLE"),
            sum(1 for item in mcp_results if item.get("evidence_status") == "ACCESS_UNAVAILABLE"),
            sum(1 for item in mcp_results if item.get("evidence_status") == "ACCESS_UNCONFIRMED"),
        )

    order = {article_id: index for index, article_id in enumerate(article_ids)}
    results.sort(key=lambda item: order.get(int(item.get("article_id") or 0), len(order)))
    counts = _counts(results)
    return {
        "enabled": True,
        "mode": "on_demand_access_preflight_with_mcp_v2",
        "extraction_policy": "on_click_only",
        "processed": len(results),
        "available_candidates": len(rows),
        "workers": workers,
        "configured_workers": configured_workers,
        "counts": counts,
        "access_available_count": counts.get("ACCESS_AVAILABLE", 0),
        "access_unavailable_count": counts.get("ACCESS_UNAVAILABLE", 0),
        "browser_download_required_count": counts.get("BROWSER_DOWNLOAD_REQUIRED", 0),
        "access_unconfirmed_count": counts.get("ACCESS_UNCONFIRMED", 0),
        "fulltext_ready_count": counts.get("FULLTEXT_READY", 0),
        "mcp_processed_count": len(mcp_results),
        "mcp_search_all": True,
        "oa_discovery": discovery_summary,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "results": results,
    }


def _process_direct_stage(project_id: int, article_id: int) -> Dict[str, Any]:
    """Passe A : accès connus et extraction locale, sans appel MCP."""
    db = SessionLocal()
    started = time.perf_counter()
    try:
        project = db.get(Project, int(project_id))
        article = db.get(Article, int(article_id))
        if project is None or article is None:
            raise ValueError("project_or_article_not_found")

        existing = _existing_fulltext_result(article)
        if existing is not None:
            return existing

        from services.scholar_fulltext_cache_service import (
            get_cached_fulltext,
            store_cached_fulltext,
        )

        cached = get_cached_fulltext(db, article)
        if cached is not None:
            evidence = _classify(article, cached)
            source_json = dict(article.source_json or {})
            source_json["fulltext_cache_id"] = cached.get("fulltext_cache_id")
            source_json["fulltext_cache_key"] = cached.get("fulltext_cache_key")
            article.source_json = source_json
            _set_article_evidence(db, article, evidence, cached)
            db.commit()
            LOGGER.info(
                "[EnnoScholar extraction][CACHE] article=%s texte réutilisé depuis PostgreSQL",
                article_id,
            )
            return {
                "article_id": int(article.id),
                "verrou_id": article.verrou_id,
                "title": article.title,
                "ok": True,
                "terminal": True,
                "reused": True,
                **evidence,
            }

        _set_article_evidence(
            db,
            article,
            _running_evidence(article, "Extraction directe depuis les liens connus."),
        )
        db.commit()
        LOGGER.info(
            "[EnnoScholar extraction][DIRECT] article=%s démarré — %s",
            article_id,
            str(article.title or "Sans titre")[:120],
        )

        try:
            result = dict(resolve_and_extract_fulltext_for_article(
                db=db,
                project=project,
                article_id=int(article_id),
                refresh_resolution=False,
                force_reextract=False,
            ) or {})
        except Exception as exc:
            result = {
                "ok": False,
                "status": "direct_extraction_exception",
                "full_text_status": "missing_or_blocked_fulltext",
                "needs_legal_recovery": True,
                "error": f"{type(exc).__name__}: {exc}",
            }

        fulltext_ready = bool(
            result.get("ok")
            and result.get("full_text_status") == "text_extracted"
        )
        if fulltext_ready:
            cache_row = store_cached_fulltext(db, article, result)
            if cache_row is not None:
                source_json = dict(article.source_json or {})
                source_json["fulltext_cache_id"] = int(cache_row.id)
                source_json["fulltext_cache_key"] = cache_row.cache_key
                article.source_json = source_json
            evidence = _classify(article, result)
        else:
            evidence = _queued_evidence(
                article,
                result,
                "Extraction directe terminée ; recherche MCP approfondie planifiée en dernier recours.",
            )
        _set_article_evidence(db, article, evidence, result)
        db.commit()
        LOGGER.info(
            "[EnnoScholar extraction][DIRECT] article=%s statut=%s durée=%.1fs",
            article_id,
            evidence.get("evidence_status"),
            time.perf_counter() - started,
        )
        return {
            "article_id": int(article.id),
            "verrou_id": article.verrou_id,
            "title": article.title,
            "ok": True,
            "terminal": fulltext_ready,
            "needs_legal": not fulltext_ready,
            **evidence,
        }
    finally:
        db.close()


def _process_legal_stage(
    project_id: int,
    article_id: int,
    *,
    search_all: bool,
) -> Dict[str, Any]:
    """Dernière passe : MCP approfondi pour les seuls échecs déterministes."""
    db = SessionLocal()
    started = time.perf_counter()
    phase = "MCP_LARGE" if search_all else "MCP_CIBLE"
    try:
        project = db.get(Project, int(project_id))
        article = db.get(Article, int(article_id))
        if project is None or article is None:
            raise ValueError("project_or_article_not_found")

        existing = _existing_fulltext_result(article)
        if existing is not None:
            return existing

        label = (
            "Recherche MCP approfondie de dernier recours en cours."
            if search_all
            else "Recherche légale MCP ciblée en cours."
        )
        _set_article_evidence(db, article, _running_evidence(article, label))
        db.commit()
        LOGGER.info(
            "[EnnoScholar extraction][%s] article=%s démarré — %s",
            phase,
            article_id,
            str(article.title or "Sans titre")[:120],
        )

        result = _legal_recovery(
            db,
            project,
            int(article_id),
            search_all=search_all,
            expand_on_failure=False,
        ) or {
            "ok": False,
            "status": "legal_recovery_unavailable",
            "full_text_status": "missing_or_blocked_fulltext",
        }
        fulltext_ready = bool(
            result.get("ok")
            and result.get("full_text_status") == "text_extracted"
        )
        terminal = bool(fulltext_ready or search_all)
        if terminal:
            if fulltext_ready:
                from services.scholar_fulltext_cache_service import store_cached_fulltext

                cache_row = store_cached_fulltext(db, article, result)
                if cache_row is not None:
                    source_json = dict(article.source_json or {})
                    source_json["fulltext_cache_id"] = int(cache_row.id)
                    source_json["fulltext_cache_key"] = cache_row.cache_key
                    article.source_json = source_json
            evidence = _classify(article, result)
        else:
            evidence = _queued_evidence(
                article,
                result,
                "MCP ciblé terminé ; recherche légale élargie planifiée.",
            )
        _set_article_evidence(db, article, evidence, result)
        db.commit()
        LOGGER.info(
            "[EnnoScholar extraction][%s] article=%s statut=%s cause=%s durée=%.1fs",
            phase,
            article_id,
            evidence.get("evidence_status"),
            evidence.get("reason_code"),
            time.perf_counter() - started,
        )
        return {
            "article_id": int(article.id),
            "verrou_id": article.verrou_id,
            "title": article.title,
            "ok": True,
            "terminal": terminal,
            "needs_deep_legal": not terminal,
            **evidence,
        }
    finally:
        db.close()



# ---------------------------------------------------------------------------
# EnnoScholar V6.3 — MCP resolution / extraction decoupling
# ---------------------------------------------------------------------------

def _resolve_mcp_only_stage(
    project_id: int,
    article_id: int,
    *,
    search_all: bool,
) -> Dict[str, Any]:
    """Résout les candidats MCP sans télécharger/parser le PDF."""
    from types import SimpleNamespace

    db = SessionLocal()
    phase = "MCP_LARGE_RESOLVE" if search_all else "MCP_TARGETED_RESOLVE"
    started = time.perf_counter()
    try:
        article = db.get(Article, int(article_id))
        if article is None:
            raise ValueError("article_not_found")

        existing = _existing_fulltext_result(article)
        if existing is not None:
            return {
                **existing,
                "article_id": int(article.id),
                "terminal": True,
                "_mcp_result": None,
            }

        label = "Résolution MCP élargie en cours." if search_all else "Résolution MCP ciblée en cours."
        _set_article_evidence(db, article, _running_evidence(article, label))
        db.commit()

        snapshot = SimpleNamespace(
            id=int(article.id),
            title=str(article.title or ""),
            doi=getattr(article, "doi", None),
            authors=getattr(article, "authors", None),
            year=getattr(article, "year", None),
            source=getattr(article, "source", None),
            url=getattr(article, "url", None),
            source_json=dict(article.source_json or {}),
        )
        title = str(article.title or "")
        verrou_id = article.verrou_id
    finally:
        db.close()

    from services.scholar_legal_fulltext_bridge import resolve_mcp_for_article

    try:
        mcp_result = resolve_mcp_for_article(
            snapshot,
            force=bool(search_all),
            search_all=bool(search_all),
        )
        if not isinstance(mcp_result, dict):
            mcp_result = {
                "ok": False,
                "found": False,
                "status": "invalid_mcp_response",
                "locations": [],
                "retry_recommended": False,
            }
    except Exception as exc:
        mcp_result = {
            "ok": False,
            "found": False,
            "status": "mcp_resolution_exception",
            "failure_code": "mcp_resolution_exception",
            "locations": [],
            "retry_recommended": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    LOGGER.info(
        "[EnnoScholar extraction][%s] article=%s resolved=%s locations=%s durée=%.1fs",
        phase,
        article_id,
        bool(mcp_result.get("found") or mcp_result.get("locations")),
        len(mcp_result.get("locations") or []),
        time.perf_counter() - started,
    )
    return {
        "article_id": int(article_id),
        "verrou_id": verrou_id,
        "title": title,
        "ok": True,
        "terminal": False,
        "_mcp_result": mcp_result,
    }


def _extract_pre_resolved_legal_stage(
    project_id: int,
    article_id: int,
    *,
    search_all: bool,
    mcp_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Extrait les candidats déjà résolus, sans rappeler le MCP."""
    db = SessionLocal()
    started = time.perf_counter()
    phase = "MCP_LARGE_EXTRACT" if search_all else "MCP_TARGETED_EXTRACT"
    try:
        project = db.get(Project, int(project_id))
        article = db.get(Article, int(article_id))
        if project is None or article is None:
            raise ValueError("project_or_article_not_found")

        existing = _existing_fulltext_result(article)
        if existing is not None:
            return {**existing, "article_id": int(article.id), "terminal": True}

        from services.scholar_legal_recovery_service import recover_legal_fulltext_for_article

        result = recover_legal_fulltext_for_article(
            db,
            project,
            int(article_id),
            force_refresh=True,
            search_all=bool(search_all),
            expand_on_failure=False,
            pre_resolved_mcp_result=dict(mcp_result or {}),
        ) or {
            "ok": False,
            "status": "legal_recovery_unavailable",
            "full_text_status": "missing_or_blocked_fulltext",
        }

        fulltext_ready = bool(result.get("ok") and result.get("full_text_status") == "text_extracted")
        terminal = bool(fulltext_ready or search_all)

        if terminal:
            if fulltext_ready:
                from services.scholar_fulltext_cache_service import store_cached_fulltext
                cache_row = store_cached_fulltext(db, article, result)
                if cache_row is not None:
                    source_json = dict(article.source_json or {})
                    source_json["fulltext_cache_id"] = int(cache_row.id)
                    source_json["fulltext_cache_key"] = cache_row.cache_key
                    article.source_json = source_json
            evidence = _classify(article, result)
        else:
            evidence = _queued_evidence(
                article,
                result,
                "MCP ciblé terminé ; recherche élargie planifiée si prioritaire.",
            )

        _set_article_evidence(db, article, evidence, result)
        db.commit()
        LOGGER.info(
            "[EnnoScholar extraction][%s] article=%s statut=%s cause=%s durée=%.1fs",
            phase,
            article_id,
            evidence.get("evidence_status"),
            evidence.get("reason_code"),
            time.perf_counter() - started,
        )
        return {
            "article_id": int(article.id),
            "verrou_id": article.verrou_id,
            "title": article.title,
            "ok": True,
            "terminal": terminal,
            "needs_deep_legal": not terminal,
            **evidence,
        }
    finally:
        db.close()

def _persist_stage_pending(
    project_id: int,
    article_id: int,
    exc: Exception,
    label: str,
) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        article = db.get(Article, int(article_id))
        if article is None:
            return {"article_id": article_id, "terminal": False, "ok": False}
        result = {
            "ok": False,
            "status": "preflight_stage_exception",
            "full_text_status": "missing_or_blocked_fulltext",
            "needs_legal_recovery": True,
            "error": f"{type(exc).__name__}: {exc}",
        }
        evidence = _queued_evidence(article, result, label)
        _set_article_evidence(db, article, evidence, result)
        db.commit()
        return {
            "article_id": int(article.id),
            "verrou_id": article.verrou_id,
            "title": article.title,
            "ok": False,
            "terminal": False,
            **evidence,
        }
    finally:
        db.close()

def _persist_unhandled_failure(
    project_id: int,
    article_id: int,
    exc: Exception,
) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        article = db.get(Article, int(article_id))
        if article is None:
            return {
                "article_id": article_id,
                "ok": False,
                "evidence_status": "EXTRACTION_FAILED",
                "status": "not_found",
            }
        result = {
            "ok": False,
            "status": "preflight_worker_exception",
            "full_text_status": "missing_or_blocked_fulltext",
            "needs_legal_recovery": True,
            "error": f"{type(exc).__name__}: {exc}",
        }
        classification = _classify(article, result)
        _set_article_evidence(db, article, classification, result)
        db.commit()
        return {
            "article_id": int(article_id),
            "verrou_id": article.verrou_id,
            "title": article.title,
            "ok": False,
            **classification,
        }
    finally:
        db.close()

def _score(article: Article) -> float:
    try:
        return float(article.score or 0.0)
    except Exception:
        return 0.0

def _fair_order(rows: List[Article], min_per_verrou: int) -> List[Article]:
    groups = defaultdict(list)
    for article in rows:
        key = str(article.verrou_id) if article.verrou_id is not None else "__unlinked__"
        groups[key].append(article)

    for group_rows in groups.values():
        group_rows.sort(key=_score, reverse=True)

    group_keys = sorted(
        groups,
        key=lambda key: _score(groups[key][0]) if groups[key] else 0.0,
        reverse=True,
    )

    out, seen = [], set()
    for round_index in range(max(0, int(min_per_verrou))):
        for key in group_keys:
            if round_index >= len(groups[key]):
                continue
            article = groups[key][round_index]
            if int(article.id) in seen:
                continue
            seen.add(int(article.id))
            out.append(article)

    out.extend(
        sorted(
            [a for a in rows if int(a.id) not in seen],
            key=_score,
            reverse=True,
        )
    )
    return out

def _counts(results: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts = {}
    for row in results:
        key = str(row.get("evidence_status") or "ERROR")
        counts[key] = counts.get(key, 0) + 1
    return counts

def _fulltext_by_verrou(results: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts = {}
    for row in results:
        if row.get("evidence_status") != "FULLTEXT_READY":
            continue
        key = str(row.get("verrou_id") or "__unlinked__")
        counts[key] = counts.get(key, 0) + 1
    return counts

def _coverage_target(all_rows: List[Article], min_per_verrou: int):
    candidate_counts = {}
    for article in all_rows:
        key = str(article.verrou_id or "__unlinked__")
        candidate_counts[key] = candidate_counts.get(key, 0) + 1
    return {
        key: min(max(0, int(min_per_verrou)), count)
        for key, count in candidate_counts.items()
        if count > 0 and key != "__unlinked__"
    }

def _coverage_satisfied(results, target) -> bool:
    if not target:
        return True
    actual = _fulltext_by_verrou(results)
    return all(actual.get(key, 0) >= needed for key, needed in target.items())

def _preflight_scholar_run_legacy(
    db: Session,
    project: Project,
    run: ScholarRun,
    *,
    max_articles: int | None = None,
) -> Dict[str, Any]:
    if not _bool_env("ENNOSCHOLAR_PREFLIGHT_ENABLED", True):
        return {"enabled": False, "processed": 0, "fulltext_ready_count": 0, "results": []}

    preflight_mode = str(os.getenv("ENNOSCHOLAR_PREFLIGHT_MODE", "all") or "all").strip().lower()
    exhaustive = preflight_mode not in {"smart_stop", "target", "partial"}
    target_fulltext = max(1, int(os.getenv("ENNOSCHOLAR_TARGET_FULLTEXT", "10")))
    initial_batch = max(1, int(os.getenv("ENNOSCHOLAR_PREFLIGHT_INITIAL_BATCH", "10")))
    batch_size = max(1, int(os.getenv("ENNOSCHOLAR_PREFLIGHT_BATCH_SIZE", "5")))
    workers = max(1, min(int(os.getenv("ENNOSCHOLAR_PREFLIGHT_WORKERS", "4")), 8))
    min_per_verrou = max(0, int(os.getenv("ENNOSCHOLAR_MIN_FULLTEXT_PER_VERROU", "2")))

    rows = (
        db.query(Article)
        .filter(Article.scholar_run_id == run.id)
        .order_by(Article.score.desc().nullslast(), Article.id.asc())
        .all()
    )
    rows = _dedupe_article_rows(rows)

    if not rows:
        return {
            "enabled": True,
            "mode": "full_catalog_preflight_v3" if exhaustive else "smart_stop_fulltext_v2",
            "processed": 0,
            "fulltext_ready_count": 0,
            "target_fulltext": target_fulltext,
            "results": [],
        }

    # Par défaut, chaque article présenté au consultant est pré-vérifié. Une
    # limite ne s'applique que lorsqu'un appelant la fournit explicitement.
    max_scan = min(len(rows), max(1, int(max_articles))) if max_articles else len(rows)
    ordered = _fair_order(rows, min_per_verrou)[:max_scan]
    coverage_target = _coverage_target(rows, min_per_verrou)

    LOGGER.info(
        "[EnnoScholar extraction] run=%s catalogue=%s workers=%s legal_workers=%s",
        run.id,
        len(ordered),
        workers,
        LEGAL_CONCURRENCY,
    )

    results, processed_ids, batches = [], set(), []
    cursor = 0
    current_batch_size = initial_batch

    while cursor < len(ordered):
        batch_rows = ordered[cursor : min(len(ordered), cursor + current_batch_size)]
        if not batch_rows:
            break

        ids = [
            int(a.id)
            for a in batch_rows
            if int(a.id) not in processed_ids
        ]

        if not ids:
            cursor += current_batch_size
            current_batch_size = batch_size
            continue

        batch_results = []
        unhandled_failures = []
        with ThreadPoolExecutor(max_workers=min(workers, len(ids))) as executor:
            futures = {
                executor.submit(_process_one, int(project.id), article_id): article_id
                for article_id in ids
            }
            for future in as_completed(futures):
                article_id = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    unhandled_failures.append((article_id, exc))
                    continue
                batch_results.append(row)
                processed_ids.add(int(article_id))
                LOGGER.info(
                    "[EnnoScholar extraction] run=%s progression=%s/%s",
                    run.id,
                    len(results) + len(batch_results),
                    len(ordered),
                )

        # Après la fermeture du pool, aucune autre écriture du batch n'est en
        # concurrence : on peut donc enregistrer un motif terminal au lieu de
        # laisser indéfiniment l'article au statut EXTRACTION_QUEUED.
        for article_id, exc in unhandled_failures:
            try:
                row = _persist_unhandled_failure(int(project.id), article_id, exc)
            except Exception as persist_exc:
                row = {
                    "article_id": article_id,
                    "ok": False,
                    "evidence_status": "EXTRACTION_FAILED",
                    "evidence_ui_status": "extraction_failed",
                    "evidence_label": "Vérification automatique échouée",
                    "evidence_usable": False,
                    "fulltext_ready": False,
                    "error": f"{type(persist_exc).__name__}: {persist_exc}",
                }
            batch_results.append(row)
            processed_ids.add(int(article_id))

        LOGGER.info(
            "[EnnoScholar extraction] run=%s lot=%s terminé progression=%s/%s statuts=%s",
            run.id,
            len(batches) + 1,
            len(results) + len(batch_results),
            len(ordered),
            _counts(list(results) + list(batch_results)),
        )

        results.extend(batch_results)
        counts = _counts(results)
        fulltext_count = counts.get("FULLTEXT_READY", 0)

        batches.append({
            "batch_number": len(batches) + 1,
            "article_ids": ids,
            "processed_in_batch": len(batch_results),
            "fulltext_ready_after_batch": fulltext_count,
            "counts_after_batch": counts,
        })

        if (
            not exhaustive
            and fulltext_count >= target_fulltext
            and _coverage_satisfied(results, coverage_target)
        ):
            break

        cursor += current_batch_size
        current_batch_size = batch_size

    counts = _counts(results)
    fulltext_count = counts.get("FULLTEXT_READY", 0)
    by_verrou = _fulltext_by_verrou(results)
    coverage_gaps = {
        key: {"target": needed, "actual": by_verrou.get(key, 0)}
        for key, needed in coverage_target.items()
        if by_verrou.get(key, 0) < needed
    }

    return {
        "enabled": True,
        "mode": "full_catalog_preflight_v3" if exhaustive else "smart_stop_fulltext_v2",
        "exhaustive": exhaustive,
        "processed": len(results),
        "available_candidates": len(rows),
        "max_scan": max_scan,
        "workers": workers,
        "legal_workers": LEGAL_CONCURRENCY,
        "target_fulltext": target_fulltext,
        "fulltext_ready_count": fulltext_count,
        "target_reached": fulltext_count >= target_fulltext,
        "min_fulltext_per_verrou": min_per_verrou,
        "fulltext_by_verrou": by_verrou,
        "coverage_target": coverage_target,
        "coverage_gaps": coverage_gaps,
        "coverage_satisfied": not coverage_gaps,
        "counts": counts,
        "batches": batches,
        "results": results,
        "needs_more_discovery": (
            fulltext_count < target_fulltext
            and len(processed_ids) >= min(len(rows), max_scan)
        ),
    }

def _deterministic_candidate_count(article: Article | None) -> int:
    if article is None:
        return 0
    src = article.source_json if isinstance(article.source_json, dict) else {}
    values = src.get("deterministic_oa_candidates")
    return len(values) if isinstance(values, list) else 0


def _deep_mcp_priority(article: Article) -> tuple:
    """Priorité de la recherche large : Direct d'abord, puis score scientifique."""
    tag = str(article.tag_article or "").strip().casefold()
    tag_rank = 0 if tag == "direct" else (1 if tag == "connexe" else 2)
    try:
        score = float(article.score or 0.0)
    except Exception:
        score = 0.0
    return (tag_rank, -score, int(article.id))


def _deep_mcp_is_eligible(article: Article) -> bool:
    """Le deep MCP est budgété ; tous les articles restent néanmoins vérifiés."""
    scope = str(os.getenv("ENNOSCHOLAR_DEEP_MCP_SCOPE", "important") or "important").strip().lower()
    if scope in {"all", "exhaustive", "1", "true"}:
        return True
    if scope in {"none", "off", "0", "false"}:
        return False

    tag = str(article.tag_article or "").strip().casefold()
    if tag == "direct":
        return True
    if tag == "connexe":
        try:
            threshold = float(os.getenv("ENNOSCHOLAR_DEEP_MCP_CONNEXE_MIN_SCORE", "0.60") or 0.60)
            return float(article.score or 0.0) >= threshold
        except Exception:
            return False
    return False


def _finalize_without_deep_mcp(
    project_id: int,
    article_id: int,
    reason: str = "deep_search_budget_not_selected",
) -> Dict[str, Any]:
    """Termine proprement un article déjà vérifié sans relancer une recherche large.

    L'article a déjà passé cache + URLs connues + OA déterministe + MCP ciblé.
    Il ne reste donc jamais NOT_CHECKED : abstract ou métadonnées deviennent le
    statut terminal si aucun texte intégral légal n'a été trouvé.
    """
    del project_id
    db = SessionLocal()
    try:
        article = db.get(Article, int(article_id))
        if article is None:
            return {
                "article_id": int(article_id),
                "ok": False,
                "terminal": True,
                "evidence_status": "EXTRACTION_FAILED",
                "reason_code": "ARTICLE_NOT_FOUND",
            }

        src = dict(article.source_json or {})
        previous = src.get("fulltext_preflight_result")
        result = dict(previous) if isinstance(previous, dict) else {}
        result.update({
            "ok": False,
            "status": reason,
            "full_text_status": "missing_or_blocked_fulltext",
            "needs_legal_recovery": False,
            "deep_search_skipped": True,
            "finalized": True,
        })
        evidence = _classify(article, result)
        evidence["deep_search_skipped"] = True
        evidence["deep_search_skip_reason"] = reason
        _set_article_evidence(db, article, evidence, result)
        db.commit()
        return {
            "article_id": int(article.id),
            "verrou_id": article.verrou_id,
            "title": article.title,
            "ok": True,
            "terminal": True,
            **evidence,
        }
    finally:
        db.close()


def preflight_scholar_run(
    db: Session,
    project: Project,
    run: ScholarRun,
    *,
    max_articles: int | None = None,
) -> Dict[str, Any]:
    """Préflight exhaustif optimisé.

    Pipeline :
      CACHE
        -> DIRECT_KNOWN (réutilise les URLs de la recherche)
        -> OA_DISCOVERY (uniquement les échecs)
        -> DIRECT_OA (uniquement quand de nouveaux candidats OA existent)
        -> MCP_TARGETED (uniquement les échecs restants)
        -> MCP_LARGE budgété pour les articles importants
        -> FINALIZE_FAST pour tous les autres.

    Tous les articles deviennent terminaux avant la sélection consultant ; aucun
    NOT_CHECKED n'est laissé en base.
    """
    if not _bool_env("ENNOSCHOLAR_PREFLIGHT_ENABLED", True):
        return {"enabled": False, "processed": 0, "fulltext_ready_count": 0, "results": []}

    del max_articles
    target_fulltext = max(1, int(os.getenv("ENNOSCHOLAR_TARGET_FULLTEXT", "10")))
    configured_workers = max(
        1,
        min(int(os.getenv("ENNOSCHOLAR_PREFLIGHT_WORKERS", "16")), 20),
    )
    workers = _safe_db_worker_count(configured_workers)
    min_per_verrou = max(0, int(os.getenv("ENNOSCHOLAR_MIN_FULLTEXT_PER_VERROU", "2")))
    deep_max = max(0, int(os.getenv("ENNOSCHOLAR_DEEP_MCP_MAX_ARTICLES", "20")))

    # Cree/verifie la table avant toute parallelisation. L'ancienne
    # initialisation paresseuse depuis chacun des 16 threads pouvait epuiser
    # les 15 connexions du pool puis attendre une 16e connexion en cascade.
    from services.scholar_fulltext_cache_service import ensure_fulltext_cache_ready
    ensure_fulltext_cache_ready()

    rows = (
        db.query(Article)
        .filter(Article.scholar_run_id == run.id)
        .order_by(Article.score.desc().nullslast(), Article.id.asc())
        .all()
    )
    rows = _dedupe_article_rows(rows)
    if not rows:
        return {
            "enabled": True,
            "mode": "staged_fulltext_preflight_v6_3_streaming",
            "processed": 0,
            "fulltext_ready_count": 0,
            "target_fulltext": target_fulltext,
            "results": [],
        }

    ordered = _fair_order(rows, min_per_verrou)
    article_ids = [int(article.id) for article in ordered]
    by_id = {int(article.id): article for article in rows}
    coverage_target = _coverage_target(rows, min_per_verrou)
    results: List[Dict[str, Any]] = []
    stages: List[Dict[str, Any]] = []

    LOGGER.info(
        "[EnnoScholar extraction] run=%s pipeline="
        "cache->direct_known->oa_discovery->direct_oa->mcp_targeted_stream->mcp_large_stream "
        "catalogue=%s direct_workers=%s configured_workers=%s targeted_mcp=%s broad_mcp=%s",
        run.id,
        len(article_ids),
        workers,
        configured_workers,
        TARGETED_LEGAL_CONCURRENCY,
        BROAD_LEGAL_CONCURRENCY,
    )

    def execute_stage(
        stage_name: str,
        ids: List[int],
        stage_workers: int,
        action,
        *,
        final_stage: bool = False,
    ) -> List[Dict[str, Any]]:
        if not ids:
            stages.append({
                "stage": stage_name,
                "input_count": 0,
                "completed_count": 0,
                "terminal_count": 0,
                "next_stage_count": 0,
                "elapsed_seconds": 0.0,
            })
            return []

        stage_started = time.perf_counter()
        stage_rows: List[Dict[str, Any]] = []
        LOGGER.info(
            "[EnnoScholar extraction][%s] démarrage articles=%s workers=%s",
            stage_name, len(ids), stage_workers,
        )
        with ThreadPoolExecutor(max_workers=min(max(1, stage_workers), len(ids))) as executor:
            futures = {executor.submit(action, article_id): article_id for article_id in ids}
            for future in as_completed(futures):
                article_id = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    if final_stage:
                        try:
                            row = _persist_unhandled_failure(int(project.id), article_id, exc)
                            row["terminal"] = True
                        except Exception as persist_exc:
                            row = {
                                "article_id": article_id,
                                "ok": False,
                                "terminal": True,
                                "evidence_status": "EXTRACTION_FAILED",
                                "evidence_ui_status": "extraction_failed",
                                "evidence_label": "Vérification automatique échouée",
                                "evidence_usable": False,
                                "fulltext_ready": False,
                                "error": f"{type(persist_exc).__name__}: {persist_exc}",
                            }
                    else:
                        row = _persist_stage_pending(
                            int(project.id),
                            article_id,
                            exc,
                            f"Échec de la passe {stage_name} ; passage suivant planifié.",
                        )
                stage_rows.append(row)

        terminal_count = sum(1 for item in stage_rows if item.get("terminal"))
        elapsed = round(time.perf_counter() - stage_started, 3)
        stages.append({
            "stage": stage_name,
            "input_count": len(ids),
            "completed_count": len(stage_rows),
            "terminal_count": terminal_count,
            "next_stage_count": len(stage_rows) - terminal_count,
            "elapsed_seconds": elapsed,
        })
        LOGGER.info(
            "[EnnoScholar extraction][%s] terminé terminaux=%s suivants=%s durée=%.1fs",
            stage_name,
            terminal_count,
            len(stage_rows) - terminal_count,
            elapsed,
        )
        return stage_rows

    # 0) Cache global PostgreSQL avant tout reseau. Une seule requete et une
    # seule transaction remplacent ici N sessions/transactions concurrentes.
    cache_started = time.perf_counter()
    LOGGER.info(
        "[EnnoScholar extraction][CACHE] demarrage groupe articles=%s",
        len(ordered),
    )
    try:
        cache_rows = _process_cache_stage_batch(db, ordered)
    except Exception:
        db.rollback()
        LOGGER.exception(
            "[EnnoScholar extraction][CACHE] echec du mode groupe, repli sequentiel"
        )
        # Le repli reste volontairement sequentiel : il ne peut donc jamais
        # reproduire l'epuisement du pool qui a motive ce correctif.
        cache_rows = []
        for article_id in article_ids:
            try:
                cache_rows.append(_process_cache_stage(int(project.id), article_id))
            except Exception as exc:
                cache_rows.append(_persist_stage_pending(
                    int(project.id),
                    article_id,
                    exc,
                    "Echec de la passe CACHE ; passage suivant planifie.",
                ))
    cache_terminal_count = sum(1 for item in cache_rows if item.get("terminal"))
    cache_elapsed = round(time.perf_counter() - cache_started, 3)
    stages.append({
        "stage": "CACHE",
        "input_count": len(article_ids),
        "completed_count": len(cache_rows),
        "terminal_count": cache_terminal_count,
        "next_stage_count": len(cache_rows) - cache_terminal_count,
        "elapsed_seconds": cache_elapsed,
        "batch_query": True,
    })
    LOGGER.info(
        "[EnnoScholar extraction][CACHE] termine terminaux=%s suivants=%s duree=%.1fs",
        cache_terminal_count,
        len(cache_rows) - cache_terminal_count,
        cache_elapsed,
    )
    results.extend(row for row in cache_rows if row.get("terminal"))
    remaining_ids = [int(row["article_id"]) for row in cache_rows if not row.get("terminal")]

    # 1) URLs déjà découvertes pendant la recherche scientifique.
    direct_known_rows = execute_stage(
        "DIRECT_KNOWN",
        remaining_ids,
        workers,
        lambda article_id: _process_direct_stage(int(project.id), article_id),
    )
    results.extend(row for row in direct_known_rows if row.get("terminal"))
    remaining_ids = [int(row["article_id"]) for row in direct_known_rows if not row.get("terminal")]

    # 2) Résolution OA déterministe uniquement pour les échecs directs.
    # Les étapes DIRECT utilisent leurs propres sessions SQLAlchemy. On recharge
    # donc les lignes ici pour ne jamais écraser leurs nouveaux statuts avec
    # des objets restés en cache dans la session principale.
    discovery_started = time.perf_counter()
    db.expire_all()
    discovery_articles = (
        db.query(Article)
        .filter(Article.id.in_(remaining_ids))
        .all()
        if remaining_ids
        else []
    )
    for article in discovery_articles:
        _set_article_evidence(
            db,
            article,
            _running_evidence(
                article,
                "Résolution OA ciblée : OpenAlex batch, Unpaywall, Crossref puis CORE.",
            ),
        )
    db.commit()

    try:
        from services.scholar_deterministic_oa_service import enrich_articles_with_deterministic_oa
        discovery_summary = enrich_articles_with_deterministic_oa(db, discovery_articles)
    except Exception as exc:
        db.rollback()
        LOGGER.exception("[EnnoScholar extraction][OA_DISCOVERY] échec global")
        discovery_summary = {
            "input_count": len(discovery_articles),
            "articles_with_candidates": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.perf_counter() - discovery_started, 3),
        }

    stages.append({
        **discovery_summary,
        "stage": "OA_DISCOVERY",
        "completed_count": len(discovery_articles),
        "terminal_count": 0,
        "next_stage_count": len(discovery_articles),
    })

    # Ne pas retélécharger une seconde fois les mêmes URL si aucun nouveau
    # candidat OA n'a été ajouté.
    db.expire_all()
    refreshed = {
        int(article.id): article
        for article in db.query(Article).filter(Article.id.in_(remaining_ids)).all()
    } if remaining_ids else {}
    oa_retry_ids = [
        article_id
        for article_id in remaining_ids
        if _deterministic_candidate_count(refreshed.get(article_id) or by_id.get(article_id)) > 0
    ]
    no_oa_retry_ids = [article_id for article_id in remaining_ids if article_id not in set(oa_retry_ids)]

    direct_oa_rows = execute_stage(
        "DIRECT_OA",
        oa_retry_ids,
        workers,
        lambda article_id: _process_direct_stage(int(project.id), article_id),
    )
    results.extend(row for row in direct_oa_rows if row.get("terminal"))
    remaining_after_oa = [
        int(row["article_id"]) for row in direct_oa_rows if not row.get("terminal")
    ] + no_oa_retry_ids


    def execute_streaming_legal_stage(
        stage_name: str,
        ids: List[int],
        resolver_workers: int,
        extractor_workers: int,
        *,
        search_all: bool,
        final_stage: bool = False,
    ) -> List[Dict[str, Any]]:
        if not ids:
            stages.append({
                "stage": stage_name,
                "input_count": 0,
                "completed_count": 0,
                "terminal_count": 0,
                "next_stage_count": 0,
                "resolved_count": 0,
                "extract_submitted_count": 0,
                "resolver_workers": resolver_workers,
                "extractor_workers": extractor_workers,
                "elapsed_seconds": 0.0,
                "streaming": True,
            })
            return []

        stage_started = time.perf_counter()
        stage_rows: List[Dict[str, Any]] = []
        extraction_futures = {}
        LOGGER.info(
            "[EnnoScholar extraction][%s_STREAM] démarrage articles=%s resolver_workers=%s extractor_workers=%s",
            stage_name, len(ids), resolver_workers, extractor_workers,
        )

        with ThreadPoolExecutor(max_workers=min(max(1, resolver_workers), len(ids))) as resolver_pool, \
             ThreadPoolExecutor(max_workers=min(max(1, extractor_workers), len(ids))) as extractor_pool:
            resolver_futures = {
                resolver_pool.submit(
                    _resolve_mcp_only_stage,
                    int(project.id),
                    article_id,
                    search_all=bool(search_all),
                ): article_id
                for article_id in ids
            }

            for future in as_completed(resolver_futures):
                article_id = resolver_futures[future]
                try:
                    resolved = future.result()
                except Exception as exc:
                    if final_stage:
                        row = _persist_unhandled_failure(int(project.id), article_id, exc)
                        row["terminal"] = True
                    else:
                        row = _persist_stage_pending(
                            int(project.id), article_id, exc,
                            f"Échec résolution {stage_name}; étape suivante planifiée.",
                        )
                    stage_rows.append(row)
                    continue

                if resolved.get("terminal"):
                    stage_rows.append(resolved)
                    continue

                mcp_result = resolved.get("_mcp_result")
                if not isinstance(mcp_result, dict):
                    mcp_result = {
                        "ok": False,
                        "found": False,
                        "status": "missing_pre_resolved_mcp_result",
                        "locations": [],
                    }

                ef = extractor_pool.submit(
                    _extract_pre_resolved_legal_stage,
                    int(project.id),
                    article_id,
                    search_all=bool(search_all),
                    mcp_result=mcp_result,
                )
                extraction_futures[ef] = article_id

            for future in as_completed(extraction_futures):
                article_id = extraction_futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    if final_stage:
                        row = _persist_unhandled_failure(int(project.id), article_id, exc)
                        row["terminal"] = True
                    else:
                        row = _persist_stage_pending(
                            int(project.id), article_id, exc,
                            f"Échec extraction {stage_name}; étape suivante planifiée.",
                        )
                stage_rows.append(row)

        terminal_count = sum(1 for item in stage_rows if item.get("terminal"))
        elapsed = round(time.perf_counter() - stage_started, 3)
        stages.append({
            "stage": stage_name,
            "input_count": len(ids),
            "completed_count": len(stage_rows),
            "terminal_count": terminal_count,
            "next_stage_count": len(stage_rows) - terminal_count,
            "resolved_count": len(ids),
            "extract_submitted_count": len(extraction_futures),
            "resolver_workers": resolver_workers,
            "extractor_workers": extractor_workers,
            "elapsed_seconds": elapsed,
            "streaming": True,
        })
        LOGGER.info(
            "[EnnoScholar extraction][%s_STREAM] terminé terminaux=%s suivants=%s durée=%.1fs",
            stage_name, terminal_count, len(stage_rows) - terminal_count, elapsed,
        )
        return stage_rows

    # 3) MCP ciblé streaming : résolution et extraction dans deux pools distincts.
    extract_workers = max(
        1,
        min(int(os.getenv("ENNOSCHOLAR_PREFLIGHT_EXTRACT_WORKERS", "8")), 12),
    )
    targeted_rows = execute_streaming_legal_stage(
        "MCP_TARGETED",
        remaining_after_oa,
        TARGETED_LEGAL_CONCURRENCY,
        extract_workers,
        search_all=False,
    )
    results.extend(row for row in targeted_rows if row.get("terminal"))
    targeted_failed_ids = [
        int(row["article_id"]) for row in targeted_rows if not row.get("terminal")
    ]

    # 4) Recherche large seulement pour les articles à fort enjeu, avec budget.
    deep_candidates = [
        by_id[article_id]
        for article_id in targeted_failed_ids
        if article_id in by_id and _deep_mcp_is_eligible(by_id[article_id])
    ]
    deep_candidates.sort(key=_deep_mcp_priority)
    deep_ids = [int(article.id) for article in deep_candidates[:deep_max]]
    deep_id_set = set(deep_ids)
    fast_finalize_ids = [
        article_id for article_id in targeted_failed_ids if article_id not in deep_id_set
    ]

    broad_rows = execute_streaming_legal_stage(
        "MCP_LARGE",
        deep_ids,
        BROAD_LEGAL_CONCURRENCY,
        extract_workers,
        search_all=True,
        final_stage=True,
    )
    results.extend(broad_rows)

    finalized_rows = execute_stage(
        "FINALIZE_FAST",
        fast_finalize_ids,
        workers,
        lambda article_id: _finalize_without_deep_mcp(
            int(project.id),
            article_id,
            reason="deep_search_budget_exhausted_or_low_priority",
        ),
        final_stage=True,
    )
    results.extend(finalized_rows)

    # Sécurité : tout article doit avoir exactement un résultat terminal.
    terminal_ids = {int(row["article_id"]) for row in results if row.get("article_id") is not None}
    missing_terminal_ids = [article_id for article_id in article_ids if article_id not in terminal_ids]
    if missing_terminal_ids:
        safety_rows = execute_stage(
            "FINALIZE_SAFETY",
            missing_terminal_ids,
            workers,
            lambda article_id: _finalize_without_deep_mcp(
                int(project.id), article_id, reason="preflight_terminal_safety_finalize"
            ),
            final_stage=True,
        )
        results.extend(safety_rows)

    # Dédupliquer par article en conservant le dernier résultat terminal.
    terminal_by_id: Dict[int, Dict[str, Any]] = {}
    for row in results:
        try:
            terminal_by_id[int(row.get("article_id"))] = row
        except Exception:
            continue
    results = [terminal_by_id[article_id] for article_id in article_ids if article_id in terminal_by_id]

    counts = _counts(results)
    fulltext_count = counts.get("FULLTEXT_READY", 0)
    by_verrou_fulltext = _fulltext_by_verrou(results)
    coverage_gaps = {
        key: {"target": needed, "actual": by_verrou_fulltext.get(key, 0)}
        for key, needed in coverage_target.items()
        if by_verrou_fulltext.get(key, 0) < needed
    }

    return {
        "enabled": True,
        "mode": "staged_fulltext_preflight_v6_3_streaming",
        "exhaustive": True,
        "pipeline_stages": [
            "CACHE",
            "DIRECT_KNOWN",
            "OA_DISCOVERY",
            "DIRECT_OA",
            "MCP_TARGETED",
            "MCP_LARGE",
            "FINALIZE_FAST",
        ],
        "processed": len(results),
        "available_candidates": len(rows),
        "max_scan": len(rows),
        "workers": workers,
        "configured_workers": configured_workers,
        "targeted_legal_workers": TARGETED_LEGAL_CONCURRENCY,
        "broad_legal_workers": BROAD_LEGAL_CONCURRENCY,
        "deep_mcp_scope": str(os.getenv("ENNOSCHOLAR_DEEP_MCP_SCOPE", "important")),
        "deep_mcp_max_articles": deep_max,
        "deep_mcp_processed": len(deep_ids),
        "deep_mcp_skipped": len(fast_finalize_ids),
        "target_fulltext": target_fulltext,
        "fulltext_ready_count": fulltext_count,
        "target_reached": fulltext_count >= target_fulltext,
        "min_fulltext_per_verrou": min_per_verrou,
        "fulltext_by_verrou": by_verrou_fulltext,
        "coverage_target": coverage_target,
        "coverage_gaps": coverage_gaps,
        "coverage_satisfied": not coverage_gaps,
        "counts": counts,
        "batches": stages,
        "results": results,
        "not_checked_count": 0,
        "needs_more_discovery": False,
    }

def _save_report_on_run(db: Session, run: ScholarRun, report: Dict[str, Any]) -> None:
    raw = dict(run.raw_result_json or {})
    raw["evidence_preflight"] = report
    run.raw_result_json = raw
    db.add(run)
    db.commit()
    db.refresh(run)

def _mark_run_queued(db: Session, run: ScholarRun) -> int:
    rows = db.query(Article).filter(Article.scholar_run_id == run.id).all()
    queued = 0
    for article in rows:
        current = (
            (article.source_json or {}).get("evidence_preflight")
            if isinstance(article.source_json, dict)
            else None
        )
        if isinstance(current, dict) and current.get("evidence_status") == "FULLTEXT_READY":
            continue

        pending_result = {
            "ok": False,
            "status": "access_check_queued",
            "full_text_status": "not_checked",
            "needs_legal_recovery": False,
        }
        evidence = _classify(article, pending_result)
        evidence.update({
            "evidence_status": "ACCESS_CHECKING",
            "evidence_ui_status": "checking_access",
            "evidence_label": "Verification de l'acces direct puis MCP si necessaire",
            "reason_code": "ACCESS_CHECK_PENDING",
            "reason_detail": "Recherche d'une copie publique ; les echecs seront verifies par le MCP avant conclusion.",
            "recommended_action": "Attendre le statut final puis cliquer sur un article accessible pour l'extraire.",
            "access_kind": "pending",
        })
        _set_article_evidence(db, article, evidence, pending_result)
        queued += 1
    db.commit()
    return queued

def run_or_queue_preflight(db: Session, project: Project, run: ScholarRun) -> Dict[str, Any]:
    try:
        from services.research_runtime import celery_preflight_enabled
        use_celery = celery_preflight_enabled()
    except Exception:
        use_celery = False

    if use_celery:
        try:
            from worker.tasks import preflight_run
            queued_count = _mark_run_queued(db, run)
            task = preflight_run.delay(int(project.id), int(run.id))
            report = {
                "enabled": True,
                "mode": "celery_on_demand_access_preflight_with_mcp_v2",
                "status": "queued",
                "task_id": task.id,
                "queued_articles": queued_count,
                "extraction_policy": "on_click_only",
            }
            _save_report_on_run(db, run, report)
            return report
        except Exception as exc:
            fallback_reason = f"{type(exc).__name__}: {exc}"
    else:
        fallback_reason = "redis_or_celery_unavailable"

    report = inspect_scholar_run_access(db, project, run)
    report["execution_mode"] = "synchronous_fallback"
    report["async_fallback_reason"] = fallback_reason
    _save_report_on_run(db, run, report)
    return report
