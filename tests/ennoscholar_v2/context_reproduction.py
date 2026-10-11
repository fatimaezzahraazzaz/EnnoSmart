"""Deterministic, network-free reproduction shared by regression tests and size report."""
from __future__ import annotations

from conftest import evidence_reference

import copy
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agents.EnnoScholarV2.agent import ScholarChatAgent, TOOLS
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.context import context_metrics, estimate_tokens, serialized_bytes
from agents.EnnoScholarV2.models import Article, FulltextResult, TextChunk
from agents.EnnoScholarV2.tools.scientific_search import article_id

QUESTION = "Je travaille en mécanique sur la génération automatique de maillages 3D à l’aide de modèles entraînés. Je cherche les articles scientifiques les plus pertinents sur ce sujet, y compris les articles payants. Pour les meilleurs articles, indique-moi si tu as uniquement l’abstract ou si tu as réellement pu accéder au texte complet."
QUERIES = ["3D mesh generation deep learning", "tetrahedral neural meshing"]
EXCERPT = "We propose a learned meshing method."


class Item(dict):
    def model_dump(self, **kwargs):
        return dict(self)


def response(*items, input_tokens=2500):
    return SimpleNamespace(output=[Item(item) for item in items], usage=SimpleNamespace(input_tokens=input_tokens))


def function(name, arguments, call_id):
    return {"type": "function_call", "name": name, "arguments": json.dumps(arguments), "call_id": call_id, "id": "fc_" + call_id}


def message(text):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": []}]}


def run_reproduction(agent_class=ScholarChatAgent, *, native=False, pressure=False):
    abstract = ("We investigate automatic three dimensional finite element mesh generation using trained neural networks. The model predicts tetrahedral elements from geometric inputs and evaluates mesh quality, generalization and mechanical simulation robustness. " * 9) + "BACKEND_ONLY_ABSTRACT_END"
    articles = []
    for i in range(289):
        article = Article(title=f"Learned automatic 3D tetrahedral mesh generation method {i:03d}",
            authors=["A. Researcher", "B. Engineer"], year=2024, abstract=abstract, doi=f"10.1234/mesh{i:03d}",
            url=f"https://example.org/research/mesh-{i:03d}", source="openalex", sources=["openalex", "semantic_scholar"], citation_count=42)
        article.id = article_id(article)
        articles.append(article)
    client = Mock()
    client.with_options.return_value = client
    agent = agent_class(ScholarConfig(model="diagnostic-stub"), client=client)
    requests, live_items, tool_results, retrieved = [], [], [], []
    after_four = {}
    search_number = 0

    def batch(queries, top_k, **kwargs):
        nonlocal search_number
        subset = articles[:145] if search_number == 0 else articles[145:]
        search_number += 1
        return {"queries": queries, "top_k": top_k, "articles": [a.model_dump() for a in subset[:top_k]],
            "all_articles": [a.model_dump() for a in subset], "total_found": len(subset), "warnings": [],
            "ranking": "title/abstract relevance, reciprocal provider ranks, query coverage, citations; no access preference"}

    def scientific(query, limit=10):
        return {"query": query, "articles": [a.model_dump() for a in articles[:limit]], "warnings": []}

    def retrieve(article, *args, **kwargs):
        retrieved.append(article.id)
        return FulltextResult(access_level="fulltext", text=EXCERPT,
            chunks=[TextChunk(page=3, section="Methods", text=EXCERPT)], fulltext_source="public fixture")

    def create(**kwargs):
        if "text" in kwargs:
            return SimpleNamespace(status="completed", output_text=json.dumps({"method": "Méthode apprise", "access_level": "fulltext", "analysis_basis": "fulltext",
                "evidence": [evidence_reference(json.loads(kwargs["input"]), EXCERPT, ["method"])]}))
        if len(requests) == 2:
            after_four["items"] = copy.deepcopy(kwargs["input"] if kwargs.get("tools") else live_items[-1])
            after_four["instructions"] = kwargs["instructions"] if kwargs.get("tools") else requests[0]["instructions"]
        requests.append(copy.deepcopy(kwargs))
        live_items.append(kwargs["input"])
        number = len(requests)
        if kwargs.get("tool_choice") == "none":
            return response(message("Synthèse partielle des notices."))
        if number == 1:
            items = [function("search_scientific", {"queries": QUERIES, "top_k": 8}, "c1"),
                function("find_similar_articles", {"article_id": None, "article_or_topic": "3D mesh learning", "queries": ["tetrahedral mesh neural network"], "limit": 8}, "c2")]
            if native:
                items.insert(0, {"type": "reasoning", "id": "rs_old", "summary": [], "encrypted_content": "opaque" * 14000})
            return response(*items)
        if number == 2:
            items = [function("find_similar_articles", {"article_id": None, "article_or_topic": "3D mesh learning", "queries": ["automatic finite element meshing machine learning"], "limit": 8}, "c3"),
                function("search_scientific", {"queries": QUERIES, "top_k": 8}, "c4")]
            if native:
                items.insert(0, {"type": "compaction", "id": "cmp_native", "encrypted_content": "native_compacted_state"})
            return response(*items, input_tokens=50001 if pressure else 2500)
        if number == 3:
            return response(function("get_fulltext", {"article_ids": [a.id for a in articles[:5]], "candidate_urls": []}, "c5"))
        if number == 4:
            return response(function("analyze_articles", {"article_ids": [a.id for a in articles[:3]], "user_context": "maillage 3D"}, "c6"))
        return response(message("Textes complets récupérés pour les cinq articles vérifiés; analyse de trois articles avec preuves."))

    client.responses.create.side_effect = create
    client.responses.compact.return_value = response({"type": "compaction", "id": "cmp_standalone", "encrypted_content": "compacted_state"}, {"role": "user", "content": QUESTION})
    execute = agent.execute_tool

    def record_tool(name, arguments, *args, **kwargs):
        result = execute(name, arguments, *args, **kwargs)
        tool_results.append((name, copy.deepcopy(result)))
        return result

    with patch.object(agent.search, "search_batch", side_effect=batch), patch.object(agent.search, "search_scientific", side_effect=scientific), \
        patch.object(agent, "execute_tool", side_effect=record_tool), patch("agents.EnnoScholarV2.agent.get_fulltext", side_effect=retrieve), \
        patch("modules.LLM.llm_concurrency.llm_capacity_slot", side_effect=lambda *args: nullcontext()):
        registry = {}
        turn = agent.run(QUESTION, [], registry)
    metrics = context_metrics(after_four["items"], after_four["instructions"], TOOLS)
    wire_outputs = [item for item in after_four["items"] if item.get("type") == "function_call_output"]
    report = {"after_four_tools": metrics, "backend_articles": len(registry), "partial": turn.partial, "reason": turn.reason,
        "rounds": turn.rounds, "tool_counts": turn.tool_counts, "fulltexts": sum(bool(record.fulltext) for record in registry.values()),
        "analyses": sum(bool(record.analysis) for record in registry.values()),
        "per_tool_result": [{"tool": name, "bytes": serialized_bytes(result), "estimated_tokens": estimate_tokens(result), "articles": len(result.get("articles", []))} for name, result in tool_results[:4]],
        "wire_outputs_after_four": [{"call_id": item["call_id"], "bytes": serialized_bytes(item), "estimated_tokens": estimate_tokens(item)} for item in wire_outputs]}
    return SimpleNamespace(report=report, requests=requests, registry=registry, result=turn, client=client,
        tool_results=tool_results, retrieved=retrieved, after_four=after_four)
