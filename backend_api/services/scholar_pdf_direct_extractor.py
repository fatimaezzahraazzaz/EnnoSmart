# -*- coding: utf-8 -*-

"""
scholar_pdf_direct_extractor.py

Phase 2B — Extraction directe du texte depuis PDF distant SANS stocker le PDF.

Objectif :
- prendre les articles gardés par le consultant ;
- réutiliser les candidats PDF trouvés par scholar_fulltext_fetcher ;
- lire le PDF en mémoire ;
- extraire le texte avec PyMuPDF ;
- sauvegarder uniquement le JSON texte extrait ;
- ne pas stocker le PDF sur disque.

Important :
- Si le serveur renvoie une page anti-robot au lieu du PDF, extraction impossible.
- Dans ce cas on retourne : pdf_url_found_but_download_blocked.
"""

import gc
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import hashlib
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from sqlalchemy.orm import Session
from services.http_client import GLOBAL_FETCHER
from db.models import Article, Project, ScholarRun

from services.scholar_fulltext_fetcher import (
    DEFAULT_TIMEOUT,
    HEADERS,
    MAX_PDF_BYTES,
    MAX_PDF_MB,
    USER_AGENT,
    _build_hal_pdf_candidates_for_article,
    _extract_nnt_from_text,
    _extract_pdf_candidates_from_html,
    _fetch_html,
    _json_read,
    _mcp_summary_from_diagnostics,
    _status_path,
    build_candidate_urls_for_article,
)
from services.scholar_selection_scope import get_current_selected_articles


# ============================================================
# Config
# ============================================================

MAX_PAGE_TEXT_CHARS = int(os.getenv("ENNOSCHOLAR_EXTRACT_MAX_PAGE_CHARS", "20000"))
MIN_USEFUL_TEXT_CHARS = int(os.getenv("ENNOSCHOLAR_MIN_USEFUL_FULLTEXT_CHARS", "1000"))
MIN_USEFUL_PAGE_CHARS = int(os.getenv("ENNOSCHOLAR_MIN_USEFUL_PAGE_CHARS", "30"))
OCR_FALLBACK_ENABLED = os.getenv("ENNOSCHOLAR_OCR_FALLBACK_ENABLED", "1") == "1"
OCR_MIXED_PDF_ENABLED = os.getenv("ENNOSCHOLAR_OCR_MIXED_PDF_ENABLED", "1") == "1"
OCR_TEMP_DELETE_RETRIES = int(os.getenv("ENNOSCHOLAR_OCR_TEMP_DELETE_RETRIES", "12"))
OCR_TEMP_DELETE_DELAY_SECONDS = float(os.getenv("ENNOSCHOLAR_OCR_TEMP_DELETE_DELAY_SECONDS", "0.4"))


# ============================================================
# Helpers
# ============================================================

def _safe_text(value: Any, max_chars: int = 0) -> str:
    if value is None:
        return ""

    text = str(value).replace("\x00", " ").strip()
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    if max_chars and len(text) > max_chars:
        return text[:max_chars].strip()

    return text.strip()


def _strip_accents(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _slugify(value: Any, max_len: int = 80) -> str:
    text = _strip_accents(str(value or ""))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")

    if not text:
        text = "unknown"

    return text[:max_len].strip("_")


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))


def _json_dump(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _json_read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    return None


def _project_ennoscholar_dir(project: Project) -> Path:
    root = Path(os.getenv("ENNOSMART_STORAGE_ROOT", "C:/EnnoSmart/storage"))

    organisme = _slugify(getattr(project, "organisme", "") or "organisme")
    project_name = _slugify(getattr(project, "project_name", "") or "project")
    year = _slugify(getattr(project, "year", "") or "year")

    return (
        root
        / "organismes"
        / organisme
        / "projects"
        / project_name
        / "years"
        / year
        / "ennoscholar"
    )


def _fulltext_dirs(project: Project) -> Dict[str, Path]:
    base = _project_ennoscholar_dir(project) / "fulltext"

    return {
        "base": base,
        "extracted_direct": base / "extracted_direct",
        "debug": base / "debug",
    }


def _article_file_prefix(article: Article) -> str:
    title_slug = _slugify(getattr(article, "title", "") or "article", 60)
    return f"article_{article.id}_{title_slug}"


def _direct_extracted_path(project: Project, article: Article) -> Path:
    return (
        _fulltext_dirs(project)["extracted_direct"]
        / f"{_article_file_prefix(article)}_direct_fulltext.json"
    )


def _debug_html_path(project: Project, article: Article) -> Path:
    return (
        _fulltext_dirs(project)["debug"]
        / f"{_article_file_prefix(article)}_not_pdf_debug.html"
    )


def _looks_like_pdf_bytes(content: bytes) -> bool:
    return bool(content and content[:5] == b"%PDF-")


def _is_antibot_html(content: bytes) -> bool:
    try:
        text = content[:5000].decode("utf-8", errors="ignore").lower()
    except Exception:
        return False

    markers = [
        "je m'assure que vous n'êtes pas un robot",
        "je m&#39;assure que vous n&#39;",
        "not a robot",
        "anubis",
        "captcha",
        "bot",
        "robot",
    ]

    return any(marker in text for marker in markers)


def _content_start(content: bytes, max_chars: int = 200) -> str:
    try:
        return content[:max_chars].decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _guess_referer(pdf_url: str) -> str:
    """
    Pour HAL/TEL, on visite la page notice avant le fichier.
    Exemple :
    https://theses.hal.science/tel-04122997/file/x.pdf
    -> https://theses.hal.science/tel-04122997
    """
    try:
        parsed = urlparse(pdf_url)
        parts = parsed.path.strip("/").split("/")

        if parsed.netloc and parts:
            return f"{parsed.scheme}://{parsed.netloc}/{parts[0]}"

        return f"{parsed.scheme}://{parsed.netloc}/"
    except Exception:
        return ""


def _build_theses_diffusion_candidates(nnt: str) -> List[str]:
    """
    Candidats API theses.fr diffusion.
    """
    nnt = _safe_text(nnt, 100).upper()

    if not nnt:
        return []

    return [
        f"https://theses.fr/api/v1/diffusion/{nnt}",
        f"https://theses.fr/api/v1/diffusion/{nnt}/document",
        f"https://theses.fr/api/v1/diffusion/{nnt}/fichier",
    ]


# ============================================================
# DB
# ============================================================

def get_selected_articles_for_project(db: Session, project: Project) -> List[Article]:
    return get_current_selected_articles(db, project)


def get_article_for_project(db: Session, project: Project, article_id: int) -> Article:
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


# ============================================================
# PDF bytes fetch — sans stockage PDF
# ============================================================

def _fetch_pdf_bytes_from_url(
    url: str,
    project: Project,
    article: Article,
    depth: int = 0,
    visited: Optional[set] = None,
) -> Tuple[bool, Dict[str, Any], Optional[bytes]]:
    """
    Récupère les bytes PDF en mémoire, avec une limite MAX_PDF_BYTES.
    Utilise le fetcher unifié.
    """
    if visited is None:
        visited = set()

    if depth > 2:
        return False, {"status": "max_depth_reached", "url": url}, None

    url_key = (url or "").lower().strip()
    if not url_key or url_key in visited:
        return False, {"status": "skipped_duplicate_or_empty", "url": url}, None
    visited.add(url_key)

    referer = _guess_referer(url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        headers["Referer"] = referer

    # Télécharger le PDF entier, mais limité à MAX_PDF_BYTES
    ok, info, pdf_bytes = GLOBAL_FETCHER.fetch_bytes(
        url=url,
        headers=headers,
        max_bytes=MAX_PDF_BYTES,
        referer=referer,
    )

    info["url"] = url
    info["referer"] = referer
    info["saved_pdf"] = False

    if not ok:
        return False, info, None

    # Vérifier si c'est bien un PDF
    if not _looks_like_pdf_bytes(pdf_bytes):
        # Sauvegarder un debug HTML si nécessaire
        try:
            debug_path = _debug_html_path(project, article)
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_bytes(pdf_bytes[:500_000])
            info["debug_html_path"] = str(debug_path)
        except Exception:
            pass

        antibot = _is_antibot_html(pdf_bytes)
        status = "blocked_by_antibot" if antibot else "not_pdf_response"
        info.update({
            "status": status,
            "reason": (
                "Le serveur a renvoyé une page HTML au lieu du PDF."
                if not antibot
                else "PDF trouvé mais téléchargement automatique bloqué par une page anti-robot."
            ),
            "content_start": pdf_bytes[:160].decode("utf-8", errors="ignore") if pdf_bytes else "",
        })

        # Essayer de trouver un lien PDF dans le HTML
        html_text = ""
        try:
            html_text = pdf_bytes.decode("utf-8", errors="ignore")
        except Exception:
            try:
                html_text = pdf_bytes.decode("latin-1", errors="ignore")
            except Exception:
                pass

        if html_text and "<html" in html_text.lower() and not antibot:
            nested_candidates = _extract_pdf_candidates_from_html(
                html=html_text,
                base_url=info.get("final_url") or url,
            )
            for nested_url in nested_candidates[:8]:
                if nested_url == url:
                    continue
                ok_nested, info_nested, pdf_nested = _fetch_pdf_bytes_from_url(
                    nested_url,
                    project=project,
                    article=article,
                    depth=depth + 1,
                    visited=visited,
                )
                info_nested["nested_from_url"] = url
                info_nested["nested_from_final_url"] = info.get("final_url")
                if ok_nested and pdf_nested:
                    return True, info_nested, pdf_nested

        return False, info, None

    # Succès : on a un vrai PDF
    info["status"] = "pdf_bytes_received"
    info["bytes"] = len(pdf_bytes)
    info["sha256"] = hashlib.sha256(pdf_bytes).hexdigest()
    return True, info, pdf_bytes
# ============================================================
# PDF text extraction from bytes
# ============================================================

def _import_fitz():
    try:
        import fitz
        return fitz
    except Exception as exc:
        raise RuntimeError(
            "PyMuPDF n'est pas installé. Lance : pip install pymupdf"
        ) from exc


def _extract_text_from_pdf_bytes(
    pdf_bytes: bytes,
) -> Dict[str, Any]:
    """
    Extrait le texte natif page par page depuis les bytes PDF.

    Le document PyMuPDF est toujours fermé, même en cas d'erreur.
    """
    fitz = _import_fitz()

    pages: List[Dict[str, Any]] = []
    full_text_parts: List[str] = []

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        metadata = dict(doc.metadata or {})
        pages_count = int(doc.page_count or 0)

        for idx in range(pages_count):
            page = doc.load_page(idx)

            raw_text = page.get_text("text") or ""
            text = _safe_text(raw_text, MAX_PAGE_TEXT_CHARS)

            page_item = {
                "page": idx + 1,
                "text": text,
                "chars": len(text),
                "words": _word_count(text),
                "has_text": bool(text.strip()),
                "extraction_method": "native",
            }

            pages.append(page_item)

            if text:
                full_text_parts.append(f"\n\n--- PAGE {idx + 1} ---\n{text}")

    full_text = _safe_text("\n".join(full_text_parts), 0)
    pages_with_text = sum(1 for p in pages if p.get("has_text"))
    empty_pages_count = sum(1 for p in pages if not p.get("has_text"))

    return {
        "metadata": metadata,
        "pages_count": len(pages),
        "pages_with_text": pages_with_text,
        "text_chars": len(full_text),
        "text_words": _word_count(full_text),
        "pages": pages,
        "full_text_preview": _safe_text(full_text, 3000),
        "extraction_method": "native",
        "ocr_attempted": False,
        "ocr_engine": None,
        "ocr_confidence": None,
        "ocr_pages_processed": [],
        "ocr_errors": [],
        "temporary_pdf_deleted": True,
        "quality": {
            "is_text_extractable": len(full_text) >= MIN_USEFUL_TEXT_CHARS,
            "needs_ocr": len(full_text) < MIN_USEFUL_TEXT_CHARS,
            "empty_pages_count": empty_pages_count,
            "native_text_chars": len(full_text),
            "native_pages_with_text": pages_with_text,
        },
    }


def _import_ocr_extractor():
    """
    Importe le moteur OCR seulement lorsqu'il est nécessaire.

    Le backend peut être lancé depuis C:/EnnoSmart/backend_api ; dans ce cas,
    la racine C:/EnnoSmart n'est pas toujours présente dans sys.path.
    """
    project_root = Path(__file__).resolve().parents[2]
    project_root_str = str(project_root)

    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)

    try:
        from modules.extraction.text.pdf_ocr import extract_pdf_ocr
        return extract_pdf_ocr
    except Exception as exc:
        raise RuntimeError(f"Import OCR impossible : {type(exc).__name__}: {exc}") from exc


def _delete_temporary_pdf(path: Optional[Path]) -> bool:
    """Supprime un PDF temporaire, avec retries pour les verrous Windows."""
    if path is None:
        return True

    for attempt in range(1, OCR_TEMP_DELETE_RETRIES + 1):
        try:
            gc.collect()

            if not path.exists():
                return True

            path.unlink()
            return True

        except FileNotFoundError:
            return True

        except PermissionError:
            if attempt >= OCR_TEMP_DELETE_RETRIES:
                return False
            time.sleep(OCR_TEMP_DELETE_DELAY_SECONDS)

        except Exception:
            if attempt >= OCR_TEMP_DELETE_RETRIES:
                return False
            time.sleep(OCR_TEMP_DELETE_DELAY_SECONDS)

    return not path.exists()


def _ocr_pdf_bytes(
    pdf_bytes: bytes,
    target_pages: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    Lance l'OCR sur un PDF temporaire, puis supprime obligatoirement ce PDF.

    Le PDF n'est jamais conservé dans le stockage EnnoScholar.
    """
    temp_path: Optional[Path] = None
    temporary_pdf_deleted = False
    ocr_payload: Dict[str, Any] = {
        "ok": False,
        "pages": [],
        "pages_count": 0,
        "pages_with_text": 0,
        "text_chars": 0,
        "text_words": 0,
        "full_text_preview": "",
        "ocr_engine": None,
        "ocr_confidence": 0.0,
        "ocr_pages_processed": [],
        "ocr_errors": [],
        "ocr_tags": [],
    }

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".pdf",
            prefix="ennoscholar_ocr_",
            delete=False,
        ) as tmp:
            tmp.write(pdf_bytes)
            tmp.flush()
            temp_path = Path(tmp.name)

        extract_pdf_ocr = _import_ocr_extractor()
        ocr_result = extract_pdf_ocr(temp_path, target_pages=target_pages)

        pages: List[Dict[str, Any]] = []
        full_text_parts: List[str] = []

        for page_result in ocr_result.pages or []:
            text = _safe_text(getattr(page_result, "raw_text", ""), MAX_PAGE_TEXT_CHARS)
            page_number = int(getattr(page_result, "page_number", 0) or 0)
            confidence = float(getattr(page_result, "confidence", 0.0) or 0.0)

            page_item = {
                "page": page_number,
                "text": text,
                "chars": len(text),
                "words": _word_count(text),
                "has_text": bool(text.strip()),
                "extraction_method": "ocr",
                "ocr_engine": getattr(
                    getattr(page_result, "engine_used", None),
                    "value",
                    str(getattr(page_result, "engine_used", "none")),
                ),
                "ocr_confidence": confidence,
                "orientation_detected": getattr(
                    getattr(page_result, "orientation_detected", None),
                    "value",
                    str(getattr(page_result, "orientation_detected", "normal")),
                ),
                "orientation_corrected": bool(
                    getattr(page_result, "orientation_corrected", False)
                ),
                "extraction_errors": list(
                    getattr(page_result, "extraction_errors", []) or []
                ),
            }

            pages.append(page_item)

            if text:
                full_text_parts.append(
                    f"\n\n--- PAGE {page_number} [OCR] ---\n{text}"
                )

        pages.sort(key=lambda item: int(item.get("page") or 0))
        full_text = _safe_text("\n".join(full_text_parts), 0)
        engine_value = getattr(
            getattr(ocr_result, "engine_used", None),
            "value",
            str(getattr(ocr_result, "engine_used", "none")),
        )

        ocr_payload = {
            "ok": len(full_text) >= MIN_USEFUL_TEXT_CHARS,
            "pages": pages,
            "pages_count": int(getattr(ocr_result, "page_count", 0) or len(pages)),
            "pages_with_text": sum(1 for p in pages if p.get("has_text")),
            "text_chars": len(full_text),
            "text_words": _word_count(full_text),
            "full_text_preview": _safe_text(full_text, 3000),
            "ocr_engine": engine_value,
            "ocr_confidence": float(
                getattr(ocr_result, "confidence_score", 0.0) or 0.0
            ),
            "ocr_pages_processed": list(
                getattr(ocr_result, "pages_processed", []) or []
            ),
            "ocr_errors": list(
                getattr(ocr_result, "extraction_errors", []) or []
            ),
            "ocr_tags": list(getattr(ocr_result, "tags", []) or []),
        }

    except Exception as exc:
        ocr_payload["ocr_errors"] = [
            f"{type(exc).__name__}: {exc}"
        ]

    finally:
        temporary_pdf_deleted = _delete_temporary_pdf(temp_path)
        ocr_payload["temporary_pdf_deleted"] = temporary_pdf_deleted
        ocr_payload["temporary_pdf_path"] = None

    return ocr_payload


def _merge_native_and_ocr_pages(
    native_pages: List[Dict[str, Any]],
    ocr_pages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Fusionne les pages natives et OCR.

    Pour chaque page, on garde la version qui contient le plus de texte utile.
    """
    native_index = {
        int(page.get("page") or 0): dict(page)
        for page in native_pages
        if int(page.get("page") or 0) > 0
    }
    ocr_index = {
        int(page.get("page") or 0): dict(page)
        for page in ocr_pages
        if int(page.get("page") or 0) > 0
    }

    page_numbers = sorted(set(native_index) | set(ocr_index))
    merged: List[Dict[str, Any]] = []

    for page_number in page_numbers:
        native_page = native_index.get(page_number) or {}
        ocr_page = ocr_index.get(page_number) or {}

        native_chars = int(native_page.get("chars") or 0)
        ocr_chars = int(ocr_page.get("chars") or 0)

        if ocr_chars > native_chars:
            selected = dict(ocr_page)
            selected["native_chars_before_ocr"] = native_chars
        else:
            selected = dict(native_page or ocr_page)
            if ocr_page:
                selected["ocr_chars_candidate"] = ocr_chars

        selected["page"] = page_number
        merged.append(selected)

    return merged


def _build_extraction_payload_from_pages(
    pages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    extraction_method: str,
    native_payload: Dict[str, Any],
    ocr_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pages = sorted(pages, key=lambda item: int(item.get("page") or 0))
    full_text_parts: List[str] = []

    for page in pages:
        text = _safe_text(page.get("text") or "", MAX_PAGE_TEXT_CHARS)
        page["text"] = text
        page["chars"] = len(text)
        page["words"] = _word_count(text)
        page["has_text"] = bool(text.strip())

        if text:
            method_label = str(page.get("extraction_method") or extraction_method).upper()
            full_text_parts.append(
                f"\n\n--- PAGE {page.get('page')} [{method_label}] ---\n{text}"
            )

    full_text = _safe_text("\n".join(full_text_parts), 0)
    pages_with_text = sum(1 for page in pages if page.get("has_text"))
    empty_pages_count = sum(1 for page in pages if not page.get("has_text"))
    ocr_payload = ocr_payload or {}

    return {
        "metadata": metadata,
        "pages_count": len(pages),
        "pages_with_text": pages_with_text,
        "text_chars": len(full_text),
        "text_words": _word_count(full_text),
        "pages": pages,
        "full_text_preview": _safe_text(full_text, 3000),
        "extraction_method": extraction_method,
        "ocr_attempted": bool(ocr_payload),
        "ocr_engine": ocr_payload.get("ocr_engine"),
        "ocr_confidence": ocr_payload.get("ocr_confidence"),
        "ocr_pages_processed": ocr_payload.get("ocr_pages_processed") or [],
        "ocr_errors": ocr_payload.get("ocr_errors") or [],
        "ocr_tags": ocr_payload.get("ocr_tags") or [],
        "temporary_pdf_deleted": ocr_payload.get("temporary_pdf_deleted", True),
        "quality": {
            "is_text_extractable": len(full_text) >= MIN_USEFUL_TEXT_CHARS,
            "needs_ocr": len(full_text) < MIN_USEFUL_TEXT_CHARS,
            "empty_pages_count": empty_pages_count,
            "native_text_chars": int(native_payload.get("text_chars") or 0),
            "native_pages_with_text": int(native_payload.get("pages_with_text") or 0),
            "ocr_text_chars": int(ocr_payload.get("text_chars") or 0),
            "ocr_succeeded": bool(ocr_payload.get("ok")),
        },
    }


def _extract_text_with_ocr_fallback(
    pdf_bytes: bytes,
) -> Dict[str, Any]:
    """
    Pipeline automatique :
      1. extraction native PyMuPDF ;
      2. OCR des pages vides/pauvres si nécessaire ;
      3. fusion native + OCR ;
      4. statut d'échec si le texte final reste insuffisant.
    """
    native = _extract_text_from_pdf_bytes(pdf_bytes)
    native_pages = list(native.get("pages") or [])
    native_text_chars = int(native.get("text_chars") or 0)

    poor_pages = [
        int(page.get("page") or 0)
        for page in native_pages
        if int(page.get("page") or 0) > 0
        and int(page.get("chars") or 0) < MIN_USEFUL_PAGE_CHARS
    ]

    native_is_enough = native_text_chars >= MIN_USEFUL_TEXT_CHARS
    mixed_ocr_needed = (
        OCR_MIXED_PDF_ENABLED
        and bool(poor_pages)
        and len(poor_pages) < max(len(native_pages), 1)
    )

    if not OCR_FALLBACK_ENABLED or (native_is_enough and not mixed_ocr_needed):
        native["quality"]["needs_ocr"] = False if native_is_enough else True
        return native

    # PDF entièrement ou presque entièrement scanné : OCR de toutes les pages.
    # PDF mixte : OCR uniquement des pages pauvres.
    target_pages = None if not native_is_enough else poor_pages
    ocr = _ocr_pdf_bytes(pdf_bytes, target_pages=target_pages)

    merged_pages = _merge_native_and_ocr_pages(
        native_pages=native_pages,
        ocr_pages=list(ocr.get("pages") or []),
    )

    used_methods = {
        str(page.get("extraction_method") or "native")
        for page in merged_pages
        if page.get("has_text")
    }

    if used_methods == {"ocr"}:
        extraction_method = "ocr"
    elif "ocr" in used_methods and "native" in used_methods:
        extraction_method = "hybrid_native_ocr"
    else:
        extraction_method = "native"

    return _build_extraction_payload_from_pages(
        pages=merged_pages,
        metadata=dict(native.get("metadata") or {}),
        extraction_method=extraction_method,
        native_payload=native,
        ocr_payload=ocr,
    )


# ============================================================
# Candidate expansion
# ============================================================

def _expand_landing_candidates_to_pdf_urls(
    candidate: Dict[str, str],
    article: Article,
) -> Tuple[List[Dict[str, str]], List[Dict[str, Any]]]:
    """
    Prend une landing page DOI/Semantic Scholar/éditeur
    et retourne des candidats PDF.
    """
    url = candidate.get("url") or ""
    source = candidate.get("source") or "landing"

    pdf_candidates: List[Dict[str, Any]] = []
    attempts: List[Dict[str, Any]] = []

    html_ok, html_info = _fetch_html(url)

    html_info["candidate_source"] = source
    html_info["candidate_kind"] = "landing"
    attempts.append({k: v for k, v in html_info.items() if k != "html"})

    if not html_ok:
        return pdf_candidates, attempts

    html = html_info.get("html") or ""
    final_url = html_info.get("final_url") or url

    # Cas 1 : landing elle-même = PDF.
    if html_info.get("status") == "html_fetch_is_pdf":
        pdf_candidates.append(
            {
                "kind": "pdf",
                "source": f"{source}_landing_is_pdf",
                "url": url,
            }
        )

    # Cas 2 : theses.fr -> NNT -> API diffusion + HAL.
    nnt = _extract_nnt_from_text(f"{final_url}\n{html or ''}")

    if nnt:
        for theses_url in _build_theses_diffusion_candidates(nnt):
            pdf_candidates.append(
                {
                    "kind": "pdf",
                    "source": "theses_fr_api_diffusion",
                    "url": theses_url,
                }
            )

        for hal_url in _build_hal_pdf_candidates_for_article(article, nnt=nnt):
            pdf_candidates.append(
                {
                    "kind": "pdf",
                    "source": "hal_api_from_theses_nnt",
                    "url": hal_url,
                }
            )

    # Cas 3 : liens PDF dans HTML.
    for pdf_url in _extract_pdf_candidates_from_html(html=html, base_url=final_url):
        pdf_candidates.append(
            {
                "kind": "pdf",
                "source": "html_scraped_pdf_link",
                "url": pdf_url,
            }
        )

    # Déduplication
    seen = set()
    out: List[Dict[str, str]] = []

    for item in pdf_candidates:
        u = item.get("url", "").strip()
        if not u:
            continue

        key = u.lower()
        if key in seen:
            continue

        seen.add(key)
        out.append(item)

    return out, attempts


def _candidate_from_phase_2a_status(project: Project, article: Article) -> Optional[Dict[str, Any]]:
    """Réutilise exactement l'URL validée par la Phase 2A, avec sa provenance."""
    status = _json_read(_status_path(project, article))
    if not isinstance(status, dict):
        return None
    if status.get("full_text_status") != "pdf_url_available":
        return None

    url = (
        status.get("pdf_final_url")
        or status.get("final_url")
        or status.get("download_url")
        or status.get("pdf_source_url")
    )
    if not isinstance(url, str) or not url.strip():
        return None

    return {
        "kind": "pdf",
        "source": status.get("resolver") or "phase_2a_status",
        "url": url.strip(),
        "retrieved_via_mcp": bool(status.get("retrieved_via_mcp")),
        "legal_access": status.get("legal_access"),
        "license": status.get("legal_license"),
        "version": status.get("legal_version"),
        "host_type": status.get("host_type"),
        "access_type": status.get("access_type"),
        "rights_status": status.get("rights_status"),
        "source_domain": status.get("source_domain"),
        "discovered_via": status.get("discovered_via"),
        "identity_score": status.get("identity_score"),
        "identity_method": status.get("identity_method"),
        "same_article": status.get("same_article"),
        "verified_pdf": status.get("verified_pdf"),
        "mcp_status": status.get("mcp_status"),
        "mcp_cache_hit": status.get("mcp_cache_hit"),
    }


# ============================================================
# Main service
# ============================================================

def extract_direct_fulltext_for_article(
    db: Session,
    project: Project,
    article_id: int,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Trouve un PDF et extrait directement son texte, sans stocker le PDF.
    """
    article = get_article_for_project(db, project, article_id)

    out_file = _direct_extracted_path(project, article)

    if out_file.exists() and not force:
        saved = _json_read(out_file)
        if saved:
            saved["already_extracted"] = True
            return saved

    phase_2a_status = _json_read(_status_path(project, article)) or {}
    phase_2a_candidate = _candidate_from_phase_2a_status(project, article)

    mcp_diagnostics: List[Dict[str, Any]] = []

    # Lorsque la phase 2A a déjà été exécutée, conserver son diagnostic MCP
    # sans rappeler le serveur une seconde fois.
    if phase_2a_candidate and phase_2a_status.get("mcp_called"):
        mcp_diagnostics.append(
            {
                "status": "mcp_resolution",
                "candidate_source": "legal_mcp",
                "candidate_kind": "diagnostic",
                "mcp_called": True,
                "mcp_ok": phase_2a_status.get("mcp_ok"),
                "mcp_found": phase_2a_status.get("mcp_found"),
                "mcp_status": phase_2a_status.get("mcp_status"),
                "mcp_cache_hit": phase_2a_status.get("mcp_cache_hit"),
                "mcp_legal_access": phase_2a_status.get("mcp_legal_access"),
                "mcp_same_article": phase_2a_status.get("mcp_same_article"),
                "mcp_needs_consultant_upload": phase_2a_status.get(
                    "mcp_needs_consultant_upload"
                ),
                "mcp_locations_count": phase_2a_status.get(
                    "mcp_locations_count", 0
                ),
                "mcp_verified_candidates_count": phase_2a_status.get(
                    "mcp_verified_candidates_count", 0
                ),
                "mcp_legal_same_article_locations_count": phase_2a_status.get(
                    "mcp_legal_same_article_locations_count", 0
                ),
                "mcp_provider_attempts": phase_2a_status.get(
                    "mcp_provider_attempts", []
                ),
                "mcp_locations": phase_2a_status.get("mcp_locations", []),
            }
        )

    candidates: List[Dict[str, Any]] = []
    if phase_2a_candidate:
        candidates.append(phase_2a_candidate)

    # Si la Phase 2A a déjà validé un PDF, ne pas rappeler le MCP.
    # Les candidats locaux restent ajoutés comme fallback.
    candidates.extend(
        build_candidate_urls_for_article(
            article,
            include_mcp=not bool(phase_2a_candidate),
            force_mcp=force,
            mcp_diagnostics=mcp_diagnostics,
        )
    )

    attempts: List[Dict[str, Any]] = list(mcp_diagnostics)
    mcp_summary = _mcp_summary_from_diagnostics(mcp_diagnostics)
    expanded_pdf_candidates: List[Dict[str, Any]] = []

    # 1. Candidats PDF directs déjà connus.
    for candidate in candidates:
        if candidate.get("kind") == "pdf":
            expanded_pdf_candidates.append(candidate)

    # 2. Développer les landing pages en candidats PDF.
    for candidate in candidates:
        if candidate.get("kind") != "landing":
            continue

        pdfs, landing_attempts = _expand_landing_candidates_to_pdf_urls(
            candidate=candidate,
            article=article,
        )
        attempts.extend(landing_attempts)
        expanded_pdf_candidates.extend(pdfs)

    # 3. Déduplication des candidats PDF.
    seen = set()
    pdf_candidates: List[Dict[str, Any]] = []

    for candidate in expanded_pdf_candidates:
        url = (candidate.get("url") or "").strip()

        if not url:
            continue

        key = url.lower()
        if key in seen:
            continue

        seen.add(key)
        pdf_candidates.append(candidate)

    pdf_url_found_count = len(pdf_candidates)

    # 4. Essayer chaque PDF en mémoire.
    blocked_candidates: List[str] = []
    downloaded_pdf_count = 0
    text_extraction_failed_count = 0

    for candidate in pdf_candidates:
        url = candidate.get("url") or ""

        ok, info, pdf_bytes = _fetch_pdf_bytes_from_url(
            url=url,
            project=project,
            article=article,
        )

        info["candidate_source"] = candidate.get("source")
        info["candidate_kind"] = candidate.get("kind")
        attempts.append(info)

        if info.get("status") == "blocked_by_antibot":
            blocked_candidates.append(url)

        if not ok or not pdf_bytes:
            continue

        downloaded_pdf_count += 1

        try:
            extracted = _extract_text_with_ocr_fallback(pdf_bytes)

            extraction_method = str(extracted.get("extraction_method") or "native")
            text_chars = int(extracted.get("text_chars") or 0)
            is_extractable = bool(
                (extracted.get("quality") or {}).get("is_text_extractable")
            ) and text_chars >= MIN_USEFUL_TEXT_CHARS

            if not is_extractable:
                text_extraction_failed_count += 1
                attempts.append(
                    {
                        "status": "ocr_failed_or_insufficient_text",
                        "url": url,
                        "reason": (
                            "Le PDF a été téléchargé, mais le texte natif et l'OCR "
                            f"restent insuffisants ({text_chars} caractères)."
                        ),
                        "candidate_source": candidate.get("source"),
                        "extraction_method": extraction_method,
                        "text_chars": text_chars,
                        "ocr_engine": extracted.get("ocr_engine"),
                        "ocr_errors": extracted.get("ocr_errors") or [],
                        "temporary_pdf_deleted": extracted.get("temporary_pdf_deleted"),
                    }
                )
                continue

            if extraction_method == "ocr":
                success_status = "text_extracted_ocr"
            elif extraction_method == "hybrid_native_ocr":
                success_status = "text_extracted_hybrid"
            else:
                success_status = "text_extracted_direct"

            result = {
                "ok": True,
                "article_id": article.id,
                "title": article.title,
                "doi": article.doi,
                "url": article.url,
                "source": article.source,
                "tag": article.tag_article,
                "status": success_status,
                "full_text_status": "text_extracted",
                "evidence_level": "full_text",
                "extraction_method": extraction_method,
                "needs_consultant_upload": False,
                "storage_mode": "json_only_no_pdf_saved",
                "saved_pdf": False,
                "pdf_source_url": candidate.get("pdf_url") or url,
                "pdf_final_url": info.get("final_url") or candidate.get("final_url") or url,
                "resolver": candidate.get("resolver") or candidate.get("source"),
                "pdf_source_resolver": candidate.get("resolver") or candidate.get("source"),
                "retrieved_via_mcp": bool(candidate.get("retrieved_via_mcp")),
                "article_identity_sent": candidate.get("article_identity_sent") or mcp_summary.get("article_identity_sent"),
                "article_identity_origin": candidate.get("article_identity_origin") or mcp_summary.get("article_identity_origin"),
                "article_identity_warnings": mcp_summary.get("article_identity_warnings") or [],
                "legal_access": candidate.get("legal_access"),
                "legal_provider": (
                    str(candidate.get("source") or "").split(":", 1)[1]
                    if str(candidate.get("source") or "").startswith("legal_mcp:")
                    else None
                ),
                "legal_license": candidate.get("license"),
                "legal_version": candidate.get("version"),
                "host_type": candidate.get("host_type"),
                "access_type": candidate.get("access_type"),
                "rights_status": candidate.get("rights_status"),
                "source_domain": candidate.get("source_domain"),
                "discovered_via": candidate.get("discovered_via"),
                "identity_score": candidate.get("identity_score"),
                "identity_method": candidate.get("identity_method"),
                "same_article": candidate.get("same_article"),
                "verified_pdf": candidate.get("verified_pdf"),
                "mcp_status": candidate.get("mcp_status"),
                "mcp_cache_hit": candidate.get("mcp_cache_hit"),
                **mcp_summary,
                "pdf_bytes": info.get("bytes"),
                "pdf_sha256": info.get("sha256"),
                "output_path": str(out_file),
                "attempts": attempts,
                **extracted,
                "generated_at": datetime.utcnow().isoformat(),
            }

            _json_dump(out_file, result)

            return result

        except Exception as exc:
            text_extraction_failed_count += 1
            attempts.append(
                {
                    "status": "extract_failed",
                    "url": url,
                    "reason": str(exc),
                    "candidate_source": candidate.get("source"),
                }
            )

    # 5. Aucun PDF exploitable.
    if downloaded_pdf_count > 0 and text_extraction_failed_count > 0:
        status = "pdf_downloaded_but_text_extraction_failed"
        message = (
            "Un PDF valide a été téléchargé, mais son texte natif et son OCR "
            "restent insuffisants. Un upload consultant ou une vérification OCR est nécessaire."
        )
    elif pdf_url_found_count > 0 and blocked_candidates:
        status = "pdf_url_found_but_download_blocked"
        message = (
            "Un ou plusieurs liens PDF publics ont été trouvés, "
            "mais le serveur renvoie une page anti-robot au backend. "
            "Extraction automatique impossible sans accès navigateur ou import manuel."
        )
    elif pdf_url_found_count > 0:
        status = "pdf_url_found_but_not_pdf_response"
        message = (
            "Des liens PDF ont été trouvés, mais aucun n'a retourné un vrai fichier PDF exploitable."
        )
    else:
        status = "no_pdf_url_found"
        message = "Aucun lien PDF exploitable n'a été trouvé automatiquement."

    result = {
        "ok": False,
        "article_id": article.id,
        "title": article.title,
        "doi": article.doi,
        "url": article.url,
        **mcp_summary,
        "status": status,
        "full_text_status": (
            "ocr_failed_or_insufficient_text"
            if status == "pdf_downloaded_but_text_extraction_failed"
            else "missing_or_blocked_pdf"
        ),
        "storage_mode": "json_only_no_pdf_saved",
        "saved_pdf": False,
        "pdf_url_found_count": pdf_url_found_count,
        "downloaded_pdf_count": downloaded_pdf_count,
        "text_extraction_failed_count": text_extraction_failed_count,
        "blocked_candidates": blocked_candidates,
        "needs_consultant_upload": status in {
            "pdf_url_found_but_download_blocked",
            "pdf_url_found_but_not_pdf_response",
            "pdf_downloaded_but_text_extraction_failed",
            "no_pdf_url_found",
        },
        "message": message,
        "candidates": candidates,
        "pdf_candidates": pdf_candidates,
        "attempts": attempts,
        "generated_at": datetime.utcnow().isoformat(),
    }

    # Sauvegarder aussi le statut JSON, même si échec.
    _json_dump(out_file, result)

    return result


def extract_direct_fulltext_for_selected_articles(
    db: Session,
    project: Project,
    force: bool = False,
    max_articles: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Extraction directe pour tous les articles gardés.

    Règle métier corrigée :
    - max_articles = None ou 0 => traiter TOUS les articles sélectionnés ;
    - max_articles > 0      => limiter volontairement le traitement.
    """
    selected_articles = get_selected_articles_for_project(db, project)
    total_selected_articles = len(selected_articles)

    if max_articles is not None and max_articles > 0:
        articles_to_process = selected_articles[:max_articles]
        max_articles_applied: Optional[int] = max_articles
    else:
        articles_to_process = selected_articles
        max_articles_applied = None

    results: List[Dict[str, Any]] = []

    for article in articles_to_process:
        try:
            result = extract_direct_fulltext_for_article(
                db=db,
                project=project,
                article_id=article.id,
                force=force,
            )
            results.append(result)

        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "article_id": article.id,
                    "title": article.title,
                    "status": "error",
                    "reason": str(exc),
                }
            )

    text_extracted_count = sum(
        1 for r in results if r.get("full_text_status") == "text_extracted"
    )
    blocked_count = sum(
        1 for r in results if r.get("status") == "pdf_url_found_but_download_blocked"
    )
    no_pdf_count = sum(
        1 for r in results if r.get("status") == "no_pdf_url_found"
    )
    ocr_extracted_count = sum(
        1 for r in results if r.get("status") == "text_extracted_ocr"
    )
    hybrid_extracted_count = sum(
        1 for r in results if r.get("status") == "text_extracted_hybrid"
    )
    ocr_failed_count = sum(
        1 for r in results
        if r.get("status") == "pdf_downloaded_but_text_extraction_failed"
    )

    summary = {
        "ok": True,
        "project_id": project.id,
        "selected_articles_count": total_selected_articles,
        "total_selected_articles_count": total_selected_articles,
        "processed_articles_count": len(articles_to_process),
        "max_articles_requested": max_articles,
        "max_articles_applied": max_articles_applied,
        "processed_all_selected_articles": len(articles_to_process) == total_selected_articles,
        "text_extracted_count": text_extracted_count,
        "ocr_extracted_count": ocr_extracted_count,
        "hybrid_extracted_count": hybrid_extracted_count,
        "ocr_failed_count": ocr_failed_count,
        "blocked_count": blocked_count,
        "no_pdf_count": no_pdf_count,
        "storage_mode": "json_only_no_pdf_saved",
        "results": results,
        "generated_at": datetime.utcnow().isoformat(),
    }

    out_path = _fulltext_dirs(project)["base"] / "direct_extract_report.json"
    _json_dump(out_path, summary)

    summary["report_path"] = str(out_path)

    return summary


def get_direct_extract_status_for_selected_articles(
    db: Session,
    project: Project,
) -> Dict[str, Any]:
    """
    Statut des extractions directes.
    """
    articles = get_selected_articles_for_project(db, project)

    results: List[Dict[str, Any]] = []

    for article in articles:
        out_file = _direct_extracted_path(project, article)
        saved = _json_read(out_file)

        if saved:
            results.append(
                {
                    "article_id": article.id,
                    "title": article.title,
                    "tag": article.tag_article,
                    "source": article.source,
                    "status": saved.get("status"),
                    "full_text_status": saved.get("full_text_status"),
                    "storage_mode": saved.get("storage_mode"),
                    "saved_pdf": saved.get("saved_pdf"),
                    "pdf_source_url": saved.get("pdf_source_url"),
                    "output_path": str(out_file),
                    "text_chars": saved.get("text_chars"),
                    "text_words": saved.get("text_words"),
                    "pages_count": saved.get("pages_count"),
                    "pages_with_text": saved.get("pages_with_text"),
                    "extraction_method": saved.get("extraction_method"),
                    "ocr_attempted": saved.get("ocr_attempted"),
                    "ocr_engine": saved.get("ocr_engine"),
                    "ocr_confidence": saved.get("ocr_confidence"),
                    "ocr_pages_processed": saved.get("ocr_pages_processed"),
                    "ocr_errors": saved.get("ocr_errors"),
                    "temporary_pdf_deleted": saved.get("temporary_pdf_deleted"),
                    "mcp_called": saved.get("mcp_called"),
                    "mcp_ok": saved.get("mcp_ok"),
                    "mcp_found": saved.get("mcp_found"),
                    "mcp_status": saved.get("mcp_status"),
                    "mcp_cache_hit": saved.get("mcp_cache_hit"),
                    "mcp_locations_count": saved.get("mcp_locations_count"),
                    "mcp_verified_candidates_count": saved.get(
                        "mcp_verified_candidates_count"
                    ),
                    "needs_consultant_upload": saved.get("needs_consultant_upload"),
                }
            )
        else:
            results.append(
                {
                    "article_id": article.id,
                    "title": article.title,
                    "tag": article.tag_article,
                    "source": article.source,
                    "status": "not_checked",
                    "full_text_status": "not_checked",
                    "storage_mode": "json_only_no_pdf_saved",
                    "saved_pdf": False,
                }
            )

    return {
        "ok": True,
        "project_id": project.id,
        "selected_articles_count": len(articles),
        "text_extracted_count": sum(
            1 for r in results if r.get("full_text_status") == "text_extracted"
        ),
        "blocked_count": sum(
            1 for r in results if r.get("status") == "pdf_url_found_but_download_blocked"
        ),
        "not_checked_count": sum(
            1 for r in results if r.get("status") == "not_checked"
        ),
        "storage_mode": "json_only_no_pdf_saved",
        "results": results,
    }
