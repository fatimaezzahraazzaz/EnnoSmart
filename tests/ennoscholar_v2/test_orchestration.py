from __future__ import annotations

import json

import httpx
import pytest
from openai import APITimeoutError

from agents.EnnoScholarV2.agent import ScholarChatAgent, TOOLS
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.service import ScholarChatService
from conftest import call, message, response
from context_reproduction import QUESTION
from orchestration_reproduction import prepare_reproduction
from test_batching import client_for_service, make_registry
from test_context_window import active_notices


def test_catalogue_has_batch_and_no_unitary_notice_tool():
    names = [tool.get("name") for tool in TOOLS]
    assert "get_articles" in names and "get_article" not in names
    tool = next(tool for tool in TOOLS if tool.get("name") == "get_articles")
    assert tool["parameters"]["required"] == ["article_ids", "request_kind"]
    assert "bibliographic_search" in tool["parameters"]["properties"]["request_kind"]["enum"]
    assert tool["parameters"]["properties"]["article_ids"]["maxItems"] == 12


def test_batch_notices_keeps_metadata_abstracts_and_item_errors(fake_client):
    registry = make_registry(3)
    registry["a0"].article.abstract = "z" * 5000
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    result = agent.execute_tool("get_articles", {"article_ids": ["a0", "a1", "a0", "unknown"]}, registry, fake_client)
    assert len(result["articles"]) == 3
    assert result["articles"][0]["doi"] == "10.1234/mesh0"
    assert len(result["articles"][0]["abstract"]) == 4000
    assert result["articles"][1]["id"] == "a1"
    assert result["articles"][2]["error"]
    assert len(registry["a0"].article.abstract) == 5000
    fake_client.responses.create.assert_not_called()


@pytest.mark.parametrize("identifiers", [[], ["a0"] * 13, "a0", [None], [{}]])
def test_batch_notices_rejects_invalid_arguments(identifiers, fake_client):
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    with pytest.raises(ValueError):
        agent.execute_tool("get_articles", {"article_ids": identifiers}, make_registry(), fake_client)


@pytest.mark.parametrize("timeouts", [0, 1, 2])
def test_original_run_becomes_five_calls_and_preserves_completed_research(monkeypatch, timeouts):
    run = prepare_reproduction(monkeypatch, timeouts=timeouts)
    service = ScholarChatService(run.agent.config, agent=run.agent)
    client = client_for_service(monkeypatch, service)
    result = client.post("/scholar-v2/chat", json={"message": QUESTION})
    assert result.status_code == 200
    payload = result.json()
    assert payload["tool_counts"] == {"search_scientific": 2, "get_articles": 1, "get_fulltext": 1, "analyze_articles": 1}
    assert payload["tool_calls"] == 5  # Previously 20: 75% fewer model tool calls.
    assert payload["articles_total"] == 289 and len(payload["articles"]) <= 12
    assert set(run.retrieved) == {article.id for article in run.articles[:3]} and len(run.retrieved) == 3
    session = service.sessions[payload["session_id"]]
    assert len(session.messages) == 2
    assert all(session.articles[article.id].fulltext and session.articles[article.id].analysis for article in run.articles[:3])
    assert len(run.requests) == (4 if timeouts == 0 else 5)
    assert payload["partial"] == (timeouts == 2)
    assert payload["reason"] == ("synthesis_timeout" if timeouts == 2 else None)
    if timeouts:
        retry = run.requests[4]
        assert retry["tools"] == [] and retry["tool_choice"] == "none"
        assert retry["input"] == run.requests[3]["input"]
    if timeouts == 2:
        assert "La synthèse finale n'a pas pu être générée." in payload["answer"]
        assert "Learned 3D finite element mesh method 000" in payload["answer"]
        assert "texte intégral récupéré" in payload["answer"] and "Méthode de maillage apprise" in payload["answer"]
        assert payload["citations"]
        # RAM session survives; follow-up can revisit exactly the collected articles.
        run.client.responses.create.side_effect = lambda **kwargs: response(message("Les trois articles analysés sont conservés."))
        following = client.post("/scholar-v2/chat", json={"message": "Reviens sur ces articles", "session_id": payload["session_id"]})
        assert following.status_code == 200 and following.json()["articles_total"] == 289
    for request in run.requests:
        assert len(active_notices(request)) <= 12
    details_output = next(item for item in run.requests[2]["input"] if item.get("type") == "function_call_output" and item["call_id"] == "details")
    assert len(json.loads(details_output["output"])["article_ids"]) == 8
    assert "abstract" not in details_output["output"]  # Details live in the existing active selection.
    assert any(len(row["abstract"]) > 800 for row in active_notices(run.requests[2]))


def test_more_than_16_unitary_calls_cannot_execute(fake_client, monkeypatch):
    registry = make_registry(20)
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    execute = agent.execute_tool
    seen = []
    def record(name, *args, **kwargs):
        seen.append(name)
        return execute(name, *args, **kwargs)
    monkeypatch.setattr(agent, "execute_tool", record)
    fake_client.responses.create.side_effect = [response(*(call("get_article", {"article_id": f"a{i}"}, f"unit{i}") for i in range(17))),
        response(call("get_articles", {"article_ids": ["a0", "a1", "a2"]}, "batch")), response(message("Notices disponibles."))]
    result = agent.run("Choisis quelques candidats utiles", [], registry)
    assert result.tool_counts == {"get_articles": 1}
    assert sum(item.tool == "get_article" and item.status == "skipped" for item in result.activity) == 17
    # Internal compatibility reader may run per row; no unitary model call is executed.
    assert seen.count("get_article") <= 3 and not result.partial


def test_soft_limit_stops_repeated_discovery_but_allows_batch_fulltext_and_analysis(monkeypatch):
    run = prepare_reproduction(monkeypatch, timeouts=0, soft=2)
    registry = {}
    result = run.agent.run(QUESTION, [], registry)
    assert sum(result.tool_counts.values()) == 5 and not result.partial
    names = lambda request: {tool.get("name") for tool in request["tools"]}
    assert "search_scientific" not in names(run.requests[1])
    assert "find_similar_articles" not in names(run.requests[1])
    assert "get_articles" in names(run.requests[1])
    assert "get_articles" not in names(run.requests[2])
    assert {"get_fulltext", "analyze_articles"} <= names(run.requests[2])


def test_soft_limit_rechecks_already_proposed_searches(fake_client, monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock-model", soft_tool_budget=1), client=fake_client)
    monkeypatch.setattr(agent.search, "search_batch", lambda *args, **kwargs: {"all_articles": [record.article.model_dump() for record in make_registry(3).values()]})
    fake_client.responses.create.side_effect = [response(call("search_scientific", {"queries": ["mesh"], "top_k": 3}, "first"),
        call("search_scientific", {"queries": ["mesh again"], "top_k": 3}, "repeat")), response(message("Trois candidats trouvés."))]
    result = agent.run("Maillage", [], {})
    assert result.tool_counts == {"search_scientific": 1}
    assert result.activity[-1].status == "skipped" and not result.partial


def test_timeout_before_useful_tools_still_reports_upstream_failure(fake_client, monkeypatch):
    fake_client.responses.create.side_effect = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    service = ScholarChatService(agent.config, agent=agent)
    result = client_for_service(monkeypatch, service).post("/scholar-v2/chat", json={"message": QUESTION})
    assert result.status_code == 502 and not service.sessions
    assert fake_client.responses.create.call_count == 1


def test_timeout_in_existing_limit_finalizer_also_retries_once(fake_client):
    agent = ScholarChatAgent(ScholarConfig(model="mock-model", hard_tool_budget=1), client=fake_client)
    timeout = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    fake_client.responses.create.side_effect = [response(call("get_articles", {"article_ids": ["a0"]})), timeout, timeout]
    result = agent.run("Retrouve cet article", [], make_registry(1))
    assert result.partial and result.reason == "synthesis_timeout" and result.tool_counts == {"get_articles": 1}
    assert fake_client.responses.create.call_count == 3
    assert all(request.kwargs["tools"] == [] for request in fake_client.responses.create.call_args_list[1:])
