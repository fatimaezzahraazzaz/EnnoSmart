"""Targeted axes keep room in the unchanged limit; local search is never silently empty."""
import time
from unittest.mock import Mock

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.toolkit import ScholarTools


def rows(prefix, count, title="mesh study", abstract="Volumetric meshing."):
    return [Article(title=f"{prefix} {title} {i}", abstract=abstract, authors=["A"], year=2024,
        doi=f"10.1234/{prefix}{i}", sources=["openalex"]) for i in range(count)]


def tools():
    return ScholarTools({"articles": {}, "messages": []}, Mock(), ScholarV4Config(api_key="mock"))


def search(t, query, family="axis"):
    return t.execute("search_scientific", {"query": query, "sources": None, "limit_per_source": 20,
        "offset": 0, "family": family}, time.monotonic()+30)


def test_broad_queries_cannot_starve_the_hexahedral_axis():
    t = tools()
    t.sources.search.side_effect = [(rows("a", 40), []), (rows("b", 40), []), (rows("c", 10), []), (rows("hex", 12), [])]
    counts = [search(t, q)["new_candidates"] for q in ("broad one", "broad two", "quality", "hexahedral polycube")]
    assert counts[0] == 27 and counts[1] == 18
    assert counts[3] > 0 and t.new_candidates <= t.config.max_search_results == 80
    assert not t.state.get("refused_searches")


def test_unknown_family_label_is_ignored_and_reported():
    t = tools()
    t.sources.search.return_value = (rows("a", 5) + rows("p", 1, "polycube hexahedral generation"), [])
    search(t, "mesh", family="learned-mesh-adaptation")
    found = t.execute("search_collected_articles", {"query": "polycube hexahedral", "limit": 8,
        "exclude_presented": False, "family": "rl-adaptive-refinement"}, time.monotonic()+30)
    assert found["articles"] and found["articles"][0]["title"].startswith("p polycube")
    assert found["retrieval"]["family_filter_ignored"] == "rl-adaptive-refinement"
    assert "learned-mesh-adaptation" in found["retrieval"]["known_families"]


def test_generic_words_do_not_promote_off_topic_notices():
    t = tools()
    generic = [Article(title=f"Deep neural network learning study {i}", abstract="Deep learning neural network generation.",
        authors=["A"], year=2024, doi=f"10.1234/g{i}") for i in range(8)]
    relevant = [Article(title="Tetrahedral mesh generator", abstract="Volumetric tetrahedral meshes for finite elements.",
        authors=["A"], year=2024, doi="10.1234/tet")]
    t.store.insert(generic + relevant)
    found = t.execute("search_collected_articles", {"query": "deep learning neural network tetrahedral mesh generation",
        "limit": 3, "exclude_presented": False, "family": None}, time.monotonic()+30)
    assert found["articles"][0]["title"] == "Tetrahedral mesh generator"
