"""The unchanged new-candidate limit counts only works absent from the corpus."""
import time
from dataclasses import replace
from unittest.mock import Mock

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.toolkit import ScholarTools


def rows(prefix, count, source="openalex"):
    return [Article(title=f"{prefix} mesh study {i}", abstract="Volumetric meshing.", authors=["A"], year=2024,
        doi=f"10.1234/{prefix}{i}", sources=[source]) for i in range(count)]


def tools(limit=80):
    sources = Mock()
    return ScholarTools({"articles": {}, "messages": []}, sources, replace(ScholarV4Config(api_key="mock"), max_search_results=limit))


def search(t, query):
    return t.execute("search_scientific", {"query": query, "sources": None, "limit_per_source": 30,
        "offset": 0, "family": "axis"}, time.monotonic()+30)


def test_cross_provider_duplicates_and_known_works_do_not_consume_the_limit():
    t = tools(limit=20)
    t.sources.search.return_value = (rows("a", 10, "openalex") + rows("a", 10, "semantic_scholar"), [])
    first = search(t, "first axis")
    assert first["new_candidates"] == 10 and first["new_candidate_budget_remaining"] == 10
    t.sources.search.return_value = (rows("a", 5) + rows("b", 15), [])
    second = search(t, "second axis")
    assert second["new_candidates"] == 10 and second["already_known"] == 5
    assert second["capacity_dropped"] == 5 and len(t.registry) == 20
    assert t.new_candidates == t.config.max_search_results == 20


def test_refused_search_spends_no_round_and_is_recorded():
    t = tools(limit=10)
    t.sources.search.return_value = (rows("a", 10), [])
    search(t, "first axis")
    # Limit reached: refused before any provider call, without spending a search round.
    calls, rounds = t.sources.search.call_count, t.search_count
    refused = search(t, "third axis")
    assert refused["refused"] == "new_candidate_limit" and refused["collected"] == 0
    assert t.sources.search.call_count == calls and t.search_count == rounds
    assert t.state["refused_searches"][-1]["query"] == "third axis"


def test_partial_room_merges_known_records_and_bounds_only_new_ones():
    t = tools(limit=12)
    t.sources.search.return_value = (rows("a", 10), [])
    search(t, "first axis")
    t.sources.search.return_value = (rows("a", 3) + rows("d", 6), [])
    result = search(t, "second axis")
    assert result["already_known"] == 3 and result["new_candidates"] == 2
    presented = {a["id"] for a in result["articles"]}
    assert {t.registry[aid].id for aid in presented} <= set(t.registry)
    assert any(t.registry[a["id"]].doi.startswith("10.1234/a") for a in result["articles"])
