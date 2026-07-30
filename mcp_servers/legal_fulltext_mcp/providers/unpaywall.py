from __future__ import annotations

from urllib.parse import quote, urlsplit, urlunsplit

from ..domain.models import ArticleIdentity, FulltextCandidate
from ..domain.normalizers import normalize_doi
from ..infrastructure.http import ResilientHttpClient
from .base import FulltextProvider


def _without_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _pdf_variants(pdf_url: str | None, landing_url: str | None) -> list[str]:
    urls: list[str] = []
    if pdf_url:
        urls.append(pdf_url.strip())
    elif landing_url:
        # La page OA reste exploitable : le probe inspecte citation_pdf_url,
        # Dublin Core et les liens de téléchargement.
        urls.append(landing_url.strip())
    for raw in [pdf_url, landing_url]:
        if not raw or "mdpi.com" not in raw.lower():
            continue
        clean = _without_query(raw.strip()).rstrip("/")
        if clean.lower().endswith("/pdf") or clean.lower().endswith("/pdf-vor"):
            urls.append(clean)
        else:
            urls.append(clean + "/pdf")
            urls.append(clean + "/pdf-vor")
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        key = url.lower()
        if url.startswith(("http://", "https://")) and key not in seen:
            seen.add(key)
            out.append(url)
    return out


class UnpaywallProvider(FulltextProvider):
    name = "unpaywall"
    priority = 1

    def __init__(self, email: str) -> None:
        self.email = (email or "").strip()

    def enabled(self) -> bool:
        return bool(self.email)

    def disabled_reason(self) -> str | None:
        return None if self.enabled() else "UNPAYWALL_EMAIL manquant"

    async def search(self, article: ArticleIdentity, http: ResilientHttpClient) -> list[FulltextCandidate]:
        doi = normalize_doi(article.doi)
        if not doi:
            return []
        data = await http.get_json(
            f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
            params={"email": self.email},
        )
        raw_locations = data.get("oa_locations") or []
        best = data.get("best_oa_location")
        if isinstance(best, dict):
            raw_locations = [best] + [x for x in raw_locations if x != best]

        authors: list[str] = []
        for item in data.get("z_authors") or []:
            if isinstance(item, dict):
                name = f"{item.get('given') or ''} {item.get('family') or ''}".strip()
                if name:
                    authors.append(name)

        out: list[FulltextCandidate] = []
        seen: set[str] = set()
        for loc in raw_locations:
            if not isinstance(loc, dict):
                continue
            landing_url = loc.get("url_for_landing_page") or loc.get("url")
            variants = _pdf_variants(loc.get("url_for_pdf"), landing_url)
            for pdf_url in variants:
                key = pdf_url.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    FulltextCandidate(
                        provider=self.name,
                        provider_priority=self.priority,
                        pdf_url=pdf_url,
                        landing_url=landing_url,
                        license=loc.get("license"),
                        version=loc.get("version"),
                        host_type=loc.get("host_type"),
                        legal_access=True,
                        candidate_doi=data.get("doi"),
                        candidate_title=data.get("title"),
                        candidate_authors=authors,
                        candidate_year=data.get("year"),
                        raw_metadata={
                            "is_oa": data.get("is_oa"),
                            "oa_status": data.get("oa_status"),
                            "original_pdf_url": loc.get("url_for_pdf"),
                        },
                    )
                )
        return out
