from __future__ import annotations

import hashlib
import logging
import json
import re
import threading
import time
import unicodedata
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import quote
from xml.etree import ElementTree as ET

import httpx

from ..config import ScholarConfig
from ..models import Article, SearchIntent
from ..canonical import (article_id, identity_keys, normalize_doi, normalized_title, grouped_articles,
    merge_metadata, preserve_notices, same_record)


logger = logging.getLogger("uvicorn.error")


@dataclass
class SearchTurn:
    results: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    client: httpx.Client | None = None


def deduplicate(articles: list[Article], limit: int) -> list[Article]:
    for article in articles:
        article.doi = normalize_doi(article.doi)
        article.arxiv_id = re.sub(r"v\d+$", "", article.arxiv_id)
        article.id = article.id or article_id(article)
        preserve_notices(article)
    output = []
    for group in grouped_articles(articles):
        versions = []
        for article in group:
            existing = next((row for row in versions if same_record(row, article)), None)
            if existing:
                merge_metadata(existing, article)
            elif article.title.strip():
                article.access_level = "abstract_only" if article.abstract else "metadata_only"
                versions.append(article)
        # Keep distinct versions and the transitive association discovered before merging.
        ids = [article.id for article in versions]
        for article in versions:
            article.related_article_ids = list(dict.fromkeys([*article.related_article_ids, *ids]))
        output.extend(versions)
    positions = {id(article): index for index, article in enumerate(articles)}
    output.sort(key=lambda article: positions[id(article)])
    return output[:limit]


def reconstruct_abstract(index: dict | None) -> str:
    positions = [(position, word) for word, locations in (index or {}).items() for position in locations]
    return " ".join(word for _, word in sorted(positions))


class ScientificSearch:
    """Isolated V2 I/O; caches expire at the end of each conversational turn."""

    def __init__(self, config: ScholarConfig, client: httpx.Client | None = None):
        self.config = config
        self.client = client
        self._arxiv_lock = threading.Lock()
        self._arxiv_next_request = 0.0
        self._turn: ContextVar[SearchTurn | None] = ContextVar("scholar_v2_search_turn", default=None)
        self._limits = {"semantic_scholar": threading.BoundedSemaphore(max(1, min(4, config.semantic_concurrency))),
            "openalex": threading.BoundedSemaphore(max(1, min(4, config.openalex_concurrency)))}
        self._rate_locks = {name: threading.Lock() for name in self._limits}
        self._next_request = {name: 0.0 for name in self._limits}

    @contextmanager
    def turn_cache(self):
        existing = self._turn.get()
        if existing is not None:
            yield existing
            return
        turn = SearchTurn()
        token = self._turn.set(turn)
        try:
            yield turn
        finally:
            self._turn.reset(token)
            if turn.client is not None:
                turn.client.close()

    def _query(self, name, provider, query, limit, deadline, turn):
        # Preserve quotes, operators and case: whitespace normalization only.
        parts = re.split(r'''("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''', query)
        normalized_query = "".join(part if index % 2 else re.sub(r"\s+", " ", part)
            for index, part in enumerate(parts)).strip()
        key = (name, normalized_query, limit)
        started = time.monotonic()
        with turn.lock:
            cached = key in turn.results
            future = turn.results.setdefault(key, Future())
        token = self._turn.set(turn)
        try:
            if not cached:
                try:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError("Délai de collecte atteint.")
                    future.set_result(provider(query, limit, deadline=deadline))
                except Exception as exc:
                    future.set_exception(exc)
            found = future.result(timeout=max(0.001, deadline - time.monotonic()) if deadline is not None else None)
            return [article.model_copy(deep=True) for article in found], cached
        finally:
            self._turn.reset(token)
            logger.info("EnnoScholar V2 search provider=%s query=%s elapsed=%.3f cache_hit=%s",
                name, json.dumps(query, ensure_ascii=False), time.monotonic() - started, str(cached).lower())

    def _request(self, url, params, headers, deadline):
        remaining = deadline - time.monotonic() if deadline is not None else 15
        if remaining <= 0:
            raise TimeoutError("Délai de collecte atteint.")
        turn = self._turn.get()
        if self.client:
            return self.client.get(url, params=params, headers=headers, timeout=min(15, remaining))
        if turn is not None:
            with turn.lock:
                if turn.client is None:
                    turn.client = httpx.Client(follow_redirects=True)
            return turn.client.get(url, params=params, headers=headers, timeout=min(self.config.timeout, 15, remaining))
        with httpx.Client(timeout=min(self.config.timeout, 15, remaining), follow_redirects=True) as client:
            return client.get(url, params=params, headers=headers)

    def _get(self, url: str, *, params: dict | None = None, headers: dict | None = None, deadline: float | None = None) -> httpx.Response:
        deadline = deadline if deadline is not None else time.monotonic() + 15
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Délai de collecte atteint.")
        request_headers = {"User-Agent": "EnnoScholarV2/0.1 (scientific literature assistant)", **(headers or {})}
        name = "semantic_scholar" if "semanticscholar.org" in url else "openalex" if "api.openalex.org" in url else None
        if name:
            acquired = self._limits[name].acquire(timeout=max(0.001, remaining))
            if not acquired:
                raise TimeoutError("Délai de collecte atteint avant disponibilité fournisseur.")
            try:
                with self._rate_locks[name]:
                    delay = max(0, self._next_request[name] - time.monotonic())
                    if deadline is not None and time.monotonic() + delay >= deadline:
                        raise TimeoutError("Délai de collecte atteint avant disponibilité fournisseur.")
                    time.sleep(delay)
                    interval = max(1, self.config.semantic_request_interval) if name == "semantic_scholar" else max(0.1, self.config.openalex_request_interval)
                    self._next_request[name] = time.monotonic() + interval
                response = self._request(url, params, request_headers, deadline)
                if response.status_code == 429:
                    try:
                        cooldown = max(interval, float(response.headers.get("retry-after", "5")))
                    except ValueError:
                        cooldown = 5
                    with self._rate_locks[name]:
                        self._next_request[name] = max(self._next_request[name], time.monotonic() + cooldown)
            finally:
                self._limits[name].release()
        else:
            response = self._request(url, params, request_headers, deadline)
        response.raise_for_status()
        return response

    def _semantic(self, data: dict) -> Article:
        ids = data.get("externalIds") or {}
        return Article(title=data.get("title") or "", authors=[a.get("name", "") for a in data.get("authors", [])],
            year=data.get("year"), abstract=data.get("abstract") or "", doi=ids.get("DOI") or "",
            arxiv_id=ids.get("ArXiv") or "", url=data.get("url") or "",
            pdf_url=(data.get("openAccessPdf") or {}).get("url") or "",
            source="semantic_scholar", sources=["semantic_scholar"], citation_count=data.get("citationCount") or 0,
            external_ids={"semantic_scholar": data["paperId"]} if data.get("paperId") else {})

    def _openalex(self, data: dict) -> Article:
        location = data.get("best_oa_location") or data.get("primary_location") or {}
        locations = [location, *(data.get("locations") or [])]
        pdf_url = next((loc.get("pdf_url") for loc in locations if loc.get("pdf_url") and loc.get("is_oa", loc is location)), "")
        landing = location.get("landing_page_url") or (data.get("open_access") or {}).get("oa_url") or data.get("doi") or data.get("id") or ""
        arxiv = next((re.search(r"arxiv\.org/(?:abs|pdf|html)/([^?#]+)", loc.get("landing_page_url") or "", re.I)
            for loc in locations if "arxiv.org/" in (loc.get("landing_page_url") or "")), None)
        return Article(title=data.get("title") or data.get("display_name") or "",
            authors=[(a.get("author") or {}).get("display_name", "") for a in data.get("authorships", [])],
            year=data.get("publication_year"), abstract=reconstruct_abstract(data.get("abstract_inverted_index")),
            doi=data.get("doi") or "", url=landing,
            arxiv_id=arxiv.group(1).removesuffix(".pdf") if arxiv else "",
            pdf_url=pdf_url, source="openalex", sources=["openalex"],
            citation_count=data.get("cited_by_count") or 0,
            external_ids={"openalex": data["id"]} if data.get("id") else {})

    def semantic(self, query: str, limit: int, *, deadline: float | None = None) -> list[Article]:
        headers = {"x-api-key": self.config.semantic_key} if self.config.semantic_key else {}
        response = self._get("https://api.semanticscholar.org/graph/v1/paper/search", headers=headers,
            params={"query": query, "limit": limit, "fields": "title,authors,year,abstract,externalIds,url,openAccessPdf,citationCount"}, deadline=deadline)
        return [self._semantic(row) for row in response.json().get("data", [])]

    def openalex(self, query: str, limit: int, *, deadline: float | None = None) -> list[Article]:
        params = {"search": query, "per_page": limit}
        if self.config.openalex_key:
            params["api_key"] = self.config.openalex_key
        if self.config.contact_email:
            params["mailto"] = self.config.contact_email
        return [self._openalex(row) for row in self._get("https://api.openalex.org/works", params=params, deadline=deadline).json().get("results", [])]

    def arxiv(self, query: str, limit: int, *, identifier: str = "", deadline: float | None = None) -> list[Article]:
        # Parentheses keep the LLM's scientific AND/OR expressions intact.
        params = {"start": 0, "max_results": limit, "sortBy": "relevance"}
        params.update({"id_list": identifier} if identifier else {"search_query": "all:(" + query + ")"})
        # arXiv requires one connection and a three-second interval. Only V2 state.
        with self._arxiv_lock:
            delay = max(0, self._arxiv_next_request - time.monotonic())
            if deadline is not None and time.monotonic() + delay >= deadline:
                raise TimeoutError("Délai de collecte atteint avant disponibilité arXiv.")
            time.sleep(delay)
            try:
                data = self._get("https://export.arxiv.org/api/query", params=params, deadline=deadline).content
            finally:
                self._arxiv_next_request = time.monotonic() + 3
        root = ET.fromstring(data)
        ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
        output = []
        for entry in root.findall("a:entry", ns):
            title = " ".join(entry.findtext("a:title", "", ns).split())
            url = entry.findtext("a:id", "", ns).replace("http://", "https://")
            if not title or "/abs/" not in url:
                continue
            published = entry.findtext("a:published", "", ns)
            output.append(Article(title=title, authors=[a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)],
                year=int(published[:4]) if published[:4].isdigit() else None,
                abstract=" ".join(entry.findtext("a:summary", "", ns).split()),
                doi=entry.findtext("x:doi", "", ns), arxiv_id=url.split("/abs/")[-1], url=url,
                pdf_url=url.replace("/abs/", "/pdf/"), source="arxiv", sources=["arxiv"]))
        return output

    def search_scientific(self, query: str, limit: int = 10) -> dict:
        if self._turn.get() is None:
            with self.turn_cache():
                return self.search_scientific(query, limit)
        query = query.strip()[:1500]
        if not query:
            raise ValueError("Une requête scientifique est nécessaire.")
        limit = max(1, min(limit, 20))
        articles, warnings = [], []
        def collect(name, provider):
            try:
                found, _ = self._query(name, provider, query, limit, None, turn)
                return found, []
            except (httpx.HTTPError, TimeoutError, ValueError, ET.ParseError, KeyError, TypeError) as exc:
                return [], [{"source": name, "reason": "API indisponible, quota atteint ou réponse invalide.",
                    "http_status": getattr(getattr(exc, "response", None), "status_code", None)}]
        turn = self._turn.get()
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="scholar-v2-search") as pool:
            futures = [pool.submit(collect, name, provider) for name, provider in
                (("semantic_scholar", self.semantic), ("openalex", self.openalex), ("arxiv", self.arxiv))]
            for future in futures:
                rows, errors = future.result()
                articles.extend(rows)
                warnings.extend(errors)
        return {"query": query, "articles": [a.model_dump() for a in deduplicate(articles, limit)], "warnings": warnings}

    def search_batch(self, queries: list[str], top_k: int = 8, *, intent: SearchIntent | dict | None = None, deadline: float | None = None) -> dict:
        if self._turn.get() is None:
            with self.turn_cache():
                return self.search_batch(queries, top_k, intent=intent, deadline=deadline)
        if not isinstance(queries, list) or not 1 <= len(queries) <= 8:
            raise ValueError("Le batch accepte de une à huit requêtes.")
        queries = list(dict.fromkeys(query.strip()[:1500] for query in queries if isinstance(query, str) and query.strip()))
        if not queries or not 1 <= top_k <= 20:
            raise ValueError("Requêtes ou top_k invalides.")
        intent = SearchIntent.model_validate(intent) if intent is not None else None
        deadline = min(deadline, time.monotonic() + self.config.search_timeout) if deadline is not None else time.monotonic() + self.config.search_timeout
        candidate_limit = self.config.search_candidate_limit
        turn = self._turn.get()

        def collect(name, provider):
            rows, warnings, telemetry, buckets = [], [], [], {}
            def collect_query(query):
                started = time.monotonic()
                try:
                    found, cache_hit = self._query(name, provider, query, candidate_limit, deadline, turn)
                    for article in found:
                        article.matched_queries = list(dict.fromkeys([*article.matched_queries, query]))
                    return found, [], [{"source": name, "query": query, "returned": len(found),
                        "seconds": round(time.monotonic() - started, 3), "cache_hit": cache_hit}]
                except (httpx.HTTPError, TimeoutError, ValueError, ET.ParseError, KeyError, TypeError) as exc:
                    return [], [{"source": name, "query": query, "reason": "API indisponible, quota atteint ou réponse invalide.",
                        "http_status": getattr(getattr(exc, "response", None), "status_code", None)}], []
            concurrency = max(1, min(4, self.config.semantic_concurrency if name == "semantic_scholar" else self.config.openalex_concurrency)) if name != "arxiv" else 1
            with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="scholar-v2-" + name) as pool:
                futures = [pool.submit(collect_query, query) for query in queries]
                # Read in submitted order, not completion order: unchanged scientific selection.
                for query, future in zip(queries, futures):
                    found, errors, entries = future.result()
                    rows.extend(found)
                    buckets[query] = found
                    warnings.extend(errors)
                    telemetry.extend(entries)
            return rows, warnings, telemetry, buckets

        providers = (("semantic_scholar", self.semantic), ("openalex", self.openalex), ("arxiv", self.arxiv))
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="scholar-v2-search") as pool:
            futures = [pool.submit(collect, name, provider) for name, provider in providers]
            collected = [future.result() for future in futures]
        rows = [article for source_rows, _, _, _ in collected for article in source_rows]
        warnings = [warning for _, source_warnings, _, _ in collected for warning in source_warnings]
        articles = deduplicate([article.model_copy(deep=True) for article in rows], len(rows))
        # Rotate the first provider across queries; each query gets a turn before
        # a second provider for the same query. Keep each provider's native order.
        buckets = [collected[(round_index + query_index) % len(providers)][3].get(query, [])
            for round_index in range(len(providers)) for query_index, query in enumerate(queries)]
        from .compact_results import compact_results
        candidates = compact_results(articles, min(top_k, 12), buckets=buckets)
        return {"queries": queries, "top_k": top_k, "articles": candidates,
            "all_articles": [article.model_dump() for article in articles], "total_found": len(articles),
            "selection": "native provider order; query/source round robin; deduplicated works; scientific judgment by chat",
            "candidate_limit_per_provider_query": candidate_limit,
            "collection": [entry for _, _, entries, _ in collected for entry in entries],
            "warnings": warnings}

    def lookup_doi(self, doi: str, *, deadline: float | None = None) -> list[Article]:
        doi = normalize_doi(doi)
        articles = []
        params = {"api_key": self.config.openalex_key} if self.config.openalex_key else {}
        try:
            articles.append(self._openalex(self._get("https://api.openalex.org/works/" + quote("https://doi.org/" + doi, safe=""), params=params, deadline=deadline).json()))
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
            pass
        try:
            headers = {"x-api-key": self.config.semantic_key} if self.config.semantic_key else {}
            articles.append(self._semantic(self._get("https://api.semanticscholar.org/graph/v1/paper/DOI:" + quote(doi, safe=""),
                params={"fields": "title,authors,year,abstract,externalIds,url,openAccessPdf,citationCount"}, headers=headers, deadline=deadline).json()))
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
            pass
        return deduplicate(articles, 1)
