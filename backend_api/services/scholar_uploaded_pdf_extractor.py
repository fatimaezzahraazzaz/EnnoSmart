# -*- coding: utf-8 -*-

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import UploadFile
from sqlalchemy.orm import Session

from db.models import Article, Project, ScholarRun


MAX_PAGE_TEXT_CHARS = int(os.getenv("ENNOSCHOLAR_EXTRACT_MAX_PAGE_CHARS", "20000"))
MAX_UPLOAD_PDF_MB = int(os.getenv("ENNOSCHOLAR_UPLOAD_PDF_MAX_MB", "120"))
MAX_UPLOAD_PDF_BYTES = MAX_UPLOAD_PDF_MB * 1024 * 1024


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
    text = _strip_accents(str(value or "")).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "unknown")[:max_len].strip("_")


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))


def _json_dump(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _project_ennoscholar_dir(project: Project) -> Path:
    root = Path(os.getenv("ENNOSMART_STORAGE_ROOT", "C:/EnnoSmart/storage"))
    organisme = _slugify(getattr(project, "organisme", "") or "organisme")
    project_name = _slugify(getattr(project, "project_name", "") or "project")
    year = _slugify(getattr(project, "year", "") or "year")
    return root / "organismes" / organisme / "projects" / project_name / "years" / year / "ennoscholar"


def _article_file_prefix(article: Article) -> str:
    return f"article_{article.id}_{_slugify(article.title or 'article', 60)}"


def _uploaded_extracted_path(project: Project, article: Article) -> Path:
    return (
        _project_ennoscholar_dir(project)
        / "fulltext"
        / "extracted_uploaded"
        / f"{_article_file_prefix(article)}_uploaded_fulltext.json"
    )


def uploaded_pdf_path(project: Project, article: Article) -> Path:
    source_json = dict(article.source_json) if isinstance(article.source_json, dict) else {}
    stored_path = _safe_text(source_json.get("uploaded_pdf_path"), 4000)
    if stored_path and Path(stored_path).exists():
        return Path(stored_path)
    return (
        _project_ennoscholar_dir(project)
        / "fulltext"
        / "uploaded_pdf"
        / f"{_article_file_prefix(article)}.pdf"
    )


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


def _import_fitz():
    try:
        import fitz
        return fitz
    except Exception as exc:
        raise RuntimeError("PyMuPDF n'est pas installé. Lance : pip install pymupdf") from exc


def _extract_text_from_pdf_bytes(pdf_bytes: bytes) -> Dict[str, Any]:
    fitz = _import_fitz()

    pages: List[Dict[str, Any]] = []
    full_text_parts: List[str] = []

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    metadata = dict(doc.metadata or {})
    pages_count = int(doc.page_count or 0)

    for idx in range(pages_count):
        page = doc.load_page(idx)
        text = _safe_text(page.get_text("text") or "", MAX_PAGE_TEXT_CHARS)

        pages.append({
            "page": idx + 1,
            "text": text,
            "chars": len(text),
            "words": _word_count(text),
            "has_text": bool(text.strip()),
        })

        if text:
            full_text_parts.append(f"\n\n--- PAGE {idx + 1} ---\n{text}")

    doc.close()

    full_text = _safe_text("\n".join(full_text_parts), 0)

    return {
        "metadata": metadata,
        "pages_count": len(pages),
        "pages_with_text": sum(1 for p in pages if p.get("has_text")),
        "text_chars": len(full_text),
        "text_words": _word_count(full_text),
        "pages": pages,
        "full_text_preview": _safe_text(full_text, 3000),
        "quality": {
            "is_text_extractable": len(full_text) >= 1000,
            "needs_ocr": len(full_text) < 1000,
            "empty_pages_count": sum(1 for p in pages if not p.get("has_text")),
        },
    }


def _looks_like_file_title(value: Any, filename: str = "") -> bool:
    """DÃ©tecte les titres techniques produits par un nom de fichier."""

    title = _safe_text(value, 300)
    stem = Path(filename or "").stem.strip()
    normalized = re.sub(r"[^a-z0-9]+", "", title.casefold())
    normalized_stem = re.sub(r"[^a-z0-9]+", "", stem.casefold())
    return bool(
        not title
        or normalized in {"sourceimportee", "article", "document", "paper"}
        or (normalized_stem and normalized == normalized_stem)
        or re.fullmatch(r"(?:arxiv)?\d{4}[._-]?\d{4,6}(?:v\d+)?", title, re.I)
    )


def normalize_existing_uploaded_article_identity(
    db: Session,
    project: Project,
    article: Article,
) -> Dict[str, Any]:
    """Migre sans rÃ©-upload les PDF crÃ©Ã©s avant la normalisation d'identitÃ©."""

    source_json = dict(article.source_json) if isinstance(article.source_json, dict) else {}
    if not source_json.get("manual_upload_source"):
        return {}
    extracted_path = _safe_text(source_json.get("uploaded_fulltext_path"), 4000)
    if not extracted_path or not Path(extracted_path).exists():
        return {}
    try:
        extracted = json.loads(Path(extracted_path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    identity = _normalize_uploaded_article_identity(
        article,
        extracted,
        filename=_safe_text(source_json.get("uploaded_filename"), 500),
        source_url=article.url,
    )
    refreshed_source_json = (
        dict(article.source_json) if isinstance(article.source_json, dict) else {}
    )
    refreshed_source_json["guided_candidate_id"] = (
        refreshed_source_json.get("guided_candidate_id")
        or f"UPLOAD-{int(article.id)}"
    )
    article.source_json = refreshed_source_json
    db.add(article)
    db.commit()
    db.refresh(article)
    return identity


def _normalize_uploaded_article_identity(
    article: Article,
    extracted: Dict[str, Any],
    *,
    filename: str,
    source_url: Optional[str],
) -> Dict[str, Any]:
    """RÃ©cupÃ¨re l'identitÃ© bibliographique du PDF avant la Phase 2.

    ``Article`` ne possÃ¨de pas de colonne ``authors`` : les auteurs normalisÃ©s
    sont donc conservÃ©s dans ``source_json``, comme pour les sources issues de
    la recherche guidÃ©e.
    """

    metadata = extracted.get("metadata") if isinstance(extracted.get("metadata"), dict) else {}
    preview = _safe_text(extracted.get("full_text_preview"), 12000)
    pdf_title = _safe_text(metadata.get("title"), 700)
    invalid_pdf_title = bool(
        not pdf_title
        or len(pdf_title.split()) < 3
        or re.search(r"microsoft\s+word|untitled|document\d*\b", pdf_title, re.I)
    )
    if _looks_like_file_title(article.title, filename) and not invalid_pdf_title:
        article.title = pdf_title

    raw_authors = _safe_text(metadata.get("author"), 1200)
    authors = [
        item.strip()
        for item in re.split(r"\s*(?:;|\band\b|\|)\s*", raw_authors, flags=re.I)
        if item.strip()
    ]

    arxiv_match = re.search(
        r"\barxiv\s*:\s*(\d{4}\.\d{4,6})(?:v\d+)?\b",
        preview,
        flags=re.I,
    )
    arxiv_id = arxiv_match.group(1) if arxiv_match else ""
    if arxiv_id and not article.url:
        article.url = f"https://arxiv.org/abs/{arxiv_id}"
    elif source_url and not article.url:
        article.url = source_url

    if not article.year:
        creation_date = _safe_text(metadata.get("creationDate") or metadata.get("modDate"), 100)
        year_match = re.search(r"\b((?:19|20)\d{2})\b", creation_date)
        if not year_match and arxiv_id:
            year_match = re.match(r"(\d{2})", arxiv_id)
            if year_match:
                article.year = 2000 + int(year_match.group(1))
        elif year_match:
            article.year = int(year_match.group(1))

    if not article.doi:
        doi_match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", preview, re.I)
        if doi_match:
            article.doi = doi_match.group(0).rstrip(".,;)")

    source_json = dict(article.source_json) if isinstance(article.source_json, dict) else {}
    if authors:
        source_json["authors"] = authors
    source_json["extracted_identity"] = {
        "title": article.title,
        "authors": authors,
        "year": article.year,
        "doi": article.doi,
        "url": article.url,
        "arxiv_id": arxiv_id or None,
        "source": "uploaded_pdf_metadata_and_first_pages",
    }
    source_json["identity_normalized_from_pdf"] = True
    article.source_json = source_json
    return source_json["extracted_identity"]


async def upload_and_extract_pdf_for_article(
    db: Session,
    project: Project,
    article_id: int,
    file: UploadFile,
    source_url: Optional[str] = None,
) -> Dict[str, Any]:
    article = get_article_for_project(db, project, article_id)

    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        return {
            "ok": False,
            "article_id": article.id,
            "status": "invalid_file_type",
            "message": "Le fichier importé doit être un PDF.",
        }

    pdf_bytes = await file.read()

    if not pdf_bytes.startswith(b"%PDF-"):
        return {
            "ok": False,
            "article_id": article.id,
            "status": "invalid_pdf",
            "message": "Le fichier importé ne semble pas être un vrai PDF.",
        }

    if len(pdf_bytes) > MAX_UPLOAD_PDF_BYTES:
        return {
            "ok": False,
            "article_id": article.id,
            "status": "file_too_large",
            "message": f"PDF trop volumineux. Limite : {MAX_UPLOAD_PDF_MB} MB.",
        }

    extracted = _extract_text_from_pdf_bytes(pdf_bytes)
    extracted_identity = _normalize_uploaded_article_identity(
        article,
        extracted,
        filename=filename,
        source_url=source_url,
    )
    out_file = _uploaded_extracted_path(project, article)
    pdf_file = uploaded_pdf_path(project, article)
    pdf_file.parent.mkdir(parents=True, exist_ok=True)
    pdf_file.write_bytes(pdf_bytes)
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    result = {
        "ok": True,
        "article_id": article.id,
        "title": article.title,
        "doi": article.doi,
        "url": article.url,
        "source": article.source,
        "tag": article.tag_article,
        "status": "text_extracted_from_uploaded_pdf",
        "full_text_status": "text_extracted",
        "evidence_level": "full_text",
        "storage_mode": "uploaded_pdf_and_extracted_json",
        "saved_pdf": True,
        "uploaded_filename": filename,
        "uploaded_pdf_bytes": len(pdf_bytes),
        "uploaded_pdf_sha256": pdf_sha256,
        "uploaded_pdf_path": str(pdf_file),
        "pdf_source_url": source_url,
        "extracted_identity": extracted_identity,
        "output_path": str(out_file),
        **extracted,
        "generated_at": datetime.utcnow().isoformat(),
    }

    _json_dump(out_file, result)
    source_json = (
        dict(article.source_json)
        if isinstance(article.source_json, dict)
        else {}
    )
    source_json.update(
        {
            "manual_upload_source": True,
            "uploaded_pdf_available": True,
            "uploaded_filename": filename,
            "uploaded_pdf_sha256": pdf_sha256,
            "uploaded_pdf_bytes": len(pdf_bytes),
            "uploaded_pdf_path": str(pdf_file),
            "uploaded_fulltext_path": str(out_file),
        }
    )
    article.source_json = source_json
    db.add(article)
    db.commit()
    db.refresh(article)
    return result
