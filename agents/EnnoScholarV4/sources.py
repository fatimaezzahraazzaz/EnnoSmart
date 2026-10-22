"""Read-only adapters to existing scientific clients; all state belongs to V4."""
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


BOOLEAN_SYNTAX = re.compile(r'\b(?:OR|AND|NOT)\b|[()"]')


TOKEN = re.compile(r'"[^"]*"|\(|\)|[^\s()"]+')


def _parse(tokens, position=0):
    """Boolean expression -> ("OR", [("AND", [item, ...]), ...]); adjacency is AND."""
    alternatives, current = [], []
    while position < len(tokens):
        token = tokens[position]
        if token == ")":
            break
        if token == "(":
            group, position = _parse(tokens, position+1)
            current.append(group)
        elif token == "OR":
            alternatives.append(current)
            current = []
        elif token == "NOT":
            current.append("NOT")
        elif token != "AND":
            current.append(token)
        position += 1
    alternatives.append(current)
    return ("OR", [("AND", items) for items in alternatives if items]), position


def _arxiv(node, alternative=False):
    kind, children = node
    if kind == "OR":
        parts = [_arxiv(child, len(children) > 1) for child in children]
        parts = [p for p in parts if p]
        return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"
    words = [c for c in children if isinstance(c, str) and c != "NOT" and not c.startswith('"')]
    if alternative and words and len(words) == len(children):
        # "deep learning OR neural network": each alternative is one concept.
        return '"' + " ".join(words) + '"'
    out = []
    for child in children:
        if child == "NOT":
            out.append("ANDNOT")
            continue
        text = _arxiv(child) if isinstance(child, tuple) else child
        if text:
            out.append(text if not out or out[-1] == "ANDNOT" else "AND " + text)
    return " ".join(out)


def _keywords(query):
    # An excluded concept must not become a searched keyword.
    query = re.sub(r'\bNOT\s+(?:"[^"]*"|\([^)]*\)|\S+)', " ", query)
    words = re.sub(r'\b(?:OR|AND|NOT)\b|[()"]', " ", query).replace("-", " ").split()
    seen, kept = set(), []
    for word in words:
        if word.casefold() not in seen:
            seen.add(word.casefold())
            kept.append(word)
    return " ".join(kept) or query


def provider_query(provider, query):
    """Translate the model's query to what each provider actually supports.

    OpenAlex: boolean search supported, query unchanged. arXiv: explicit boolean
    with AND between concepts and OR between quoted alternatives (implicit
    adjacency would turn OR groups into broad disjunctions, and the plain-query
    rewrite requires every word). Semantic Scholar: plain ranked relevance search
    without boolean syntax, where hyphenated terms match nothing: keywords only.
    """
    if provider == "openalex":
        return query
    if provider == "semantic_scholar":
        return _keywords(query) if BOOLEAN_SYNTAX.search(query) or "-" in query else query
    if not BOOLEAN_SYNTAX.search(query):
        return query
    tree, _ = _parse(TOKEN.findall(query))
    return _arxiv(tree) or query


# arXiv has no relevance quorum: every AND term must appear in the title or abstract.
# Six required terms returned nothing in the real runs (e7bae458, d71d17b0).
ARXIV_REQUIRED_TERMS = 4


def arxiv_plain_query(terms):
    """A plain query for arXiv: short queries require every term; longer ones require
    their first two concepts and at least one of the others, ranked by arXiv relevance."""
    field = lambda term: f"ti:{term} OR abs:{term}"
    if len(terms) <= ARXIV_REQUIRED_TERMS:
        return " AND ".join(f"({field(term)})" for term in terms)
    return " AND ".join(f"({field(term)})" for term in terms[:2]) + " AND (" + " OR ".join(field(t) for t in terms[2:]) + ")"


def title_key(value):
    return " ".join(re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFKC", value).casefold()).split())


class V4ScientificSearch(ScientificSearch):
    """Capture publisher access from original provider payloads without changing V2."""
    def __init__(self, *args, retries=2, **kwargs):
        super().__init__(*args, **kwargs)
        self.retries = retries
        self.publisher_access = {}
        self.bibliographic_details = {}
        self.document_locations = {}
        self._paging = __import__("threading").local()

    @property
    def search_offset(self):
        return getattr(self._paging, "offset", 0)

    @search_offset.setter
    def search_offset(self, offset):
        self._paging.offset = offset

    # Semantic Scholar answers 429 to concurrent calls (seen in run 80a58dae):
    # one request at a time, about one per second, like the arXiv lock.
    _semantic_lock = __import__("threading").Lock()
    _semantic_next_request = 0.0

    def _get(self, url, *, params=None, headers=None, deadline=None):
        if "api.semanticscholar.org" in url:
            with V4ScientificSearch._semantic_lock:
                delay = max(0, V4ScientificSearch._semantic_next_request-time.monotonic())
                if deadline is not None and time.monotonic()+delay >= deadline:
                    raise TimeoutError("Délai de collecte atteint avant disponibilité Semantic Scholar.")
                time.sleep(delay)
                V4ScientificSearch._semantic_next_request = time.monotonic()+1.1
                return self._get_paced(url, params=params, headers=headers, deadline=deadline)
        return self._get_paced(url, params=params, headers=headers, deadline=deadline)

    def _get_paced(self, url, *, params=None, headers=None, deadline=None):
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
                    params = {**params, "search_query": arxiv_plain_query(terms)}
        import httpx
        for attempt in range(self.retries):
            try:
                trace = getattr(self._paging, "requests", None)
                if trace is not None:
                    safe = {k: v for k, v in (params or {}).items() if k in {
                        "search", "search_query", "start", "page", "per_page", "max_results", "limit", "offset",
                        "filter", "sort", "sortBy", "sortOrder", "fields"}}
                    endpoint = urlsplit(url)
                    trace.append({"endpoint": endpoint.scheme+"://"+endpoint.netloc+endpoint.path,
                        "params": safe, "attempt": attempt+1})
                return super()._get(url, params=params, headers=headers, deadline=deadline)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {429, 500, 502, 503, 504} or attempt+1 >= self.retries:
                    raise
                try:
                    delay = min(8, max(.5, float(exc.response.headers.get("retry-after", 2**attempt))))
                except ValueError:
                    delay = 2**attempt
                if deadline is not None and time.monotonic()+delay >= deadline:
                    raise
                time.sleep(delay)


    def _openalex(self, data):
        row = super()._openalex(data)
        self.document_locations[normalize_doi(row.doi) or title_key(row.title)] = [
            {"url": url, "version": loc.get("version", ""), "is_oa": loc.get("is_oa"),
             "provider": "openalex", "format": kind}
            for loc in (data.get("locations") or [])
            for kind, url in (("pdf", loc.get("pdf_url")), ("landing", loc.get("landing_page_url")))
            if url and loc.get("is_oa")]
        primary_source = (data.get("primary_location") or {}).get("source") or {}
        self.bibliographic_details[normalize_doi(row.doi) or title_key(row.title)] = {
            "journal": primary_source.get("display_name") or "", "publisher": primary_source.get("host_organization_name") or "",
            "publication_date": data.get("publication_date") or ""}
        primary = data.get("primary_location") or {}
        if (primary.get("source") or {}).get("type") in {"journal", "conference"} and "is_oa" in primary:
            self.publisher_access[normalize_doi(row.doi)] = "open_access" if primary["is_oa"] else "paywalled"
        return row


class ScientificSources:
    _local = __import__("threading").local()

    @property
    def last_provider_stats(self):
        return getattr(ScientificSources._local, "stats", None)

    @last_provider_stats.setter
    def last_provider_stats(self, value):
        ScientificSources._local.stats = value

    def __init__(self, timeout=15, client=None, config=None):
        # Construct a private adapter instance. Never use the V2 singleton or config env overrides.
        import os
        from .config import ScholarV4Config
        self.config = config or ScholarV4Config.from_env()
        io_config = ScholarConfig(timeout=min(timeout, 15), semantic_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY", ""),
            openalex_key=os.getenv("OPENALEX_API_KEY", ""), contact_email=os.getenv("ENNOSCHOLAR_V4_CONTACT_EMAIL", ""))
        self.client = client or V4ScientificSearch(io_config, retries=self.config.max_retry_attempts)
        self._verification = {}

    def normalize(self, row, query=""):
        data = row.model_dump() if hasattr(row, "model_dump") else row
        article = Article.model_validate(data)
        candidates = getattr(self.client, "document_locations", {}).get(normalize_doi(article.doi) or title_key(article.title), [])
        article.document_candidates = [*article.document_candidates, *candidates]
        article.work_urls = list(dict.fromkeys([*article.work_urls, *[c["url"] for c in candidates]]))
        details = getattr(self.client, "bibliographic_details", {}).get(normalize_doi(article.doi) or title_key(article.title), {})
        for name, value in details.items():
            setattr(article, name, value)
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
                article.publication_date = "-".join(str(part).zfill(4 if index==0 else 2) for index, part in enumerate(date))
                article.journal = (record.get("container-title") or [article.journal])[0]
                article.publisher = record.get("publisher") or article.publisher
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
        sent = {name: provider_query(name, query) for name in set(chosen)}
        self.last_provider_stats = {name: {"query_sent": q, "returned": None} for name, q in sent.items()}
        stats = self.last_provider_stats
        def collect(name):
            # Pagination belongs to this provider invocation, never to the
            # shared adapter: concurrent query/page prefetches must stay distinct.
            self.client.search_offset = offset
            if hasattr(self.client, "_paging"):
                self.client._paging.requests = []
            try:
                return providers[name](sent[name], limit, deadline=deadline)
            finally:
                if hasattr(self.client, "_paging"):
                    stats[name]["effective_requests"] = list(self.client._paging.requests)
                    del self.client._paging.requests
                self.client.search_offset = 0
        # Providers run concurrently, each with a deadline and independent rate limits.
        with self.client.turn_cache(), ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(collect, name): name for name in set(chosen)}
            for future in as_completed(futures):
                check()
                try:
                    rows = [self.normalize(row, query) for row in future.result()]
                    articles.extend(rows)
                    self.last_provider_stats[futures[future]]["returned"] = len(rows)
                except Exception as exc:
                    response = getattr(exc, "response", None)
                    warnings.append({"source": futures[future], "error": type(exc).__name__,
                        "http_status": getattr(response, "status_code", None),
                        "retry_after": getattr(response, "headers", {}).get("retry-after") if response is not None else None})
        self.client.search_offset = 0
        # Registry verification is performed on examined/final articles, not hundreds of candidates.
        return articles, warnings

    def abstracts(self, articles, deadline):
        """Abstracts of notices collected without one, read from their DOI records
        (OpenAlex, Semantic Scholar). Only the provider's text for the same title is
        returned; nothing is generated. {article_id: (abstract, provider)}."""
        def one(article):
            for row in self.client.lookup_doi(article.doi, deadline=min(deadline, time.monotonic()+10)):
                same = title_key(row.title) == title_key(article.title) or (
                    title_key(row.title)[:60] and title_key(article.title).startswith(title_key(row.title)[:60]))
                if row.abstract and same:
                    return article.id, (row.abstract, getattr(row, "source", None) or (getattr(row, "sources", None) or ["provider"])[0])
            return article.id, None
        found = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for future in [pool.submit(one, a) for a in articles if a.doi]:
                try:
                    aid, value = future.result(timeout=max(.1, deadline-time.monotonic()))
                except Exception:
                    continue
                if value:
                    found[aid] = value
        return found

    def metadata(self, identifier, deadline, check=lambda: None):
        identifier = identifier.strip()
        pmcid = re.fullmatch(r"(?:pmcid:)?(PMC\d+)", identifier, re.I)
        if pmcid:
            identifier = "https://www.ebi.ac.uk/europepmc/webservices/rest/" + pmcid.group(1).upper() + "/fullTextXML"
            from .tools.extraction import xml_document
            data, _, url = fetch_public(identifier, deadline, check)
            raw, chunks, _ = xml_document(data, url, self.config)
            return [self.verify(self.normalize(raw), deadline)] if chunks else []
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
