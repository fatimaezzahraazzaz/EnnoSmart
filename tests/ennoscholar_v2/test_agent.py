from __future__ import annotations

import json

import pytest

from agents.EnnoScholarV2.agent import ScholarChatAgent, StoredArticle, TOOLS
from agents.EnnoScholarV2.config import ScholarConfig, ScholarConfigurationError
from agents.EnnoScholarV2.models import Article
from agents.EnnoScholarV2.tools.scientific_search import ScientificSearch
from conftest import call, message, response


def test_agent_can_search_for_complex_mesh_request(fake_client, monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    monkeypatch.setattr(agent.search, "search_scientific", lambda query, limit: {"articles": [{"title": "Mesh generation", "abstract": "A learned method.", "doi": "10.1234/mesh", "source": "arxiv"}], "warnings": []})
    fake_client.responses.create.side_effect = [response(call("search_scientific", {"query": "learned 3D mesh generation", "limit": 5})), response(message("Un article pertinent a été trouvé."))]
    registry = {}
    answer, _, activity = agent.run("Trouve des articles sur génération automatique de maillage 3D, y compris limites et généralisation.", [], registry)
    assert answer and registry and activity[0].tool == "search_scientific"
    assert next(iter(registry.values())).article.doi == "10.1234/mesh"
    request = fake_client.responses.create.call_args_list[0].kwargs
    assert request["tool_choice"] == "auto" and request["store"] is False
    assert any(tool["type"] == "web_search" for tool in request["tools"])
    second = fake_client.responses.create.call_args_list[1].kwargs["input"]
    assert any(item.get("type") == "function_call_output" for item in second)


def test_similar_articles_dispatch_for_article_followup(fake_client, monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    article = Article(id="art_known", title="MeshingNet3D", abstract="Learned mesh generation", doi="10.1234/original")
    monkeypatch.setattr(agent.search, "search_scientific", lambda query, limit: {"articles": [{"title": "Comparable learned meshing", "doi": "10.1234/similar", "abstract": "A comparable approach."}], "warnings": []})
    fake_client.responses.create.side_effect = [response(call("find_similar_articles", {"article_id": "art_known", "article_or_topic": "MeshingNet3D", "queries": ["neural 3D meshing"], "limit": 5})), response(message("Voici un article comparable."))]
    registry = {"art_known": StoredArticle(article)}
    _, _, activity = agent.run("Voici un article X, trouve des articles similaires.", [], registry)
    assert activity[0].tool == "find_similar_articles" and len(registry) == 2


def test_direct_reply_or_one_question_needs_no_tools(fake_client):
    fake_client.responses.create.return_value = response(message("Quel type de maillage souhaitez-vous étudier ?"))
    answer, _, activity = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client).run("J'hésite entre plusieurs domaines.", [], {})
    assert answer.count("?") == 1 and activity == []


def test_successive_calls_keep_protocol_reasoning_but_do_not_expose_it(fake_client, monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    monkeypatch.setattr(agent.search, "search_scientific", lambda *args: {"articles": [], "warnings": []})
    opaque = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque-protocol-content"}
    fake_client.responses.create.side_effect = [response(opaque, call("search_scientific", {"query": "mesh limits", "limit": 2})), response(call("search_scientific", {"query": "mesh robustness", "limit": 2}, "call_2")), response(message("Recherche terminée."))]
    answer, _, activity = agent.run("Compare les méthodes et leurs limites.", [], {})
    assert len(activity) == 2 and "opaque" not in answer
    assert opaque in fake_client.responses.create.call_args_list[1].kwargs["input"]


def test_native_web_search_keeps_citations_and_consulted_sources(fake_client):
    text = "Source [1]"
    fake_client.responses.create.return_value = response(
        {"type": "web_search_call", "status": "completed", "action": {"sources": [{"url": "https://arxiv.org/abs/1234?x=1", "title": "Arxiv"}, {"url": "https://example.org/consulted"}]}},
        message(text, [{"type": "url_citation", "start_index": 7, "end_index": 10, "url": "https://arxiv.org/abs/1234?x=1", "title": "Arxiv"}]))
    answer, citations, activity = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client).run("Cherche une version auteur.", [], {})
    assert answer == text and activity[0].tool == "web_search"
    assert any(c.cited and c.url.endswith("?x=1") and c.start_index == 7 for c in citations)
    assert any(not c.cited and c.url.endswith("consulted") for c in citations)


def test_tool_failure_returns_error_not_fabricated_articles(fake_client):
    fake_client.responses.create.side_effect = [response(call("get_fulltext", {"article_id": "unknown", "candidate_urls": []})), response(message("Article inconnu; fournissez une URL."))]
    answer, _, activity = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client).run("Lis cet article.", [], {})
    assert activity[0].status == "error" and "inconnu" in answer
    output = fake_client.responses.create.call_args_list[1].kwargs["input"][-1]
    assert "error" in json.loads(output["output"])


def test_round_limit_prevents_infinite_tool_loop(fake_client, monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock-model", max_rounds=1), client=fake_client)
    monkeypatch.setattr(agent.search, "search_scientific", lambda *args: {"articles": [], "warnings": []})
    fake_client.responses.create.return_value = response(call("search_scientific", {"query": "mesh", "limit": 1}))
    result = agent.run("Recherche", [], {})
    assert result.partial and result.reason == "round_budget_reached"
    assert fake_client.responses.create.call_args.kwargs["tool_choice"] == "none"
    assert result.answer


def test_missing_configuration_is_explicit_and_not_a_model_fallback():
    with pytest.raises(ScholarConfigurationError, match="ENNOSCHOLAR_V2_MODEL"):
        ScholarChatAgent(ScholarConfig()).client
    with pytest.raises(ScholarConfigurationError, match="OPENAI_API_KEY"):
        ScholarChatAgent(ScholarConfig(model="mock-model")).client


def test_strict_tool_schemas_and_no_diagnostic_tools():
    for tool in TOOLS:
        if tool["type"] == "function":
            assert tool["strict"] and tool["parameters"]["additionalProperties"] is False
            assert set(tool["parameters"]["required"]) == set(tool["parameters"]["properties"])
    assert "get_project_locks" not in str(TOOLS)


def test_reregistering_fulltext_article_preserves_actual_access_and_id(fake_client):
    from agents.EnnoScholarV2.models import FulltextResult
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    article = Article(id="stable_id", title="Learned meshing", doi="10.1234/mesh", access_level="fulltext", analysis_basis="fulltext")
    registry = {article.id: StoredArticle(article, FulltextResult(access_level="fulltext", text="Retrieved text"))}
    output = agent.register([Article(title=article.title, doi=article.doi, abstract="New abstract").model_dump()], registry)
    assert output[0]["id"] == "stable_id" and output[0]["access_level"] == "fulltext"
    assert output[0]["analysis_basis"] == "fulltext"
