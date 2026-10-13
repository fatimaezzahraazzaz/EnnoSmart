from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..domain.models import ArticleIdentity, FulltextCandidate
from ..domain.normalizers import author_last_name, normalize_doi, normalize_title
from ..infrastructure.http import ResilientHttpClient
from .base import FulltextProvider


ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _phrase(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace('"', " ")).strip()


def _title_keywords(value: str, limit: int = 10) -> str:
    tokens = [x for x in normalize_title(value).split() if len(x) >= 3]
    return " ".join(tokens[:limit])


class ArxivProvider(FulltextProvider):
    name = "arxiv"
    priority = 5

    @staticmethod
    def _queries(article: ArticleIdentity) -> list[str]:
        doi = normalize_doi(article.doi)
        title = _phrase(article.title)
        surname = author_last_name(article.authors[0]) if article.authors else ""
        queries: list[str] = []
        if doi:
            queries.append(f"doi:{doi}")
        if title and surname:
            queries.append(f'(ti:"{title}") AND au:"{surname}"')
        if title:
            queries.append(f'ti:"{title}"')
        keywords = _title_keywords(article.title)
        if keywords and keywords.lower() != title.lower():
            queries.append(f"all:{keywords}")
        out: list[str] = []
        seen: set[str] = set()
        for query in queries:
            key = query.lower()
            if key not in seen:
                seen.add(key)
                out.append(query)
        return out

    async def _search_query(
        self,
        search_query: str,
        http: ResilientHttpClient,
    ) -> list[FulltextCandidate]:
        xml_text = await http.get_text(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": search_query,
                "start": 0,
                "max_results": 12,
                "sortBy": "relevance",
                "sortOrder": "descending",
            },
        )
        root = ET.fromstring(xml_text)
        out: list[FulltextCandidate] = []
        for entry in root.findall("a:entry", ATOM):
            entry_id = (entry.findtext("a:id", default="", namespaces=ATOM) or "").strip()
            title = " ".join((entry.findtext("a:title", default="", namespaces=ATOM) or "").split())
            published = entry.findtext("a:published", default="", namespaces=ATOM) or ""
            year = int(published[:4]) if published[:4].isdigit() else None
            candidate_doi = entry.findtext("arxiv:doi", default="", namespaces=ATOM) or None
            authors: list[str] = []
            for author in entry.findall("a:author", ATOM):
                name = author.findtext("a:name", default="", namespaces=ATOM)
                if name:
                    authors.append(name)
            pdf_url = None
            for link in entry.findall("a:link", ATOM):
                if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                    pdf_url = link.attrib.get("href")
                    break
            if not pdf_url and entry_id:
                arxiv_id = entry_id.rstrip("/").split("/")[-1]
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            if pdf_url:
                out.append(
                    FulltextCandidate(
                        provider=self.name,
                        provider_priority=self.priority,
                        pdf_url=pdf_url,
                        landing_url=entry_id,
                        version="submittedVersion",
                        host_type="repository",
                        legal_access=True,
                        access_type="preprint",
                        rights_status="repository_terms",
                        source_domain="arxiv.org",
                        discovered_via="arxiv_api",
                        candidate_doi=candidate_doi,
                        candidate_title=title,
                        candidate_authors=authors,
                        candidate_year=year,
                        raw_metadata={"search_query": search_query},
                    )
                )
        return out

    async def search(self, article: ArticleIdentity, http: ResilientHttpClient) -> list[FulltextCandidate]:
        out: list[FulltextCandidate] = []
        seen: set[str] = set()
        for query in self._queries(article):
            candidates = await self._search_query(query, http)
            for candidate in candidates:
                key = (candidate.pdf_url or "").lower().strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append(candidate)
            # Un DOI exact est suffisant. Sinon on continue avec titre/auteur.
            if query.startswith("doi:") and any(normalize_doi(x.candidate_doi) == normalize_doi(article.doi) for x in candidates):
                break
        return out
