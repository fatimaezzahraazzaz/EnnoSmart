"""Offline wall-clock comparison against the actual timestamped pre-change sources."""
from __future__ import annotations

import argparse
import importlib.util
import json
import socket
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conftest
from agents.EnnoScholarV2 import agent as after_agent
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.models import Article, ArticleAnalysis, FulltextResult
from agents.EnnoScholarV2.tools import fulltext as after_fulltext, scientific_search as after_search


def load_before(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_benchmark(backup):
    before_search = load_before("agents.EnnoScholarV2.tools._before_search", backup / "agents/EnnoScholarV2/tools/scientific_search.py")
    before_fulltext = load_before("agents.EnnoScholarV2.tools._before_fulltext", backup / "agents/EnnoScholarV2/tools/fulltext.py")
    before_agent = load_before("agents.EnnoScholarV2._before_agent", backup / "agents/EnnoScholarV2/agent.py")
    config = ScholarConfig(model="mock", fulltext_attempt_timeout=0.025, fulltext_article_timeout=0.25, fulltext_batch_timeout=0.4)
    reports = []
    with pytest.MonkeyPatch.context() as mp:
        def forbidden(*args, **kwargs): raise AssertionError("Real calls forbidden")
        mp.setattr(socket.socket, "connect", forbidden)
        from openai.resources.responses.responses import Responses
        mp.setattr(Responses, "create", forbidden)
        for label, search_module, full_module, agent_module in [("before", before_search, before_fulltext, before_agent),
            ("after", after_search, after_fulltext, after_agent)]:
            stage_started = total_started = time.perf_counter()
            search = search_module.ScientificSearch(config)
            provider_lock = threading.Lock()
            active, peak = {}, {}
            def provider(name, latency):
                def fetch(query, limit, **kwargs):
                    guard = provider_lock if name == "arxiv" else threading.Lock()
                    with guard:
                        active[name] = active.get(name, 0) + 1
                        peak[name] = max(peak.get(name, 0), active[name])
                        try:
                            time.sleep(latency)
                            return [Article(title=f"{name} {query} native {i}", doi=f"10.9999/{name}-{query}-{i}",
                                abstract="Complete abstract", source=name, sources=[name]) for i in range(limit)]
                        finally: active[name] -= 1
                return fetch
            # Real API calls are replaced with fixed latency stubs. arXiv stays
            # serial in both versions; its synthetic spacing is included in 20ms.
            mp.setattr(search, "semantic", provider("semantic", 0.06))
            mp.setattr(search, "openalex", provider("openalex", 0.07))
            mp.setattr(search, "arxiv", provider("arxiv", 0.02))
            result = search.search_batch([f"axis{i}" for i in range(8)])
            search_seconds = time.perf_counter() - stage_started
            client = Mock(); client.with_options.return_value = client
            agent = agent_module.ScholarChatAgent(config, client=client, search=search)
            registry = {f"a{i}": agent_module.StoredArticle(Article(id=f"a{i}", title=f"Scientific article {i}",
                abstract="Complete abstract", pdf_url=f"https://public.example/slow-{i}.pdf", url=f"https://public.example/page-{i}")) for i in range(6)}
            attempts = []
            def read(url, article, timeout=15, **kwargs):
                attempts.append(url)
                latency = 0.08 if "slow" in url else 0.01
                remaining = kwargs.get("deadline", time.monotonic() + 10) - time.monotonic()
                time.sleep(max(0, min(latency, remaining)))
                if latency > remaining or "slow" in url: raise httpx.ReadTimeout("Synthetic slow URL")
                if "404" in url: raise httpx.HTTPStatusError("404", request=httpx.Request("GET", url), response=httpx.Response(404))
                return FulltextResult(access_level="fulltext", text="Verified scientific text", fulltext_url=url)
            mp.setattr(full_module, "read_fulltext", read)
            mp.setattr(agent_module, "get_fulltext", full_module.get_fulltext)
            stage_started = time.perf_counter()
            full = agent.execute_tool("get_fulltext", {"article_ids": list(registry), "candidate_urls": ["https://public.example/404"]}, registry, client)
            full_seconds = time.perf_counter() - stage_started
            analysis_calls = []
            def analyze(*args):
                analysis_calls.append(1); time.sleep(0.03)
                return ArticleAnalysis(access_level="fulltext", analysis_basis="fulltext", method="Verified method")
            mp.setattr(agent_module, "analyze_article", analyze)
            stage_started = time.perf_counter()
            args = {"article_ids": list(registry), "user_context": "Same goal"}
            agent.execute_tool("analyze_articles", args, registry, client)
            first_analysis = time.perf_counter() - stage_started
            stage_started = time.perf_counter()
            agent.execute_tool("analyze_articles", args, registry, client)
            second_analysis = time.perf_counter() - stage_started
            reports.append({"version": label, "search_seconds": round(search_seconds, 4), "fulltext_seconds": round(full_seconds, 4),
                "first_analysis_seconds": round(first_analysis, 4), "repeated_analysis_seconds": round(second_analysis, 4),
                "total_wall_seconds": round(time.perf_counter() - total_started, 4), "provider_peak_concurrency": peak,
                "backend_candidates": result["total_found"], "compact_candidates": len(result["articles"]),
                "ordered_dois": [row["doi"] for row in result["articles"]], "fulltext_successes": sum(row.get("available", False) for row in full["articles"]),
                "fulltext_attempts": len(attempts), "analysis_calls": len(analysis_calls)})
    assert reports[0]["backend_candidates"] == reports[1]["backend_candidates"] == 1200
    assert reports[0]["ordered_dois"] == reports[1]["ordered_dois"]
    assert reports[0]["fulltext_successes"] == reports[1]["fulltext_successes"] == 6
    assert reports[0]["analysis_calls"] == 12 and reports[1]["analysis_calls"] == 6
    return {"simulated": True, "real_openai_calls": 0, "real_provider_calls": 0,
        "arxiv_note": "Serial latency stub includes scaled spacing; separate deterministic tests enforce the real 3-second rule.",
        "runs": reports}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_benchmark(args.backup)
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream: json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2))
