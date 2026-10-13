# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scholar_fulltext_fetcher.py

Résolution directe des PDF / full text EnnoScholar SANS stockage PDF.

Objectif :
- prendre les articles gardés par le consultant ;
- chercher un PDF déjà référencé depuis arXiv, URL directe, source_json, DOI ou page HTML ;
- exploiter seulement les liens déjà présents dans les métadonnées ;
- retourner un statut clair :
    * pdf_url_available : un PDF public a été trouvé, mais le PDF n'est PAS sauvegardé ;
    * missing_pdf / blocked / paywall : upload consultant nécessaire ;
- sauvegarder uniquement des JSON de statut et éventuellement des HTML de debug/landing ;
- ne jamais stocker le PDF dans fulltext/pdf.

Important :
- L'extraction texte directe se fait dans services/scholar_pdf_direct_extractor.py.
- L'upload consultant se fait dans services/scholar_uploaded_pdf_extractor.py.
- Ce module garde les mêmes fonctions publiques pour ne pas casser les routes existantes :
    fetch_fulltext_pdf_for_article
    fetch_fulltext_pdf_for_selected_articles
    get_fulltext_status_for_selected_articles
"""

import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from modules.common.runtime_paths import storage_root
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
from services.http_client import GLOBAL_FETCHER
import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from db.models import Article, Project, ScholarRun
from services.scholar_selection_scope import get_current_selected_articles

# ============================================================
# Config
# ============================================================

DEFAULT_TIMEOUT = int(os.getenv("ENNOSCHOLAR_FULLTEXT_TIMEOUT", "25"))
MAX_PDF_MB = int(os.getenv("ENNOSCHOLAR_FULLTEXT_MAX_MB", "100"))
MAX_PDF_BYTES = MAX_PDF_MB * 1024 * 1024

USER_AGENT = os.getenv(
    "ENNOSCHOLAR_FULLTEXT_USER_AGENT",
    "EnnoScholar/1.0 (legal open-access full-text retrieval)",
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ============================================================
# Helpers
# ============================================================

def _safe_text(value: Any, max_chars: int = 1000) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if max_chars and len(text) > max_chars:
        return text[:max_chars].strip()
    return text


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strip_accents(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _slugify(value: Any, max_len: int = 80) -> str:
    text = _strip_accents(str(value or ""))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "unknown")[:max_len].strip("_")


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
    root = storage_root()

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
        "html": base / "html",
        "status": base / "status",
        "debug": base / "debug",
        "extracted_direct": base / "extracted_direct",
        "extracted_uploaded": base / "extracted_uploaded",

        # Ancien dossier conservé uniquement pour compatibilité/lecture,
        # mais ce module n'écrit plus jamais dedans.
        "pdf": base / "pdf",
    }


def _article_file_prefix(article: Article) -> str:
    title_slug = _slugify(getattr(article, "title", "") or "article", 60)
    return f"article_{article.id}_{title_slug}"


def _status_path(project: Project, article: Article) -> Path:
    return _fulltext_dirs(project)["status"] / f"{_article_file_prefix(article)}_status.json"


def _html_path(project: Project, article: Article) -> Path:
    return _fulltext_dirs(project)["html"] / f"{_article_file_prefix(article)}.html"


def _debug_html_path(project: Project, article: Article, suffix: str = "not_pdf_debug") -> Path:
    return _fulltext_dirs(project)["debug"] / f"{_article_file_prefix(article)}_{suffix}.html"


def _legacy_pdf_path(project: Project, article: Article) -> Path:
    """
    Ancienne logique.
    Ne plus écrire ici. Gardée pour détecter les anciens fichiers et les signaler.
    """
    return _fulltext_dirs(project)["pdf"] / f"{_article_file_prefix(article)}.pdf"


def _direct_extracted_path(project: Project, article: Article) -> Path:
    return (
        _fulltext_dirs(project)["extracted_direct"]
        / f"{_article_file_prefix(article)}_direct_fulltext.json"
    )


def _uploaded_extracted_path(project: Project, article: Article) -> Path:
    return (
        _fulltext_dirs(project)["extracted_uploaded"]
        / f"{_article_file_prefix(article)}_uploaded_fulltext.json"
    )


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content or b"").hexdigest()


def _looks_like_pdf_bytes(content: bytes) -> bool:
    return bool(content and content[:5] == b"%PDF-")


def _is_probably_paywall_or_login(html: str) -> bool:
    text = (html or "").lower()
    markers = [
        "purchase access",
        "rent this article",
        "institutional login",
        "sign in to access",
        "login to access",
        "subscribe to access",
        "access through your institution",
        "paywall",
        "acheter cet article",
        "connectez-vous pour accéder",
        "connectez vous pour accéder",
        "accès institutionnel",
    ]
    return any(marker in text for marker in markers)


def _is_antibot_html(content: bytes | str) -> bool:
    if isinstance(content, bytes):
        text = content[:8000].decode("utf-8", errors="ignore").lower()
    else:
        text = str(content or "")[:8000].lower()

    markers = [
        "je m'assure que vous n'êtes pas un robot",
        "je m&#39;assure que vous n&#39;",
        "not a robot",
        "captcha",
        "anubis",
        "cloudflare",
        "akamai/interstitial",
        "akamai bot manager",
        "bm-verify",
        "/_sec/verify",
        "provider=interstitial",
        "robot",
        "bot detection",
    ]
    return any(marker in text for marker in markers)


def _normalize_url(url: Any) -> str:
    text = _safe_text(url, 3000)
    if not text:
        return ""
    if text.startswith("//"):
        return "https:" + text
    return text


def _looks_like_direct_pdf_url(url: Any) -> bool:
    """
    Détecte les URLs PDF qui ne finissent pas forcément par `.pdf` :
    MDPI `/pdf?...`, bepress `viewcontent.cgi`, OJS `viewFile`, etc.

    Cette fonction classe seulement le candidat. La validation finale exige
    toujours des bytes commençant par `%PDF-`.
    """
    normalized = _normalize_url(url)
    if not normalized:
        return False

    try:
        parsed = urlparse(normalized)
        path = (parsed.path or "").lower()
        query = (parsed.query or "").lower()
    except Exception:
        path = normalized.lower()
        query = ""

    return any(
        (
            path.endswith(".pdf"),
            path.endswith("/pdf"),
            "/pdf/" in path,
            "/document" in path,
            "/file/" in path,
            "/viewfile/" in path,
            "viewcontent.cgi" in path,
            "downloadpdf" in path,
            "download-pdf" in path,
            "viewpdf" in path,
            "view-pdf" in path,
            "format=pdf" in query,
            "type=pdf" in query,
            "download=pdf" in query,
        )
    )


def _known_content_kind(url: Any) -> str:
    normalized = _normalize_url(url)
    if _looks_like_direct_pdf_url(normalized):
        return "pdf"
    try:
        parsed = urlparse(normalized)
        path = (parsed.path or "").casefold()
        query = (parsed.query or "").casefold()
        if path.endswith((".xml", ".jats", ".nxml")) or "format=xml" in query:
            return "xml"
    except Exception:
        pass
    return "landing"


def _dedupe_urls(urls: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for url in urls:
        url = _normalize_url(url)
        if not url:
            continue
        key = url.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


def _dedupe_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for c in candidates:
        url = _normalize_url(c.get("url"))
        if not url:
            continue
        key = url.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        item = dict(c)
        item["url"] = url
        out.append(item)
    return out


def _mcp_summary_from_diagnostics(
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Compatibilité avec l'ancien extracteur PDF, sans appel externe.

    `scholar_pdf_direct_extractor.py` importe encore cette fonction. La garder
    permet son chargement, mais elle ne consulte aucun résolveur et ne traite
    pas les diagnostics fournis.
    """
    _ = diagnostics
    return {
        "retrieval_stage": "direct_known_urls",
        "external_resolver_called": False,
    }


def _guess_referer(pdf_url: str) -> str:
    try:
        parsed = urlparse(pdf_url)
        parts = parsed.path.strip("/").split("/")
        if parsed.netloc and parts:
            return f"{parsed.scheme}://{parsed.netloc}/{parts[0]}"
        return f"{parsed.scheme}://{parsed.netloc}/"
    except Exception:
        return ""


# ============================================================
# HAL / TEL / theses.fr resolver
# ============================================================

def _extract_nnt_from_text(value: Any) -> str:
    """
    Extrait un NNT uniquement lorsqu'il est explicitement relié à theses.fr
    ou à un champ NNT. Une chaîne hexadécimale/hash quelconque ne doit jamais
    être interprétée comme un numéro de thèse.
    """
    text = str(value or "")
    patterns = [
        r"https?://(?:www\.)?theses\.fr/([0-9]{4}[A-Z0-9]{6,24})(?:[/\s?#]|$)",
        r"\bNNT\s*[:：=]?\s*([0-9]{4}[A-Z0-9]{6,24})\b",
        r"""["']?nntId_s["']?\s*[:=]\s*["']?([0-9]{4}[A-Z0-9]{6,24})\b""",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()

    return ""


def _hal_doc_to_pdf_candidates(doc: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []

    def add(value: Any) -> None:
        if not value:
            return

        if isinstance(value, str):
            v = value.strip()
            if not v:
                return
            if v.startswith("http"):
                candidates.append(v)
            elif v.startswith("/"):
                candidates.append("https://hal.science" + v)

        elif isinstance(value, list):
            for item in value:
                add(item)

        elif isinstance(value, dict):
            for key in [
                "url",
                "file",
                "filename",
                "downloadUrl",
                "download_url",
                "href",
            ]:
                add(value.get(key))

    for key in [
        "fileMain_s",
        "files_s",
        "file_s",
        "fulltext_s",
        "linkExtUrl_s",
    ]:
        add(doc.get(key))

    uri = doc.get("uri_s")
    hal_id = doc.get("halId_s") or doc.get("halId_id")
    docid = doc.get("docid")

    if isinstance(uri, str) and uri.startswith("http"):
        candidates.append(uri.rstrip("/") + "/document")

    if hal_id:
        candidates.append(f"https://hal.science/{hal_id}/document")
        candidates.append(f"https://theses.hal.science/{hal_id}/document")
        candidates.append(f"https://tel.archives-ouvertes.fr/{hal_id}/document")

    if docid:
        candidates.append(f"https://hal.science/hal-{docid}/document")
        candidates.append(f"https://theses.hal.science/tel-{docid}/document")

    filtered: List[str] = []
    for url in candidates:
        u = _normalize_url(url)
        low = u.lower()
        if not u:
            continue
        if (
            low.endswith(".pdf")
            or "/document" in low
            or "/file/" in low
            or "pdf" in low
            or "hal.science" in low
            or "archives-ouvertes.fr" in low
        ):
            filtered.append(u)

    return _dedupe_urls(filtered)


def _search_hal_api_pdf_candidates(
    title: str = "",
    doi: str = "",
    nnt: str = "",
    max_results: int = 8,
) -> List[Dict[str, Any]]:
    queries: List[Dict[str, str]] = []

    title = _safe_text(title, 600)
    doi = _safe_text(doi, 300)
    nnt = _safe_text(nnt, 100).upper()

    if nnt:
        queries.append({"mode": "hal_nnt_exact", "q": f'nntId_s:"{nnt}"'})
        queries.append({"mode": "hal_nnt_free", "q": nnt})

    if doi:
        doi_clean = (
            doi.replace("https://doi.org/", "")
            .replace("http://doi.org/", "")
            .strip()
        )
        if doi_clean:
            queries.append({"mode": "hal_doi_exact", "q": f'doiId_s:"{doi_clean}"'})
            queries.append({"mode": "hal_doi_free", "q": doi_clean})

    if title:
        title_query = title.replace('"', " ")
        queries.append({"mode": "hal_title_exact", "q": f'title_t:"{title_query}"'})
        queries.append({"mode": "hal_title_free", "q": f'"{title_query}"'})

    endpoint = "https://api.archives-ouvertes.fr/search/"
    all_candidates: List[Dict[str, Any]] = []

    for query in queries:
        params = {
            "wt": "json",
            "rows": str(max_results),
            "q": query["q"],
            "fl": (
                "docid,halId_s,title_s,uri_s,fileMain_s,files_s,"
                "linkExtUrl_s,doiId_s,nntId_s,submittedDate_s,docType_s"
            ),
        }

        try:
            resp = requests.get(
                endpoint,
                params=params,
                headers=HEADERS,
                timeout=DEFAULT_TIMEOUT,
                allow_redirects=True,
            )

            if resp.status_code >= 400:
                continue

            data = resp.json()
            docs = data.get("response", {}).get("docs", []) if isinstance(data, dict) else []

            for doc in docs:
                for pdf_url in _hal_doc_to_pdf_candidates(doc):
                    all_candidates.append(
                        {
                            "ok": True,
                            "mode": query["mode"],
                            "source": "hal_api",
                            "url": pdf_url,
                            "title": doc.get("title_s"),
                            "hal_id": doc.get("halId_s"),
                            "docid": doc.get("docid"),
                            "doi": doc.get("doiId_s"),
                            "nnt": doc.get("nntId_s"),
                            "uri": doc.get("uri_s"),
                        }
                    )

        except Exception:
            continue

    out: List[Dict[str, Any]] = []
    seen = set()
    for candidate in all_candidates:
        url = _normalize_url(candidate.get("url"))
        if not url:
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        candidate["url"] = url
        out.append(candidate)

    return out


def _build_hal_pdf_candidates_for_article(article: Article, nnt: str = "") -> List[str]:
    """Compatibilité : aucune recherche HAL n'est autorisée au stade direct.

    Les liens HAL déjà présents dans ``source_json`` restent exploités par
    ``_extract_pdf_candidates_from_source_json``. Une recherche par titre ou
    DOI appartient exclusivement au futur stade de récupération OA légale.
    """
    del article, nnt
    return []


# ============================================================
# Candidate PDF URLs
# ============================================================

def _arxiv_pdf_url(url: str) -> str:
    if not url:
        return ""

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "arxiv.org" not in host:
        return ""

    path = parsed.path.strip("/")

    if path.startswith("pdf/"):
        if url.lower().endswith(".pdf"):
            return url
        return url.rstrip("/") + ".pdf"

    if path.startswith("abs/"):
        arxiv_id = path.replace("abs/", "", 1).strip("/")
        if arxiv_id:
            return f"https://arxiv.org/pdf/{arxiv_id}.pdf"

    if url.lower().endswith(".pdf"):
        return url

    return ""


def _extract_pdf_candidates_from_source_json(source_json: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []

    def add(value: Any) -> None:
        url = _normalize_url(value)
        if url:
            candidates.append(url)

    for key in [
        "pdf_url",
        "pdfUrl",
        "pdf",
        "oa_url",
        "url_for_pdf",
        "download_url",
        "fulltext_url",
        "full_text_url",
    ]:
        add(source_json.get(key))

    open_access_pdf = source_json.get("openAccessPdf") or source_json.get("open_access_pdf")
    if isinstance(open_access_pdf, dict):
        add(open_access_pdf.get("url"))

    open_access = source_json.get("open_access")
    if isinstance(open_access, dict):
        add(open_access.get("oa_url"))

    primary_location = source_json.get("primary_location")
    if isinstance(primary_location, dict):
        add(primary_location.get("pdf_url"))
        add(primary_location.get("landing_page_url"))

        source = primary_location.get("source")
        if isinstance(source, dict):
            add(source.get("homepage_url"))

    best_oa = source_json.get("best_oa_location")
    if isinstance(best_oa, dict):
        add(best_oa.get("pdf_url"))
        add(best_oa.get("landing_page_url"))

    locations = source_json.get("locations")
    if isinstance(locations, list):
        for loc in locations[:10]:
            if isinstance(loc, dict):
                add(loc.get("pdf_url"))
                add(loc.get("landing_page_url"))

    article_url = _normalize_url(source_json.get("url"))
    arxiv_pdf = _arxiv_pdf_url(article_url)
    if arxiv_pdf:
        candidates.append(arxiv_pdf)

    return _dedupe_urls(candidates)


def _extract_pdf_candidates_from_html(html: str, base_url: str) -> List[str]:
    candidates: List[str] = []
    soup = BeautifulSoup(html or "", "html.parser")

    meta_names = [
        "citation_pdf_url",
        "bepress_citation_pdf_url",
        "wkhealth_pdf_url",
        "eprints.document_url",
    ]

    for name in meta_names:
        for meta in soup.find_all("meta", attrs={"name": name}):
            content = meta.get("content")
            if content:
                candidates.append(urljoin(base_url, content))

    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        content = meta.get("content")
        if not content:
            continue
        if "pdf" in prop and ("url" in prop or "citation" in prop):
            candidates.append(urljoin(base_url, content))

    for link in soup.find_all("link"):
        href = link.get("href")
        if not href:
            continue
        typ = (link.get("type") or "").lower()
        rel = " ".join(link.get("rel") or []).lower()
        if "pdf" in typ or "pdf" in rel or href.lower().endswith(".pdf"):
            candidates.append(urljoin(base_url, href))

    for a in soup.find_all("a"):
        href = a.get("href")
        if not href:
            continue

        text = _safe_text(a.get_text(" "), 120).lower()
        href_low = href.lower()

        absolute_url = urljoin(base_url, href)
        is_pdf_like = (
            _looks_like_direct_pdf_url(absolute_url)
            or text in {"pdf", "download pdf", "view pdf", "article pdf"}
            or ("pdf" in text and len(text) < 80)
        )

        if is_pdf_like:
            candidates.append(absolute_url)

    candidates = _dedupe_urls(candidates)
    candidates.sort(
        key=lambda u: (
            0 if u.lower().endswith(".pdf") else 1,
            0 if "pdf" in u.lower() else 1,
            len(u),
        )
    )
    return candidates


def build_candidate_urls_for_article(
    article: Article,
    *,
    include_mcp: bool = False,
    force_mcp: bool = False,
    mcp_diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Construit seulement les URLs déjà connues par la première recherche.

    Cette version directe n'interroge aucun résolveur externe : elle exploite
    les métadonnées déjà stockées dans l'article et les liens présents dans ses
    données source. Les copies ouvertes trouvées ultérieurement sont une étape
    séparée, volontairement absente de ce service.
    """
    # Paramètres historiques conservés seulement pour l'ancien extracteur PDF.
    # Ils sont volontairement ignorés dans ce test direct.
    _ = include_mcp, force_mcp
    if mcp_diagnostics is not None:
        mcp_diagnostics.append(
            {
                "status": "external_resolution_disabled",
                "external_resolver_called": False,
            }
        )

    sj = _as_dict(getattr(article, "source_json", None))
    candidates: List[Dict[str, Any]] = []

    # Repli editeur sans navigateur : les PDF OA de MDPI sont aussi publies
    # sur leur CDN statique, qui ne depend pas du challenge Akamai de
    # ``www.mdpi.com``. Cette voie reste validee par magic byte et identite.
    try:
        from services.scholar_deterministic_oa_service import (
            mdpi_static_pdf_candidates_for_article,
        )
        candidates.extend(mdpi_static_pdf_candidates_for_article(article))
    except Exception:
        pass

    # Candidats légaux déjà vérifiés par le MCP pendant le contrôle d'accès.
    # Ils sont prioritaires lors de l'extraction déclenchée au clic et ne
    # provoquent aucun nouvel appel MCP dans ce service direct.
    mcp_candidates = sj.get("mcp_fulltext_candidates")
    if isinstance(mcp_candidates, list):
        for raw in mcp_candidates[:20]:
            if not isinstance(raw, dict):
                continue
            if raw.get("legal_access") is not True or raw.get("same_article") is not True:
                continue
            url = _normalize_url(raw.get("final_url") or raw.get("pdf_url") or raw.get("url"))
            if not url:
                continue
            item = dict(raw)
            item["url"] = url
            item.setdefault("kind", _known_content_kind(url))
            item.setdefault("source", f"legal_mcp:{str(item.get('provider') or 'unknown')}")
            item.setdefault("retrieval_stage", "legal_mcp_access_probe")
            candidates.append(item)

    # URLs OA préparées en une passe déterministe globale (OpenAlex batch,
    # Unpaywall, Crossref, CORE). Elles sont déjà qualifiées comme publiques ;
    # le téléchargement et le contrôle d'identité restent effectués ci-après.
    deterministic_oa = sj.get("deterministic_oa_candidates")
    if isinstance(deterministic_oa, list):
        for raw in deterministic_oa[:20]:
            if not isinstance(raw, dict) or raw.get("legal_access") is not True:
                continue
            url = _normalize_url(raw.get("url"))
            if not url:
                continue
            item = dict(raw)
            item["url"] = url
            item.setdefault(
                "kind",
                _known_content_kind(url),
            )
            item.setdefault(
                "source",
                f"{str(item.get('provider') or 'oa')}_deterministic",
            )
            item.setdefault("retrieval_stage", "deterministic_oa")
            candidates.append(item)

    article_url = _normalize_url(getattr(article, "url", None) or sj.get("url"))
    doi = _safe_text(getattr(article, "doi", None) or sj.get("doi"), 300)

    arxiv_pdf = _arxiv_pdf_url(article_url)
    if arxiv_pdf:
        candidates.append({"kind": "pdf", "source": "arxiv_url_transform", "url": arxiv_pdf})

    if article_url and _looks_like_direct_pdf_url(article_url):
        candidates.append(
            {
                "kind": "pdf",
                "source": "article_url_pdf_like",
                "url": article_url,
            }
        )

    for url in _extract_pdf_candidates_from_source_json(sj):
        kind = _known_content_kind(url)
        candidates.append(
            {
                "kind": kind,
                "source": "source_json",
                "url": url,
            }
        )

    if doi:
        doi_clean = (
            doi.replace("https://doi.org/", "")
            .replace("http://doi.org/", "")
            .strip()
        )
        if doi_clean:
            candidates.append({"kind": "landing", "source": "doi", "url": f"https://doi.org/{doi_clean}"})

    if article_url:
        candidates.append({"kind": _known_content_kind(article_url), "source": "article_url", "url": article_url})

    return _dedupe_candidates(candidates)


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


# ============================================================
# HTTP / HTML / PDF probe SANS stockage PDF
# ============================================================

def _request_get(url: str, stream: bool = False) -> requests.Response:
    return requests.get(
        url,
        headers=HEADERS,
        timeout=DEFAULT_TIMEOUT,
        allow_redirects=True,
        stream=stream,
    )


def _fetch_html(url: str) -> Tuple[bool, Dict[str, Any]]:
    """
    Télécharge une page HTML via le fetcher unifié.
    Retourne (ok, info) avec 'html' dans info si succès.
    """
    ok, info, html_text = GLOBAL_FETCHER.fetch_text(
        url=url,
        headers=HEADERS,
        max_chars=500_000,  # on limite à ~500k caractères pour éviter la mémoire
        referer=_guess_referer(url),
    )

    # Normalisation du retour pour correspondre à l'ancienne signature
    if ok:
        content_type = str(info.get("content_type") or "").casefold()
        if "application/pdf" in content_type:
            info["status"] = "html_fetch_is_pdf"
            info["html"] = ""
            info["paywall_detected"] = False
            info["antibot_detected"] = False
        else:
            info["status"] = "html_fetched"
            info["html"] = html_text
            info["paywall_detected"] = _is_probably_paywall_or_login(html_text)
            info["antibot_detected"] = _is_antibot_html(html_text)
    else:
        # Si l'échec est dû à une erreur HTTP, on le remonte proprement
        if "http_status" in info and info["http_status"] >= 400:
            info["status"] = "html_fetch_failed"
            info["reason"] = f"HTTP {info['http_status']}"
        else:
            info["status"] = "html_fetch_failed"
    return ok, info


def _looks_like_scientific_fulltext_html(html: str, article_title: str = "") -> bool:
    """Distingue une page article complete d'une simple notice/abstract."""
    if not html or _is_antibot_html(html) or _is_probably_paywall_or_login(html):
        return False
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "svg", "nav", "header", "footer"]):
        node.decompose()
    text = _strip_accents(_safe_text(soup.get_text(" ", strip=True), 0)).casefold()
    if len(text) < 5_000:
        return False
    markers = sum(
        marker in text
        for marker in (
            "abstract", "introduction", "method", "methods", "results",
            "discussion", "conclusion", "references", "bibliography",
        )
    )
    title_words = {
        word
        for word in re.sub(
            r"[^a-z0-9]+",
            " ",
            _strip_accents(str(article_title or "")).casefold(),
        ).split()
        if len(word) >= 5
    }
    text_words = set(re.sub(r"[^a-z0-9]+", " ", text[:8_000]).split())
    title_match = not title_words or bool(title_words.intersection(text_words))
    return markers >= 3 and title_match

def _probe_pdf_url_without_saving(
    url: str,
    project: Optional[Project] = None,
    article: Optional[Article] = None,
    depth: int = 0,
    visited: Optional[set] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Vérifie qu'une URL renvoie un vrai PDF sans sauvegarder le PDF.
    Utilise le fetcher unifié avec une limite de téléchargement (256 Ko suffisent).
    """
    if visited is None:
        visited = set()

    if depth > 2:
        return False, {"status": "max_depth_reached", "url": url, "saved_pdf": False}

    url_key = (url or "").lower().strip()
    if not url_key or url_key in visited:
        return False, {"status": "skipped_duplicate_or_empty", "url": url, "saved_pdf": False}
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

    # Télécharger seulement les premiers 256 Ko pour vérifier le type
    ok, info, content = GLOBAL_FETCHER.fetch_bytes(
        url=url,
        headers=headers,
        max_bytes=256 * 1024,  # suffisant pour détecter %PDF-
        referer=referer,
    )

    info["url"] = url
    info["referer"] = referer
    info["saved_pdf"] = False

    if not ok:
        # On normalise le statut
        if info.get("http_status", 0) >= 400:
            info["status"] = "download_failed"
            info["reason"] = f"HTTP {info['http_status']}"
        else:
            info["status"] = "download_failed"
            info["reason"] = info.get("reason", "unknown_error")
        return False, info

    # Vérifier si c'est un vrai PDF
    if content[:5] == b"%PDF-":
        info["status"] = "pdf_url_available"
        info["full_text_status"] = "pdf_url_available"
        info["bytes_checked"] = len(content)
        info["sha256_head"] = hashlib.sha256(content).hexdigest()
        info["message"] = "PDF public trouvé. Aucun PDF n'a été stocké."
        return True, info

    # Sinon, on regarde si c'est une page HTML avec des liens PDF imbriqués
    html_text = ""
    try:
        html_text = content.decode("utf-8", errors="ignore")
    except Exception:
        try:
            html_text = content.decode("latin-1", errors="ignore")
        except Exception:
            pass

    antibot = _is_antibot_html(content)
    if html_text and "<html" in html_text.lower() and not antibot:
        nested_candidates = _extract_pdf_candidates_from_html(
            html=html_text,
            base_url=info.get("final_url") or url,
        )

        for nested_url in nested_candidates[:8]:
            if nested_url == url:
                continue
            ok_nested, info_nested = _probe_pdf_url_without_saving(
                nested_url,
                project=project,
                article=article,
                depth=depth + 1,
                visited=visited,
            )
            info_nested["nested_from_url"] = url
            info_nested["nested_from_final_url"] = info.get("final_url")
            if ok_nested:
                return True, info_nested

    status = "blocked_by_antibot" if antibot else "not_pdf_response"
    info.update({
        "status": status,
        "reason": (
            "PDF trouvé mais téléchargement automatique bloqué par une page anti-robot."
            if antibot
            else "Le serveur a renvoyé une réponse qui ne commence pas par %PDF-."
        ),
        "content_start": content[:160].decode("utf-8", errors="ignore") if content else "",
    })
    return False, info
# Ancien nom conservé pour compatibilité, mais il ne télécharge plus.
def _download_pdf_url(url: str, target_path: Optional[Path] = None) -> Tuple[bool, Dict[str, Any]]:
    """
    Compatibilité ancienne.
    Ne sauvegarde plus le PDF même si target_path est fourni.
    """
    ok, info = _probe_pdf_url_without_saving(url)
    info["deprecated_function"] = "_download_pdf_url"
    info["storage_policy"] = "json_only_no_pdf_saved"
    if target_path is not None:
        info["ignored_target_path"] = str(target_path)
    return ok, info


# ============================================================
# DB article selection
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
# Extracted status helpers
# ============================================================

def _read_extracted_status(project: Project, article: Article) -> Optional[Dict[str, Any]]:
    """
    Regarde si le texte complet est déjà extrait par :
    - extraction directe PDF distant ;
    - upload PDF consultant.
    """
    candidates = [
        ("uploaded", _uploaded_extracted_path(project, article)),
        ("direct", _direct_extracted_path(project, article)),
    ]

    for source_kind, path in candidates:
        data = _json_read(path)
        if not isinstance(data, dict):
            continue

        text_chars = int(data.get("text_chars") or 0)
        pages_count = int(data.get("pages_count") or 0)

        if text_chars >= 1000:
            return {
                "ok": True,
                "article_id": article.id,
                "title": article.title,
                "tag": article.tag_article,
                "source": article.source,
                "doi": article.doi,
                "url": article.url,
                "status": "text_extracted",
                "full_text_status": "text_extracted",
                "evidence_level": "full_text",
                "extraction_source": source_kind,
                "extracted_text_path": str(path),
                "pages_count": pages_count,
                "text_chars": text_chars,
                "needs_consultant_upload": False,
                "saved_pdf": False,
                "storage_mode": data.get("storage_mode") or "json_only_no_pdf_saved",
                "generated_at": data.get("generated_at"),
            }

    return None


# ============================================================
# Main service
# ============================================================

def fetch_fulltext_pdf_for_article(
    db: Session,
    project: Project,
    article_id: int,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Compatibilité route existante.

    Avant :
    - téléchargeait et stockait le PDF dans fulltext/pdf.

    Maintenant :
    - cherche seulement une URL PDF publique ;
    - sauvegarde un status JSON ;
    - ne stocke jamais le PDF ;
    - l'extraction doit être faite ensuite par scholar_pdf_direct_extractor.py.
    """
    article = get_article_for_project(db, project, article_id)

    dirs = _fulltext_dirs(project)
    dirs["status"].mkdir(parents=True, exist_ok=True)
    dirs["html"].mkdir(parents=True, exist_ok=True)
    dirs["debug"].mkdir(parents=True, exist_ok=True)

    status_file = _status_path(project, article)
    html_file = _html_path(project, article)

    # 0. Si texte déjà extrait, c'est le meilleur statut.
    extracted_status = _read_extracted_status(project, article)
    if extracted_status and not force:
        _json_dump(status_file, extracted_status)
        return extracted_status

    saved_status = _json_read(status_file)
    if saved_status and not force:
        # Si ancien status pdf_path, on le neutralise.
        saved_status["saved_pdf"] = False
        saved_status["pdf_path"] = None
        if saved_status.get("full_text_status") == "pdf_available":
            saved_status["full_text_status"] = "pdf_url_available"
            saved_status["status"] = "pdf_url_available"
        return saved_status

    candidates = build_candidate_urls_for_article(article)
    attempts: List[Dict[str, Any]] = []

    # 0. Les sources XML/JATS publiques sont extractibles au clic, comme les
    # PDF et les pages HTML scientifiques completes.
    for candidate in candidates:
        if candidate.get("kind") != "xml":
            continue
        ok, info, content = GLOBAL_FETCHER.fetch_bytes(
            url=candidate["url"],
            headers=HEADERS,
            max_bytes=256 * 1024,
        )
        attempts.append({**info, "candidate_source": candidate.get("source"), "candidate_kind": "xml"})
        content_type = str(info.get("content_type") or "").casefold()
        head = content[:500].lstrip().casefold()
        if ok and content and ("xml" in content_type or head.startswith(b"<?xml") or b"<article" in head):
            status = {
                "ok": True,
                "article_id": article.id,
                "title": article.title,
                "doi": article.doi,
                "url": article.url,
                "status": "xml_fulltext_available",
                "full_text_status": "xml_fulltext_available",
                "evidence_level": "fulltext_url",
                "content_source_kind": "xml",
                "storage_mode": "json_only_no_remote_source_saved",
                "saved_pdf": False,
                "fulltext_source_url": candidate["url"],
                "fulltext_final_url": info.get("final_url") or candidate["url"],
                "resolver": candidate.get("source"),
                **_candidate_provenance(candidate),
                "needs_consultant_upload": False,
                "next_step": "run_direct_extraction",
                "message": "Texte integral XML/JATS public trouve ; extraction au clic.",
                "attempts": attempts,
                "candidates": candidates,
                "generated_at": datetime.utcnow().isoformat(),
            }
            _json_dump(status_file, status)
            return status

    # 1. Tester les candidats PDF directs sans stockage.
    for candidate in candidates:
        if candidate.get("kind") != "pdf":
            continue

        ok, info = _probe_pdf_url_without_saving(
            candidate["url"],
            project=project,
            article=article,
        )
        info["candidate_source"] = candidate.get("source")
        info["candidate_kind"] = "pdf"
        attempts.append(info)

        if ok:
            status = {
                "ok": True,
                "article_id": article.id,
                "title": article.title,
                "doi": article.doi,
                "url": article.url,
                "status": "pdf_url_available",
                "full_text_status": "pdf_url_available",
                "evidence_level": "pdf_url",
                "storage_mode": "json_only_no_pdf_saved",
                "saved_pdf": False,
                "pdf_path": None,
                "pdf_source_url": candidate.get("pdf_url") or info.get("url") or candidate.get("url"),
                "pdf_final_url": info.get("final_url") or candidate.get("final_url") or candidate.get("url"),
                "download_url": candidate.get("pdf_url") or info.get("url") or candidate.get("url"),
                "final_url": info.get("final_url") or candidate.get("final_url") or candidate.get("url"),
                "resolver": candidate.get("resolver") or candidate.get("source"),
                **_candidate_provenance(candidate),
                "needs_consultant_upload": False,
                "next_step": "run_direct_extraction",
                "message": (
                    "PDF public trouvé. Aucun PDF n'a été stocké. "
                    "Lance l'extraction directe pour créer extracted_direct/*.json."
                ),
                "attempts": attempts,
                "candidates": candidates,
                "generated_at": datetime.utcnow().isoformat(),
            }
            _json_dump(status_file, status)
            return status

    # 2. Scraper landing pages.
    for candidate in candidates:
        if candidate.get("kind") != "landing":
            continue

        url = candidate["url"]
        html_ok, html_info = _fetch_html(url)
        html_info["candidate_source"] = candidate.get("source")
        html_info["candidate_kind"] = "landing"

        attempts.append({k: v for k, v in html_info.items() if k != "html"})

        if not html_ok:
            continue

        html = html_info.get("html") or ""

        # Une page HTML scientifique complete est elle-meme une source de
        # texte integral. La phase legere la marque disponible sans lancer ici
        # l'extraction, qui restera une action explicite du consultant.
        if _looks_like_scientific_fulltext_html(html, article.title or ""):
            status = {
                "ok": True,
                "article_id": article.id,
                "title": article.title,
                "doi": article.doi,
                "url": article.url,
                "status": "html_fulltext_available",
                "full_text_status": "html_fulltext_available",
                "evidence_level": "fulltext_url",
                "content_source_kind": "html",
                "storage_mode": "json_only_no_remote_source_saved",
                "saved_pdf": False,
                "fulltext_source_url": url,
                "fulltext_final_url": html_info.get("final_url") or url,
                "resolver": candidate.get("source"),
                **_candidate_provenance(candidate),
                "needs_consultant_upload": False,
                "next_step": "run_direct_extraction",
                "message": (
                    "Texte integral HTML public trouve. "
                    "L'extraction sera lancee uniquement au clic du consultant."
                ),
                "attempts": attempts,
                "candidates": candidates,
                "generated_at": datetime.utcnow().isoformat(),
            }
            _json_dump(status_file, status)
            return status

        # 2A. DOI/landing redirige vers PDF.
        if html_info.get("status") == "html_fetch_is_pdf":
            ok, info = _probe_pdf_url_without_saving(
                url,
                project=project,
                article=article,
            )
            info["candidate_source"] = candidate.get("source")
            info["candidate_kind"] = "landing_is_pdf"
            attempts.append(info)

            if ok:
                status = {
                    "ok": True,
                    "article_id": article.id,
                    "title": article.title,
                    "doi": article.doi,
                    "url": article.url,
                    "status": "pdf_url_available",
                    "full_text_status": "pdf_url_available",
                    "evidence_level": "pdf_url",
                    "storage_mode": "json_only_no_pdf_saved",
                    "saved_pdf": False,
                    "pdf_path": None,
                    "pdf_source_url": info.get("url"),
                    "pdf_final_url": info.get("final_url"),
                    "download_url": info.get("url"),
                    "final_url": info.get("final_url"),
                    "resolver": "landing_is_pdf",
                    "needs_consultant_upload": False,
                    "next_step": "run_direct_extraction",
                    "attempts": attempts,
                    "candidates": candidates,
                    "generated_at": datetime.utcnow().isoformat(),
                }
                _json_dump(status_file, status)
                return status

        # 2B. theses.fr / NNT -> HAL/TEL.
        nnt = _extract_nnt_from_text(f"{html_info.get('final_url') or ''}\n{html or ''}")
        if nnt:
            hal_pdf_candidates = _build_hal_pdf_candidates_for_article(article=article, nnt=nnt)

            for hal_pdf_url in hal_pdf_candidates[:10]:
                ok, info = _probe_pdf_url_without_saving(
                    hal_pdf_url,
                    project=project,
                    article=article,
                )
                info["candidate_source"] = "hal_api_from_theses_nnt"
                info["nnt"] = nnt
                info["landing_url"] = url
                attempts.append(info)

                if ok:
                    status = {
                        "ok": True,
                        "article_id": article.id,
                        "title": article.title,
                        "doi": article.doi,
                        "url": article.url,
                        "status": "pdf_url_available",
                        "full_text_status": "pdf_url_available",
                        "evidence_level": "pdf_url",
                        "storage_mode": "json_only_no_pdf_saved",
                        "saved_pdf": False,
                        "pdf_path": None,
                        "pdf_source_url": info.get("url"),
                        "pdf_final_url": info.get("final_url"),
                        "download_url": info.get("url"),
                        "final_url": info.get("final_url"),
                        "html_path": str(html_file) if html_file.exists() else None,
                        "needs_consultant_upload": False,
                        "resolver": "hal_api_from_theses_nnt",
                        "nnt": nnt,
                        "next_step": "run_direct_extraction",
                        "attempts": attempts,
                        "candidates": candidates,
                        "generated_at": datetime.utcnow().isoformat(),
                    }
                    _json_dump(status_file, status)
                    return status

        # 2C. Sauvegarder HTML landing/debug seulement.
        if html:
            html_file.parent.mkdir(parents=True, exist_ok=True)
            html_file.write_text(html, encoding="utf-8", errors="ignore")

        # 2D. Chercher PDF dans HTML.
        pdf_candidates = _extract_pdf_candidates_from_html(
            html=html,
            base_url=html_info.get("final_url") or url,
        )

        for pdf_url in pdf_candidates[:15]:
            ok, info = _probe_pdf_url_without_saving(
                pdf_url,
                project=project,
                article=article,
            )
            info["candidate_source"] = "html_scraped_pdf_link"
            info["landing_url"] = url
            attempts.append(info)

            if ok:
                status = {
                    "ok": True,
                    "article_id": article.id,
                    "title": article.title,
                    "doi": article.doi,
                    "url": article.url,
                    "status": "pdf_url_available",
                    "full_text_status": "pdf_url_available",
                    "evidence_level": "pdf_url",
                    "storage_mode": "json_only_no_pdf_saved",
                    "saved_pdf": False,
                    "pdf_path": None,
                    "pdf_source_url": info.get("url"),
                    "pdf_final_url": info.get("final_url"),
                    "download_url": info.get("url"),
                    "final_url": info.get("final_url"),
                    "html_path": str(html_file) if html_file.exists() else None,
                    "needs_consultant_upload": False,
                    "resolver": "html_scraped_pdf_link",
                    "next_step": "run_direct_extraction",
                    "attempts": attempts,
                    "candidates": candidates,
                    "generated_at": datetime.utcnow().isoformat(),
                }
                _json_dump(status_file, status)
                return status

    # 3. Aucun PDF exploitable.
    paywall_seen = any(a.get("paywall_detected") for a in attempts)
    antibot_seen = any(a.get("status") == "blocked_by_antibot" or a.get("antibot_detected") for a in attempts)

    status_name = "blocked_need_upload" if antibot_seen else ("paywall_or_unavailable" if paywall_seen else "not_available_need_upload")

    status = {
        "ok": False,
        "article_id": article.id,
        "title": article.title,
        "doi": article.doi,
        "url": article.url,
        "status": status_name,
        "full_text_status": "missing_pdf",
        "evidence_level": "metadata_or_abstract_only",
        "storage_mode": "json_only_no_pdf_saved",
        "saved_pdf": False,
        "pdf_path": None,
        "needs_consultant_upload": True,
        "message": (
            "Aucun PDF public exploitable n'a été résolu automatiquement "
            "ou le téléchargement automatique est bloqué. "
            "Le consultant doit ouvrir le PDF public si disponible puis l'importer."
        ),
        "attempts": attempts,
        "candidates": candidates,
        "html_path": str(html_file) if html_file.exists() else None,
        "generated_at": datetime.utcnow().isoformat(),
    }

    _json_dump(status_file, status)
    return status


def fetch_fulltext_pdf_for_selected_articles(
    db: Session,
    project: Project,
    force: bool = False,
    max_articles: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Tente de résoudre les URLs PDF de tous les articles gardés.
    Ne stocke aucun PDF.

    Règle métier corrigée :
    - max_articles = None ou 0 => traiter TOUS les articles sélectionnés ;
    - max_articles > 0      => limiter volontairement le traitement.

    Important : le consultant peut garder 60, 80 ou plus d'articles. Le service
    ne doit donc plus appliquer de limite technique par défaut.
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
            result = fetch_fulltext_pdf_for_article(
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
                    "needs_consultant_upload": True,
                    "saved_pdf": False,
                    "pdf_path": None,
                }
            )

    pdf_url_available = sum(1 for r in results if r.get("full_text_status") == "pdf_url_available")
    text_extracted = sum(1 for r in results if r.get("full_text_status") == "text_extracted")
    need_upload = sum(1 for r in results if r.get("needs_consultant_upload") is True)

    summary = {
        "ok": True,
        "project_id": project.id,
        "selected_articles_count": total_selected_articles,
        "total_selected_articles_count": total_selected_articles,
        "processed_articles_count": len(articles_to_process),
        "max_articles_requested": max_articles,
        "max_articles_applied": max_articles_applied,
        "processed_all_selected_articles": len(articles_to_process) == total_selected_articles,
        "pdf_url_available_count": pdf_url_available,
        "text_extracted_count": text_extracted,
        "pdf_available_count": pdf_url_available + text_extracted,  # compat ancien front
        "need_upload_count": need_upload,
        "storage_policy": "json_only_no_pdf_saved",
        "results": results,
        "generated_at": datetime.utcnow().isoformat(),
    }

    out_path = _fulltext_dirs(project)["base"] / "fulltext_fetch_report.json"
    _json_dump(out_path, summary)
    summary["report_path"] = str(out_path)

    return summary


def get_fulltext_status_for_selected_articles(
    db: Session,
    project: Project,
) -> Dict[str, Any]:
    """
    Liste le statut PDF/fulltext des articles gardés.

    Priorité :
    1. texte déjà extrait ;
    2. status JSON de résolution PDF ;
    3. ancien PDF stocké signalé comme legacy seulement ;
    4. non vérifié.
    """
    articles = get_selected_articles_for_project(db, project)
    results: List[Dict[str, Any]] = []

    for article in articles:
        extracted_status = _read_extracted_status(project, article)
        status_file = _status_path(project, article)
        saved_status = _json_read(status_file)
        legacy_pdf = _legacy_pdf_path(project, article)

        if extracted_status:
            status = extracted_status

        elif saved_status:
            status = {
                "article_id": article.id,
                "title": article.title,
                "tag": article.tag_article,
                "source": article.source,
                "doi": article.doi,
                "url": article.url,
                **saved_status,
            }
            status["saved_pdf"] = False
            status["pdf_path"] = None
            if status.get("full_text_status") == "pdf_available":
                status["full_text_status"] = "pdf_url_available"
                status["status"] = "pdf_url_available"

        elif legacy_pdf.exists():
            # On ne l'utilise plus comme source principale, mais on le signale
            # pour que tu puisses nettoyer le dossier si besoin.
            status = {
                "ok": True,
                "article_id": article.id,
                "title": article.title,
                "tag": article.tag_article,
                "source": article.source,
                "doi": article.doi,
                "url": article.url,
                "status": "legacy_pdf_exists_not_used",
                "full_text_status": "legacy_pdf_exists_not_used",
                "legacy_pdf_path": str(legacy_pdf),
                "legacy_pdf_bytes": legacy_pdf.stat().st_size,
                "needs_consultant_upload": False,
                "saved_pdf": False,
                "pdf_path": None,
                "message": (
                    "Ancien PDF présent sur disque, mais la nouvelle logique ne dépend plus du stockage PDF. "
                    "Relance l'extraction directe ou l'upload pour créer le JSON fulltext."
                ),
            }

        else:
            status = {
                "ok": False,
                "article_id": article.id,
                "title": article.title,
                "tag": article.tag_article,
                "source": article.source,
                "doi": article.doi,
                "url": article.url,
                "full_text_status": "not_checked",
                "status": "not_checked",
                "needs_consultant_upload": None,
                "saved_pdf": False,
                "pdf_path": None,
            }

        results.append(status)

    return {
        "ok": True,
        "project_id": project.id,
        "selected_articles_count": len(articles),
        "text_extracted_count": sum(1 for r in results if r.get("full_text_status") == "text_extracted"),
        "pdf_url_available_count": sum(1 for r in results if r.get("full_text_status") == "pdf_url_available"),
        "pdf_available_count": sum(
            1 for r in results if r.get("full_text_status") in {"text_extracted", "pdf_url_available"}
        ),  # compat ancien front
        "not_checked_count": sum(1 for r in results if r.get("full_text_status") == "not_checked"),
        "need_upload_count": sum(1 for r in results if r.get("needs_consultant_upload") is True),
        "legacy_pdf_count": sum(1 for r in results if r.get("full_text_status") == "legacy_pdf_exists_not_used"),
        "storage_policy": "json_only_no_pdf_saved",
        "results": results,
    }
