"""Read-only adapters to existing scientific clients; all state belongs to V3.1."""
from __future__ import annotations

import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, unquote, urlsplit

from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.tools.scientific_search import ScientificSearch
from agents.EnnoScholarV2.tools.article_reader import html_document
from .models import Article, normalize_doi, public_link
from .network import fetch_public


def title_key(value):
    return " ".join(re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFKC", value).casefold()).split())


class V31ScientificSearch(ScientificSearch):
    """Capture publisher access from original provider payloads without changing V2."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.publisher_access = {}

    def _get(self, url, *, params=None, headers=None, deadline=None):
        if params and getattr(self, "search_offset", 0):
            offset = self.search_offset
            if url.endswith("/paper/search"):
                params = {**params, "offset": offset}
            elif url.endswith("/works") and "search" in params:
                params = {**params, "page": offset // params.get("per_page", 20) + 1}
            elif "export.arxiv.org/api/query" in url:
                params = {**params, "start": offset}
        if "export.arxiv.org/api/query" in url and params and "search_query" in params:
            expression = params["search_query"]
            query = expression[5:-1] if expression.startswith("all:(") and expression.endswith(")") else expression
            # arXiv prose has implicit OR and can match authors rather than scientific content.
            if not re.search(r"\b(?:AND|OR|ANDNOT)\b|\b(?:ti|abs|cat|au|all):", query):
                terms = re.findall(r'"[^"\n]+"|[\w-]+', query)
                terms = [term for term in terms if term.casefold() not in {"a", "an", "the", "for", "of", "in", "on", "with", "and", "using", "based"}]
                if terms:
                    params = {**params, "search_query": " AND ".join(f"(ti:{term} OR abs:{term})" for term in terms)}
        return super()._get(url, params=params, headers=headers, deadline=deadline)

    def _openalex(self, data):
        row = super()._openalex(data)
        primary = data.get("primary_location") or {}
        if (primary.get("source") or {}).get("type") in {"journal", "conference"} and "is_oa" in primary:
            self.publisher_access[normalize_doi(row.doi)] = "open_access" if primary["is_oa"] else "paywalled"
        return row


class ScientificSources:
    def __init__(self, timeout=15, client=None):
        # Construct a private adapter instance. Never use the V2 singleton or config env overrides.
        import os
        config = ScholarConfig(timeout=min(timeout, 15), semantic_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY", ""),
            openalex_key=os.getenv("OPENALEX_API_KEY", ""), contact_email=os.getenv("ENNOSCHOLAR_V31_CONTACT_EMAIL", ""))
        self.client = client or V31ScientificSearch(config)
        self._verification = {}

    @staticmethod
    def normalize(row, query=""):
        data = row.model_dump() if hasattr(row, "model_dump") else row
        article = Article.model_validate(data)
        article.id = ""
        article.verified = bool(data.get("source") == "arxiv" and article.arxiv_id)
        article.verification_sources = [article.url] if article.verified else []
        if data.get("source") == "arxiv" and (not article.doi or article.doi.casefold().startswith("10.48550/arxiv.")):
            article.publisher_access = "open_access"
        article.matched_queries = [query] if query else []
        article.sources = data.get("sources") or [data.get("source", "academic")]
        return article

    def verify(self, article, deadline, expected_title=""):
        article.publisher_access = getattr(self.client, "publisher_access", {}).get(normalize_doi(article.doi), article.publisher_access)
        if article.doi:
            doi = normalize_doi(article.doi)
            if doi not in self._verification or self._verification[doi] is None:
                try:
                    if doi.startswith("10.48550/arxiv."):
                        response = self.client._get("https://api.datacite.org/dois/" + quote(doi, safe=""), deadline=deadline)
                        attrs = response.json()["data"]["attributes"]
                        self._verification[doi] = {"DOI": attrs["doi"], "title": [t["title"] for t in attrs.get("titles", [])],
                            "author": [{"given": a.get("givenName", ""), "family": a.get("familyName") or a.get("name", "")} for a in attrs.get("creators", [])],
                            "published": {"date-parts": [[attrs["publicationYear"]]]}, "resource": {"primary": {"URL": attrs.get("url")}}}
                        article.arxiv_id = doi.split("arxiv.", 1)[1]
                    else:
                        response = self.client._get("https://api.crossref.org/works/" + quote(doi, safe=""), deadline=deadline)
                        self._verification[doi] = response.json()["message"]
                except Exception:
                    self._verification[doi] = None
            record = self._verification[doi]
            if record:
                exact = (record.get("title") or [""])[0]
                registry_url = "https://api.datacite.org/dois/" if doi.startswith("10.48550/arxiv.") else "https://api.crossref.org/works/"
                article.verification_sources = [*article.verification_sources, registry_url + doi]
                if normalize_doi(record.get("DOI", "")) != doi or (expected_title and title_key(exact) != title_key(expected_title)):
                    article.verified = False
                    article.metadata_errors.append("DOI/title mismatch")
                    return article
                if article.title and title_key(article.title) != title_key(exact):
                    article.verified = False
                    article.metadata_errors.append("DOI/title mismatch")
                    return article
                article.title = exact
                article.authors = [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in record.get("author", [])]
                date = (record.get("published") or record.get("issued") or {}).get("date-parts", [[]])[0]
                article.year = date[0] if date else article.year
                article.url = (record.get("resource") or {}).get("primary", {}).get("URL") or "https://doi.org/" + doi
                if not article.abstract and record.get("abstract"):
                    from bs4 import BeautifulSoup
                    article.abstract = BeautifulSoup(record["abstract"], "html.parser").get_text(" ", strip=True)
                article.verified = bool(exact and article.authors and article.year)
                article.metadata_errors = [error for error in article.metadata_errors if error != "DOI registry unavailable; verify original publication page"]
            elif not article.verified:
                # A DOI registry outage must not silently validate a publisher/title association.
                article.metadata_errors.append("DOI registry unavailable; verify original publication page")
        if expected_title and title_key(article.title) != title_key(expected_title):
            article.verified = False
            article.metadata_errors.append("Expected title differs from retrieved title")
        return article

    def search(self, query, sources, limit, deadline, check=lambda: None, offset=0):
        providers = {"semantic_scholar": self.client.semantic, "openalex": self.client.openalex, "arxiv": self.client.arxiv}
        chosen = sources or list(providers)
        if not 1 <= len(chosen) <= 3 or any(name not in providers for name in chosen):
            raise ValueError("Sources : semantic_scholar, openalex, arxiv.")
        articles, warnings = [], []
        self.client.search_offset = offset
        # Providers run concurrently, each with a deadline and independent rate limits.
        with self.client.turn_cache(), ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(providers[name], query, limit, deadline=deadline): name for name in set(chosen)}
            for future in as_completed(futures):
                check()
                try:
                    articles.extend(self.normalize(row, query) for row in future.result())
                except Exception as exc:
                    warnings.append({"source": futures[future], "error": type(exc).__name__})
        self.client.search_offset = 0
        # Registry verification is performed on examined/final articles, not hundreds of candidates.
        return articles, warnings

    def metadata(self, identifier, deadline, check=lambda: None):
        identifier = identifier.strip()
        doi = normalize_doi(unquote(identifier))
        if doi:
            # Reserve registry time before slower providers; their outages must not poison DOI verification.
            canonical = self.verify(Article(title="", doi=doi, url="https://doi.org/" + doi, sources=["crossref"]),
                min(deadline, time.monotonic() + 12))
            rows = [self.normalize(row) for row in self.client.lookup_doi(doi, deadline=min(deadline, time.monotonic() + 10))]
            if not rows:
                return [canonical] if canonical.title else []
            verified = [self.verify(row, deadline) for row in rows if row.doi == doi or normalize_doi(row.doi) == doi]
            return verified or ([canonical] if canonical.title else [])
        arxiv = re.fullmatch(r"(?:https?://(?:www\.)?arxiv\.org/(?:abs|pdf|html)/|arxiv:)?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+/\d{7}(?:v\d+)?)(?:\.pdf)?", identifier, re.I)
        if arxiv:
            return [self.normalize(row) for row in self.client.arxiv("", 1, identifier=arxiv.group(1), deadline=deadline)]
        semantic = re.fullmatch(r"(?:https?://www\.semanticscholar\.org/paper/(?:[^/]+/)?|semantic_scholar:)([a-f0-9]{40})", identifier, re.I)
        openalex = re.fullmatch(r"(?:https?://(?:api\.)?openalex\.org/(?:works/)?|openalex:)(W\d+)", identifier, re.I)
        if semantic:
            headers = {"x-api-key": self.client.config.semantic_key} if self.client.config.semantic_key else {}
            data = self.client._get("https://api.semanticscholar.org/graph/v1/paper/" + semantic.group(1),
                params={"fields": "title,authors,year,abstract,externalIds,url,openAccessPdf,citationCount"}, headers=headers, deadline=deadline).json()
            return [self.verify(self.normalize(self.client._semantic(data)), deadline)]
        if openalex:
            params = {"api_key": self.client.config.openalex_key} if self.client.config.openalex_key else {}
            data = self.client._get("https://api.openalex.org/works/" + openalex.group(1), params=params, deadline=deadline).json()
            return [self.verify(self.normalize(self.client._openalex(data)), deadline)]
        if not public_link(identifier):
            # Exact title lookup, only accept the title actually returned by the providers.
            rows, _ = self.search(identifier[:1500], ["openalex", "semantic_scholar"], 5, deadline, check)
            return [row for row in rows if title_key(row.title) == title_key(identifier)]
        data, content_type, url = fetch_public(identifier, deadline, check)
        if "html" not in content_type:
            return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(data, "html.parser")
        # A generic page title is insufficient to identify a scientific article.
        if not soup.find("meta", attrs={"name": "citation_title"}) and not soup.find("meta", attrs={"name": "dc.title"}):
            return []
        raw, _, _ = html_document(data, url)
        article = self.normalize(raw)
        article.doi = normalize_doi(article.doi)
        article.verified = bool(article.title and article.authors and (article.doi or article.url))
        article.verification_sources = [url]
        year = soup.find("meta", attrs={"name": "citation_publication_date"})
        match = re.match(r"\d{4}", str(year.get("content", ""))) if year else None
        article.year = int(match[0]) if match else None
        if article.doi:
            return [self.verify(article, deadline)]
        return [article]

    def citations(self, article, deadline, mode="citations", limit=30):
        paper = article.external_ids.get("semantic_scholar") or ("DOI:" + article.doi if article.doi else "ARXIV:" + article.arxiv_id if article.arxiv_id else "")
        if not paper:
            return [], [{"source": "semantic_scholar", "error": "MissingStableIdentifier"}]
        headers = {"x-api-key": self.client.config.semantic_key} if self.client.config.semantic_key else {}
        try:
            relation = "references" if mode == "references" else "citations"
            data = self.client._get("https://api.semanticscholar.org/graph/v1/paper/" + quote(paper, safe=":") + "/" + relation,
                params={"limit": limit, "fields": "title,authors,year,abstract,externalIds,url,openAccessPdf,citationCount"},
                headers=headers, deadline=deadline).json()
            field = "citedPaper" if relation == "references" else "citingPaper"
            return [self.normalize(self.client._semantic(row[field]), relation + ":" + article.id)
                    for row in data.get("data", []) if row.get(field)], []
        except Exception as exc:
            return [], [{"source": "semantic_scholar", "error": type(exc).__name__}]
