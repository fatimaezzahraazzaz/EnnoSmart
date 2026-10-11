"""Network-free reproduction of 2 searches, 16 notices, fulltext, analysis, timeout."""
from __future__ import annotations

from conftest import evidence_reference

import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from openai import APITimeoutError

from agents.EnnoScholarV2.agent import ScholarChatAgent
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.models import Article, FulltextResult, TextChunk
from agents.EnnoScholarV2.tools.scientific_search import article_id
from context_reproduction import EXCERPT, QUESTION, function, message, response


def prepare_reproduction(monkeypatch, *, agent_class=ScholarChatAgent, timeouts=1, soft=16):
    articles = [Article(id=f"mesh{i}", title=f"Learned 3D finite element mesh method {i:03d}",
        doi=f"10.1234/mesh{i}", abstract=EXCERPT + " Supplementary abstract detail." * 200,
        access_level="abstract_only", source="openalex") for i in range(289)]
    for article in articles:
        article.id = article_id(article)
    ids = [article.id for article in articles]
    client = Mock()
    client.with_options.return_value = client
    agent = agent_class(ScholarConfig(model="orchestration-stub", soft_tool_budget=soft), client=client)
    requests, retrieved = [], []
    search_count = 0

    def search(queries, top_k, **kwargs):
        nonlocal search_count
        rows = articles[:145] if search_count == 0 else articles[145:]
        search_count += 1
        return {"all_articles": [article.model_dump() for article in rows], "queries": queries,
            "articles": [], "total_found": len(rows), "warnings": []}

    def retrieve(article, *args, **kwargs):
        retrieved.append(article.id)
        return FulltextResult(access_level="fulltext", text=EXCERPT,
            chunks=[TextChunk(page=3, section="Methods", text=EXCERPT)], fulltext_source="public fixture")

    def create(**kwargs):
        if "text" in kwargs:
            return SimpleNamespace(status="completed", output_text=json.dumps({"method": "Méthode de maillage apprise",
                "access_level": "fulltext", "analysis_basis": "fulltext",
                "evidence": [evidence_reference(json.loads(kwargs["input"]), EXCERPT, ["method"])]}))
        requests.append(copy.deepcopy(kwargs))
        number = len(requests)
        if number == 1:
            return response(function("search_scientific", {"queries": ["learned 3D mesh"], "top_k": 8}, "s1"),
                function("search_scientific", {"queries": ["neural finite element meshing"], "top_k": 8}, "s2"))
        if number == 2:
            names = {tool.get("name") for tool in kwargs["tools"]}
            if "get_articles" in names:
                # The model follows the new catalogue/prompt: detail only useful candidates.
                return response(function("get_articles", {"article_ids": ids[:8]}, "details"))
            return response(*(function("get_article", {"article_id": ids[i]}, f"d{i}") for i in range(16)))
        if number == 3:
            return response(function("get_fulltext", {"article_ids": ids[:3], "candidate_urls": []}, "full"),
                function("analyze_articles", {"article_ids": ids[:3], "user_context": QUESTION}, "analysis"))
        if number in range(4, 4 + timeouts):
            raise APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
        return response(message("Trois articles : textes complets accessibles, passages analysés avec preuves."))

    client.responses.create.side_effect = create
    monkeypatch.setattr(agent.search, "search_batch", search)
    monkeypatch.setattr("agents.EnnoScholarV2.agent.get_fulltext", retrieve)
    return SimpleNamespace(agent=agent, client=client, requests=requests, retrieved=retrieved, articles=articles)
