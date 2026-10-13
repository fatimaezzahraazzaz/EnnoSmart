from __future__ import annotations

"""Découverte générique de candidats PDF depuis une page scientifique.

Aucune URL d'article ni aucun identifiant bibliographique n'est codé en dur.
Les règles s'appuient uniquement sur les standards HTML/JSON et sur des
conventions stables de plateformes éditoriales.
"""

import html
import json
import re
from urllib.parse import unquote, urljoin, urlparse

from ..domain.normalizers import looks_like_download_url

_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+", re.I)
_ESCAPED_URL_RE = re.compile(r"https?:\\/\\/[^\s\"'<>]+", re.I)
_PDF_JSON_KEYS = {
    "pdf", "pdfurl", "pdf_url", "pdfpath", "pdf_path", "citationpdfurl",
    "downloadurl", "download_url", "fulltexturl", "full_text_url",
    "contenturl", "content_url", "fileurl", "file_url", "documenturl",
    "document_url", "articlepdf", "article_pdf",
}


def _clean_url(value: str | None, base_url: str) -> str | None:
    if not value:
        return None
    candidate = html.unescape(str(value)).strip().strip("\"' ")
    candidate = candidate.replace("\\/", "/").replace("\\u002F", "/")
    candidate = unquote(candidate)
    if candidate.startswith("javascript:") or candidate.startswith("data:"):
        return None
    absolute = urljoin(base_url, candidate)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return absolute


def _looks_pdfish(value: str, context: str = "") -> bool:
    text = f"{value} {context}".lower()
    return bool(
        looks_like_download_url(value)
        or re.search(r"\b(pdf|full[ -]?text|download|t[eé]l[eé]charger|view pdf|get pdf)\b", text)
        or "application/pdf" in text
    )


def _walk_json(value, base_url: str, out: list[str], parent_key: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            norm_key = re.sub(r"[^a-z0-9]+", "", str(key).lower())
            if isinstance(item, str) and (norm_key in _PDF_JSON_KEYS or _looks_pdfish(item, str(key))):
                url = _clean_url(item, base_url)
                if url and url not in out:
                    out.append(url)
            _walk_json(item, base_url, out, str(key))
    elif isinstance(value, list):
        for item in value:
            _walk_json(item, base_url, out, parent_key)
    elif isinstance(value, str) and _looks_pdfish(value, parent_key):
        url = _clean_url(value, base_url)
        if url and url not in out:
            out.append(url)


def extract_script_pdf_urls(script_text: str, base_url: str) -> list[str]:
    """Extrait des URLs PDF de JSON-LD, états JS et variables embarquées."""
    out: list[str] = []
    text = html.unescape(script_text or "")
    stripped = text.strip()

    # JSON complet ou fragment JSON-LD.
    if stripped.startswith(("{", "[")):
        try:
            _walk_json(json.loads(stripped), base_url, out)
        except Exception:
            pass

    # Paires clé/valeur fréquentes dans les bundles JavaScript.
    key_pattern = re.compile(
        r"(?:pdfUrl|pdf_url|pdfPath|pdf_path|citation_pdf_url|downloadUrl|download_url|"
        r"fullTextUrl|full_text_url|contentUrl|documentUrl)\s*[\"']?\s*[:=]\s*[\"']([^\"']+)[\"']",
        re.I,
    )
    for match in key_pattern.finditer(text):
        url = _clean_url(match.group(1), base_url)
        if url and url not in out:
            out.append(url)

    for regex in (_URL_RE, _ESCAPED_URL_RE):
        for match in regex.finditer(text):
            raw = match.group(0)
            if _looks_pdfish(raw):
                url = _clean_url(raw, base_url)
                if url and url not in out:
                    out.append(url)
    return out


def platform_pdf_candidates(base_url: str, discovered: list[str]) -> list[str]:
    """Complète les candidats à partir de conventions génériques de plateforme.

    Ces transformations ne ciblent aucun article. Elles ne sont utilisées que
    lorsque la page courante appartient à la plateforme concernée, et chaque
    URL produite devra encore être vérifiée par signature PDF et identité.
    """
    out = list(discovered)
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")

    def add(value: str) -> None:
        url = _clean_url(value, base_url)
        if url and url not in out:
            out.append(url)

    # MDPI : les pages d'article exposent habituellement /pdf et /pdf?version=...
    if host == "mdpi.com" or host.endswith(".mdpi.com"):
        if path and not path.lower().endswith(".pdf"):
            add(path + "/pdf")

    # SPIE : une page de proceeding/article possède un endpoint frère .pdf.
    if host.endswith("spiedigitallibrary.org") and path and not path.lower().endswith(".pdf"):
        add(path + ".pdf")

    # J-STAGE : endpoint PDF standard dérivé de la page _article.
    if host.endswith("jstage.jst.go.jp") and path.endswith("/_article"):
        add(path[:-len("/_article")] + "/_pdf")

    # IEEE : ne fabrique pas d'identifiants iel. On exploite seulement le
    # document id présent dans l'URL officielle pour l'endpoint stampPDF.
    if host.endswith("ieeexplore.ieee.org"):
        match = re.search(r"/(?:document|abstract/document)/(\d+)", path)
        if match:
            add(f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={match.group(1)}")

    return out
