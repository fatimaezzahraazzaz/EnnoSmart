"""Boolean queries reach each provider in a form it supports (run 5768a223 regression)."""
import time
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.sources import ScientificSources, provider_query
from agents.EnnoScholarV4.toolkit import ScholarTools

OBSERVED = ('("3D mesh generation" OR "volumetric mesh generation" OR "tetrahedral mesh generation") '
    '(deep learning OR neural network OR generative model) finite element mechanical simulation')


def test_boolean_query_becomes_keywords_for_semantic_scholar_and_arxiv_only():
    keywords = provider_query("semantic_scholar", OBSERVED)
    assert " OR " not in keywords and "(" not in keywords and '"' not in keywords
    assert keywords.split()[:4] == ["3D", "mesh", "generation", "volumetric"]
    assert keywords.split().count("mesh") == 1 and "tetrahedral" in keywords and "finite" in keywords
    assert provider_query("arxiv", OBSERVED) == keywords
    assert provider_query("openalex", OBSERVED) == OBSERVED
    plain = "learning hexahedral mesh generation polycube"
    assert all(provider_query(p, plain) == plain for p in ("semantic_scholar", "arxiv", "openalex"))
    # Lowercase words such as "and" in a title are content, not operators.
    assert provider_query("semantic_scholar", "HexGen and Hex2Spline") == "HexGen and Hex2Spline"


def fake_sources(answers):
    sent = {}
    def provider(name):
        def call(query, limit, deadline=None):
            sent[name] = query
            return answers.get(name, [])
        return call
    client = SimpleNamespace(semantic=provider("semantic_scholar"), openalex=provider("openalex"), arxiv=provider("arxiv"),
        turn_cache=nullcontext, search_offset=0)
    sources = ScientificSources.__new__(ScientificSources)
    sources.client = client
    sources.normalize = lambda row, query="": row
    return sources, sent


def test_each_provider_receives_its_query_and_empty_results_are_reported():
    sources, sent = fake_sources({"openalex": [], "semantic_scholar": [], "arxiv": []})
    rows, warnings = sources.search(OBSERVED, None, 10, time.monotonic()+30)
    assert sent["openalex"] == OBSERVED and sent["semantic_scholar"] == provider_query("semantic_scholar", OBSERVED)
    assert sources.last_provider_stats["semantic_scholar"]["returned"] == 0 and not warnings
    t = ScholarTools({"articles": {}, "messages": []}, sources, ScholarV4Config(api_key="mock"))
    result = t.execute("search_scientific", {"query": OBSERVED, "sources": None, "limit_per_source": 10,
        "offset": 0, "family": "volumetric"}, time.monotonic()+30)
    assert set(result["empty_providers"]) == {"openalex", "semantic_scholar", "arxiv"}
    assert "sans OR" in result["empty_guidance"]
    assert t.state["recent_searches"][-1]["providers"]["arxiv"]["query_sent"] == provider_query("arxiv", OBSERVED)


def test_mocked_sources_without_provider_stats_keep_working():
    sources = Mock()
    sources.search.return_value = ([], [])
    t = ScholarTools({"articles": {}, "messages": []}, sources, ScholarV4Config(api_key="mock"))
    result = t.execute("search_scientific", {"query": "mesh", "sources": None, "limit_per_source": 10,
        "offset": 0, "family": "x"}, time.monotonic()+30)
    assert "empty_providers" not in result and result["collected"] == 0
