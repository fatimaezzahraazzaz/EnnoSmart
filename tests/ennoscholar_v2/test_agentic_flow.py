from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from agents.EnnoScholarV2.agent import ScholarChatAgent, StoredArticle
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.context import ActiveSources
from agents.EnnoScholarV2.models import Article, FulltextResult, TextChunk
from agents.EnnoScholarV2.tools.compact_results import compact_results
from agents.EnnoScholarV2.tools.article_analysis import analyze_article
from conftest import call, message, response, evidence_reference
from scientific_fixture import CENTRAL_TITLES, INTENT, QUERIES, EXCERPT, fixture_articles
from context_reproduction import QUESTION
from test_context_window import active_notices


def test_native_provider_positions_and_query_diversity_not_global_scores(monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock"))
    queries = ["query-a", "query-b", "query-c", "query-d"]
    native_first = []
    for source, method in enumerate(("semantic", "openalex", "arxiv")):
        def provider(query, limit, deadline=None, source=source):
            rows = [Article(title=f"Native {source} {query} {i}", doi=f"10.9999/{source}-{query}-{i}",
                abstract="Complete backend abstract. " * 100, sources=[str(source)], source=str(source),
                relevance_score=100000 if i > 0 else -100000) for i in range(limit)]
            native_first.append(rows[0].doi)
            return rows
        monkeypatch.setattr(agent.search, method, provider)
    result = agent.search.search_batch(queries, 8, intent=INTENT)
    assert len(result["all_articles"]) == 600 and len(result["articles"]) == 8
    assert set(queries) <= {q for row in result["articles"] for q in row["matched_queries"]}
    assert {str(i) for i in range(3)} <= {s for row in result["articles"] for s in row["sources"]}
    assert all(row["doi"] in native_first for row in result["articles"])
    assert all(len(row["abstract_excerpt"]) <= 800 for row in result["articles"])
    assert all("relevance_score" not in row for row in result["articles"])


def test_explicit_details_fulltext_analysis_remain_pinned_through_searches():
    registry = {f"a{i}": StoredArticle(Article(id=f"a{i}", title=f"Paper {i}")) for i in range(50)}
    active = ActiveSources(registry)
    active.inspect([{"article_id": "a0"}, {"article_id": "a1"}], detailed=True)
    active.tool_result("get_fulltext", {"articles": [{"article_id": "a2", "available": True}]})
    active.tool_result("analyze_articles", {"articles": [{"article_id": "a3", "analysis_basis": "abstract"}]})
    for start in (10, 20, 30):
        active.tool_result("search_scientific", {"articles": [{"article_id": f"a{i}"} for i in range(start, start + 8)]})
        assert set(active.identifiers[:4]) == {"a0", "a1", "a2", "a3"}
        assert len(active.identifiers) == 12 and {"a0", "a1"} <= active.detailed


def test_explicit_requests_above_capacity_keep_latest_batch():
    registry = {f"a{i}": StoredArticle(Article(id=f"a{i}", title=f"Paper {i}")) for i in range(30)}
    active = ActiveSources(registry)
    active.inspect([{"article_id": f"a{i}"} for i in range(12)])
    active.inspect([{"article_id": f"a{i}"} for i in range(12, 20)])
    assert len(active.identifiers) == 12 and len(active.pinned) == 12
    assert set(f"a{i}" for i in range(12, 20)) <= set(active.identifiers)


def test_compact_versions_one_work_all_records_and_urls_stay_backend(fake_client):
    pub = Article(id="pub", title="Learned construction of scientific numerical computational structures and elements", doi="10.9999/pub",
        authors=["A"], year=2025, sources=["openalex"], url="https://publisher.example/pub")
    pre = Article(id="pre", title=pub.title.replace("and elements", "and element"), arxiv_id="2501.12345",
        authors=["A"], year=2025, sources=["arxiv"], url="https://arxiv.org/abs/2501.12345", abstract="Complete abstract.")
    text = FulltextResult(access_level="fulltext", text="PRIVATE_FULLTEXT")
    registry = {pub.id: StoredArticle(pub), pre.id: StoredArticle(pre, fulltext=text)}
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    agent.register([pub.model_dump(), pre.model_dump()], registry)
    assert len(registry) == 2 and registry["pre"].fulltext is text
    assert len(compact_results([record.article for record in registry.values()])) == 1
    assert len(registry["pub"].article.work_urls) >= 3
    active = ActiveSources(registry)
    active.inspect([{"article_id": "pre"}])
    active.update([{"article_id": "pub"}])
    assert active.identifiers == ["pre"]


def test_evidence_ids_restore_exact_backend_quote_and_reject_unknown(fake_client):
    quote = "The method  preserves EXACT punctuation and spacing."
    full = FulltextResult(access_level="fulltext", text=quote, chunks=[TextChunk(page=7, section="Results", text=quote)])
    def create(**kwargs):
        data = json.loads(kwargs["input"])
        schema = json.dumps(kwargs["text"])
        assert '"excerpt"' not in schema and '"evidence_id"' in schema
        return SimpleNamespace(status="completed", output_text=json.dumps({"results": "Supported result", "method": "Unsupported method",
            "evidence": [evidence_reference(data, quote, ["results"]), {"evidence_id": "invented", "supports": ["method"]}],
            "access_level": "fulltext", "analysis_basis": "fulltext"}))
    fake_client.responses.create.side_effect = create
    result = analyze_article(Article(title="Paper"), full, fake_client, ScholarConfig(model="mock"))
    assert result.results and not result.method and len(result.evidence) == 1
    assert result.evidence[0].excerpt == quote and result.evidence[0].page == 7
    assert result.evidence[0].section == "Results" and result.evidence[0].evidence_id.startswith("ev_")


def test_benchmark_iterative_conversation_600_registry_no_internal_ranking(monkeypatch, fake_client):
    articles = fixture_articles()
    # Editorial and adjacent tasks are native first in the broad search fixture.
    articles[8].title = "Editorial message from the editor-in-chief"
    refined = ["structured discretization learning", "volume element synthesis", "learned preparation and adaptation"]
    requests, tool_results = [], []
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    for source, method in enumerate(("semantic", "openalex", "arxiv")):
        def provider(query, limit, deadline=None, source=source):
            if query in refined:
                offset = (refined.index(query) * 3 + source) % 8
                rows = articles[offset:8] + articles[:offset] + articles[600:642]
            else:
                index = source * 200 + QUERIES.index(query) * 50
                rows = articles[index:index + 50]
                if index == 0:
                    rows = articles[8:20] + articles[:8] + articles[20:50]
            return [article.model_copy(deep=True) for article in rows[:limit]]
        monkeypatch.setattr(agent.search, method, provider)
    inspected = []
    def create(**kwargs):
        if "text" in kwargs:
            assert kwargs["text"]["format"]["name"] == "article_analysis"
            data = json.loads(kwargs["input"])
            return SimpleNamespace(status="completed", output_text=json.dumps({"method": "Learned generation", "access_level": "fulltext",
                "analysis_basis": "fulltext", "evidence": [evidence_reference(data, EXCERPT, ["method"])]}))
        requests.append(copy.deepcopy(kwargs))
        number = len(requests)
        if number == 1:
            return response(call("search_scientific", {"queries": QUERIES, "top_k": 8, "intent": INTENT}, "broad"))
        if number == 2:
            assert not any(notice["title"] in CENTRAL_TITLES for notice in active_notices(kwargs))
            return response(call("search_scientific", {"queries": refined, "top_k": 8, "intent": INTENT}, "refine"))
        if number == 3:
            inspected.extend(notice["id"] for notice in active_notices(kwargs) if notice["title"] in CENTRAL_TITLES)
            assert len(inspected) == 8
            return response(call("get_articles", {"article_ids": inspected}, "details"))
        if number == 4:
            return response(call("get_fulltext", {"article_ids": inspected[:3], "candidate_urls": []}, "full"))
        if number == 5:
            return response(call("analyze_articles", {"article_ids": inspected[:3], "user_context": QUESTION}, "analysis"))
        notices = [notice for notice in active_notices(kwargs) if notice["id"] in inspected]
        return response(message("\n".join(notice["title"] for notice in notices)))
    fake_client.responses.create.side_effect = create
    monkeypatch.setattr("agents.EnnoScholarV2.agent.get_fulltext", lambda *a, **kw: FulltextResult(access_level="fulltext",
        text=EXCERPT, chunks=[TextChunk(page=3, section="Methods", text=EXCERPT)]))
    execute = agent.execute_tool
    def capture(name, *args, **kwargs):
        result = execute(name, *args, **kwargs)
        tool_results.append((name, result))
        return result
    monkeypatch.setattr(agent, "execute_tool", capture)
    registry = {}
    result = agent.run(QUESTION, [], registry)
    assert len(registry) >= 600 and len(result.active_article_ids) <= 12 and not result.partial
    assert result.tool_counts == {"search_scientific": 2, "get_articles": 1, "get_fulltext": 1, "analyze_articles": 1}
    assert all(len(output["articles"]) <= 12 for name, output in tool_results if name == "search_scientific")
    assert all(title in result.answer for title in CENTRAL_TITLES) and "Editorial" not in result.answer
    assert all(len(active_notices(request)) <= 12 for request in requests)
    assert all("BACKEND_COMPLETE_ABSTRACT_TAIL" not in request["instructions"] for request in requests)
    assert len(requests) == 6 and fake_client.responses.create.call_count == 9  # 6 chat + 3 analyses, no ranking
