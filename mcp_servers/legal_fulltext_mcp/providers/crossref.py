from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from ..domain.models import ArticleIdentity, FulltextCandidate
from ..domain.normalizers import looks_like_pdf_url, normalize_doi
from ..infrastructure.http import HttpRequestError, ResilientHttpClient
from .base import FulltextProvider
from .source_utils import classify_public_source, source_domain


def _first_title(message: dict[str, Any]) -> str | None:
    title = message.get("title")
    if isinstance(title, list) and title:
        return str(title[0])
    return str(title) if isinstance(title, str) else None


def _authors(message: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for author in message.get("author") or []:
        if not isinstance(author, dict):
            continue
        name = f"{author.get('given') or ''} {author.get('family') or ''}".strip()
        if name:
            out.append(name)
    return out


def _year(message: dict[str, Any]) -> int | None:
    for key in ["published-print", "published-online", "issued", "created"]:
        value = message.get(key)
        if not isinstance(value, dict):
            continue
        parts = value.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            try:
                return int(parts[0][0])
            except Exception:
                pass
    return None


def _license_urls(message: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in message.get("license") or []:
        if isinstance(item, dict) and isinstance(item.get("URL"), str):
            out.append(item["URL"])
    return out


def _explicit_open_license(urls: list[str]) -> bool:
    joined = " ".join(urls).lower()
    return "creativecommons.org" in joined or "openaccess" in joined


def _mdpi_static_pdf_urls(message: dict[str, Any]) -> list[str]:
    """Construit les URLs statiques officielles MDPI à partir de Crossref.

    MDPI peut renvoyer un interstitiel sur ``www.mdpi.com/.../pdf`` alors que
    le même PDF OA est servi par ``mdpi-res.com``. Aucun DOI d'article n'est
    codé en dur : le nom du journal, le volume et le numéro d'article viennent
    exclusivement de la notice Crossref.
    """
    doi = normalize_doi(message.get("DOI"))
    if not doi.startswith("10.3390/"):
        return []

    container = message.get("container-title") or []
    journal = container[0] if isinstance(container, list) and container else container
    journal_slug = re.sub(r"[^a-z0-9]+", "", str(journal or "").lower())
    volume = re.sub(r"\D+", "", str(message.get("volume") or ""))
    article_number = re.sub(
        r"\D+",
        "",
        str(message.get("article-number") or message.get("page") or ""),
    )

    # Repli générique pour les DOI MDPI au format <revue><vol><issue><article>.
    suffix = doi.split("/", 1)[1]
    match = re.fullmatch(r"([a-z]+)(\d{2})(\d{2})(\d{3,6})", suffix)
    doi_prefix = match.group(1) if match else ""
    if not volume and match:
        volume = str(int(match.group(2)))
    if not article_number and match:
        article_number = str(int(match.group(4)))

    if not volume or not article_number:
        return []

    slugs = [journal_slug, doi_prefix]
    out: list[str] = []
    for slug in slugs:
        if not slug:
            continue
        basename = f"{slug}-{int(volume):02d}-{int(article_number):05d}"
        base = (
            f"https://mdpi-res.com/d_attachment/{slug}/{basename}/"
            f"article_deploy/{basename}"
        )
        for suffix_value in (".pdf", "-v2.pdf", "-v3.pdf", "-v1.pdf"):
            url = base + suffix_value
            if url not in out:
                out.append(url)
    return out


class CrossrefProvider(FulltextProvider):
    name = "crossref"
    priority = 3

    def __init__(self, mailto: str = "") -> None:
        self.mailto = (mailto or "").strip()

    async def _load_messages(self, article: ArticleIdentity, http: ResilientHttpClient) -> list[dict[str, Any]]:
        doi = normalize_doi(article.doi)
        params = {"mailto": self.mailto} if self.mailto else None
        messages: list[dict[str, Any]] = []
        doi_error: HttpRequestError | None = None
        if doi:
            try:
                data = await http.get_json(
                    f"https://api.crossref.org/works/{quote(doi, safe='')}",
                    params=params,
                )
                message = data.get("message")
                if isinstance(message, dict):
                    messages.append(message)
            except HttpRequestError as exc:
                doi_error = exc
        query_params: dict[str, Any] = {"query.title": article.title, "rows": 5}
        if self.mailto:
            query_params["mailto"] = self.mailto
        try:
            data = await http.get_json("https://api.crossref.org/works", params=query_params)
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            messages.extend(x for x in (message.get("items") or []) if isinstance(x, dict))
        except HttpRequestError:
            if not messages and doi_error is not None:
                raise doi_error
            if not messages:
                raise
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for message in messages:
            key = str(message.get("DOI") or _first_title(message) or "").lower()
            if key and key not in seen:
                seen.add(key)
                out.append(message)
        return out

    async def search(self, article: ArticleIdentity, http: ResilientHttpClient) -> list[FulltextCandidate]:
        out: list[FulltextCandidate] = []
        seen: set[str] = set()
        for message in await self._load_messages(article, http):
            license_urls = _license_urls(message)
            open_license = _explicit_open_license(license_urls)
            landing = message.get("URL") if isinstance(message.get("URL"), str) else None
            emitted = False

            # Le CDN statique MDPI est une source éditeur OA officielle et
            # reste accessible lorsque le site principal sert un interstitiel.
            if open_license:
                for url in _mdpi_static_pdf_urls(message):
                    key = url.lower().strip()
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(
                        FulltextCandidate(
                            provider=self.name,
                            provider_priority=self.priority,
                            pdf_url=url,
                            landing_url=landing,
                            license=license_urls[0] if license_urls else None,
                            version="publishedVersion",
                            host_type="publisher",
                            legal_access=True,
                            access_type="publisher_open_access",
                            rights_status="explicit_open_license",
                            source_domain=source_domain(url),
                            discovered_via="crossref_mdpi_static_cdn",
                            candidate_doi=message.get("DOI"),
                            candidate_title=_first_title(message),
                            candidate_authors=_authors(message),
                            candidate_year=_year(message),
                            raw_metadata={
                                "license_urls": license_urls,
                                "container_title": message.get("container-title"),
                                "article_number": message.get("article-number"),
                            },
                        )
                    )
                    emitted = True

            for item in message.get("link") or []:
                if not isinstance(item, dict):
                    continue
                url = item.get("URL") or item.get("url")
                content_type = str(item.get("content-type") or item.get("content_type") or "").lower()
                if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                    continue
                if "pdf" not in content_type and not looks_like_pdf_url(url):
                    continue
                access_type, rights_status, domain = classify_public_source(
                    url,
                    license_value=license_urls[0] if license_urls else None,
                )
                repository_like = access_type in {
                    "repository_copy",
                    "preprint",
                    "public_author_copy",
                }
                legal_access = open_license or repository_like
                if not legal_access:
                    continue
                key = url.lower().strip()
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    FulltextCandidate(
                        provider=self.name,
                        provider_priority=self.priority,
                        pdf_url=url,
                        landing_url=landing,
                        license=license_urls[0] if license_urls else None,
                        version=item.get("content-version") or item.get("content_version"),
                        host_type="publisher" if not repository_like else "repository",
                        legal_access=True,
                        access_type=access_type,
                        rights_status=rights_status,
                        source_domain=source_domain(url),
                        discovered_via="crossref_link",
                        candidate_doi=message.get("DOI"),
                        candidate_title=_first_title(message),
                        candidate_authors=_authors(message),
                        candidate_year=_year(message),
                        raw_metadata={
                            "intended_application": item.get("intended-application"),
                            "content_type": content_type,
                            "license_urls": license_urls,
                        },
                    )
                )
                emitted = True

            # Crossref peut annoncer une licence ouverte ou une notice de
            # dépôt public sans fournir de lien typé PDF. La landing page est
            # alors sondée pour découvrir le fichier public.
            landing_access = classify_public_source(landing)[0] if landing else None
            landing_is_repository = landing_access in {
                "repository_copy",
                "preprint",
                "public_author_copy",
            }
            if (
                (open_license or landing_is_repository)
                and not emitted
                and isinstance(landing, str)
                and landing.startswith(("http://", "https://"))
            ):
                key = landing.lower().strip()
                if key not in seen:
                    seen.add(key)
                    access_type, rights_status, domain = classify_public_source(
                        landing,
                        license_value=license_urls[0] if license_urls else None,
                    )
                    out.append(
                        FulltextCandidate(
                            provider=self.name,
                            provider_priority=self.priority,
                            pdf_url=landing,
                            landing_url=landing,
                            license=license_urls[0] if license_urls else None,
                            host_type="repository" if landing_is_repository else "publisher",
                            legal_access=True,
                            access_type=access_type,
                            rights_status=rights_status,
                            source_domain=domain,
                            discovered_via=(
                                "crossref_repository_landing"
                                if landing_is_repository
                                else "crossref_open_landing"
                            ),
                            candidate_doi=message.get("DOI"),
                            candidate_title=_first_title(message),
                            candidate_authors=_authors(message),
                            candidate_year=_year(message),
                            raw_metadata={"license_urls": license_urls, "direct_pdf_available": False},
                        )
                    )
        return out
