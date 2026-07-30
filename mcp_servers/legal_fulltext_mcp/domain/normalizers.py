from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse


def normalize_doi(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"^doi\s*:\s*", "", text)
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    return text.strip().strip(" .;,()[]{}")


def normalize_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(value: str | None) -> str:
    text = normalize_text(value)
    stop = {"a", "an", "the", "of", "and", "for", "to", "in", "on", "de", "la", "le", "les", "des", "du", "et"}
    tokens = [token for token in text.split() if token not in stop]
    return " ".join(tokens)


def author_last_name(value: str | None) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    if "," in (value or ""):
        return normalize_text((value or "").split(",", 1)[0])
    parts = text.split()
    return parts[-1] if parts else ""


def normalize_url(value: str | None) -> str:
    text = (value or "").strip()
    if text.startswith("//"):
        text = "https:" + text
    return text


def is_http_url(value: str | None) -> bool:
    try:
        parsed = urlparse(normalize_url(value))
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def looks_like_pdf_url(value: str | None) -> bool:
    url = normalize_url(value).lower()
    return bool(
        url
        and (
            url.endswith(".pdf")
            or ".pdf?" in url
            or "/pdf/" in url
            or "/pdf?" in url
            or url.endswith("/pdf")
            or "/pdf-vor" in url
            or "/document" in url
            or "download/pdf" in url
            or "downloadpdf" in url
            or "/viewfile/" in url
            or "viewcontent.cgi" in url
            or "download_file" in url
            or "downloadfile" in url
        )
    )


def looks_like_download_url(value: str | None) -> bool:
    """Indice seulement : la décision finale repose toujours sur les octets reçus."""
    url = normalize_url(value).lower()
    return bool(
        looks_like_pdf_url(url)
        or "/download/" in url
        or "download=" in url
        or "attachment=" in url
        or "/bitstream/" in url
    )
