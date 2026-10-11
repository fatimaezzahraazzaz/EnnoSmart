from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import APIConnectionError

from agents.EnnoScholarV2.agent import ScholarChatAgent, StoredArticle
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.context import estimate_tokens
from agents.EnnoScholarV2.models import Article, ChatRequest, FulltextResult, TextChunk
from agents.EnnoScholarV2.service import ScholarChatService
from agents.EnnoScholarV2.tools.scientific_search import ScientificSearch, reconstruct_abstract
from core.deps import get_current_user
from routers import scholar_v2
from conftest import call, message, response, evidence_reference

ROOT = Path(__file__).resolve().parents[2]
QUERIES = ["3D mesh generation deep learning", "tetrahedral mesh neural network",
    "automatic finite element meshing machine learning", "point cloud to mesh deep learning"]


def setup_batch_search(monkeypatch, agent):
    providers = []
    for name in ("semantic", "openalex", "arxiv"):
        def provider(query, limit, name=name, **kwargs):
            return [Article(title=f"Learned 3D mesh {QUERIES.index(query)} candidate {index}",
                doi=f"10.1234/mesh{QUERIES.index(query)}-{index}",
                abstract="Mesh generation with deep learning. " * 500 + "BACKEND_ABSTRACT_TAIL",
                source=name, sources=[name]) for index in range(limit)]
        stub = Mock(side_effect=provider)
        monkeypatch.setattr(agent.search, name, stub)
        providers.append(stub)
    return providers


def make_registry(number=5):
    return {f"a{i}": StoredArticle(Article(id=f"a{i}", title=f"Learned mesh paper {i}",
        doi=f"10.1234/mesh{i}", abstract="We propose a learned meshing method.", access_level="abstract_only")) for i in range(number)}


def test_four_queries_count_once_and_keep_all_complete_results(fake_client, monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    providers = setup_batch_search(monkeypatch, agent)
    fake_client.responses.create.side_effect = [response(call("search_scientific", {"queries": QUERIES, "top_k": 8})), response(message("Voici les meilleurs candidats, y compris payants."))]
    registry = {}
    result = agent.run("Génération automatique de maillage 3D, y compris payants", [], registry)
    assert result.tool_counts == {"search_scientific": 1}
    assert all(provider.call_count == 4 for provider in providers)
    assert len(registry) == 200
    assert all(record.article.abstract.endswith("BACKEND_ABSTRACT_TAIL") for record in registry.values())
    output = next(item for item in fake_client.responses.create.call_args.kwargs["input"] if item.get("type") == "function_call_output")
    payload = json.loads(output["output"])
    assert len(payload["article_ids"]) == 8 and payload["total_stored"] == 200
    assert "all_articles" not in payload and "BACKEND_ABSTRACT_TAIL" not in output["output"]
    assert all(len(registry[identifier].article.sources) == 3 for identifier in payload["article_ids"])
    assert "BACKEND_ABSTRACT_TAIL" not in fake_client.responses.create.call_args.kwargs["instructions"]


def test_fulltext_batch_five_keeps_text_and_chunks_backend(fake_client, monkeypatch):
    registry = make_registry()
    registry["a4"].article.abstract = ""
    seen = []
    def retrieve(article, search, candidate_urls=None, **kwargs):
        seen.append(article.id)
        if article.id in {"a0", "a1"}:
            return FulltextResult(access_level="fulltext", text="PRIVATE_FULLTEXT_SENTINEL" * 100,
                chunks=[TextChunk(page=4, section="Methods", text="PRIVATE_FULLTEXT_SENTINEL")],
                fulltext_source="arxiv", fulltext_url="https://arxiv.org/pdf/2401.12345")
        return FulltextResult(access_level="abstract_only" if article.abstract else "metadata_only", text=article.abstract)
    monkeypatch.setattr("agents.EnnoScholarV2.agent.get_fulltext", retrieve)
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    result = agent.execute_tool("get_fulltext", {"article_ids": list(registry)}, registry, fake_client)
    assert set(seen) == set(registry) and len(result["articles"]) == 5
    assert [row["access_level"] for row in result["articles"]] == ["fulltext", "fulltext", "abstract_only", "abstract_only", "metadata_only"]
    assert [row["available"] for row in result["articles"]] == [True, True, False, False, False]
    assert registry["a0"].fulltext.chunks[0].page == 4
    assert "PRIVATE_FULLTEXT_SENTINEL" not in json.dumps(result)
    notice = agent.execute_tool("get_article", {"article_id": "a0"}, registry, fake_client)
    assert notice["fulltext"]["available"] and "PRIVATE_FULLTEXT_SENTINEL" not in json.dumps(notice)


def test_analyze_three_uses_real_stored_passages_and_verified_evidence(fake_client):
    registry = make_registry()
    excerpt = "We propose a learned meshing method."
    registry["a0"].fulltext = FulltextResult(access_level="fulltext", text=excerpt,
        chunks=[TextChunk(page=3, section="Abstract", text=excerpt)])
    fake_client.responses.create.side_effect = lambda **kwargs: SimpleNamespace(status="completed", output_text=json.dumps({
        "method": "Méthode apprise", "results": "Invented 99% result", "access_level": "fulltext", "analysis_basis": "fulltext",
        "evidence": [evidence_reference(json.loads(kwargs["input"]), excerpt, ["method"]),
            {"evidence_id": "unknown", "supports": ["results"]}]}))
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    result = agent.execute_tool("analyze_articles", {"article_ids": ["a0", "a1", "a2"]}, registry, fake_client)
    assert fake_client.responses.create.call_count == 3
    assert len(result["articles"]) == 3 and registry["a3"].analysis is None and registry["a4"].analysis is None
    assert result["articles"][0]["analysis_basis"] == "fulltext"
    assert result["articles"][0]["evidence"][0]["page"] == 3
    for row in result["articles"]:
        assert row["results"] == "" and row["method"] and len(row["evidence"]) == 1
        assert row["evidence"][0]["excerpt"] == excerpt
    for request in fake_client.responses.create.call_args_list:
        assert excerpt in json.dumps(request.kwargs["input"])


def test_soft_budget_injects_synthesis_instruction_without_error(fake_client, monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock-model", soft_tool_budget=1, hard_tool_budget=3), client=fake_client)
    setup_batch_search(monkeypatch, agent)
    fake_client.responses.create.side_effect = [response(call("search_scientific", {"queries": QUERIES, "top_k": 2})), response(message("Synthèse des résultats collectés."))]
    result = agent.run("Cherche des articles", [], {})
    request = fake_client.responses.create.call_args.kwargs
    assert "N'appelle un nouvel outil que si" in request["instructions"]
    assert result.answer == "Synthèse des résultats collectés." and not result.partial
    assert result.tool_counts == {"search_scientific": 1}


def client_for_service(monkeypatch, service):
    monkeypatch.setattr(scholar_v2, "get_service", lambda: service)
    app = FastAPI()
    app.include_router(scholar_v2.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    return TestClient(app)


def test_hard_budget_http200_retains_articles_citations_and_memory(fake_client, monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock-model", soft_tool_budget=1, hard_tool_budget=1), client=fake_client)
    setup_batch_search(monkeypatch, agent)
    fake_client.responses.create.side_effect = [response(call("search_scientific", {"queries": QUERIES, "top_k": 2})), response(message("Voici les articles trouvés.")), response(message("Les articles précédents sont toujours disponibles."))]
    service = ScholarChatService(agent.config, agent=agent)
    client = client_for_service(monkeypatch, service)
    first = client.post("/scholar-v2/chat", json={"message": "Maillage 3D avec modèles entraînés, y compris payants"})
    assert first.status_code == 200
    payload = first.json()
    assert payload["partial"] and payload["reason"] == "tool_budget_reached"
    assert payload["tool_calls"] == 1 and payload["tool_counts"] == {"search_scientific": 1}
    assert payload["articles"] and payload["citations"] and "Réponse partielle" in payload["answer"]
    assert fake_client.responses.create.call_args_list[1].kwargs["tool_choice"] == "none"
    assert fake_client.responses.create.call_args_list[1].kwargs["tools"] == []
    session = service.sessions[payload["session_id"]]
    assert session.articles and len(session.messages) == 2
    second = client.post("/scholar-v2/chat", json={"session_id": payload["session_id"], "message": "Reviens sur ces articles"})
    assert second.status_code == 200 and second.json()["articles_total"] == payload["articles_total"]


def test_real_upstream_network_exception_remains_distinct_http502(fake_client, monkeypatch):
    fake_client.responses.create.side_effect = APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    service = ScholarChatService(agent.config, agent=agent)
    response_value = client_for_service(monkeypatch, service).post("/scholar-v2/chat", json={"message": "Bonjour"})
    assert response_value.status_code == 502
    assert "partial" not in response_value.json() and service.sessions == {}


def test_web_search_counts_and_prevents_pending_custom_calls(fake_client):
    agent = ScholarChatAgent(ScholarConfig(model="mock-model", soft_tool_budget=1, hard_tool_budget=1), client=fake_client)
    fake_client.responses.create.side_effect = [response({"type": "web_search_call", "status": "completed", "action": {"sources": [{"url": "https://example.org/paper", "title": "Paper"}]}}, call("get_article", {"article_id": "unknown"})), response(message("Synthèse de la source consultée."))]
    result = agent.run("Cherche une version publique", [], {})
    assert result.tool_counts == {"web_search": 1} and result.partial
    assert result.activity[-1].status == "skipped" and result.citations
    assert fake_client.responses.create.call_args_list[0].kwargs["max_tool_calls"] == 1


def test_synthesis_failure_after_limit_returns_grounded_fallback(fake_client):
    agent = ScholarChatAgent(ScholarConfig(model="mock-model", soft_tool_budget=1, hard_tool_budget=1), client=fake_client)
    fake_client.responses.create.side_effect = [response(call("get_articles", {"article_ids": ["a0"]})), RuntimeError("Upstream unavailable")]
    registry = make_registry(1)
    result = agent.run("Cherche", [], registry)
    assert result.partial and result.reason == "tool_budget_reached"
    assert "Learned mesh paper 0" in result.answer and "abstract disponible" in result.answer
    assert registry["a0"].article.doi == "10.1234/mesh0"


def test_batch_item_failure_does_not_discard_other_results(fake_client, monkeypatch):
    def retrieve(article, *args, **kwargs):
        if article.id == "a0":
            raise ValueError("Inaccessible")
        return FulltextResult(access_level="fulltext", text="Actually retrieved", fulltext_source="public")
    monkeypatch.setattr("agents.EnnoScholarV2.agent.get_fulltext", retrieve)
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    registry = make_registry(2)
    result = agent.execute_tool("get_fulltext", {"article_ids": ["a0", "unknown", "a1"]}, registry, fake_client)
    assert result["articles"][0]["error"] and result["articles"][1]["error"]
    assert result["articles"][2]["available"] and registry["a1"].fulltext


def test_provider_failure_and_ranking_never_prioritize_free_access(fake_client, monkeypatch):
    search = ScientificSearch(ScholarConfig())
    monkeypatch.setattr(search, "semantic", lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("Unavailable")))
    monkeypatch.setattr(search, "openalex", lambda *args, **kwargs: [Article(title="3D mesh deep learning generation", doi="10.1234/paid")])
    monkeypatch.setattr(search, "arxiv", lambda *args, **kwargs: [Article(title="Astronomy telescope calibration", pdf_url="https://arxiv.org/pdf/2401.12345")])
    result = search.search_batch([QUERIES[0]], 8)
    assert result["warnings"] and result["total_found"] == 2
    assert result["articles"][0]["doi"] == "10.1234/paid"


def test_native_compaction_and_input_budget_keep_opaque_protocol(fake_client):
    registry = make_registry(1)
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    opaque = {"type": "compaction", "id": "cmp_1", "encrypted_content": "opaque-content"}
    fake_client.responses.create.side_effect = [response(opaque, call("get_articles", {"article_ids": ["a0"]})), response(message("Article retrouvé."))]
    result = agent.run("Reviens sur cet article", [{"role": "user", "content": "Old content" * 20000}], registry)
    requests = [value.kwargs for value in fake_client.responses.create.call_args_list]
    assert requests[0]["context_management"] == [{"type": "compaction", "compact_threshold": 12000}]
    assert opaque in requests[1]["input"] and "opaque-content" not in result.answer
    for request in requests:
        assert estimate_tokens(request["input"]) + estimate_tokens(request["instructions"]) + estimate_tokens(request["tools"]) <= agent.config.max_input_tokens


def test_budgets_are_environment_configurable(monkeypatch):
    monkeypatch.setenv("ENNOSCHOLAR_V2_SOFT_TOOL_BUDGET", "4")
    monkeypatch.setenv("ENNOSCHOLAR_V2_HARD_TOOL_BUDGET", "9")
    monkeypatch.setenv("ENNOSCHOLAR_V2_MAX_ROUNDS", "5")
    config = ScholarConfig.from_env()
    assert (config.soft_tool_budget, config.hard_tool_budget, config.max_rounds) == (4, 9, 5)
    long_abstract = "word " * 4000
    assert reconstruct_abstract({"word": list(range(4000))}) == long_abstract.strip()


@pytest.mark.parametrize("paths", [
    ["agents/EnnoScholar", "backend_api/routers/scholar.py"],
    ["agents/EnnoDiagnostic", "backend_api/routers/diagnostic.py"],
])
def test_v1_and_diagnostic_files_unchanged(paths):
    result = subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", *paths], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
