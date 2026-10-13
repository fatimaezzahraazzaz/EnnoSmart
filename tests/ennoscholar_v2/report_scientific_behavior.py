"""Controlled before/after collection, soft-time and complete-corpus report."""
from __future__ import annotations

from conftest import evidence_reference

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import conftest
import pytest

from agents.EnnoScholarV2.agent import ScholarChatAgent
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.context import context_metrics
from agents.EnnoScholarV2.models import FulltextResult, TextChunk
from agents.EnnoScholarV2.service import ScholarChatService
from context_reproduction import QUESTION, function, message, response
from scientific_fixture import CENTRAL_TITLES, EXCERPT, INTENT, QUERIES, prepare_scientific_run
from test_batching import client_for_service
from test_context_window import active_notices


ROOT = Path(__file__).resolve().parents[2]


def legacy_module(name, path):
    package = "agents.EnnoScholarV2.tools." if path.parent.name == "tools" else "agents.EnnoScholarV2."
    spec = importlib.util.spec_from_file_location(package + name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def case(timeouts=0):
    with pytest.MonkeyPatch.context() as patch:
        conftest.forbid_real_network_and_openai.__wrapped__(patch)
        run = prepare_scientific_run(patch, timeouts=timeouts)
        service = ScholarChatService(run.agent.config, agent=run.agent)
        result = client_for_service(patch, service).post("/scholar-v2/chat", json={"message": QUESTION})
        payload = result.json()
        assert result.status_code == 200
        session = service.sessions[payload["session_id"]]
        return {"http_status": result.status_code, "backend_articles": len(session.articles),
            "chat_preview": len(payload["articles"]), "active_max": max(len(active_notices(request)) for request in run.requests),
            "benchmark_titles_active": len(set(CENTRAL_TITLES) & {notice["title"] for notice in active_notices(run.requests[-1])}),
            "partial": payload["partial"], "reason": payload["reason"], "tool_counts": payload["tool_counts"],
            "chat_bytes": len(result.content), "fulltexts": sum(bool(record.fulltext) for record in session.articles.values()),
            "analyses": sum(bool(record.analysis) for record in session.articles.values()),
            "requests": [{"number": i + 1, **context_metrics(request["input"], request["instructions"], request["tools"]),
                "active": len(active_notices(request)), "tool_choice": request["tool_choice"]} for i, request in enumerate(run.requests)]}


def collection_before(backup):
    old = legacy_module("_old_scientific_search", backup / "agents/EnnoScholarV2/tools/scientific_search.py")
    with pytest.MonkeyPatch.context() as patch:
        conftest.forbid_real_network_and_openai.__wrapped__(patch)
        run = prepare_scientific_run(patch)
        search = old.ScientificSearch(ScholarConfig())
        for method in ("semantic", "openalex", "arxiv"):
            patch.setattr(search, method, getattr(run.agent.search, method))
        result = search.search_batch(QUERIES, 8)
        return {"backend_candidates": len(result["all_articles"]), "compact_top": len(result["articles"]),
            "requested_depths": sorted({row[2] for row in run.provider_calls}),
            "benchmark_found": len(set(CENTRAL_TITLES) & {row["title"] for row in result["all_articles"]})}


def timed_case(backup, before=False):
    with pytest.MonkeyPatch.context() as patch:
        conftest.forbid_real_network_and_openai.__wrapped__(patch)
        run = prepare_scientific_run(patch)
        clock = SimpleNamespace(value=0.0)
        patch.setattr("agents.EnnoScholarV2.agent.time.monotonic", lambda: clock.value)
        if before:
            old_agent = legacy_module("_old_scientific_agent", backup / "agents/EnnoScholarV2/agent.py")
            old_prompt = legacy_module("_old_scientific_prompt", backup / "agents/EnnoScholarV2/prompts.py")
            old_search = legacy_module("_old_timed_search", backup / "agents/EnnoScholarV2/tools/scientific_search.py")
            old_agent.SYSTEM_PROMPT = old_prompt.SYSTEM_PROMPT
            search = old_search.ScientificSearch(ScholarConfig())
            for method in ("semantic", "openalex", "arxiv"):
                patch.setattr(search, method, getattr(run.agent.search, method))
            agent = old_agent.ScholarChatAgent(ScholarConfig(model="mock", turn_timeout=180, max_articles=512), client=run.client, search=search)
        else:
            agent = run.agent
        requests, ids = [], []

        def retrieve(article, *args, **kwargs):
            clock.value = max(clock.value, 190)
            return FulltextResult(access_level="fulltext", text=EXCERPT,
                chunks=[TextChunk(page=4, section="Methods", text=EXCERPT)], fulltext_source="mock public source")

        patch.setattr("agents.EnnoScholarV2.agent.get_fulltext", retrieve)
        if before:
            patch.setattr(old_agent, "get_fulltext", retrieve)

        def create(**kwargs):
            if "text" in kwargs:
                return SimpleNamespace(status="completed", output_text=json.dumps({"method": "Méthode apprise", "access_level": "fulltext", "analysis_basis": "fulltext",
                    "evidence": [evidence_reference(json.loads(kwargs["input"]), EXCERPT, ["method"])]}))
            requests.append(copy.deepcopy(kwargs))
            number = len(requests)
            if number == 1:
                return response(function("search_scientific", {"queries": QUERIES, "top_k": 8, "intent": INTENT}, "search"))
            notices = active_notices(kwargs)
            if number == 2:
                ids[:] = [notice["id"] for notice in notices[:3]]
                return response(function("get_articles", {"article_ids": ids}, "details"))
            if number == 3:
                clock.value = 170
                return response(function("get_fulltext", {"article_ids": ids, "candidate_urls": []}, "full"))
            if number == 4:
                return response(function("analyze_articles", {"article_ids": ids, "user_context": QUESTION}, "analysis"))
            return response(message("Réponse finale scientifique fondée sur les résultats disponibles."))

        run.client.responses.create.side_effect = create
        registry = {}
        result = agent.run(QUESTION, [], registry)
        return {"elapsed_simulated": clock.value, "backend_articles": len(registry), "tool_counts": result.tool_counts,
            "partial": result.partial, "reason": result.reason, "analyses": sum(bool(record.analysis) for record in registry.values()),
            "tools_at_190s": [tool.get("name", tool["type"]) for tool in requests[3]["tools"]] if len(requests) > 3 else []}


def main():
    backup = Path(sys.argv[1])
    report = {"mode": "All scientific/OpenAI/fulltext calls mocked; real HTTP service, context manager and evidence validation; network forbidden.",
        "collection_before": collection_before(backup), "normal_after": case(),
        "after_two_synthesis_timeouts": case(2), "time_before": timed_case(backup, True), "time_after": timed_case(backup)}
    assert report["time_before"]["reason"] == "time_budget_reached"
    assert report["time_before"]["tool_counts"] == {"search_scientific": 1, "get_articles": 1, "get_fulltext": 1}
    assert not report["time_after"]["partial"] and report["time_after"]["analyses"] == 3
    path = ROOT / ".codex_tmp/scholar-v2-scientific-reproduction.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    # Synthetic metadata only, used by the isolated UI check; no backend session/key.
    with pytest.MonkeyPatch.context() as patch:
        run = prepare_scientific_run(patch)
        (ROOT / ".codex_tmp/scholar-v2-ui-fixture.json").write_text(json.dumps([article.model_dump() for article in run.articles], ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
