from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV2.agent import ScholarChatAgent, StoredArticle
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.models import Article, ChatRequest
from agents.EnnoScholarV2.service import ScholarChatService
from agents.EnnoScholarV2.tools.ranking import rank_articles
from conftest import call, message, response
from context_reproduction import QUESTION
from scientific_fixture import CENTRAL_TITLES, INTENT, QUERIES, fixture_articles, prepare_scientific_run
from test_batching import client_for_service
from test_context_window import active_notices


def test_scientific_ranking_promotes_objective_over_simulation_and_graphics():
    articles = fixture_articles()
    ranked = rank_articles(articles, QUERIES, INTENT)
    assert set(article.title for article in ranked[:8]) == set(CENTRAL_TITLES)
    assert all(article.relevance_tag in {"DIRECT", "FONDAMENTAL"} for article in ranked[:8])
    assert all(article.relevance_tag == "CONNEXE" for article in ranked[8:])


def test_ranking_is_generic_for_a_different_scientific_object():
    intent = {"target_terms": ["proteins"], "primary_operations": ["design"], "secondary_operations": ["optimization"],
        "adjacent_operations": ["simulation", "reconstruction"], "method_terms": ["neural"], "context_terms": []}
    direct = Article(id="direct", title="Neural design of proteins", abstract="A new method.")
    adjacent = Article(id="adjacent", title="Neural simulation of proteins", abstract="We compare to protein design.", citation_count=100000)
    assert rank_articles([adjacent, direct], ["neural protein design simulation"], intent)[0].id == "direct"
    # Changing the user's goal must change the result; no fixed operation priority.
    intent["primary_operations"], intent["adjacent_operations"] = ["simulation"], ["design"]
    assert rank_articles([direct, adjacent], ["neural protein simulation"], intent)[0].id == "adjacent"


def test_fulltext_and_payment_do_not_change_ranking():
    articles = fixture_articles()
    before = [article.id for article in rank_articles(articles, QUERIES, INTENT)]
    for article in articles:
        article.access_level = "fulltext" if "unrelated" in article.title else "metadata_only"
        article.pdf_url = "https://example.org/free.pdf" if "unrelated" in article.title else ""
    after = [article.id for article in rank_articles(articles, QUERIES, INTENT)]
    assert before == after


def test_collection_depth_is_independent_of_top_compact(monkeypatch):
    run = prepare_scientific_run(monkeypatch)
    result = run.agent.search.search_batch(QUERIES, 8, intent=INTENT)
    assert len(result["all_articles"]) == 600 and len(result["articles"]) == 8
    assert len(run.provider_calls) == 12 and {entry[2] for entry in run.provider_calls} == {50}
    assert all(len(row["abstract_excerpt"]) <= 800 for row in result["articles"])
    assert len(result["collection"]) == 12 and result["warnings"] == []


@pytest.mark.parametrize("timeouts", [0, 1, 2])
def test_reference_query_global_best_fulltext_analysis_and_separate_corpus(monkeypatch, timeouts):
    run = prepare_scientific_run(monkeypatch, timeouts=timeouts)
    service = ScholarChatService(run.agent.config, agent=run.agent)
    client = client_for_service(monkeypatch, service)
    result = client.post("/scholar-v2/chat", json={"message": QUESTION})
    assert result.status_code == 200
    payload = result.json()
    assert payload["articles_total"] == 650 and len(payload["articles"]) <= 12
    assert len(payload["active_article_ids"]) == 12
    assert payload["tool_counts"] == {"search_scientific": 2, "get_articles": 1, "get_fulltext": 1, "analyze_articles": 1}
    assert payload["tool_calls"] == 5 and payload["partial"] == (timeouts == 2)
    assert payload["reason"] == ("synthesis_timeout" if timeouts == 2 else None)
    assert len(payload["citations"]) <= 12
    session = service.sessions[payload["session_id"]]
    assert len(session.articles) == 650
    assert session.articles[run.articles[0].id].article.access_level == "metadata_only"
    assert session.articles[run.articles[1].id].article.access_level == "fulltext"
    assert session.articles[run.articles[2].id].article.access_level == "abstract_only"
    assert session.articles[run.articles[0].id].analysis.analysis_basis == "metadata"
    assert session.articles[run.articles[1].id].analysis.analysis_basis == "fulltext"
    assert session.articles[run.articles[2].id].analysis.analysis_basis == "abstract"
    assert session.articles[run.articles[1].id].analysis.results == ""
    assert session.articles[run.articles[1].id].analysis.evidence[0].page == 4
    assert session.articles[run.articles[1].id].article.abstract.endswith("BACKEND_COMPLETE_ABSTRACT_TAIL")
    for request in run.requests[3:]:
        notices = active_notices(request)
        assert len(notices) <= 12
        # Explicitly inspected references stay pinned; no global scores select them.
        assert set(CENTRAL_TITLES) <= {notice["title"] for notice in notices}
        assert "BACKEND_COMPLETE_ABSTRACT_TAIL" not in request["instructions"]
    # Walk the entire authenticated corpus, including articles never sent to GPT.
    seen = []
    for offset in range(0, 650, 50):
        page = client.get(f"/scholar-v2/sessions/{payload['session_id']}/articles", params={"offset": offset, "limit": 50})
        assert page.status_code == 200 and page.json()["total"] == 650
        seen.extend(article["id"] for article in page.json()["articles"])
    assert len(seen) == len(set(seen)) == 650


def test_soft_time_after_three_calls_keeps_fulltext_analysis_and_normal_synthesis(monkeypatch):
    clock = SimpleNamespace(value=0.0)
    run = prepare_scientific_run(monkeypatch, soft=10, hard=120, clock=clock)
    registry = {}
    result = run.agent.run(QUESTION, [], registry)
    assert not result.partial and result.reason is None
    assert result.tool_counts == {"search_scientific": 1, "get_articles": 1, "get_fulltext": 1, "analyze_articles": 1}
    assert clock.value > 10 and len(registry) == 600
    request = run.requests[2]
    names = {tool.get("name") for tool in request["tools"]}
    assert "search_scientific" not in names
    assert {"get_articles", "get_fulltext", "analyze_articles"} <= names
    assert request["tool_choice"] == "auto"
    assert any(item.tool == "search_scientific" and item.status == "skipped" for item in result.activity)


def test_fulltext_deadline_reserves_time_for_analysis_and_synthesis(monkeypatch):
    clock = SimpleNamespace(value=0.0)
    run = prepare_scientific_run(monkeypatch, soft=10, hard=120, clock=clock)
    run.agent.run(QUESTION, [], {})
    assert all(deadline <= 40 for _, deadline in run.retrieved)
    assert any("text" in request.kwargs for request in run.client.responses.create.call_args_list)


def test_tool_deadline_preserves_collected_data_and_allows_separate_synthesis(fake_client, monkeypatch):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr("agents.EnnoScholarV2.agent.time.monotonic", lambda: clock.value)
    agent = ScholarChatAgent(ScholarConfig(model="mock", turn_timeout=60, soft_time_limit=10), client=fake_client)
    def search(*args, **kwargs):
        clock.value = 61
        return {"all_articles": [Article(title="Collected article", doi="10.1234/stored").model_dump()]}
    monkeypatch.setattr(agent.search, "search_batch", search)
    fake_client.responses.create.side_effect = [response(call("search_scientific", {"queries": ["subject"], "top_k": 8})),
        response(message("Synthèse des résultats conservés après expiration du budget outils."))]
    service = ScholarChatService(agent.config, agent=agent)
    result = service.chat(1, ChatRequest(message=QUESTION))
    assert result.partial and result.reason == "time_budget_reached"
    assert result.articles_total == 1 and service.sessions[result.session_id].articles
    assert fake_client.responses.create.call_count == 2
    assert fake_client.responses.create.call_args.kwargs["tools"] == []


def test_corpus_endpoint_checks_owner_auth_expiration_and_limits(monkeypatch):
    from core.deps import get_current_user
    agent = Mock()
    agent.run.return_value = ("Bonjour", [], [])
    service = ScholarChatService(ScholarConfig(), agent=agent)
    client = client_for_service(monkeypatch, service)
    sid = client.post("/scholar-v2/chat", json={"message": "Bonjour"}).json()["session_id"]
    url = f"/scholar-v2/sessions/{sid}/articles"
    assert client.get(url).status_code == 200
    for params in ({"limit": 51}, {"limit": 0}, {"offset": -1}):
        assert client.get(url, params=params).status_code == 422
    client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=2)
    assert client.get(url).status_code == 404
    client.app.dependency_overrides.clear()
    assert client.get(url).status_code in {401, 403}
    client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    service.sessions[sid].updated_at -= 10000
    assert client.get(url).status_code == 404


def test_followup_does_not_seed_from_global_scores(fake_client):
    records = fixture_articles()
    rank_articles(records, QUERIES, INTENT)
    registry = {article.id: StoredArticle(article) for article in records}
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    fake_client.responses.create.return_value = response(message("Articles précédents disponibles."))
    result = agent.run("Reviens sur les candidats", [], registry)
    assert result.active_article_ids == [article.id for article in reversed(records[-12:])]
    assert all("relevance_score" not in notice for notice in active_notices(fake_client.responses.create.call_args.kwargs))
