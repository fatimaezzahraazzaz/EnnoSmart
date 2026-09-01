# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scholar_direct_fulltext_service.py

Extraction directe EnnoScholar :
- utilise exclusivement les URLs déjà présentes dans l'article ;
- lit le contenu distant directement en mémoire via HTTP standard ;
- extrait le texte depuis PDF, HTML scientifique ou XML/JATS ;
- n'enregistre jamais le PDF, le HTML ou le XML source ;
- sauvegarde uniquement un JSON d'extraction et un JSON de statut ;
- ne recherche aucune nouvelle copie sur des fournisseurs externes.

VERSION AVEC CLASSIFICATION ANTI-BOT / PAYWALL
"""

import hashlib
import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from modules.common.runtime_paths import storage_root
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from db.models import Article, Project, ScholarRun
from services.http_client import GLOBAL_FETCHER
from services.scholar_fulltext_fetcher import (
    DEFAULT_TIMEOUT,
    HEADERS,
    MAX_PDF_BYTES,
    _is_antibot_html,
    _is_probably_paywall_or_login,
    _json_read,
    _looks_like_direct_pdf_url,
    _status_path,
    _uploaded_extracted_path,
    build_candidate_urls_for_article,
)
from services.scholar_fulltext_identity import verify_article_extraction
from services.scholar_selection_scope import get_current_selected_articles
from services.scholar_pdf_direct_extractor import (
    MIN_USEFUL_TEXT_CHARS,
    _direct_extracted_path,
    _extract_text_with_ocr_fallback,
)


# ============================================================
# Configuration
# ============================================================

MAX_REMOTE_BYTES = int(os.getenv("ENNOSCHOLAR_REMOTE_CONTENT_MAX_BYTES", str(MAX_PDF_BYTES)))
MIN_HTML_TEXT_CHARS = int(os.getenv("ENNOSCHOLAR_MIN_HTML_FULLTEXT_CHARS", "5000"))
MIN_XML_TEXT_CHARS = int(os.getenv("ENNOSCHOLAR_MIN_XML_FULLTEXT_CHARS", "3000"))
MAX_CANDIDATES = max(3, min(int(os.getenv("ENNOSCHOLAR_DIRECT_MAX_CANDIDATES", "8")), 15))
SAVE_DEBUG_SOURCE = os.getenv("ENNOSCHOLAR_SAVE_REMOTE_DEBUG_SOURCE", "0").strip().lower() in {
    "1", "true", "yes", "on"
}
MDPI_MIN_ARTICLE_HTML_BYTES = max(
    4_000,
    int(os.getenv("ENNOSCHOLAR_MDPI_MIN_ARTICLE_HTML_BYTES", "10000")),
)


# ============================================================
# Helpers génériques
# ============================================================

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: Any, max_chars: int = 0) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if max_chars and len(text) > max_chars:
        return text[:max_chars].strip()
    return text


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", text or "", flags=re.UNICODE))


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _json_dump(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _article_source_json(article: Article) -> Dict[str, Any]:
    return article.source_json if isinstance(article.source_json, dict) else {}


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


def _content_kind_from_url(url: str) -> str:
    low = (url or "").lower()
    path = (urlparse(url).path or "").lower()
    if _looks_like_direct_pdf_url(url):
        return "pdf"
    if path.endswith((".xml", ".jats", ".nxml")) or "format=xml" in low:
        return "xml"
    return "landing"


def _candidate_priority(candidate: Dict[str, Any]) -> tuple:
    kind = candidate.get("kind") or "landing"
    kind_rank = {"pdf": 0, "xml": 1, "landing": 2}.get(kind, 3)
    return (kind_rank, len(str(candidate.get("url") or "")))


def _dedupe_candidates(candidates: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        key = url.casefold()
        if key in seen:
            continue
        seen.add(key)
        item = dict(raw)
        item["url"] = url
        item.setdefault("kind", _content_kind_from_url(url))
        out.append(item)
    out.sort(key=_candidate_priority)
    return out[:MAX_CANDIDATES]


def _resolve_direct_candidates(article: Article) -> List[Dict[str, Any]]:
    """Retourne uniquement les URLs déjà connues dans les métadonnées."""
    candidates = list(build_candidate_urls_for_article(article))

    # Une URL MDPI de téléchargement peut être temporairement servie comme
    # HTML. Ajouter la page article *déjà déterminable depuis cette URL* permet
    # d'extraire le texte HTML officiel ou de relire son lien PDF officiel. Ce
    # n'est pas une recherche externe et ne contourne aucune protection.
    for candidate in list(candidates):
        canonical_url = _mdpi_article_url_from_known_url(candidate.get("url"))
        if canonical_url:
            candidates.append(
                {
                    "url": canonical_url,
                    "kind": "landing",
                    "source": "mdpi_canonical_article_from_known_url",
                }
            )
    return _dedupe_candidates(candidates)


def _is_mdpi_url(url: str) -> bool:
    try:
        hostname = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return hostname == "mdpi.com" or hostname.endswith(".mdpi.com")


def _mdpi_article_url_from_known_url(url: Any) -> str:
    """Transforme uniquement une URL MDPI connue ``.../pdf`` en sa notice.

    Exemple : ``.../2072-4292/10/6/846/pdf?version=...`` devient
    ``.../2072-4292/10/6/846``. Le PDF reste validé par son magic byte après
    téléchargement ; cette fonction ne fabrique jamais de lien de dépôt tiers.
    """
    raw = str(url or "").strip()
    if not raw or not _is_mdpi_url(raw):
        return ""
    try:
        parsed = urlparse(raw)
        parts = [part for part in (parsed.path or "").split("/") if part]
        if len(parts) < 5 or parts[-1].lower() != "pdf":
            return ""
        return urlunparse((parsed.scheme or "https", parsed.netloc, "/" + "/".join(parts[:-1]), "", "", ""))
    except Exception:
        return ""


def _classify_transport_failure(remote_info: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Distingue les pannes TLS des contenus payants ou anti-bot."""
    status = str(remote_info.get("status") or "")
    error_kind = str(remote_info.get("error_kind") or "")
    reason = str(remote_info.get("reason") or "")
    if (
        status == "requests_tls_failed"
        or error_kind == "tls_certificate_verification_failed"
        or "CERTIFICATE_VERIFY_FAILED" in reason
    ):
        return {
            "status": "remote_tls_error",
            "message": (
                "Le serveur distant présente un certificat TLS non vérifiable. "
                "La vérification SSL reste active ; une copie ouverte vérifiée sera recherchée ultérieurement."
            ),
        }
    return None


# ============================================================
# Lecture réseau unique par candidat (via HTTPFetcher)
# ============================================================

def _request_headers(url: str, candidate: Dict[str, Any]) -> Dict[str, str]:
    headers = dict(HEADERS)
    if candidate.get("kind") == "pdf":
        headers["Accept"] = "application/pdf,application/octet-stream,text/html;q=0.8,*/*;q=0.5"
    return headers


def _read_remote_content_once(candidate: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], bytes]:
    """
    Récupère le contenu distant en mémoire via le HTTPFetcher unifié.
    """
    url = str(candidate.get("url") or "").strip()
    if not url:
        return False, {
            "status": "invalid_url",
            "url": url,
            "candidate_source": candidate.get("source"),
        }, b""

    headers = _request_headers(url, candidate)
    ok, info, content = GLOBAL_FETCHER.fetch_bytes(
        url=url,
        headers=headers,
        max_bytes=MAX_REMOTE_BYTES,
    )

    info["candidate_source"] = candidate.get("source")
    if "reason" not in info:
        info["reason"] = info.get("status", "")
    if "remote_bytes_read" not in info and "content_bytes" in info:
        info["remote_bytes_read"] = info["content_bytes"]
    if "remote_sha256" not in info and ok and content:
        info["remote_sha256"] = hashlib.sha256(content).hexdigest()
    info["persistent_source_saved"] = False
    return ok, info, content


# ============================================================
# Détection et extraction PDF / HTML / XML
# ============================================================

def _looks_like_pdf(content: bytes) -> bool:
    return bool(content and content[:5] == b"%PDF-")


def _looks_like_xml(content: bytes, content_type: str) -> bool:
    if "xml" in (content_type or ""):
        return True
    head = content[:300].lstrip().lower()
    return head.startswith(b"<?xml") or b"<article" in head or b"<jats" in head


def _looks_like_html(content: bytes, content_type: str) -> bool:
    if "html" in (content_type or ""):
        return True
    head = content[:1000].lower()
    return b"<html" in head or b"<!doctype html" in head


def _sections_to_pages(sections: List[Dict[str, Any]], method: str) -> Dict[str, Any]:
    pages: List[Dict[str, Any]] = []
    full_parts: List[str] = []
    for index, section in enumerate(sections, start=1):
        heading = _safe_text(section.get("heading") or f"Section {index}", 500)
        text = _safe_text(section.get("text") or "")
        if not text:
            continue
        block = f"{heading}\n{text}" if heading else text
        pages.append(
            {
                "page": len(pages) + 1,
                "section": heading,
                "text": block,
                "chars": len(block),
                "words": _word_count(block),
                "has_text": True,
                "extraction_method": method,
            }
        )
        full_parts.append(block)
    full_text = _safe_text("\n\n".join(full_parts))
    return {
        "pages": pages,
        "pages_count": len(pages),
        "pages_with_text": len(pages),
        "text_chars": len(full_text),
        "text_words": _word_count(full_text),
        "full_text": full_text,
        "clean_text": full_text,
        "full_text_preview": _safe_text(full_text, 3000),
        "sections": sections,
        "extraction_method": method,
        "ocr_attempted": False,
        "ocr_engine": None,
        "ocr_confidence": None,
        "ocr_pages_processed": [],
        "ocr_errors": [],
        "temporary_pdf_deleted": True,
        "quality": {
            "is_text_extractable": len(full_text) >= MIN_USEFUL_TEXT_CHARS,
            "needs_ocr": False,
            "empty_pages_count": 0,
        },
    }


def _title_overlap(article_title: str, page_title: str) -> float:
    left = {t for t in _norm(article_title).split() if len(t) >= 4}
    right = {t for t in _norm(page_title).split() if len(t) >= 4}
    if not left or not right:
        return 0.0
    return len(left & right) / max(len(left), 1)


def _classify_html_content(html: str, article_title: str, http_status: Optional[int] = None) -> Dict[str, str]:
    """
    Classe le contenu HTML en : 'antibot', 'paywall', 'success' ou 'unknown'.
    Prend en compte le statut HTTP pour les 403 courts.
    """
    # Cas spécial : 403 avec contenu très court → anti-bot
    if http_status == 403 and len(html) < 2000:
        return {"status": "antibot_blocked", "message": "Accès 403 avec contenu minimal (probablement anti-bot)"}

    # 1) Anti-bot / challenge
    if _is_antibot_html(html):
        return {"status": "antibot_blocked", "message": "Page de challenge anti-bot détectée (Cloudflare, AWS WAF...)"}
    if _is_probably_paywall_or_login(html):
        return {"status": "paywall_blocked", "message": "Paywall / login détecté (accès restreint)"}

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True).lower()
    title_norm = _norm(article_title).lower()

    # 2) Signaux de paywall
    paywall_signals = [
        "purchase this article", "rent this article", "subscribe",
        "institutional login", "sign in to access", "buy now",
        "you are not authorized", "access denied", "pay per view",
        "acheter cet article", "connectez-vous", "accès restreint",
        "this content is for subscribers", "please log in"
    ]
    if any(signal in text for signal in paywall_signals):
        return {"status": "paywall_blocked", "message": "Paywall / accès restreint détecté"}

    # 3) Vérifier la présence du titre
    title_words = set(title_norm.split())
    if title_words:
        head_text = text[:5000]
        if not any(word in head_text for word in title_words if len(word) > 3):
            return {"status": "unknown", "message": "Titre non trouvé dans le contenu"}

    # 4) Marqueurs scientifiques
    scientific_markers = sum(
        marker in text for marker in [
            "abstract", "introduction", "method", "methods", "result", "results",
            "discussion", "conclusion", "references", "bibliography"
        ]
    )
    if len(text) < 2000 and scientific_markers < 2:
        return {"status": "paywall_blocked", "message": "Contenu insuffisant (probablement paywall)"}

    if len(text) > 5000 and scientific_markers >= 2:
        return {"status": "success", "message": "Contenu scientifique complet détecté"}

    if scientific_markers >= 1:
        return {"status": "partial_abstract", "message": "Seul l'abstract ou un résumé est présent (pas le full text)"}

    return {"status": "unknown", "message": "Contenu non reconnu"}

def _extract_html_fulltext(
    content: bytes,
    article: Article,
    final_url: str,
    http_status: Optional[int] = None,
) -> Dict[str, Any]:
    html = content.decode("utf-8", errors="ignore")
    # Classification
    classification = _classify_html_content(
        html,
        article.title or "",
        http_status=http_status,
    )
    status = classification["status"]
    message = classification["message"]

    if status in ("antibot_blocked", "paywall_blocked"):
        return {"ok": False, "status": status, "message": message}

    # Si on est ici, on tente l'extraction normale
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "svg", "nav", "header", "footer", "form", "aside"]):
        node.decompose()

    page_title = _safe_text(
        (soup.find("meta", attrs={"name": "citation_title"}) or {}).get("content")
        if soup.find("meta", attrs={"name": "citation_title"})
        else ""
    ) or _safe_text(soup.title.get_text(" ", strip=True) if soup.title else "")

    selectors = [
        "article", "main", "[role='main']", ".article-body", ".article__body",
        ".c-article-body", ".hlFld-Fulltext", ".fulltext", ".full-text",
        ".html-body", "#article-body", "#body",
        # Structure HTML officielle MDPI. Elle complète les sélecteurs génériques
        # lorsque le conteneur ``article`` est absent ou minimal.
        ".html-article-content", ".html-content__container",
    ]
    roots = []
    for selector in selectors:
        try:
            roots.extend(soup.select(selector))
        except Exception:
            continue
    if not roots and soup.body:
        roots = [soup.body]

    root = max(
        roots,
        key=lambda node: len(_safe_text(node.get_text("\n", strip=True))),
        default=None,
    )
    if root is None:
        return {"ok": False, "status": "html_no_content_root"}

    sections: List[Dict[str, Any]] = []
    current_heading = "Contenu"
    current_parts: List[str] = []

    def flush() -> None:
        nonlocal current_parts
        text = _safe_text("\n".join(current_parts))
        if len(text) >= 80:
            sections.append({"heading": current_heading, "text": text})
        current_parts = []

    for node in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote"]):
        text = _safe_text(node.get_text(" ", strip=True))
        if not text:
            continue
        if node.name in {"h1", "h2", "h3", "h4"}:
            flush()
            current_heading = text[:500]
        elif len(text) >= 25:
            current_parts.append(text)
    flush()

    payload = _sections_to_pages(sections, "html_fulltext")
    text_chars = int(payload.get("text_chars") or 0)
    full_text_norm = _norm(payload.get("full_text") or "")
    scientific_markers = sum(
        marker in full_text_norm
        for marker in [
            "abstract", "introduction", "method", "methods", "result", "results",
            "discussion", "conclusion", "references", "bibliography",
        ]
    )
    overlap = _title_overlap(article.title or "", page_title)
    accepted = (
        text_chars >= MIN_HTML_TEXT_CHARS
        and (scientific_markers >= 2 or overlap >= 0.35)
    )
    payload.update(
        {
            "ok": accepted,
            "status": "html_fulltext_extracted" if accepted else "html_not_fulltext",
            "document_type": "html_scientific_article",
            "page_title": page_title,
            "title_overlap": round(overlap, 4),
            "scientific_markers_count": scientific_markers,
            "source_url": final_url,
            "classification": classification,  # pour traçabilité
        }
    )
    return payload


def _xml_tag(element: ET.Element) -> str:
    return str(element.tag).split("}")[-1].lower()


def _element_text(element: Optional[ET.Element]) -> str:
    if element is None:
        return ""
    return _safe_text(" ".join(part.strip() for part in element.itertext() if part and part.strip()))


def _extract_xml_fulltext(content: bytes, article: Article, final_url: str) -> Dict[str, Any]:
    try:
        root = ET.fromstring(content)
    except Exception as exc:
        return {"ok": False, "status": "xml_parse_failed", "reason": str(exc)}

    sections: List[Dict[str, Any]] = []
    abstract_parts: List[str] = []
    for element in root.iter():
        if _xml_tag(element) == "abstract":
            text = _element_text(element)
            if text:
                abstract_parts.append(text)
    if abstract_parts:
        sections.append({"heading": "Abstract", "text": "\n".join(abstract_parts)})

    for sec in root.iter():
        if _xml_tag(sec) != "sec":
            continue
        heading = "Section"
        paragraphs: List[str] = []
        for child in list(sec):
            tag = _xml_tag(child)
            if tag == "title" and heading == "Section":
                heading = _element_text(child) or heading
            elif tag in {"p", "list", "disp-quote", "boxed-text"}:
                text = _element_text(child)
                if text:
                    paragraphs.append(text)
        text = _safe_text("\n".join(paragraphs))
        if len(text) >= 80:
            sections.append({"heading": heading, "text": text})

    if not sections:
        body = next((el for el in root.iter() if _xml_tag(el) == "body"), None)
        body_text = _element_text(body)
        if body_text:
            sections.append({"heading": "Body", "text": body_text})

    payload = _sections_to_pages(sections, "xml_jats_fulltext")
    accepted = int(payload.get("text_chars") or 0) >= MIN_XML_TEXT_CHARS
    payload.update(
        {
            "ok": accepted,
            "status": "xml_fulltext_extracted" if accepted else "xml_not_fulltext",
            "document_type": "xml_jats_scientific_article",
            "source_url": final_url,
        }
    )
    return payload


def _extract_pdf_fulltext(content: bytes) -> Dict[str, Any]:
    payload = _extract_text_with_ocr_fallback(content)
    text_chars = int(payload.get("text_chars") or 0)
    payload["ok"] = bool((payload.get("quality") or {}).get("is_text_extractable")) and text_chars >= MIN_USEFUL_TEXT_CHARS
    payload["status"] = "pdf_fulltext_extracted" if payload["ok"] else "pdf_text_insufficient"
    payload["document_type"] = "pdf_scientific_article"

    # >>> ENNOSMART_RESEARCH_UPGRADE_V1_GROBID
    if not payload["ok"]:
        try:
            from services.grobid_client import GROBID
            grobid_payload = GROBID.process_pdf(content)
            if grobid_payload.get("ok") and int(grobid_payload.get("text_chars") or 0) > text_chars:
                grobid_payload["fallback_after"] = payload.get("extraction_method")
                return grobid_payload
            payload["grobid_fallback"] = {
                "attempted": True,
                "status": grobid_payload.get("status"),
            }
        except Exception as exc:
            payload["grobid_fallback"] = {
                "attempted": True,
                "error": f"{type(exc).__name__}: {exc}",
            }
    # <<< ENNOSMART_RESEARCH_UPGRADE_V1_GROBID
    return payload


def _extract_embedded_candidates(content: bytes, base_url: str) -> List[Dict[str, Any]]:
    html = content.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    candidates: List[Dict[str, Any]] = []

    meta_names = {
        "citation_pdf_url", "wkhealth_pdf_url", "eprints.document_url",
        "dc.identifier", "dc.relation",
    }
    for meta in soup.find_all("meta"):
        name = str(meta.get("name") or meta.get("property") or "").lower()
        value = str(meta.get("content") or "").strip()
        if value and (name in meta_names or "pdf" in name or "fulltext" in name):
            url = urljoin(base_url, value)
            candidates.append({"url": url, "kind": _content_kind_from_url(url), "source": "html_meta"})

    for link in soup.find_all(["a", "link"]):
        href = str(link.get("href") or "").strip()
        if not href:
            continue
        label = _norm(link.get_text(" ", strip=True) if hasattr(link, "get_text") else "")
        rel = _norm(" ".join(link.get("rel") or []))
        url = urljoin(base_url, href)
        low = url.lower()
        if (
            _looks_like_direct_pdf_url(url)
            or low.endswith((".xml", ".jats", ".nxml"))
            or "full text" in label
            or "fulltext" in rel
            or "jats" in low
        ):
            candidates.append({"url": url, "kind": _content_kind_from_url(url), "source": "html_embedded_link"})
    return _dedupe_candidates(candidates)


def _is_mdpi_short_interstitial(content: bytes, final_url: str) -> bool:
    """Détecte une réponse MDPI 200 courte, sans la prendre pour un article.

    MDPI sert parfois une page temporaire/intermédiaire à la place de la notice
    ou du PDF. Ce cas n'est ni un texte intégral ni une preuve de paywall.
    Il reste donc éligible à la récupération légale OA ultérieure.
    """
    return _is_mdpi_url(final_url) and 0 < len(content) < MDPI_MIN_ARTICLE_HTML_BYTES


# ============================================================
# Construction du résultat et sauvegarde JSON uniquement
# ============================================================

def _candidate_provenance(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "retrieval_stage": candidate.get("retrieval_stage") or "direct_known_urls",
        "source_domain": candidate.get("source_domain"),
        "candidate_source": candidate.get("source"),
        "legal_provider": candidate.get("provider"),
        "license": candidate.get("license"),
        "version": candidate.get("version"),
        "discovered_via": candidate.get("discovered_via"),
    }


def _save_result(project: Project, article: Article, result: Dict[str, Any]) -> Dict[str, Any]:
    out_file = _direct_extracted_path(project, article)
    status_file = _status_path(project, article)
    result["output_path"] = str(out_file)
    _json_dump(out_file, result)
    _json_dump(status_file, result)
    return result


def _existing_success(project: Project, article: Article) -> Optional[Dict[str, Any]]:
    for extraction_source, path in [
        ("direct", _direct_extracted_path(project, article)),
        ("uploaded", _uploaded_extracted_path(project, article)),
    ]:
        saved = _json_read(path)
        if (
            isinstance(saved, dict)
            and saved.get("full_text_status") == "text_extracted"
            and int(saved.get("text_chars") or 0) >= MIN_USEFUL_TEXT_CHARS
            and (
                extraction_source == "uploaded"
                or (
                    saved.get("pipeline") == "direct_known_urls_fulltext_v1"
                    and isinstance(saved.get("identity_verification"), dict)
                    and saved.get("identity_verification", {}).get("verified") is True
                    and saved.get("identity_verification", {}).get("same_article") is True
                )
            )
        ):
            saved["already_extracted"] = True
            saved["extraction_source"] = extraction_source
            saved["output_path"] = str(path)
            return saved
    return None


# ============================================================
# API publique unifiée
# ============================================================

def resolve_and_extract_fulltext_for_article(
    db: Session,
    project: Project,
    article_id: int,
    force: bool = False,
    *,
    refresh_resolution: Optional[bool] = None,
    force_reextract: bool = False,
) -> Dict[str, Any]:
    """
    Extrait depuis les URLs déjà connues par l'article, sans recherche externe.
    Ajoute une classification : paywall_blocked, antibot_blocked, success, etc.
    """
    article = _article_for_project(db, project, article_id)
    should_refresh_resolution = force if refresh_resolution is None else bool(refresh_resolution)

    existing = _existing_success(project, article)
    if existing and not force_reextract:
        existing["pipeline"] = "direct_known_urls_fulltext_v1"
        existing["resolution_refresh_requested"] = should_refresh_resolution
        existing["reused_existing_text"] = True
        return existing

    candidates = _resolve_direct_candidates(article)
    attempts: List[Dict[str, Any]] = []
    queue = list(candidates)
    seen: set[str] = set()
    extracted_candidates = 0

    # Pour stocker le premier statut d'échec significatif. Tout échec direct
    # reste éligible à l'étape suivante de récupération légale OA.
    final_status = None
    final_message = None

    while queue and extracted_candidates < MAX_CANDIDATES:
            candidate = queue.pop(0)
            url = str(candidate.get("url") or "").strip()
            if not url or url.casefold() in seen:
                continue
            seen.add(url.casefold())
            extracted_candidates += 1

            ok, remote_info, content = _read_remote_content_once(candidate)
            attempts.append(remote_info)
            if not ok:
                transport_failure = _classify_transport_failure(remote_info)
                if transport_failure and final_status is None:
                    final_status = transport_failure["status"]
                    final_message = transport_failure["message"]
                # Les réponses HTTP refusées peuvent contenir une page courte
                # utile à la classification, sans être considérées comme un
                # succès d'extraction.
                content_type = str(remote_info.get("content_type") or "")
                if content and _looks_like_html(content, content_type):
                    refusal = _classify_html_content(
                        content.decode("utf-8", errors="ignore"),
                        article.title or "",
                        http_status=remote_info.get("http_status"),
                    )
                    if refusal.get("status") in {"antibot_blocked", "paywall_blocked"}:
                        final_status = refusal["status"]
                        final_message = refusal["message"]
                continue
            if not content:
                continue

            content_type = str(remote_info.get("content_type") or "")
            final_url = str(remote_info.get("final_url") or url)
            extraction: Dict[str, Any]
            source_kind: str

            if _looks_like_pdf(content):
                source_kind = "pdf"
                extraction = _extract_pdf_fulltext(content)
            elif _looks_like_xml(content, content_type):
                source_kind = "xml"
                extraction = _extract_xml_fulltext(content, article, final_url)
            elif _looks_like_html(content, content_type):
                source_kind = "html"
                if _is_mdpi_short_interstitial(content, final_url):
                    extraction = {
                        "ok": False,
                        "status": "publisher_interstitial",
                        "message": (
                            "MDPI a renvoyé une réponse HTML temporaire trop courte "
                            "au lieu de la notice ou du PDF officiel."
                        ),
                        "publisher": "MDPI",
                    }
                else:
                    extraction = _extract_html_fulltext(
                        content,
                        article,
                        final_url,
                        http_status=remote_info.get("http_status"),
                    )
                if not extraction.get("ok"):
                    # On peut essayer d'extraire des liens PDF depuis cette page
                    queue.extend(_extract_embedded_candidates(content, final_url))
                    queue = _dedupe_candidates(queue)
                    # On stocke le statut pour le rapport
                    if extraction.get("status") in ("paywall_blocked", "antibot_blocked", "publisher_interstitial"):
                        final_status = extraction.get("status")
                        final_message = extraction.get("message")
            else:
                attempts.append(
                    {
                        "status": "unsupported_remote_content",
                        "url": url,
                        "final_url": final_url,
                        "content_type": content_type,
                        "content_start": content[:160].decode("utf-8", errors="ignore"),
                    }
                )
                continue

            attempts.append(
                {
                    "status": extraction.get("status"),
                    "url": url,
                    "final_url": final_url,
                    "content_source_kind": source_kind,
                    "text_chars": extraction.get("text_chars"),
                    "extraction_method": extraction.get("extraction_method"),
                }
            )

            if not extraction.get("ok"):
                # Si c'est un paywall ou un anti-bot, on note mais on continue (on pourrait avoir un PDF direct)
                if extraction.get("status") in ("paywall_blocked", "antibot_blocked", "publisher_interstitial"):
                    if final_status is None:
                        final_status = extraction.get("status")
                        final_message = extraction.get("message")
                continue

            # Succès !
            method = str(extraction.get("extraction_method") or source_kind)
            result = {
                "ok": True,
                "article_id": article.id,
                "title": article.title,
                "doi": article.doi,
                "url": article.url,
                "source": article.source,
                "tag": article.tag_article,
                "status": f"text_extracted_{source_kind}",
                "full_text_status": "text_extracted",
                "evidence_level": "full_text",
                "content_source_kind": source_kind,
                "extraction_method": method,
                "needs_consultant_upload": False,
                "storage_mode": "json_only_remote_source_not_saved",
                "saved_pdf": False,
                "saved_html": False,
                "saved_xml": False,
                "remote_source_url": url,
                "remote_final_url": final_url,
                "pdf_source_url": url if source_kind == "pdf" else None,
                "pdf_final_url": final_url if source_kind == "pdf" else None,
                "fulltext_source_url": url,
                "fulltext_final_url": final_url,
                "remote_bytes_read": remote_info.get("remote_bytes_read"),
                "remote_sha256": remote_info.get("remote_sha256"),
                "source_resolver": candidate.get("source"),
                **_candidate_provenance(candidate),
                "attempts": attempts,
                **extraction,
                "generated_at": _utc_now(),
                "pipeline": "direct_known_urls_fulltext_v1",
                "resolution_refresh_requested": should_refresh_resolution,
                "force_reextract_requested": force_reextract,
            }
            identity_verification = verify_article_extraction(article, result)
            result["identity_verification"] = identity_verification
            result["identity_checked_at"] = _utc_now()
            result["needs_legal_recovery"] = False

            if not identity_verification.get("same_article"):
                attempts.append(
                    {
                        "status": "pdf_identity_mismatch",
                        "url": url,
                        "final_url": final_url,
                        "content_source_kind": source_kind,
                        "identity_verification": identity_verification,
                    }
                )
                final_status = "pdf_identity_mismatch"
                final_message = (
                    "Le document téléchargé ne correspond pas de façon fiable "
                    "à l'article sélectionné ; ce candidat est rejeté."
                )
                continue

            return _save_result(project, article, result)

    # Si on arrive ici, aucun succès. On détermine le statut final.
    # Si on a déjà un statut de paywall ou antibot, on le garde.
    if final_status:
        status = final_status
        message = final_message
    else:
        # Sinon on analyse les codes HTTP
        http_codes = [a.get("http_status") for a in attempts if isinstance(a, dict) and a.get("http_status")]
        anti_bot = any(
            str(a.get("status") or "").endswith("antibot_detected")
            for a in attempts if isinstance(a, dict)
        )
        if 403 in http_codes or anti_bot:
            status = "remote_access_blocked"
            message = "Le serveur distant refuse la lecture automatisée ou renvoie une protection anti-robot."
        elif any(code == 404 for code in http_codes):
            status = "remote_copy_unavailable"
            message = "L'URL distante n'est plus disponible (404)."
        else:
            status = "direct_known_urls_exhausted"
            message = "Aucun texte intégral exploitable n'a été obtenu depuis les URLs déjà connues."

    result = {
        "ok": False,
        "article_id": article.id,
        "title": article.title,
        "doi": article.doi,
        "url": article.url,
        "source": article.source,
        "tag": article.tag_article,
        "status": status,
        "full_text_status": "missing_or_blocked_fulltext",
        "content_source_kind": None,
        "needs_consultant_upload": False,
        "needs_legal_recovery": True,
        "storage_mode": "json_only_remote_source_not_saved",
        "saved_pdf": False,
        "saved_html": False,
        "saved_xml": False,
        "candidates_count": len(candidates),
        "attempts": attempts,
        "message": message,
        "generated_at": _utc_now(),
        "pipeline": "direct_known_urls_fulltext_v1",
        "resolution_refresh_requested": should_refresh_resolution,
        "force_reextract_requested": force_reextract,
        "classification": {
            "status": status,
            "message": message,
            "needs_consultant_upload": False,
            "needs_legal_recovery": True,
        }
    }
    return _save_result(project, article, result)


def resolve_and_extract_fulltext_for_selected_articles(
    db: Session,
    project: Project,
    force: bool = False,
    max_articles: Optional[int] = None,
    *,
    refresh_resolution: Optional[bool] = None,
    force_reextract: bool = False,
) -> Dict[str, Any]:
    """Traite tous les articles avec les seules URLs déjà connues."""
    articles = _selected_articles(db, project)
    total = len(articles)
    selected = articles[:max_articles] if max_articles and max_articles > 0 else articles
    should_refresh_resolution = force if refresh_resolution is None else bool(refresh_resolution)
    results: List[Dict[str, Any]] = []

    for article in selected:
        try:
            results.append(
                resolve_and_extract_fulltext_for_article(
                    db=db,
                    project=project,
                    article_id=article.id,
                    refresh_resolution=should_refresh_resolution,
                    force_reextract=force_reextract,
                )
            )
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "article_id": article.id,
                    "title": article.title,
                    "status": "unified_fulltext_error",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "needs_consultant_upload": False,
                    "needs_legal_recovery": True,
                }
            )

    text_extracted = sum(1 for r in results if r.get("full_text_status") == "text_extracted")
    pdf_count = sum(1 for r in results if r.get("content_source_kind") == "pdf" and r.get("ok"))
    html_count = sum(1 for r in results if r.get("content_source_kind") == "html" and r.get("ok"))
    xml_count = sum(1 for r in results if r.get("content_source_kind") == "xml" and r.get("ok"))
    antibot_count = sum(1 for r in results if r.get("status") == "antibot_blocked")
    blocked_count = sum(
        1
        for r in results
        if r.get("status") in {"remote_access_blocked", "antibot_blocked"}
    )
    paywall_count = sum(1 for r in results if r.get("status") == "paywall_blocked")
    need_upload_count = sum(1 for r in results if r.get("needs_consultant_upload") is True)

    summary = {
        "ok": True,
        "project_id": project.id,
        "pipeline": "direct_known_urls_fulltext_v1",
        "selected_articles_count": total,
        "total_selected_articles_count": total,
        "processed_articles_count": len(selected),
        "processed_all_selected_articles": len(selected) == total,
        "max_articles_requested": max_articles,
        "refresh_resolution": should_refresh_resolution,
        "force_reextract": force_reextract,
        "reused_existing_text_count": sum(
            1 for r in results if r.get("reused_existing_text") is True
        ),
        "text_extracted_count": text_extracted,
        "pdf_text_extracted_count": pdf_count,
        "html_text_extracted_count": html_count,
        "xml_text_extracted_count": xml_count,
        "blocked_count": blocked_count,
        "antibot_count": antibot_count,
        "paywall_count": paywall_count,
        "need_upload_count": need_upload_count,
        "need_legal_recovery_count": sum(1 for r in results if r.get("needs_legal_recovery") is True),
        "no_fulltext_count": len(selected) - text_extracted,
        "storage_mode": "json_only_remote_source_not_saved",
        "results": results,
        "generated_at": _utc_now(),
    }

    if articles:
        report_path = _direct_extracted_path(project, articles[0]).parent.parent / "unified_direct_fulltext_report.json"
    else:
        root = storage_root()
        report_path = root / "unified_direct_fulltext_report.json"
    _json_dump(report_path, summary)
    summary["report_path"] = str(report_path)
    return summary


def get_unified_fulltext_status_for_selected_articles(
    db: Session,
    project: Project,
) -> Dict[str, Any]:
    articles = _selected_articles(db, project)
    results: List[Dict[str, Any]] = []
    for article in articles:
        saved = _json_read(_direct_extracted_path(project, article)) or _json_read(_status_path(project, article))
        if not isinstance(saved, dict):
            saved = {
                "article_id": article.id,
                "title": article.title,
                "status": "not_checked",
                "full_text_status": "not_checked",
                "needs_consultant_upload": False,
                "saved_pdf": False,
                "storage_mode": "json_only_remote_source_not_saved",
            }
        else:
            saved = dict(saved)
            saved.setdefault("article_id", article.id)
            saved.setdefault("title", article.title)
        results.append(saved)

    return {
        "ok": True,
        "project_id": project.id,
        "pipeline": "direct_known_urls_fulltext_v1",
        "selected_articles_count": len(articles),
        "text_extracted_count": sum(1 for r in results if r.get("full_text_status") == "text_extracted"),
        "pdf_text_extracted_count": sum(1 for r in results if r.get("content_source_kind") == "pdf" and r.get("ok")),
        "html_text_extracted_count": sum(1 for r in results if r.get("content_source_kind") == "html" and r.get("ok")),
        "xml_text_extracted_count": sum(1 for r in results if r.get("content_source_kind") == "xml" and r.get("ok")),
        "blocked_count": sum(
            1
            for r in results
            if r.get("status") in {"remote_access_blocked", "antibot_blocked"}
        ),
        "antibot_count": sum(1 for r in results if r.get("status") == "antibot_blocked"),
        "paywall_count": sum(1 for r in results if r.get("status") == "paywall_blocked"),
        "need_upload_count": sum(1 for r in results if r.get("needs_consultant_upload") is True),
        "need_legal_recovery_count": sum(1 for r in results if r.get("needs_legal_recovery") is True),
        "not_checked_count": sum(1 for r in results if r.get("status") == "not_checked"),
        "storage_mode": "json_only_remote_source_not_saved",
        "results": results,
        "generated_at": _utc_now(),
    }
