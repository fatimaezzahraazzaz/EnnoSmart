from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError

from agents.EnnoScholarV2.agent import ScholarChatAgent, StoredArticle
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.conversation import CONVERSATION_INSTRUCTION
from agents.EnnoScholarV2.models import Article, ArticleAnalysis, FulltextResult, TextChunk
from agents.EnnoScholarV2.tools.article_analysis import AnalysisIncompleteError, analyze_article
from conftest import call, evidence_reference, message, response
from test_analysis_diagnostics import analysis_response, diagnostic_records
from test_context_window import active_notices


def registry_with_analyses(count=12):
    return {f"a{i}": StoredArticle(Article(id=f"a{i}", title=f"Scientific source {i}",
        doi=f"10.9999/source{i}", abstract="Existing abstract", access_level="abstract_only"),
        analysis=ArticleAnalysis(access_level="abstract_only", analysis_basis="abstract",
            method=f"Stored method {i}", analysis_limit="Existing abstract only")) for i in range(count)}


def timeout_error():
    return APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))


@pytest.mark.parametrize("question", [
    "Parmi les articles que tu viens de trouver, lesquels sont les plus utiles pour défendre qu’il existe encore un verrou scientifique sur la génération automatique de maillages 3D ?",
    "Compare ces articles à partir de leurs limites déjà analysées.",
    "Parmi les précédents, lesquels documentent les difficultés restantes ?",
])
def test_followup_memory_contract_no_unnecessary_search(fake_client, monkeypatch, question):
    # Offline prompt/agent contract: the mock makes the memory-first decision.
    # This test does not claim to evaluate a real model's instruction compliance.
    registry = registry_with_analyses()
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    monkeypatch.setattr(agent, "execute_tool", lambda *a, **kw: pytest.fail("No tool needed for stored analyses"))
    def create(**kwargs):
        assert CONVERSATION_INSTRUCTION in kwargs["instructions"]
        notices = active_notices(kwargs)
        assert len(notices) == 12 and all(row["analysis"]["method"] for row in notices)
        assert kwargs["input"][-1]["content"] == question
        assert any(tool.get("name") == "search_scientific" for tool in kwargs["tools"])
        return response(message("Comparaison fondée sur les analyses conservées, avec leurs limites."))
    fake_client.responses.create.side_effect = create
    result = agent.run(question, [{"role": "assistant", "content": "Douze articles déjà examinés."}], registry)
    assert not result.partial and result.tool_counts == {}
    assert len(result.active_article_ids) == 12
    fake_client.responses.create.assert_called_once()


def test_followup_explicit_new_sources_remains_agent_decision(fake_client, monkeypatch):
    registry = registry_with_analyses()
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    seen = []
    def execute(name, *args, **kwargs):
        seen.append(name)
        return {"articles": []}
    monkeypatch.setattr(agent, "execute_tool", execute)
    fake_client.responses.create.side_effect = [response(call("search_scientific", {"queries": ["new evidence"], "top_k": 8})),
        response(message("Recherche complémentaire effectuée à la demande."))]
    result = agent.run("Pour ces articles, cherche aussi de nouvelles publications.", [], registry)
    assert seen == ["search_scientific"] and not result.partial


@pytest.mark.parametrize("fulltext", [False, True])
@pytest.mark.parametrize("exhausted", [False, True])
def test_analysis_completed_or_token_retry_preserves_material_schema_and_evidence(fake_client, caplog, fulltext, exhausted):
    quote = "The source documents a verified scientific limitation."
    article = Article(id="a", title="Any scientific field", abstract=quote)
    text = FulltextResult(access_level="fulltext", text=quote, chunks=[TextChunk(page=7, text=quote)]) if fulltext else None
    requests = []
    def create(**kwargs):
        requests.append(copy.deepcopy(kwargs))
        payload = json.loads(kwargs["input"])
        if exhausted and len(requests) == 1:
            return analysis_response("UNFINISHED_PRIVATE", status="incomplete", details={"reason": "max_output_tokens"})
        return analysis_response(json.dumps({"access_level": "fulltext" if fulltext else "abstract_only",
            "analysis_basis": "fulltext" if fulltext else "abstract", "method": "Supported method",
            "evidence": [evidence_reference(payload, quote, ["method"])]}))
    fake_client.responses.create.side_effect = create
    config = ScholarConfig(model="mock", analysis_max_output_tokens=3500, analysis_retry_max_output_tokens=7200)
    result = analyze_article(article, text, fake_client, config, "Same scientific question")
    assert [r["max_output_tokens"] for r in requests] == ([3500, 7200] if exhausted else [3500])
    if exhausted:
        assert {k:v for k,v in requests[0].items() if k != "max_output_tokens"} == {k:v for k,v in requests[1].items() if k != "max_output_tokens"}
        assert len(diagnostic_records(caplog, "analysis_incomplete")) == 1
    assert result.method == "Supported method" and result.evidence[0].excerpt == quote
    assert result.evidence[0].page == (7 if fulltext else None)
    assert "UNFINISHED_PRIVATE" not in caplog.text


@pytest.mark.parametrize("details", [{"reason": "content_filter"}, {"reason": "other"}, None])
def test_analysis_other_incomplete_reason_has_no_retry(fake_client, details):
    fake_client.responses.create.return_value = analysis_response("PRIVATE", status="incomplete", details=details)
    with pytest.raises(AnalysisIncompleteError):
        analyze_article(Article(id="a", title="Article", abstract="Source material"), None, fake_client, ScholarConfig(model="mock"))
    fake_client.responses.create.assert_called_once()


def test_analysis_token_retry_never_loops(fake_client, caplog):
    fake_client.responses.create.return_value = analysis_response("PRIVATE", status="incomplete", details={"reason": "max_output_tokens"})
    with pytest.raises(AnalysisIncompleteError):
        analyze_article(Article(id="a", title="Article", abstract="Source material"), None, fake_client, ScholarConfig(model="mock"))
    assert fake_client.responses.create.call_count == 2
    assert len(diagnostic_records(caplog, "analysis_incomplete")) == 2


@pytest.mark.parametrize("second_timeout", [False, True])
def test_synthesis_retry_preserves_native_compacted_input_and_never_reexecutes_tools(fake_client, monkeypatch, second_timeout):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr("agents.EnnoScholarV2.agent.time.monotonic", lambda: clock.value)
    config = ScholarConfig(model="mock", timeout=4, synthesis_timeout=90, synthesis_retry_timeout=60)
    agent = ScholarChatAgent(config, client=fake_client)
    registry = registry_with_analyses(1)
    tools = []
    def execute(name, *args, **kwargs):
        tools.append(name)
        return {"articles": [{"article_id": "a0", "available": True}]}
    monkeypatch.setattr(agent, "execute_tool", execute)
    requests = []
    def create(**kwargs):
        requests.append(copy.deepcopy(kwargs))
        if len(requests) == 1:
            return response({"type": "compaction", "id": "compact_test", "encrypted_content": "opaque"},
                call("get_fulltext", {"article_ids": ["a0"], "candidate_urls": []}, "material"))
        if len(requests) == 2:
            clock.value = 650  # Even the old hard deadline is now consumed.
            raise timeout_error()
        assert kwargs["tools"] == [] and kwargs["tool_choice"] == "none"
        assert kwargs["input"] == requests[1]["input"]
        assert kwargs["input"][0]["id"] == "compact_test"
        assert fake_client.with_options.call_args.kwargs == {"timeout": 60, "max_retries": 0}
        if second_timeout:
            clock.value += 60
            raise timeout_error()
        return response(message("Synthèse répondant à la question à partir des données conservées."))
    fake_client.responses.create.side_effect = create
    result = agent.run("Compare ces sources", [], registry)
    assert tools == ["get_fulltext"] and result.tool_counts == {"get_fulltext": 1}
    assert result.partial == second_timeout
    assert result.reason == ("synthesis_timeout" if second_timeout else None)
    assert fake_client.responses.create.call_count == 3
    assert len(requests[-1]["input"]) == len(requests[-2]["input"])
    if second_timeout:
        assert "La synthèse finale n'a pas pu être générée." in result.answer
        assert "Stored method 0" in result.answer
    else:
        assert "n'a pas pu" not in result.answer


@pytest.mark.parametrize("second_timeout", [False, True])
def test_explicit_synthesis_phase_has_two_independent_budgets_after_expired_tools(fake_client, monkeypatch, second_timeout):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr("agents.EnnoScholarV2.agent.time.monotonic", lambda: clock.value)
    agent = ScholarChatAgent(ScholarConfig(model="mock", timeout=3, turn_timeout=30,
        synthesis_timeout=90, synthesis_retry_timeout=60), client=fake_client)
    registry = registry_with_analyses(1)
    tools = []
    def execute(name, *args, **kwargs):
        tools.append(name); clock.value = 35
        return {"articles": []}
    monkeypatch.setattr(agent, "execute_tool", execute)
    requests = []
    def create(**kwargs):
        requests.append(copy.deepcopy(kwargs))
        if len(requests) == 1:
            return response(call("get_articles", {"article_ids": ["a0"]}))
        assert kwargs["tools"] == []
        configured = fake_client.with_options.call_args.kwargs
        assert configured["max_retries"] == 0
        if len(requests) == 2:
            assert configured["timeout"] == 90
            clock.value += 90
            raise timeout_error()
        assert configured["timeout"] == 60 and kwargs["input"] == requests[1]["input"]
        if second_timeout:
            clock.value += 60
            raise timeout_error()
        return response(message("Synthèse conservée malgré expiration du budget outils."))
    fake_client.responses.create.side_effect = create
    result = agent.run("Compare ces sources", [], registry)
    assert tools == ["get_articles"] and fake_client.responses.create.call_count == 3
    assert result.reason == ("synthesis_timeout" if second_timeout else "time_budget_reached")
    assert clock.value <= 185


def test_existing_session_timeout_can_recover_without_tools_this_turn(fake_client, monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    monkeypatch.setattr(agent, "execute_tool", lambda *a, **kw: pytest.fail("No tool on retry"))
    fake_client.responses.create.side_effect = [timeout_error(), response(message("Réponse issue de la mémoire."))]
    result = agent.run("Compare ces articles", [], registry_with_analyses())
    assert not result.partial and result.tool_counts == {}
    assert fake_client.responses.create.call_args.kwargs["tools"] == []


def test_automatic_final_answer_timeout_is_not_clipped_to_tool_deadline(fake_client, monkeypatch):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr("agents.EnnoScholarV2.agent.time.monotonic", lambda: clock.value)
    agent = ScholarChatAgent(ScholarConfig(model="mock", timeout=3, turn_timeout=10,
        synthesis_timeout=90, synthesis_retry_timeout=60), client=fake_client)
    monkeypatch.setattr(agent, "execute_tool", lambda *a, **kw: pytest.fail("No tool during synthesis recovery"))
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            assert fake_client.with_options.call_args.kwargs["timeout"] == 90
            clock.value += 90
            raise timeout_error()
        assert fake_client.with_options.call_args.kwargs == {"timeout": 60, "max_retries": 0}
        assert kwargs["tools"] == [] and kwargs["input"] == calls[0]["input"]
        return response(message("Réponse normale issue des analyses déjà stockées."))
    fake_client.responses.create.side_effect = create
    result = agent.run("Parmi ces articles, compare les méthodes.", [], registry_with_analyses())
    assert not result.partial and result.reason is None and result.tool_counts == {}
    assert len(calls) == 2


def test_fallback_is_short_and_preserves_selection_order_not_analysis_sort(fake_client):
    registry = registry_with_analyses()
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    fake_client.responses.create.side_effect = [timeout_error(), timeout_error()]
    result = agent.run("Compare ces articles", [], registry)
    assert result.partial and result.reason == "synthesis_timeout"
    assert result.answer.count("- [Scientific source") == 5
    assert len(result.answer) < 3000
    assert result.answer.index("Scientific source 11") < result.answer.index("Scientific source 10")
    assert "Voici les analyses ou notices conservées" in result.answer


def test_targeted_configuration_defaults_and_custom_values(monkeypatch):
    names = {"SCHOLAR_ANALYSIS_MAX_OUTPUT_TOKENS": "4100", "SCHOLAR_ANALYSIS_RETRY_MAX_OUTPUT_TOKENS": "7500",
        "SCHOLAR_SYNTHESIS_TIMEOUT_SECONDS": "100", "SCHOLAR_SYNTHESIS_RETRY_TIMEOUT_SECONDS": "70"}
    for key in names: monkeypatch.delenv(key, raising=False)
    cfg = ScholarConfig.from_env()
    assert (cfg.analysis_max_output_tokens, cfg.analysis_retry_max_output_tokens, cfg.synthesis_timeout, cfg.synthesis_retry_timeout) == (3500, 6000, 90, 60)
    for key, value in names.items(): monkeypatch.setenv(key, value)
    cfg = ScholarConfig.from_env()
    assert (cfg.analysis_max_output_tokens, cfg.analysis_retry_max_output_tokens, cfg.synthesis_timeout, cfg.synthesis_retry_timeout) == (4100, 7500, 100, 70)


def test_reserved_tail_synthesis_retry_success_is_normal_not_partial(fake_client, monkeypatch):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr("agents.EnnoScholarV2.agent.time.monotonic", lambda: clock.value)
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    def execute(*args, **kwargs):
        clock.value = 520  # Reserved synthesis tail, but no business limit.
        return {"articles": []}
    monkeypatch.setattr(agent, "execute_tool", execute)
    requests = []
    def create(**kwargs):
        requests.append(copy.deepcopy(kwargs))
        if len(requests) == 1:
            return response(call("get_articles", {"article_ids": ["a0"]}))
        assert kwargs["tools"] == []
        if len(requests) == 2:
            assert fake_client.with_options.call_args.kwargs["timeout"] == 90
            clock.value += 90
            raise timeout_error()
        assert fake_client.with_options.call_args.kwargs["timeout"] == 60
        assert kwargs["input"] == requests[1]["input"]
        return response(message("Réponse scientifique normale issue des données conservées."))
    fake_client.responses.create.side_effect = create
    result = agent.run("Compare ces sources", [], registry_with_analyses(1))
    assert not result.partial and result.reason is None
    assert fake_client.responses.create.call_count == 3
    assert result.tool_counts == {"get_articles": 1}


@pytest.mark.parametrize("elapsed,expected_retry", [(110, 40), (151, None)])
def test_synthesis_phase_safety_clock_is_not_reset_after_first_timeout(fake_client, monkeypatch, elapsed, expected_retry):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr("agents.EnnoScholarV2.agent.time.monotonic", lambda: clock.value)
    agent = ScholarChatAgent(ScholarConfig(model="mock", max_rounds=1), client=fake_client)
    monkeypatch.setattr(agent, "execute_tool", lambda *a, **kw: {"articles": []})
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return response(call("get_articles", {"article_ids": ["a0"]}))
        if len(requests) == 2:
            clock.value += elapsed
            raise timeout_error()
        assert fake_client.with_options.call_args.kwargs["timeout"] == expected_retry
        return response(message("Résultats conservés."))
    fake_client.responses.create.side_effect = create
    result = agent.run("Compare ces sources", [], registry_with_analyses(1))
    assert len(requests) == (3 if expected_retry else 2)
    assert result.reason == ("round_budget_reached" if expected_retry else "synthesis_timeout")
