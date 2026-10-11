from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from agents.EnnoScholarV2.agent import ScholarChatAgent
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.models import Article, FulltextResult, TextChunk
from agents.EnnoScholarV2.prompts import SYSTEM_PROMPT
from conftest import call, evidence_reference, message, response
from scientific_fixture import CENTRAL_TITLES
from test_context_window import active_notices


@pytest.mark.parametrize("topic,object_name,discovered,operation", [
    ("modèles entraînés pour générer des maillages 3D en mécanique", "mesh", "polycube", "generation"),
    ("modèles entraînés pour concevoir des protéines catalytiques", "protein", "inverse folding", "design"),
])
def test_iterative_discovery_web_registration_and_selective_reading(monkeypatch, fake_client, topic, object_name, discovered, operation):
    # Scripted conversation checks transport/orchestration, not the intelligence of a live model.
    broad_queries = [f"{object_name} {operation} trained models", f"{object_name} {operation} generative methods",
        f"{object_name} {operation} applications", f"{object_name} {operation} survey"]
    specialized = f"{discovered} {object_name} {operation} trained models"
    quote = f"A trained model performs {operation} of {object_name} using {discovered}."
    direct = Article(title=f"Trained {discovered} {object_name} {operation}", doi="10.9999/direct", abstract=quote,
        access_level="abstract_only", citation_count=0)  # Direct and paid: no PDF URL.
    adjacent = Article(title=f"Simulation using an existing {object_name}", doi="10.9999/adjacent",
        abstract=f"Simulation uses an already constructed {object_name}. Related concepts include {discovered}.",
        access_level="abstract_only", citation_count=100000, pdf_url="https://example.org/oa.pdf")
    web_article = Article(title=f"Complementary {object_name} {operation}", doi="10.9999/web", abstract=quote,
        access_level="abstract_only", url="https://publisher.example/web")
    intent = {"target_terms": [object_name], "primary_operations": [operation], "secondary_operations": [],
        "adjacent_operations": ["simulation"], "method_terms": ["trained models"], "context_terms": []}
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    for source, method in enumerate(("semantic", "openalex", "arxiv")):
        def provider(query, limit, deadline=None, source=source):
            if query == specialized:
                return [direct.model_copy(deep=True), adjacent.model_copy(deep=True)]
            index = broad_queries.index(query)
            rows = [Article(title=f"{object_name} unrelated study {source}-{index}-{i}", doi=f"10.9999/noise-{source}-{index}-{i}",
                abstract="BACKEND_PRIVATE_LONG_ABSTRACT " * 100) for i in range(limit)]
            if source == 0 and index == 0:
                rows[0] = adjacent.model_copy(deep=True)
            return rows
        monkeypatch.setattr(agent.search, method, provider)
    monkeypatch.setattr(agent.search, "lookup_doi", lambda doi: [web_article.model_copy(deep=True)] if doi == web_article.doi else [])
    monkeypatch.setattr("agents.EnnoScholarV2.agent.get_fulltext", lambda *args, **kwargs:
        FulltextResult(access_level="abstract_only", text=quote, reason="Aucun texte intégral légal disponible."))
    requests, examined = [], []
    def create(**kwargs):
        if "text" in kwargs:
            payload = json.loads(kwargs["input"])
            return SimpleNamespace(status="completed", output_text=json.dumps({"method": "Trained model for the requested task",
                "access_level": "abstract_only", "analysis_basis": "abstract", "evidence": [evidence_reference(payload, quote, ["method"])]}))
        requests.append(copy.deepcopy(kwargs))
        notices = active_notices(kwargs)
        assert len(notices) <= 12
        number = len(requests)
        if number == 1:
            return response(call("search_scientific", {"queries": broad_queries, "top_k": 8, "intent": intent}, "broad"))
        if number == 2:
            found = next(notice for notice in notices if notice["doi"] == adjacent.doi)
            assert discovered in found["abstract"] and "already constructed" in found["abstract"]
            return response(call("get_articles", {"article_ids": [found["id"]]}, "inspect"))
        if number == 3:
            assert discovered in json.dumps(notices)
            assert any(tool.get("name") == "search_scientific" for tool in kwargs["tools"])
            return response(call("search_scientific", {"queries": [specialized], "top_k": 8, "intent": intent}, "refine"))
        if number == 4:
            assert any(notice["doi"] == adjacent.doi for notice in notices)  # Inspected source remains pinned.
            return response({"type": "web_search_call", "status": "completed", "action": {
                "sources": [{"url": web_article.url, "title": web_article.title}]}},
                call("read_article", {"url_or_doi": web_article.doi}, "register_web"))
        if number == 5:
            selected = [notice for notice in notices if notice["doi"] in {direct.doi, web_article.doi}]
            assert len(selected) == 2
            selected.sort(key=lambda notice: notice["doi"] != direct.doi)
            examined.extend(notice["id"] for notice in selected)
            return response(call("get_articles", {"article_ids": examined}, "details"))
        if number == 6:
            return response(call("get_fulltext", {"article_ids": examined[:1], "candidate_urls": []}, "fulltext"))
        if number == 7:
            return response(call("analyze_articles", {"article_ids": examined[:1], "user_context": topic}, "analysis"))
        selected = next(notice for notice in notices if notice["doi"] == direct.doi)
        assert selected["analysis"]["analysis_basis"] == "abstract" and selected["access_level"] == "abstract_only"
        assert "fulltext" not in selected  # An unsuccessful lookup does not register a fulltext.
        return response(message(f"DIRECT : {direct.title} — Abstract uniquement.\n"
            f"DIRECT : {web_article.title}.\nCONNEXE : {adjacent.title} — utilise un objet existant."))
    fake_client.responses.create.side_effect = create
    registry = {}
    result = agent.run(topic, [], registry)
    assert len(registry) == 602 and len(result.active_article_ids) <= 12 and not result.partial
    assert result.tool_counts == {"search_scientific": 2, "get_articles": 2, "web_search": 1,
        "read_article": 1, "get_fulltext": 1, "analyze_articles": 1}
    assert result.answer.index(direct.title) < result.answer.index(adjacent.title)
    assert registry[examined[0]].article.doi in {direct.doi, web_article.doi}
    assert all(identifier in result.active_article_ids for identifier in examined)
    assert any(citation.url == web_article.url for citation in result.citations)
    assert len(requests) == 8 and fake_client.responses.create.call_count == 9  # No ranking calls.
    assert all(request["context_management"] and request["tool_choice"] == "auto" for request in requests)
    assert all("BACKEND_PRIVATE_LONG_ABSTRACT " * 100 not in request["instructions"] for request in requests)
    assert any(specialized in item["arguments"] for request in requests for item in request["input"] if item.get("type") == "function_call")


def test_production_prompt_has_no_benchmark_titles_and_no_domain_axes():
    assert all(title.split(":", 1)[0] not in SYSTEM_PROMPT for title in CENTRAL_TITLES)
    assert "polycube" not in SYSTEM_PROMPT.casefold() and "maillage" not in SYSTEM_PROMPT.casefold()
