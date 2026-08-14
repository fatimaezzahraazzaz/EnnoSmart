# -*- coding: utf-8 -*-
from __future__ import annotations

"""Résolution OA rapide, déterministe et sans MCP.

Le service travaille par DOI et enrichit ``Article.source_json`` avec des URLs
publiques avant l'extraction directe. L'ordre est volontairement strict :
OpenAlex en lots de 100, puis Unpaywall, Crossref et enfin CORE uniquement pour
les DOI encore non résolus. Les réponses de métadonnées sont partagées dans
Redis afin qu'un DOI déjà consulté ne déclenche pas un nouvel appel réseau.
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, Iterable, List
from urllib.parse import quote, urlparse

import requests
from requests.adapters import HTTPAdapter
from sqlalchemy.orm import Session

from db.models import Article


LOGGER = logging.getLogger("ennoscholar.oa_discovery")
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
UNPAYWALL_URL = "https://api.unpaywall.org/v2"
CROSSREF_WORKS_URL = "https://api.crossref.org/works"
CORE_SEARCH_URL = "https://api.core.ac.uk/v3/search/works/"

OPENALEX_BATCH_SIZE = 100
HTTP_TIMEOUT = max(2.0, float(os.getenv("ENNOSCHOLAR_OA_METADATA_TIMEOUT_SECONDS", "8")))
FALLBACK_WORKERS = max(
    1,
    min(int(os.getenv("ENNOSCHOLAR_OA_METADATA_WORKERS", "12")), 20),
)
CORE_WORKERS = max(
    1,
    min(int(os.getenv("ENNOSCHOLAR_OA_CORE_WORKERS", "2")), 3),
)
POSITIVE_CACHE_TTL = max(
    3600,
    int(os.getenv("ENNOSCHOLAR_OA_REDIS_TTL_SECONDS", "604800")),
)
NEGATIVE_CACHE_TTL = max(
    300,
    int(os.getenv("ENNOSCHOLAR_OA_REDIS_NEGATIVE_TTL_SECONDS", "21600")),
)

_HTTP_LOCAL = threading.local()


def normalize_doi(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(
        r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)",
        "",
        text,
        flags=re.I,
    )
    text = text.strip().rstrip(".,;)")
    return text if text.startswith("10.") and "/" in text else ""


def _article_doi(article: Article) -> str:
    source_json = article.source_json if isinstance(article.source_json, dict) else {}
    return normalize_doi(article.doi or source_json.get("doi"))


def _valid_url(value: Any) -> str:
    value = str(value or "").strip()
    return value if value.startswith(("http://", "https://")) else ""


def _looks_like_pdf(url: str) -> bool:
    low = url.lower()
    path = (urlparse(url).path or "").lower()
    return (
        path.endswith(".pdf")
        or "/pdf/" in path
        or path.endswith("/pdf")
        or "format=pdf" in low
        or "type=pdf" in low
        or "download=pdf" in low
    )


def _dedupe_candidates(values: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, dict) or raw.get("legal_access") is not True:
            continue
        url = _valid_url(raw.get("url"))
        if not url or url.casefold() in seen:
            continue
        seen.add(url.casefold())
        item = dict(raw)
        item["url"] = url
        item.setdefault("kind", "pdf" if _looks_like_pdf(url) else "landing")
        item.setdefault("retrieval_stage", "deterministic_oa")
        out.append(item)
    out.sort(key=lambda item: (0 if item.get("kind") == "pdf" else 1, str(item.get("provider") or "")))
    return out[:20]


def _has_direct_pdf(candidates: Iterable[Dict[str, Any]]) -> bool:
    return any(
        isinstance(candidate, dict) and candidate.get("kind") == "pdf"
        for candidate in candidates
    )


def _chunks(values: List[str], size: int = OPENALEX_BATCH_SIZE) -> Iterable[List[str]]:
    for start in range(0, len(values), max(1, int(size))):
        yield values[start : start + size]


def _http_session() -> requests.Session:
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.trust_env = False
        session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": os.getenv(
                    "ENNOSCHOLAR_FULLTEXT_USER_AGENT",
                    "EnnoSmart-EnnoScholar/5 deterministic-oa",
                ),
            }
        )
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=0, pool_block=True)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _HTTP_LOCAL.session = session
    return session


def _get_json(
    url: str,
    *,
    params: Dict[str, Any] | None = None,
    headers: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = _http_session().get(
                url,
                params=params,
                headers=headers or {},
                timeout=(min(4.0, HTTP_TIMEOUT), HTTP_TIMEOUT),
                allow_redirects=True,
            )
            if response.status_code in {408, 425, 429, 500, 502, 503, 504} and attempt == 0:
                time.sleep(0.25)
                continue
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.15)
    raise RuntimeError(f"metadata_request_failed: {type(last_error).__name__}: {last_error}")


class _RedisMetadataCache:
    def __init__(self) -> None:
        self._client: Any = None
        self._initialized = False
        self._lock = threading.Lock()

    def _get_client(self):
        if self._initialized:
            return self._client
        with self._lock:
            if self._initialized:
                return self._client
            self._initialized = True
            if str(os.getenv("ENNOSCHOLAR_REDIS_ENABLED", "1")).strip().lower() not in {
                "1", "true", "yes", "on"
            }:
                return None
            try:
                import redis

                client = redis.Redis.from_url(
                    os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
                    socket_connect_timeout=0.35,
                    socket_timeout=0.5,
                    decode_responses=True,
                )
                client.ping()
                self._client = client
            except Exception:
                self._client = None
            return self._client

    @staticmethod
    def _key(provider: str, doi: str) -> str:
        digest = hashlib.sha1(doi.encode("utf-8")).hexdigest()
        return f"ennoscholar:deterministic-oa:v1:{provider}:{digest}"

    def get(self, provider: str, doi: str) -> Dict[str, Any] | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            raw = client.get(self._key(provider, doi))
            payload = json.loads(raw) if raw else None
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def set(self, provider: str, doi: str, payload: Dict[str, Any]) -> None:
        client = self._get_client()
        if client is None:
            return
        ttl = POSITIVE_CACHE_TTL if payload else NEGATIVE_CACHE_TTL
        try:
            client.setex(
                self._key(provider, doi),
                ttl,
                json.dumps(payload, ensure_ascii=False, default=str),
            )
        except Exception:
            pass


_CACHE = _RedisMetadataCache()


def _location_candidates(
    location: Dict[str, Any],
    *,
    provider: str,
    discovered_via: str,
) -> List[Dict[str, Any]]:
    if location.get("is_oa") is False:
        return []
    pdf_url = _valid_url(location.get("pdf_url") or location.get("url_for_pdf"))
    landing_url = _valid_url(
        location.get("landing_page_url")
        or location.get("url_for_landing_page")
        or location.get("url")
    )
    out: List[Dict[str, Any]] = []
    for url in [pdf_url, landing_url]:
        if not url:
            continue
        source = location.get("source") if isinstance(location.get("source"), dict) else {}
        out.append(
            {
                "url": url,
                "kind": "pdf" if url == pdf_url or _looks_like_pdf(url) else "landing",
                "source": f"{provider}_deterministic",
                "provider": provider,
                "legal_access": True,
                "license": location.get("license"),
                "version": location.get("version"),
                "host_type": location.get("host_type") or source.get("type"),
                "source_domain": (urlparse(url).hostname or "").lower(),
                "discovered_via": discovered_via,
                "retrieval_stage": "deterministic_oa",
            }
        )
    return out


def _candidates_from_openalex(work: Dict[str, Any]) -> List[Dict[str, Any]]:
    locations: List[Dict[str, Any]] = []
    for key in ["best_oa_location", "primary_location"]:
        value = work.get(key)
        if isinstance(value, dict):
            locations.append(value)
    locations.extend(item for item in (work.get("locations") or []) if isinstance(item, dict))
    return _dedupe_candidates(
        candidate
        for location in locations
        if location.get("is_oa") is True
        for candidate in _location_candidates(
            location,
            provider="openalex",
            discovered_via="openalex_batch_doi",
        )
    )


def mdpi_static_pdf_candidates_for_article(article: Article) -> List[Dict[str, Any]]:
    """Derive les assets PDF officiels ``mdpi-res.com`` sans challenge JS.

    ``www.mdpi.com/.../pdf`` peut servir un interstitiel Akamai aux clients
    HTTP, alors que le fichier OA est egalement publie sur le CDN statique de
    MDPI. Les valeurs sont derivees uniquement du DOI, de la revue et des URLs
    deja presentes dans la notice ; chaque candidat reste valide ensuite par
    sa signature PDF puis par le controle d'identite apres extraction.
    """
    source_json = article.source_json if isinstance(article.source_json, dict) else {}
    doi = _article_doi(article)
    if not doi.startswith("10.3390/"):
        return []

    suffix = doi.split("/", 1)[1]
    doi_match = re.fullmatch(r"([a-z]+)(\d{2})(\d{2})(\d{3,6})", suffix)
    volume = str(int(doi_match.group(2))) if doi_match else ""
    article_number = str(int(doi_match.group(4))) if doi_match else ""

    known_urls = [
        getattr(article, "url", None),
        source_json.get("url"),
        source_json.get("pdf_url"),
        source_json.get("primary_pdf_url"),
    ]
    open_access_pdf = source_json.get("open_access_pdf") or source_json.get("openAccessPdf")
    if isinstance(open_access_pdf, dict):
        known_urls.append(open_access_pdf.get("url"))
    for raw_url in known_urls:
        url = _valid_url(raw_url)
        if not url:
            continue
        parsed = urlparse(url)
        if not ((parsed.hostname or "").lower() in {"mdpi.com", "www.mdpi.com"}):
            continue
        match = re.search(r"/\d{4}-\d{4}/(\d+)/(?:\d+/)?(\d+)(?:/|$)", parsed.path)
        if match:
            volume = str(int(match.group(1)))
            article_number = str(int(match.group(2)))
            break

    if not volume or not article_number:
        return []

    venue = (
        source_json.get("venue")
        or source_json.get("journal")
        or source_json.get("publication_name")
        or ""
    )
    if isinstance(venue, list):
        venue = venue[0] if venue else ""
    journal_slug = re.sub(r"[^a-z0-9]+", "", str(venue or "").casefold())
    doi_prefix = doi_match.group(1) if doi_match else ""

    out: List[Dict[str, Any]] = []
    for slug in dict.fromkeys(value for value in (journal_slug, doi_prefix) if value):
        basename = f"{slug}-{int(volume):02d}-{int(article_number):05d}"
        base = (
            f"https://mdpi-res.com/d_attachment/{slug}/{basename}/"
            f"article_deploy/{basename}"
        )
        for version_suffix in (".pdf", "-v2.pdf", "-v3.pdf", "-v1.pdf"):
            url = base + version_suffix
            out.append({
                "url": url,
                "kind": "pdf",
                "source": "mdpi_official_static_cdn",
                "provider": "mdpi",
                "legal_access": True,
                "host_type": "publisher",
                "access_type": "publisher_open_access",
                "rights_status": "publisher_open_access",
                "source_domain": "mdpi-res.com",
                "discovered_via": "mdpi_static_asset_from_known_identity",
                "retrieval_stage": "deterministic_oa",
            })
    return _dedupe_candidates(out)


def _existing_oa_candidates(article: Article) -> List[Dict[str, Any]]:
    source_json = article.source_json if isinstance(article.source_json, dict) else {}
    # Priorite au CDN statique officiel MDPI : cela evite d'attendre le
    # challenge Akamai du site principal avant d'essayer le vrai fichier OA.
    out: List[Dict[str, Any]] = mdpi_static_pdf_candidates_for_article(article)
    existing = source_json.get("deterministic_oa_candidates")
    if isinstance(existing, list):
        out.extend(item for item in existing if isinstance(item, dict))

    for key in ["best_oa_location", "primary_location"]:
        location = source_json.get(key)
        if isinstance(location, dict) and location.get("is_oa") is True:
            out.extend(
                _location_candidates(
                    location,
                    provider="source_metadata",
                    discovered_via=f"existing_{key}",
                )
            )
    for location in source_json.get("locations") or []:
        if isinstance(location, dict) and location.get("is_oa") is True:
            out.extend(
                _location_candidates(
                    location,
                    provider="source_metadata",
                    discovered_via="existing_locations",
                )
            )

    open_access_pdf = source_json.get("open_access_pdf") or source_json.get("openAccessPdf")
    if isinstance(open_access_pdf, dict):
        url = _valid_url(open_access_pdf.get("url"))
        if url:
            out.append(
                {
                    "url": url,
                    "kind": "pdf" if _looks_like_pdf(url) else "landing",
                    "source": "source_metadata_open_access_pdf",
                    "provider": "source_metadata",
                    "legal_access": True,
                    "source_domain": (urlparse(url).hostname or "").lower(),
                    "discovered_via": "existing_open_access_pdf",
                    "retrieval_stage": "deterministic_oa",
                }
            )

    is_oa = source_json.get("is_open_access") is True or source_json.get("open_access") is True
    if is_oa:
        for key in ["pdf_url", "primary_pdf_url", "oa_url", "url_for_pdf"]:
            url = _valid_url(source_json.get(key))
            if url:
                out.append(
                    {
                        "url": url,
                        "kind": "pdf" if _looks_like_pdf(url) else "landing",
                        "source": "source_metadata_deterministic",
                        "provider": "source_metadata",
                        "legal_access": True,
                        "source_domain": (urlparse(url).hostname or "").lower(),
                        "discovered_via": "existing_open_access_metadata",
                        "retrieval_stage": "deterministic_oa",
                    }
                )
    return _dedupe_candidates(out)


def _openalex_batch(dois: List[str]) -> tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    resolved: Dict[str, List[Dict[str, Any]]] = {}
    pending: List[str] = []
    cache_hits = 0
    errors: List[str] = []
    for doi in dois:
        cached = _CACHE.get("openalex", doi)
        if cached is None:
            pending.append(doi)
            continue
        cache_hits += 1
        candidates = _candidates_from_openalex(cached)
        if candidates:
            resolved[doi] = candidates

    api_key = str(os.getenv("OPENALEX_API_KEY", "") or "").strip()
    mailto = str(os.getenv("OPENALEX_MAILTO", "") or "").strip()
    request_count = 0
    for chunk in _chunks(pending):
        params: Dict[str, Any] = {
            "filter": "doi:" + "|".join(chunk),
            "per_page": 100,
            "select": (
                "id,doi,title,display_name,publication_year,open_access,"
                "best_oa_location,primary_location,locations"
            ),
        }
        if api_key:
            params["api_key"] = api_key
        if mailto:
            params["mailto"] = mailto
        request_count += 1
        try:
            payload = _get_json(OPENALEX_WORKS_URL, params=params)
            works = [item for item in (payload.get("results") or []) if isinstance(item, dict)]
            by_doi = {normalize_doi(work.get("doi")): work for work in works if normalize_doi(work.get("doi"))}
            for doi in chunk:
                work = by_doi.get(doi) or {}
                _CACHE.set("openalex", doi, work)
                candidates = _candidates_from_openalex(work)
                if candidates:
                    resolved[doi] = candidates
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            LOGGER.warning("[EnnoScholar extraction][OA_OPENALEX] lot échoué: %s", exc)

    return resolved, {
        "provider": "openalex",
        "input_count": len(dois),
        "resolved_count": len(resolved),
        "cache_hits": cache_hits,
        "request_count": request_count,
        "errors": errors[:5],
    }


def _cached_provider_call(
    provider: str,
    doi: str,
    loader: Callable[[], Dict[str, Any]],
) -> tuple[Dict[str, Any], bool]:
    cached = _CACHE.get(provider, doi)
    if cached is not None:
        return cached, True
    payload = loader()
    _CACHE.set(provider, doi, payload)
    return payload, False


def _candidates_from_unpaywall(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if payload.get("is_oa") is False:
        return []
    locations = [item for item in (payload.get("oa_locations") or []) if isinstance(item, dict)]
    best = payload.get("best_oa_location")
    if isinstance(best, dict):
        locations.insert(0, best)
    return _dedupe_candidates(
        candidate
        for location in locations
        for candidate in _location_candidates(
            location,
            provider="unpaywall",
            discovered_via="unpaywall_doi",
        )
    )


def _resolve_unpaywall(doi: str) -> tuple[List[Dict[str, Any]], bool]:
    email = str(os.getenv("UNPAYWALL_EMAIL", "") or "").strip()
    if not email:
        return [], False
    payload, cache_hit = _cached_provider_call(
        "unpaywall",
        doi,
        lambda: _get_json(f"{UNPAYWALL_URL}/{quote(doi, safe='')}", params={"email": email}),
    )
    return _candidates_from_unpaywall(payload), cache_hit


_PUBLIC_REPOSITORY_DOMAINS = {
    "arxiv.org",
    "core.ac.uk",
    "europepmc.org",
    "hal.science",
    "inria.hal.science",
    "pmc.ncbi.nlm.nih.gov",
    "zenodo.org",
}


def _repository_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in _PUBLIC_REPOSITORY_DOMAINS)


def _crossref_open_license(message: Dict[str, Any]) -> str:
    for item in message.get("license") or []:
        url = _valid_url(item.get("URL") if isinstance(item, dict) else "")
        low = url.lower()
        if "creativecommons.org" in low or "openaccess" in low:
            return url
    return ""


def _candidates_from_crossref(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    license_url = _crossref_open_license(message)
    landing = _valid_url(message.get("URL"))
    out: List[Dict[str, Any]] = []
    for link in message.get("link") or []:
        if not isinstance(link, dict):
            continue
        url = _valid_url(link.get("URL") or link.get("url"))
        content_type = str(link.get("content-type") or link.get("content_type") or "").lower()
        if not url or ("pdf" not in content_type and not _looks_like_pdf(url)):
            continue
        if not license_url and not _repository_url(url):
            continue
        out.append(
            {
                "url": url,
                "kind": "pdf",
                "source": "crossref_deterministic",
                "provider": "crossref",
                "legal_access": True,
                "license": license_url or None,
                "version": link.get("content-version") or link.get("content_version"),
                "source_domain": (urlparse(url).hostname or "").lower(),
                "discovered_via": "crossref_link_tdm",
                "retrieval_stage": "deterministic_oa",
            }
        )
    if not out and landing and (license_url or _repository_url(landing)):
        out.append(
            {
                "url": landing,
                "kind": "landing",
                "source": "crossref_deterministic",
                "provider": "crossref",
                "legal_access": True,
                "license": license_url or None,
                "source_domain": (urlparse(landing).hostname or "").lower(),
                "discovered_via": "crossref_open_landing",
                "retrieval_stage": "deterministic_oa",
            }
        )
    return _dedupe_candidates(out)


def _resolve_crossref(doi: str) -> tuple[List[Dict[str, Any]], bool]:
    mailto = str(os.getenv("CROSSREF_MAILTO") or os.getenv("UNPAYWALL_EMAIL") or "").strip()
    params = {"mailto": mailto} if mailto else None
    payload, cache_hit = _cached_provider_call(
        "crossref",
        doi,
        lambda: _get_json(f"{CROSSREF_WORKS_URL}/{quote(doi, safe='')}", params=params),
    )
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return _candidates_from_crossref(message), cache_hit


_CORE_URL_KEYS = {
    "downloadUrl",
    "fullTextIdentifier",
    "fullTextLink",
    "pdfUrl",
    "sourceFulltextUrls",
    "urls",
    "links",
    "url",
    "href",
}


def _collect_core_urls(value: Any, *, key_name: str = "") -> List[str]:
    if isinstance(value, str):
        return [value] if key_name in _CORE_URL_KEYS and _valid_url(value) else []
    if isinstance(value, list):
        return [url for item in value for url in _collect_core_urls(item, key_name=key_name)]
    if isinstance(value, dict):
        return [
            url
            for key, nested in value.items()
            if key in _CORE_URL_KEYS or isinstance(nested, (dict, list))
            for url in _collect_core_urls(nested, key_name=key)
        ]
    return []


def _candidates_from_core(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for work in payload.get("results") or []:
        if not isinstance(work, dict):
            continue
        urls = _collect_core_urls(work)
        work_id = work.get("id")
        if work_id not in {None, ""}:
            urls.extend(
                [
                    f"https://core.ac.uk/download/{work_id}.pdf",
                    f"https://files.core.ac.uk/download/{work_id}.pdf",
                ]
            )
        for url in urls:
            url = _valid_url(url)
            if not url:
                continue
            out.append(
                {
                    "url": url,
                    "kind": "pdf" if _looks_like_pdf(url) else "landing",
                    "source": "core_deterministic",
                    "provider": "core",
                    "legal_access": True,
                    "license": work.get("license"),
                    "version": work.get("version"),
                    "host_type": "repository",
                    "source_domain": (urlparse(url).hostname or "").lower(),
                    "discovered_via": "core_api_v3_doi",
                    "retrieval_stage": "deterministic_oa",
                }
            )
    return _dedupe_candidates(out)


def _resolve_core(doi: str) -> tuple[List[Dict[str, Any]], bool]:
    api_key = str(os.getenv("CORE_API_KEY", "") or "").strip()
    if not api_key:
        return [], False
    payload, cache_hit = _cached_provider_call(
        "core",
        doi,
        lambda: _get_json(
            CORE_SEARCH_URL,
            params={"q": f'doi:"{doi}"', "limit": 5},
            headers={"Authorization": f"Bearer {api_key}"},
        ),
    )
    return _candidates_from_core(payload), cache_hit


def _run_provider_for_unresolved(
    provider: str,
    dois: List[str],
    resolver: Callable[[str], tuple[List[Dict[str, Any]], bool]],
    *,
    workers: int,
) -> tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    resolved: Dict[str, List[Dict[str, Any]]] = {}
    cache_hits = 0
    errors: List[str] = []
    if not dois:
        return resolved, {
            "provider": provider,
            "input_count": 0,
            "resolved_count": 0,
            "cache_hits": 0,
            "request_count": 0,
            "errors": [],
        }
    with ThreadPoolExecutor(max_workers=min(workers, len(dois))) as executor:
        futures = {executor.submit(resolver, doi): doi for doi in dois}
        for future in as_completed(futures):
            doi = futures[future]
            try:
                candidates, cache_hit = future.result()
                cache_hits += int(cache_hit)
                if candidates:
                    resolved[doi] = candidates
            except Exception as exc:
                errors.append(f"{doi}: {type(exc).__name__}: {exc}")
    return resolved, {
        "provider": provider,
        "input_count": len(dois),
        "resolved_count": len(resolved),
        "cache_hits": cache_hits,
        "request_count": max(0, len(dois) - cache_hits),
        "errors": errors[:10],
    }


def enrich_articles_with_deterministic_oa(
    db: Session,
    articles: Iterable[Article],
) -> Dict[str, Any]:
    """Ajoute les candidats OA légaux aux articles, sans télécharger le texte."""
    started = time.perf_counter()
    rows = list(articles)
    by_doi: Dict[str, List[Article]] = {}
    for article in rows:
        doi = _article_doi(article)
        if doi:
            by_doi.setdefault(doi, []).append(article)

    all_dois = list(by_doi)
    candidates_by_doi: Dict[str, List[Dict[str, Any]]] = {}
    provider_stats: List[Dict[str, Any]] = []

    for doi, doi_articles in by_doi.items():
        existing = _dedupe_candidates(
            candidate
            for article in doi_articles
            for candidate in _existing_oa_candidates(article)
        )
        if existing:
            candidates_by_doi[doi] = existing

    LOGGER.info(
        "[EnnoScholar extraction][OA_DISCOVERY] articles=%s doi_uniques=%s OpenAlex lots<=100",
        len(rows),
        len(all_dois),
    )

    openalex_input = [
        doi for doi in all_dois if not _has_direct_pdf(candidates_by_doi.get(doi, []))
    ]
    openalex, stats = _openalex_batch(openalex_input)
    for doi, found in openalex.items():
        candidates_by_doi[doi] = _dedupe_candidates(
            [*candidates_by_doi.get(doi, []), *found]
        )
    provider_stats.append(stats)
    LOGGER.info(
        "[EnnoScholar extraction][OA_OPENALEX] résolus=%s/%s requêtes=%s cache=%s",
        stats["resolved_count"],
        stats["input_count"],
        stats["request_count"],
        stats["cache_hits"],
    )

    provider_steps = [
        ("unpaywall", _resolve_unpaywall, FALLBACK_WORKERS),
        ("crossref", _resolve_crossref, FALLBACK_WORKERS),
        ("core", _resolve_core, CORE_WORKERS),
    ]
    for provider, resolver, workers in provider_steps:
        unresolved = [
            doi
            for doi in all_dois
            if not _has_direct_pdf(candidates_by_doi.get(doi, []))
        ]
        found, stats = _run_provider_for_unresolved(
            provider,
            unresolved,
            resolver,
            workers=workers,
        )
        for doi, provider_candidates in found.items():
            candidates_by_doi[doi] = _dedupe_candidates(
                [*candidates_by_doi.get(doi, []), *provider_candidates]
            )
        provider_stats.append(stats)
        LOGGER.info(
            "[EnnoScholar extraction][OA_%s] résolus=%s/%s appels=%s cache=%s",
            provider.upper(),
            stats["resolved_count"],
            stats["input_count"],
            stats["request_count"],
            stats["cache_hits"],
        )

    articles_with_candidates = 0
    provider_candidate_counts: Dict[str, int] = {}
    for article in rows:
        doi = _article_doi(article)
        discovered = _dedupe_candidates(candidates_by_doi.get(doi, [])) if doi else []
        source_json = dict(article.source_json or {})
        existing = source_json.get("deterministic_oa_candidates")
        merged = _dedupe_candidates(
            [*(existing if isinstance(existing, list) else []), *discovered]
        )
        if merged:
            articles_with_candidates += 1
        for candidate in merged:
            provider = str(candidate.get("provider") or "unknown")
            provider_candidate_counts[provider] = provider_candidate_counts.get(provider, 0) + 1
        source_json["deterministic_oa_candidates"] = merged
        source_json["deterministic_oa_discovery"] = {
            "status": "candidate_found" if merged else ("no_doi" if not doi else "providers_exhausted"),
            "doi": doi or None,
            "candidate_count": len(merged),
            "providers": [item["provider"] for item in provider_stats],
            "mcp_called": False,
        }
        article.source_json = source_json
        db.add(article)
    db.commit()

    summary = {
        "stage": "OA_DISCOVERY",
        "input_count": len(rows),
        "doi_count": len(all_dois),
        "articles_with_candidates": articles_with_candidates,
        "resolved_doi_count": len(candidates_by_doi),
        "unresolved_doi_count": len(all_dois) - len(candidates_by_doi),
        "direct_pdf_doi_count": sum(
            1 for doi in all_dois if _has_direct_pdf(candidates_by_doi.get(doi, []))
        ),
        "without_doi_count": len(rows) - sum(len(items) for items in by_doi.values()),
        "provider_candidate_counts": provider_candidate_counts,
        "providers": provider_stats,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    LOGGER.info(
        "[EnnoScholar extraction][OA_DISCOVERY] terminé articles_avec_urls=%s/%s durée=%.1fs",
        articles_with_candidates,
        len(rows),
        time.perf_counter() - started,
    )
    return summary
