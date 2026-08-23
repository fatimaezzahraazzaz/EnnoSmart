# -*- coding: utf-8 -*-
from __future__ import annotations

"""Récupération légale ciblée des textes intégraux EnnoScholar.

Cette phase intervient uniquement après l'extraction directe :
- un succès direct dont l'identité du contenu est vérifiée n'est jamais envoyé au MCP ;
- un échec direct avec ``needs_legal_recovery=true`` est envoyé au MCP ;
- un ancien faux succès direct est invalidé puis envoyé au MCP ;
- plusieurs candidats MCP vérifiés sont essayés jusqu'à obtenir un PDF extractible
  dont le contenu correspond réellement à l'article ;
- les erreurs transitoires restent à relancer et ne deviennent pas immédiatement
  des demandes d'upload consultant.

Aucun PDF source distant n'est conservé : seul le JSON du texte extrait est écrit.
"""

import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from db.models import Article, Project, ScholarRun
from services.http_client import GLOBAL_FETCHER
from services.scholar_direct_fulltext_service import _extract_pdf_fulltext
from services.scholar_fulltext_identity import verify_article_extraction
from services.scholar_legal_fulltext_bridge import build_mcp_diagnostic, resolve_mcp_for_article
from services.scholar_selection_scope import get_current_selected_articles


LEGAL_PIPELINE = "legal_mcp_fulltext_v4_generic_publisher_discovery"
DIRECT_PIPELINE = "direct_known_urls_fulltext_v1"
MIN_USEFUL_TEXT_CHARS = int(os.getenv("ENNOSCHOLAR_MIN_USEFUL_FULLTEXT_CHARS", "1000"))
MAX_REMOTE_BYTES = int(os.getenv("ENNOSCHOLAR_REMOTE_CONTENT_MAX_BYTES", str(100 * 1024 * 1024)))
MAX_RECOVERY_ATTEMPTS = max(1, min(int(os.getenv("ENNOSCHOLAR_LEGAL_RECOVERY_MAX_ATTEMPTS", "1")), 2))
RETRY_DELAYS_SECONDS = (0.0, 1.0)

_TRANSIENT_FAILURE_CODES = {
    "provider_temporarily_unavailable",
    "mcp_unavailable",
    "mcp_client_error",
    "rate_limited",
    "temporarily_unavailable",
    "network_request_failed",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: Any, max_chars: int = 0) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars].strip() if max_chars and len(text) > max_chars else text


def _slug(value: Any, max_len: int = 80) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return (re.sub(r"_+", "_", text).strip("_") or "unknown")[:max_len]


def _project_ennoscholar_dir(project: Project) -> Path:
    default_storage = Path(__file__).resolve().parents[2] / "storage"
    root = Path(os.getenv("ENNOSMART_STORAGE_ROOT") or default_storage)
    return (
        root
        / "organismes" / _slug(getattr(project, "organisme", ""))
        / "projects" / _slug(getattr(project, "project_name", ""))
        / "years" / _slug(getattr(project, "year", ""))
        / "ennoscholar"
    )


def _verified_legal_pdf_path(project: Project, article: Article) -> Path:
    """Chemin de la copie légale, uniquement après vérification d'identité."""

    return (
        _project_ennoscholar_dir(project)
        / "fulltext"
        / "legal_pdf"
        / f"article_{int(article.id)}_{_slug(article.title, 60)}.pdf"
    )


def _article_prefix(article: Article) -> str:
    return f"article_{article.id}_{_slug(article.title or 'article', 60)}"


def _fulltext_paths(project: Project, article: Article) -> Dict[str, Path]:
    base = _project_ennoscholar_dir(project) / "fulltext"
    prefix = _article_prefix(article)
    return {
        "base": base,
        "direct": base / "extracted_direct" / f"{prefix}_direct_fulltext.json",
        "uploaded": base / "extracted_uploaded" / f"{prefix}_uploaded_fulltext.json",
        "legal": base / "extracted_legal" / f"{prefix}_legal_fulltext.json",
        "legal_status": base / "legal_status" / f"{prefix}_legal_status.json",
        "report": base / "unified_legal_recovery_report.json",
    }


def _find_article_json_by_suffix(
    folder: Path,
    article: Article,
    suffix: str,
) -> Optional[Path]:
    """Retrouve un JSON historique par article_id et suffixe fonctionnel.

    Cette recherche rend le service compatible avec :
    - les anciens doubles underscores avant le suffixe ;
    - les changements de longueur de slug ;
    - les changements de normalisation du titre ;
    - les fichiers générés par une ancienne version du pipeline.

    La recherche reste strictement limitée à l'identifiant de l'article et au
    suffixe attendu, ce qui évite d'associer le texte d'un autre article.
    """
    if not folder.exists() or not folder.is_dir():
        return None

    article_id = int(article.id)
    normalized_suffix = str(suffix or "").lstrip("_")
    if not normalized_suffix:
        return None

    pattern = re.compile(
        rf"^article_{article_id}_.+_+{re.escape(normalized_suffix)}$",
        flags=re.IGNORECASE,
    )

    matches = [
        path
        for path in folder.glob(f"article_{article_id}_*.json")
        if path.is_file() and pattern.match(path.name)
    ]

    if not matches:
        return None

    # Préférer le nom canonique le plus propre, puis le nom le plus court.
    matches.sort(
        key=lambda path: (
            "__" in path.name,
            path.name.count("__"),
            len(path.name),
            path.name.casefold(),
        )
    )
    return matches[0]


def _resolve_existing_fulltext_path(
    canonical_path: Path,
    article: Article,
    suffix: str,
) -> Path:
    """Retourne le chemin canonique s'il existe, sinon un ancien chemin compatible."""
    if canonical_path.exists() and canonical_path.is_file():
        return canonical_path

    legacy_path = _find_article_json_by_suffix(
        folder=canonical_path.parent,
        article=article,
        suffix=suffix,
    )
    return legacy_path or canonical_path


def _json_read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else None
    except Exception:
        return None
    return None


def _json_dump(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _selected_articles(db: Session, project: Project) -> List[Article]:
    return get_current_selected_articles(db, project)


def _article_for_project(db: Session, project: Project, article_id: int) -> Article:
    article = (
        db.query(Article)
        .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
        .filter(ScholarRun.project_id == project.id)
        .filter(Article.id == article_id)
        .first()
    )
    if not article:
        raise ValueError(f"Article {article_id} introuvable pour le projet {project.id}")
    return article


def _identity_is_verified(payload: Dict[str, Any]) -> bool:
    identity = payload.get("identity_verification")
    return bool(
        isinstance(identity, dict)
        and identity.get("verified") is True
        and identity.get("same_article") is True
    )


def _is_text_success(payload: Optional[Dict[str, Any]], *, require_identity: bool) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("full_text_status") != "text_extracted":
        return False
    if int(payload.get("text_chars") or 0) < MIN_USEFUL_TEXT_CHARS:
        return False
    return _identity_is_verified(payload) if require_identity else True


def _save_legal_result(project: Project, article: Article, result: Dict[str, Any]) -> Dict[str, Any]:
    paths = _fulltext_paths(project, article)
    result["output_path"] = str(paths["legal"])
    _json_dump(paths["legal"], result)
    _json_dump(paths["legal_status"], result)
    return result


def _audit_direct_result(project: Project, article: Article) -> Optional[Dict[str, Any]]:
    """Ajoute une vérification de contenu ou invalide un ancien faux succès."""
    path = _fulltext_paths(project, article)["direct"]
    direct = _json_read(path)
    if not isinstance(direct, dict):
        return None
    if direct.get("full_text_status") != "text_extracted":
        return direct
    if _identity_is_verified(direct):
        return direct

    identity = verify_article_extraction(article, direct)
    direct["identity_verification"] = identity
    direct["identity_checked_at"] = _utc_now()

    if not identity.get("same_article"):
        direct.update(
            {
                "ok": False,
                "status": "pdf_identity_mismatch",
                "full_text_status": "wrong_document_rejected",
                "evidence_level": "rejected_wrong_document",
                "needs_legal_recovery": True,
                "needs_consultant_upload": False,
                "message": "Le document extrait ne correspond pas de façon fiable à l'article sélectionné.",
            }
        )
        direct.setdefault("classification", {}).update(
            {
                "status": "pdf_identity_mismatch",
                "needs_legal_recovery": True,
                "needs_consultant_upload": False,
            }
        )
    else:
        direct["needs_legal_recovery"] = False
        direct["needs_consultant_upload"] = False

    _json_dump(path, direct)
    # Le statut direct officiel doit refléter la même décision.
    status_dir = _fulltext_paths(project, article)["base"] / "status"
    status_path = status_dir / f"{_article_prefix(article)}_status.json"
    _json_dump(status_path, direct)
    return direct


def _mcp_candidates(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw: List[Dict[str, Any]] = []
    best = result.get("best_candidate")
    if isinstance(best, dict):
        raw.append(best)
    for item in result.get("locations") or []:
        if isinstance(item, dict):
            raw.append(item)

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in raw:
        url = _safe_text(candidate.get("final_url") or candidate.get("pdf_url"), 4000)
        if not url or not url.startswith(("http://", "https://")):
            continue
        if candidate.get("legal_access") is not True:
            continue
        raw_metadata = (
            candidate.get("raw_metadata")
            if isinstance(candidate.get("raw_metadata"), dict)
            else {}
        )
        html_text = _safe_text(
            candidate.get("full_text") or raw_metadata.get("full_text")
        )
        verified_html = bool(
            raw_metadata.get("verified_html_fulltext") is True
            and len(html_text) >= MIN_USEFUL_TEXT_CHARS
        )
        if candidate.get("same_article") is not True or not (
            candidate.get("verified_pdf") is True or verified_html
        ):
            continue
        key = url.casefold()
        if key in seen:
            continue
        seen.add(key)
        item = dict(candidate)
        item["download_url"] = url
        item["content_source_kind"] = "html" if verified_html else "pdf"
        if verified_html:
            item["verified_html_fulltext"] = True
            item["full_text"] = html_text
        out.append(item)
    return out


def _transient_mcp_failure(result: Dict[str, Any]) -> bool:
    if result.get("retry_recommended") is True:
        return True
    failure_code = _safe_text(result.get("failure_code") or result.get("status"), 200).lower()
    if failure_code in _TRANSIENT_FAILURE_CODES:
        return True
    return any(
        isinstance(attempt, dict) and attempt.get("transient") is True
        for attempt in result.get("attempts") or []
    )


def _candidate_audit(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": candidate.get("provider"),
        "url": candidate.get("download_url") or candidate.get("final_url") or candidate.get("pdf_url"),
        "license": candidate.get("license"),
        "version": candidate.get("version"),
        "access_type": candidate.get("access_type"),
        "rights_status": candidate.get("rights_status"),
        "source_domain": candidate.get("source_domain"),
        "identity_score": candidate.get("identity_score"),
        "identity_method": candidate.get("identity_method"),
        "same_article": candidate.get("same_article"),
        "verified_pdf": candidate.get("verified_pdf"),
    }


def recover_legal_fulltext_for_article(
    db: Session,
    project: Project,
    article_id: int,
    *,
    force_refresh: bool = False,
    search_all: bool = False,
    expand_on_failure: bool = True,
    pre_resolved_mcp_result: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    article = _article_for_project(db, project, article_id)
    paths = _fulltext_paths(project, article)

    uploaded = _json_read(paths["uploaded"])
    if _is_text_success(uploaded, require_identity=False):
        return {
            "ok": True,
            "article_id": article.id,
            "title": article.title,
            "status": "skipped_uploaded_fulltext_exists",
            "full_text_status": "text_extracted",
            "retrieval_stage": "uploaded",
            "mcp_called": False,
            "needs_legal_recovery": False,
            "needs_consultant_upload": False,
        }

    direct = _audit_direct_result(project, article)
    if _is_text_success(direct, require_identity=True):
        return {
            "ok": True,
            "article_id": article.id,
            "title": article.title,
            "status": "skipped_direct_verified_fulltext_exists",
            "full_text_status": "text_extracted",
            "retrieval_stage": "direct_verified",
            "mcp_called": False,
            "identity_verification": direct.get("identity_verification"),
            "needs_legal_recovery": False,
            "needs_consultant_upload": False,
        }

    # En force_refresh, tout article sans texte intégral vérifié devient
    # éligible au MCP, même si un ancien statut direct n'avait pas positionné
    # needs_legal_recovery. Cela évite les articles "not_targeted" oubliés.
    direct_requires_recovery = bool(
        isinstance(direct, dict) and direct.get("needs_legal_recovery") is True
    )
    if not direct_requires_recovery and not force_refresh:
        return {
            "ok": False,
            "article_id": article.id,
            "title": article.title,
            "status": "not_targeted_no_direct_legal_recovery_flag",
            "full_text_status": direct.get("full_text_status") if isinstance(direct, dict) else "not_checked",
            "mcp_called": False,
            "needs_legal_recovery": False,
            "needs_consultant_upload": False,
            "retry_recommended": False,
            "finalized": True,
        }

    existing_legal = _json_read(paths["legal"])
    if not force_refresh and _is_text_success(existing_legal, require_identity=True):
        existing_legal["reused_existing_legal_text"] = True
        existing_legal["mcp_called"] = False
        return existing_legal

    mcp_result: Dict[str, Any] = {}
    mcp_run_attempts: List[Dict[str, Any]] = []

    if isinstance(pre_resolved_mcp_result, dict):
        mcp_result = dict(pre_resolved_mcp_result)
        mcp_run_attempts.append({
            "attempt_number": 0,
            "status": mcp_result.get("status"),
            "failure_code": mcp_result.get("failure_code"),
            "found": bool(mcp_result.get("found")),
            "retry_recommended": bool(mcp_result.get("retry_recommended")),
            "pre_resolved": True,
        })
    else:
        for attempt_number in range(1, MAX_RECOVERY_ATTEMPTS + 1):
            delay = RETRY_DELAYS_SECONDS[min(attempt_number - 1, len(RETRY_DELAYS_SECONDS) - 1)]
            if delay > 0:
                time.sleep(delay)
            mcp_result = resolve_mcp_for_article(
                article,
                force=True if attempt_number > 1 else force_refresh,
                search_all=search_all,
            )
            mcp_run_attempts.append({
                "attempt_number": attempt_number,
                "status": mcp_result.get("status"),
                "failure_code": mcp_result.get("failure_code"),
                "found": bool(mcp_result.get("found")),
                "retry_recommended": bool(mcp_result.get("retry_recommended")),
            })
            if _mcp_candidates(mcp_result):
                break
            if not _transient_mcp_failure(mcp_result):
                break

    mcp_diagnostic = build_mcp_diagnostic(mcp_result, max_attempts=40, max_locations=60)
    mcp_diagnostic["backend_recovery_attempts"] = mcp_run_attempts
    mcp_diagnostic["backend_recovery_attempts_count"] = len(mcp_run_attempts)
    candidates = _mcp_candidates(mcp_result)
    candidate_attempts: List[Dict[str, Any]] = []

    for candidate in candidates:
        url = candidate["download_url"]
        landing_url = _safe_text(candidate.get("landing_url"), 4000)

        if candidate.get("verified_html_fulltext") is True:
            html_text = _safe_text(candidate.get("full_text"))
            extraction = {
                "ok": True,
                "pages": [{"page_number": 1, "text": html_text}],
                "pages_count": 1,
                "full_text": html_text,
                "text_chars": len(html_text),
                "extraction_method": "publisher_html_native",
            }
            identity = verify_article_extraction(
                article,
                extraction,
                resolver_candidate=candidate,
            )
            attempt = {
                **_candidate_audit(candidate),
                "status": (
                    "accepted_verified_html_fulltext"
                    if identity.get("same_article")
                    else "html_identity_mismatch"
                ),
                "text_chars": len(html_text),
                "identity_verification": identity,
            }
            candidate_attempts.append(attempt)
            if not identity.get("same_article"):
                continue

            result: Dict[str, Any] = {
                **extraction,
                "article_id": article.id,
                "title": article.title,
                "doi": article.doi,
                "url": article.url,
                "source": article.source,
                "tag": article.tag_article,
                "status": "legal_html_fulltext_extracted",
                "full_text_status": "text_extracted",
                "evidence_level": "full_text",
                "content_source_kind": "html",
                "retrieval_stage": "legal_mcp_recovery",
                "retrieved_via_mcp": True,
                "mcp_called": True,
                "mcp_status": mcp_result.get("status"),
                "mcp_resolver_version": mcp_result.get("resolver_version"),
                "legal_provider": candidate.get("provider"),
                "legal_access": True,
                "license": candidate.get("license"),
                "version": candidate.get("version"),
                "host_type": candidate.get("host_type"),
                "discovered_via": candidate.get("discovered_via"),
                "access_type": candidate.get("access_type"),
                "rights_status": candidate.get("rights_status"),
                "source_domain": candidate.get("source_domain"),
                "fulltext_source_url": url,
                "fulltext_final_url": url,
                "remote_sha256": hashlib.sha256(html_text.encode("utf-8")).hexdigest(),
                "storage_mode": "json_only_remote_source_not_saved",
                "saved_pdf": False,
                "needs_legal_recovery": False,
                "needs_consultant_upload": False,
                "retry_recommended": False,
                "finalized": True,
                "identity_verification": identity,
                "mcp_diagnostic": mcp_diagnostic,
                "candidate_attempts": candidate_attempts,
                "generated_at": _utc_now(),
                "pipeline": LEGAL_PIPELINE,
            }
            return _save_legal_result(project, article, result)

        browser_headers = {
            "Accept": "application/pdf,application/octet-stream,text/html;q=0.4,*/*;q=0.2",
            "User-Agent": os.getenv(
                "ENNOSCHOLAR_BROWSER_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
            ),
            "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
            "Cache-Control": "no-cache",
        }
        if landing_url:
            browser_headers["Referer"] = landing_url

        ok, remote_info, content = GLOBAL_FETCHER.fetch_bytes(
            url=url,
            headers=browser_headers,
            max_bytes=MAX_REMOTE_BYTES,
        )
        # Certains éditeurs refusent un Referer externe ou exigent au contraire
        # le DOI comme origine. Une seconde tentative reste générique et ne
        # contient aucun chemin d'article codé en dur.
        if not ok or not content:
            fallback_headers = dict(browser_headers)
            if landing_url:
                fallback_headers.pop("Referer", None)
            ok, remote_info, content = GLOBAL_FETCHER.fetch_bytes(
                url=url,
                headers=fallback_headers,
                max_bytes=MAX_REMOTE_BYTES,
            )
        attempt: Dict[str, Any] = {
            **_candidate_audit(candidate),
            "download": remote_info,
        }
        if not ok or not content:
            attempt["status"] = "candidate_download_failed"
            candidate_attempts.append(attempt)
            continue
        if not content.startswith(b"%PDF-"):
            attempt["status"] = "candidate_not_pdf"
            candidate_attempts.append(attempt)
            continue

        # _extract_pdf_fulltext() exécute déjà la chaîne native -> OCR si
        # nécessaire -> GROBID fallback. Ne jamais rappeler GROBID ici :
        # l'ancien double fallback pouvait doubler le coût d'un PDF difficile.
        extraction = _extract_pdf_fulltext(content)
        if not extraction.get("ok"):
            attempt["status"] = extraction.get("status") or "candidate_extraction_failed"
            attempt["text_chars"] = extraction.get("text_chars")
            attempt["grobid_fallback"] = extraction.get("grobid_fallback")
            candidate_attempts.append(attempt)
            continue

        identity = verify_article_extraction(article, extraction, resolver_candidate=candidate)
        attempt["identity_verification"] = identity
        attempt["text_chars"] = extraction.get("text_chars")
        if not identity.get("same_article"):
            attempt["status"] = "pdf_identity_mismatch"
            candidate_attempts.append(attempt)
            continue

        attempt["status"] = "accepted_verified_fulltext"
        candidate_attempts.append(attempt)
        # La copie n'est conservée qu'après les contrôles ``verified_pdf`` et
        # ``same_article`` ci-dessus. Elle permet ensuite d'extraire les
        # figures originales sans refaire une recherche ni une analyse vision.
        local_pdf_path = _verified_legal_pdf_path(project, article)
        local_pdf_path.parent.mkdir(parents=True, exist_ok=True)
        local_pdf_path.write_bytes(content)
        result: Dict[str, Any] = {
            **extraction,
            "ok": True,
            "article_id": article.id,
            "title": article.title,
            "doi": article.doi,
            "url": article.url,
            "source": article.source,
            "tag": article.tag_article,
            "status": "legal_pdf_fulltext_extracted",
            "full_text_status": "text_extracted",
            "evidence_level": "full_text",
            "content_source_kind": "pdf",
            "extraction_method": extraction.get("extraction_method") or "native",
            "retrieval_stage": "legal_mcp_recovery",
            "retrieved_via_mcp": True,
            "mcp_called": True,
            "mcp_status": mcp_result.get("status"),
            "mcp_resolver_version": mcp_result.get("resolver_version"),
            "mcp_locations_count": len(mcp_result.get("locations") or []),
            "mcp_verified_candidates_count": sum(
                1
                for item in (mcp_result.get("locations") or [])
                if isinstance(item, dict)
                and item.get("verified_pdf") is True
                and item.get("same_article") is True
            ),
            "legal_provider": candidate.get("provider"),
            "legal_access": True,
            "license": candidate.get("license"),
            "version": candidate.get("version"),
            "host_type": candidate.get("host_type"),
            "discovered_via": candidate.get("discovered_via"),
            "verified_pdf": True,
            "same_article": True,
            "identity_score": identity.get("score"),
            "identity_method": identity.get("method"),
            "access_type": candidate.get("access_type"),
            "rights_status": candidate.get("rights_status"),
            "source_domain": candidate.get("source_domain"),
            "fulltext_source_url": candidate.get("pdf_url") or url,
            "fulltext_final_url": remote_info.get("final_url") or url,
            "remote_bytes_read": remote_info.get("content_bytes"),
            "remote_sha256": remote_info.get("remote_sha256") or hashlib.sha256(content).hexdigest(),
            "storage_mode": "verified_legal_pdf_and_extracted_json",
            "saved_pdf": True,
            "local_pdf_path": str(local_pdf_path),
            "needs_legal_recovery": False,
            "needs_consultant_upload": False,
            "retry_recommended": False,
            "finalized": True,
            "identity_verification": identity,
            "mcp_diagnostic": mcp_diagnostic,
            "candidate_attempts": candidate_attempts,
            "generated_at": _utc_now(),
            "pipeline": LEGAL_PIPELINE,
        }
        return _save_legal_result(project, article, result)

    # V5.3 — seconde passe légale élargie automatique.
    # La première passe peut trouver un mauvais PDF. On élargit une seule fois.
    if expand_on_failure and not search_all:
        expanded_result = recover_legal_fulltext_for_article(
            db,
            project,
            article.id,
            force_refresh=True,
            search_all=True,
            expand_on_failure=False,
        )
        if isinstance(expanded_result, dict):
            expanded_result = dict(expanded_result)
            expanded_result["expanded_search_triggered"] = True
            expanded_result["initial_candidate_attempts"] = candidate_attempts
            expanded_result["initial_mcp_status"] = mcp_result.get("status")
            return _save_legal_result(project, article, expanded_result)

    verified_candidate_count = sum(
        1
        for item in (mcp_result.get("locations") or [])
        if isinstance(item, dict)
        and item.get("verified_pdf") is True
        and item.get("same_article") is True
    )
    mismatch_attempts = [
        item for item in candidate_attempts
        if isinstance(item, dict) and str(item.get("status") or "").endswith("identity_mismatch")
    ]
    identity_mismatch_only = bool(candidate_attempts) and len(mismatch_attempts) == len(candidate_attempts)

    if identity_mismatch_only:
        final_status = "legal_candidates_identity_mismatch"
        final_failure_code = "candidate_identity_mismatch"
        message = (
            "Des documents légaux ont été trouvés et lus, mais aucun ne correspond "
            "avec une identité suffisante à l'article recherché."
        )
    elif verified_candidate_count > 0 or candidates:
        final_status = "legal_pdf_found_but_extraction_failed"
        final_failure_code = "verified_pdf_extraction_failed"
        message = (
            "Une copie légale vérifiée a été trouvée, mais aucun candidat n'a "
            "produit un texte intégral exploitable après toutes les tentatives."
        )
    else:
        final_status = "no_legal_copy_found"
        final_failure_code = "exhausted_legal_search"
        message = (
            "Aucune copie légale fiable et correspondant au même article n'a "
            "été récupérée après toutes les tentatives disponibles."
        )

    result = {
        "ok": False,
        "article_id": article.id,
        "title": article.title,
        "doi": article.doi,
        "status": final_status,
        "full_text_status": "missing_legal_fulltext",
        "retrieval_stage": "legal_mcp_recovery",
        "retrieved_via_mcp": False,
        "mcp_called": True,
        "mcp_status": mcp_result.get("status"),
        "mcp_failure_code": mcp_result.get("failure_code"),
        "final_failure_code": final_failure_code,
        "message": message,
        "needs_legal_recovery": False,
        "retry_recommended": False,
        "needs_consultant_upload": True,
        "finalized": True,
        "unclassified": False,
        "mcp_diagnostic": mcp_diagnostic,
        "candidate_attempts": candidate_attempts,
        "generated_at": _utc_now(),
        "pipeline": LEGAL_PIPELINE,
    }
    return _save_legal_result(project, article, result)


def recover_legal_fulltext_for_problem_articles(
    db: Session,
    project: Project,
    *,
    force_refresh: bool = False,
    search_all: bool = False,
    max_articles: Optional[int] = None,
) -> Dict[str, Any]:
    articles = _selected_articles(db, project)
    selected = articles[:max_articles] if max_articles and max_articles > 0 else articles
    results: List[Dict[str, Any]] = []

    for article in selected:
        try:
            results.append(
                recover_legal_fulltext_for_article(
                    db,
                    project,
                    article.id,
                    force_refresh=force_refresh,
                    search_all=search_all,
                )
            )
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "article_id": article.id,
                    "title": article.title,
                    "status": "legal_recovery_internal_error",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "mcp_called": False,
                    "retry_recommended": False,
                    "needs_legal_recovery": False,
                    "needs_consultant_upload": True,
                    "finalized": True,
                    "unclassified": False,
                }
            )

    targeted = [r for r in results if r.get("mcp_called") is True or r.get("reused_existing_legal_text") is True]
    verified = [r for r in results if r.get("full_text_status") == "text_extracted"]
    report = {
        "ok": True,
        "project_id": project.id,
        "pipeline": LEGAL_PIPELINE,
        "selected_articles_count": len(articles),
        "processed_articles_count": len(selected),
        "targeted_problem_articles_count": len(targeted),
        "mcp_called_count": sum(1 for r in results if r.get("mcp_called") is True),
        "skipped_direct_or_uploaded_count": sum(1 for r in results if str(r.get("status") or "").startswith("skipped_")),
        "legal_text_extracted_count": sum(
            1
            for r in results
            if r.get("status")
            in {"legal_pdf_fulltext_extracted", "legal_html_fulltext_extracted"}
        ),
        "reused_legal_text_count": sum(1 for r in results if r.get("reused_existing_legal_text") is True),
        "all_verified_fulltext_visible_in_this_run_count": len(verified),
        "retry_pending_count": 0,
        "all_results_finalized": all(r.get("retry_recommended") is not True for r in results),
        "extraction_failed_count": sum(1 for r in results if r.get("status") == "legal_pdf_found_but_extraction_failed"),
        "no_legal_copy_found_count": sum(1 for r in results if r.get("status") == "no_legal_copy_found"),
        "consultant_upload_required_count": sum(1 for r in results if r.get("needs_consultant_upload") is True),
        "unclassified_count": sum(1 for r in results if r.get("unclassified") is True),
        "results": results,
        "generated_at": _utc_now(),
    }
    report_path = _project_ennoscholar_dir(project) / "fulltext" / "unified_legal_recovery_report.json"
    _json_dump(report_path, report)
    report["report_path"] = str(report_path)
    return report


def get_combined_fulltext_status_for_selected_articles(db: Session, project: Project) -> Dict[str, Any]:
    """État final unique : upload > direct vérifié > MCP légal vérifié > échec."""
    articles = _selected_articles(db, project)
    rows: List[Dict[str, Any]] = []

    for article in articles:
        paths = _fulltext_paths(project, article)

        uploaded_path = _resolve_existing_fulltext_path(
            canonical_path=paths["uploaded"],
            article=article,
            suffix="uploaded_fulltext.json",
        )
        direct_path = _resolve_existing_fulltext_path(
            canonical_path=paths["direct"],
            article=article,
            suffix="direct_fulltext.json",
        )
        legal_path = _resolve_existing_fulltext_path(
            canonical_path=paths["legal"],
            article=article,
            suffix="legal_fulltext.json",
        )

        uploaded = _json_read(uploaded_path)

        # L'audit direct officiel met à jour le fichier canonique. Pour un
        # ancien fichier dont le slug diffère, on le lit sans le perdre.
        if direct_path == paths["direct"]:
            direct = _audit_direct_result(project, article)
        else:
            direct = _json_read(direct_path)
            if isinstance(direct, dict) and direct.get("full_text_status") == "text_extracted":
                if not _identity_is_verified(direct):
                    identity = verify_article_extraction(article, direct)
                    direct["identity_verification"] = identity
                    direct["identity_checked_at"] = _utc_now()
                    if identity.get("same_article") is not True:
                        direct.update(
                            {
                                "ok": False,
                                "status": "pdf_identity_mismatch",
                                "full_text_status": "wrong_document_rejected",
                                "evidence_level": "rejected_wrong_document",
                                "needs_legal_recovery": True,
                                "needs_consultant_upload": False,
                                "message": (
                                    "Le document extrait ne correspond pas de façon fiable "
                                    "à l'article sélectionné."
                                ),
                            }
                        )
                    _json_dump(direct_path, direct)

        legal = _json_read(legal_path)

        if _is_text_success(uploaded, require_identity=False):
            chosen = dict(uploaded)
            chosen["final_category"] = "verified_fulltext"
            chosen["final_source"] = "uploaded"
        elif _is_text_success(direct, require_identity=True):
            chosen = dict(direct)
            chosen["final_category"] = "verified_fulltext"
            chosen["final_source"] = "direct"
        elif _is_text_success(legal, require_identity=True):
            chosen = dict(legal)
            chosen["final_category"] = "verified_fulltext"
            chosen["final_source"] = "legal_mcp"
        elif isinstance(legal, dict) and legal.get("needs_consultant_upload") is True:
            chosen = dict(legal)
            chosen["final_category"] = "needs_consultant_upload"
            chosen["final_source"] = "legal_mcp"
        elif isinstance(direct, dict) and direct.get("needs_legal_recovery") is True:
            chosen = dict(direct)
            chosen["final_category"] = "needs_legal_recovery"
            chosen["final_source"] = "direct"
        else:
            chosen = {
                "article_id": article.id,
                "title": article.title,
                "status": "not_checked",
                "full_text_status": "not_checked",
                "final_category": "not_checked",
                "final_source": None,
                "needs_consultant_upload": False,
            }
        chosen.setdefault("article_id", article.id)
        chosen.setdefault("title", article.title)
        chosen["resolved_paths"] = {
            "uploaded": str(uploaded_path),
            "direct": str(direct_path),
            "legal": str(legal_path),
            "uploaded_exists": uploaded_path.exists(),
            "direct_exists": direct_path.exists(),
            "legal_exists": legal_path.exists(),
        }
        rows.append(chosen)

    categories = {"verified_fulltext", "needs_consultant_upload", "needs_legal_recovery", "not_checked"}
    unclassified = [r for r in rows if r.get("final_category") not in categories]
    return {
        "ok": True,
        "project_id": project.id,
        "pipeline": "combined_direct_legal_uploaded_v2",
        "selected_articles_count": len(articles),
        "verified_fulltext_count": sum(1 for r in rows if r.get("final_category") == "verified_fulltext"),
        # Alias explicite consommé par le frontend.
        "text_extracted_count": sum(1 for r in rows if r.get("final_category") == "verified_fulltext"),
        "direct_verified_count": sum(1 for r in rows if r.get("final_source") == "direct" and r.get("final_category") == "verified_fulltext"),
        "mcp_verified_count": sum(1 for r in rows if r.get("final_source") == "legal_mcp" and r.get("final_category") == "verified_fulltext"),
        "uploaded_verified_count": sum(1 for r in rows if r.get("final_source") == "uploaded" and r.get("final_category") == "verified_fulltext"),
        "retry_pending_count": 0,
        "needs_legal_recovery_count": sum(1 for r in rows if r.get("final_category") == "needs_legal_recovery"),
        "consultant_upload_required_count": sum(1 for r in rows if r.get("final_category") == "needs_consultant_upload"),
        "not_checked_count": sum(1 for r in rows if r.get("final_category") == "not_checked"),
        "unclassified_count": len(unclassified),
        "results": rows,
        "generated_at": _utc_now(),
    }
