from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from agents.EnnoScholarV2.agent import ScholarChatAgent, StoredArticle
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.models import Article, ArticleAnalysis, FulltextResult, TextChunk
from agents.EnnoScholarV2.tools import article_reader, fulltext
from agents.EnnoScholarV2.tools.scientific_search import ScientificSearch
from conftest import call, message, response
from test_scientific_search import ATOM


def text_result(url="https://public.example/work.pdf"):
    return FulltextResult(access_level="fulltext", text="Verified scientific text.", fulltext_url=url,
        fulltext_source="public.example", chunks=[TextChunk(page=2, text="Verified scientific text.")])


def test_provider_queries_parallel_bounded_and_keep_native_order_and_depth(monkeypatch):
    search = ScientificSearch(ScholarConfig(semantic_concurrency=2, openalex_concurrency=3))
    queries = [f"Scientific axis {i}" for i in range(8)]
    lock = threading.Lock()
    current, peaks = {}, {}
    def provider(name):
        def fetch(query, limit, **kwargs):
            with lock:
                current[name] = current.get(name, 0) + 1
                peaks[name] = max(peaks.get(name, 0), current[name])
            try:
                time.sleep(0.02)
                return [Article(title=f"{name} {query} native {i}", doi=f"10.9999/{name}-{queries.index(query)}-{i}",
                    sources=[name], source=name, abstract="Complete abstract") for i in range(limit)]
            finally:
                with lock: current[name] -= 1
        return fetch
    for name in ("semantic", "openalex", "arxiv"):
        monkeypatch.setattr(search, name, provider(name))
    result = search.search_batch(queries)
    assert peaks == {"semantic": 2, "openalex": 3, "arxiv": 1}
    assert result["total_found"] == 1200 and len(result["all_articles"]) == 1200
    assert result["candidate_limit_per_provider_query"] == 50 and len(result["articles"]) == 8
    assert [row["query"] for row in result["collection"]] == queries * 3
    assert all(row["returned"] == 50 for row in result["collection"])


def test_arxiv_connection_serial_and_three_second_spacing_even_with_injected_client(monkeypatch):
    import agents.EnnoScholarV2.tools.scientific_search as module
    clock = SimpleNamespace(value=0.0)
    starts = []
    def sleep(seconds): clock.value += seconds
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.value, sleep=sleep))
    search = ScientificSearch(ScholarConfig(), client=object())
    def get(*args, **kwargs):
        starts.append(clock.value)
        clock.value += 0.25
        return SimpleNamespace(content=ATOM)
    monkeypatch.setattr(search, "_get", get)
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert all(future.result() for future in [pool.submit(search.arxiv, str(i), 50) for i in range(3)])
    assert starts == [0.0, 3.25, 6.5]
    assert search._arxiv_next_request == 9.75


def test_provider_cache_single_flight_copy_isolation_limit_key_and_turn_expiration(monkeypatch, caplog):
    search = ScientificSearch(ScholarConfig())
    counts = []
    def provider(query, limit, **kwargs):
        counts.append((query, limit))
        time.sleep(0.01)
        return [Article(title="Stored notice", abstract="Original abstract")]
    monkeypatch.setattr(search, "semantic", provider)
    monkeypatch.setattr(search, "openalex", provider)
    monkeypatch.setattr(search, "arxiv", provider)
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    with search.turn_cache() as turn:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outputs = [f.result() for f in [pool.submit(search._query, "semantic_scholar", provider, "same query", 50, None, turn) for _ in range(2)]]
        assert len(counts) == 1 and {cached for _, cached in outputs} == {True, False}
        outputs[0][0][0].abstract = "Mutated consumer data"
        found, cached = search._query("semantic_scholar", provider, " same   query ", 50, None, turn)
        assert cached and found[0].abstract == "Original abstract"
        search._query("semantic_scholar", provider, "same query", 20, None, turn)
        assert len(counts) == 2
    with search.turn_cache() as turn:
        search._query("semantic_scholar", provider, "same query", 50, None, turn)
    assert len(counts) == 3 and "cache_hit=true" in caplog.text


def test_cache_is_used_across_scientific_calls_in_one_real_agent_turn(fake_client, monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    calls = []
    def provider(query, limit, **kwargs):
        calls.append(query)
        return [Article(title="Candidate", abstract="Complete abstract")]
    for name in ("semantic", "openalex", "arxiv"): monkeypatch.setattr(agent.search, name, provider)
    fake_client.responses.create.side_effect = [response(call("search_scientific", {"queries": ["same"], "top_k": 8}, "s1")),
        response(call("search_scientific", {"queries": ["same"], "top_k": 8}, "s2")), response(message("Sources conservées."))]
    agent.run("Question", [], {})
    assert len(calls) == 3 and agent.search._turn.get() is None
    fake_client.responses.create.side_effect = [response(call("search_scientific", {"queries": ["same"], "top_k": 8})), response(message("New turn."))]
    agent.run("Question suivante", [], {})
    assert len(calls) == 6


def test_turn_caches_are_isolated_for_concurrent_sessions(monkeypatch):
    search = ScientificSearch(ScholarConfig())
    barrier = threading.Barrier(2)
    calls = []
    def provider(*args, **kwargs): calls.append(1); return [Article(title="Notice")]
    def run():
        with search.turn_cache() as turn:
            barrier.wait(timeout=2)
            search._query("semantic_scholar", provider, "query", 50, None, turn)
    with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(lambda _: run(), range(2)))
    assert len(calls) == 2


def test_cache_does_not_merge_different_quoted_queries_or_cache_failures_permanently():
    search = ScientificSearch(ScholarConfig())
    calls = []
    def provider(query, *args, **kwargs): calls.append(query); return [Article(title=query)]
    with search.turn_cache() as turn:
        for query in ['field:"A  B" AND C', 'field:"A B" AND C', 'field:"A B" OR C']:
            search._query("semantic_scholar", provider, query, 50, None, turn)
    assert len(calls) == 3
    def broken(*args, **kwargs): raise httpx.ReadTimeout("Unavailable")
    with search.turn_cache() as turn:
        with pytest.raises(httpx.ReadTimeout): search._query("semantic_scholar", broken, "fresh", 50, None, turn)
    with search.turn_cache() as turn:
        rows, cached = search._query("semantic_scholar", provider, "fresh", 50, None, turn)
        assert rows and not cached


def test_provider_spacing_and_retry_after_are_respected_without_request_storm(monkeypatch):
    import agents.EnnoScholarV2.tools.scientific_search as module
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.value, sleep=lambda delay: setattr(clock, "value", clock.value + delay)))
    search = ScientificSearch(ScholarConfig())
    starts = []
    def request(url, *args):
        starts.append(clock.value)
        return httpx.Response(429 if len(starts) == 1 else 200, headers={"retry-after": "5"}, request=httpx.Request("GET", url))
    monkeypatch.setattr(search, "_request", request)
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    with pytest.raises(httpx.HTTPStatusError): search._get(url)
    assert search._get(url).status_code == 200
    assert search._get(url).status_code == 200 and starts == [0, 5, 6]


def test_deadline_exhaustion_prevents_next_url_and_doi(monkeypatch):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(fulltext, "time", SimpleNamespace(monotonic=lambda: clock.value))
    calls = []
    def read(url, *args, **kwargs):
        calls.append(url); clock.value = kwargs["deadline"]; raise httpx.ReadTimeout("Expired")
    monkeypatch.setattr(fulltext, "read_fulltext", read)
    search = ScientificSearch(ScholarConfig())
    monkeypatch.setattr(search, "lookup_doi", lambda *a, **kw: pytest.fail("DOI after article deadline"))
    article = Article(id="a", title="Article", abstract="Abstract", doi="10.9999/work", pdf_url="https://public.example/dead.pdf", arxiv_id="2501.12345")
    result = fulltext.get_fulltext(article, search, deadline=0.05)
    # Less than 100ms remaining: no new network attempt is started.
    assert calls == [] and result.access_level == "abstract_only" and "incomplète" in result.reason
    clock.value = 0
    result = fulltext.get_fulltext(article, search, deadline=1)
    assert calls == [article.pdf_url] and "incomplète" in result.reason


def test_large_retry_after_does_not_block_doi_lookup_without_explicit_deadline(monkeypatch):
    import agents.EnnoScholarV2.tools.scientific_search as module
    search = ScientificSearch(ScholarConfig())
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.value, sleep=lambda _: pytest.fail("Long cooldown sleep")))
    search._next_request["semantic_scholar"] = 3600
    monkeypatch.setattr(search, "_request", lambda *args: pytest.fail("Network during cooldown"))
    with pytest.raises(TimeoutError): search._get("https://api.semanticscholar.org/graph/v1/paper/search")


def test_empty_or_abstract_cached_result_does_not_prevent_real_fulltext_attempt(fake_client, monkeypatch):
    calls = []
    def retrieve(*args, **kwargs): calls.append(1); return text_result()
    monkeypatch.setattr("agents.EnnoScholarV2.agent.get_fulltext", retrieve)
    registry = {"a": StoredArticle(Article(id="a", title="Article"), fulltext=FulltextResult(access_level="fulltext", text=""))}
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    result = agent.execute_tool("get_fulltext", {"article_ids": ["a"]}, registry, fake_client)
    assert calls == [1] and result["articles"][0]["available"]


def test_fulltext_success_stops_before_other_urls_and_doi(monkeypatch, caplog):
    seen = []
    def read(url, *args, **kwargs): seen.append(url); return text_result(url)
    monkeypatch.setattr(fulltext, "read_fulltext", read)
    search = ScientificSearch(ScholarConfig())
    monkeypatch.setattr(search, "lookup_doi", lambda *a, **kw: pytest.fail("DOI lookup after success"))
    article = Article(id="a", title="Article", pdf_url="https://public.example/direct.pdf", arxiv_id="2501.12345", doi="10.9999/work")
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    result = fulltext.get_fulltext(article, search, ["https://public.example/next"])
    assert result.access_level == "fulltext" and seen == [article.pdf_url]
    assert "status=success" in caplog.text and "attempts=1" in caplog.text
    assert result.text not in caplog.text


def test_timeout_then_next_source_and_doi_last_with_work_urls(monkeypatch, caplog):
    seen = []
    def read(url, *args, **kwargs):
        seen.append(url)
        assert isinstance(kwargs["timeout"], httpx.Timeout) and kwargs["deadline"]
        if len(seen) == 1: raise httpx.ReadTimeout("Slow first URL")
        return text_result(url)
    monkeypatch.setattr(fulltext, "read_fulltext", read)
    article = Article(id="a", title="Article", pdf_url="https://public.example/dead.pdf", arxiv_id="2501.12345",
        url="https://publisher.example/article", doi="10.9999/work", work_urls=["https://repository.example/work"])
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    search = ScientificSearch(ScholarConfig())
    monkeypatch.setattr(search, "lookup_doi", lambda *a, **kw: pytest.fail("DOI before known versions"))
    result = fulltext.get_fulltext(article, search)
    assert seen == [article.pdf_url, "https://arxiv.org/pdf/2501.12345"]
    assert result.fulltext_url == seen[-1] and "status=timeout" in caplog.text


def test_cached_fulltext_and_associated_version_have_no_network_and_exact_provenance(fake_client, monkeypatch):
    original = text_result("https://arxiv.org/pdf/2501.12345v2")
    publication = Article(id="pub", title="Published work", canonical_article_id="pub", related_article_ids=["pub", "pre"])
    preprint = Article(id="pre", title="Preprint work", canonical_article_id="pub", related_article_ids=["pub", "pre"])
    registry = {"pub": StoredArticle(publication), "pre": StoredArticle(preprint, fulltext=original)}
    monkeypatch.setattr("agents.EnnoScholarV2.agent.get_fulltext", lambda *a, **kw: pytest.fail("Cached PDF downloaded again"))
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    result = agent.execute_tool("get_fulltext", {"article_ids": ["pub", "pre"]}, registry, fake_client)
    assert all(row["available"] for row in result["articles"])
    assert registry["pub"].fulltext is registry["pre"].fulltext is original
    assert all(row["fulltext_url"] == original.fulltext_url and row["source_article_id"] == "pre" for row in result["articles"])
    assert original.text not in json.dumps(result)


def test_same_work_simultaneous_requests_download_only_once(fake_client, monkeypatch):
    registry = {identifier: StoredArticle(Article(id=identifier, title="Work " + identifier, canonical_article_id="pub",
        related_article_ids=["pub", "pre"])) for identifier in ("pub", "pre")}
    calls = []
    def read(*args, **kwargs): calls.append(1); time.sleep(0.02); return text_result()
    monkeypatch.setattr("agents.EnnoScholarV2.agent.get_fulltext", read)
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    result = agent.execute_tool("get_fulltext", {"article_ids": ["pub", "pre"]}, registry, fake_client)
    assert len(calls) == 1 and all(row["available"] for row in result["articles"])


def test_slow_article_does_not_block_other_workers_and_concurrency_is_bounded(fake_client, monkeypatch):
    registry = {str(i): StoredArticle(Article(id=str(i), title="Work " + str(i))) for i in range(6)}
    done = threading.Event()
    lock = threading.Lock()
    active = peak = 0
    def retrieve(article, *args, **kwargs):
        nonlocal active, peak
        with lock: active += 1; peak = max(peak, active)
        try:
            if article.id == "0": assert done.wait(2), "Other workers were blocked behind the first"
            else: time.sleep(0.01); done.set()
            return text_result()
        finally:
            with lock: active -= 1
    monkeypatch.setattr("agents.EnnoScholarV2.agent.get_fulltext", retrieve)
    agent = ScholarChatAgent(ScholarConfig(model="mock", fulltext_concurrency=2), client=fake_client)
    result = agent.execute_tool("get_fulltext", {"article_ids": list(registry)}, registry, fake_client)
    assert peak == 2 and all(row["available"] for row in result["articles"])


def test_near_fulltext_deadline_stops_and_synthesis_remains_available(fake_client, monkeypatch):
    import agents.EnnoScholarV2.agent as module
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock.value)
    agent = ScholarChatAgent(ScholarConfig(model="mock", turn_timeout=120, soft_time_limit=10,
        fulltext_batch_timeout=5), client=fake_client)
    registry = {"a": StoredArticle(Article(id="a", title="Article", abstract="Known abstract"))}
    def retrieve(article, *args, **kwargs):
        assert kwargs["deadline"] == 5
        clock.value = 5
        return fulltext.unavailable_fulltext(article, incomplete=True)
    monkeypatch.setattr(module, "get_fulltext", retrieve)
    fake_client.responses.create.side_effect = [response(call("get_fulltext", {"article_ids": ["a"], "candidate_urls": []})),
        response(message("Abstract disponible, fulltext non récupéré dans le temps disponible."))]
    result = agent.run("Vérifie cet article", [], registry)
    assert not result.partial and result.tool_counts == {"get_fulltext": 1} and clock.value < 40
    expired = agent.execute_tool("get_fulltext", {"article_ids": ["a"]}, registry, fake_client, deadline=5)
    assert expired["articles"][0]["access_level"] == "abstract_only" and "incomplète" in expired["articles"][0]["reason"]


def test_compatible_analysis_is_reused_but_context_material_and_model_changes_invalidate(fake_client, monkeypatch):
    calls = []
    def analyze(article, stored, *args):
        calls.append(article.id)
        return ArticleAnalysis(access_level="fulltext" if stored else "abstract_only", analysis_basis="fulltext" if stored else "abstract", method="Verified")
    monkeypatch.setattr("agents.EnnoScholarV2.agent.analyze_article", analyze)
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    registry = {"a": StoredArticle(Article(id="a", title="Article", abstract="Original abstract", access_level="abstract_only"))}
    args = {"article_ids": ["a"], "user_context": "Goal"}
    first = agent.execute_tool("analyze_articles", args, registry, fake_client)
    assert agent.execute_tool("analyze_articles", args, registry, fake_client) == first and len(calls) == 1
    agent.execute_tool("analyze_articles", {**args, "user_context": "Other goal"}, registry, fake_client)
    assert len(calls) == 2
    registry["a"].article.abstract += " richer details"
    agent.execute_tool("analyze_articles", args, registry, fake_client)
    assert len(calls) == 3
    registry["a"].fulltext = text_result()
    agent.execute_tool("analyze_articles", args, registry, fake_client)
    assert len(calls) == 4 and registry["a"].analysis.analysis_basis == "fulltext"
    agent.config = replace(agent.config, model="other-mock")
    agent.execute_tool("analyze_articles", args, registry, fake_client)
    assert len(calls) == 5
    fake_client.responses.create.assert_not_called()


def test_analysis_workers_are_really_parallel_and_bounded(fake_client, monkeypatch):
    barrier = threading.Barrier(2)
    def analyze(*args):
        barrier.wait(timeout=2)
        return ArticleAnalysis(access_level="abstract_only", analysis_basis="abstract")
    monkeypatch.setattr("agents.EnnoScholarV2.agent.analyze_article", analyze)
    registry = {str(i): StoredArticle(Article(id=str(i), title="Article " + str(i), abstract="Abstract")) for i in range(4)}
    agent = ScholarChatAgent(ScholarConfig(model="mock", analysis_concurrency=2), client=fake_client)
    result = agent.execute_tool("analyze_articles", {"article_ids": list(registry)}, registry, fake_client)
    assert all("analysis_basis" in row for row in result["articles"])


@pytest.mark.parametrize("mode", ["headers", "trickling_body", "redirects"])
def test_download_has_wall_deadline_not_just_idle_timeout(monkeypatch, mode):
    real_client = httpx.AsyncClient
    calls = []
    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(30): await asyncio.sleep(0.01); yield b"chunk"
    async def handler(request):
        calls.append(request.url)
        if mode == "headers": await asyncio.sleep(0.25)
        if mode == "redirects":
            await asyncio.sleep(0.025)
            return httpx.Response(302, headers={"location": "https://public.example/next"})
        return httpx.Response(200, stream=Trickle(), headers={"content-type": "application/pdf"})
    monkeypatch.setattr(article_reader, "validate_public_url", lambda url: None)
    monkeypatch.setattr(article_reader.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    start = time.monotonic()
    with pytest.raises(httpx.TimeoutException):
        article_reader.fetch_url("https://public.example/pdf", httpx.Timeout(0.1), deadline=start + 0.06, headers_timeout=0.04)
    assert time.monotonic() - start < 0.2
    assert len(calls) < 5


def test_performance_configuration_is_bounded_and_does_not_reduce_depth(monkeypatch):
    for key in ("FULLTEXT_CONCURRENCY", "ANALYSIS_CONCURRENCY", "SEMANTIC_CONCURRENCY", "OPENALEX_CONCURRENCY"):
        monkeypatch.setenv("ENNOSCHOLAR_V2_" + key, "100")
    monkeypatch.setenv("ENNOSCHOLAR_V2_SEMANTIC_REQUEST_INTERVAL", "0")
    monkeypatch.setenv("ENNOSCHOLAR_V2_OPENALEX_REQUEST_INTERVAL", "0")
    config = ScholarConfig.from_env()
    assert config.fulltext_concurrency == config.analysis_concurrency == 3
    assert config.semantic_concurrency == config.openalex_concurrency == 4
    assert config.semantic_request_interval == 1 and config.openalex_request_interval == 0.1
    assert config.search_candidate_limit == 50 and config.active_article_limit == 12
