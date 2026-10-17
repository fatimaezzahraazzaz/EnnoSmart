"""Collected notices must be reachable by the model (run 6f5f57f4 regression)."""
import json
import time
from unittest.mock import Mock

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.context import model_context
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.toolkit import ScholarTools


def notice(i, title, abstract, source):
    return Article(title=title, abstract=abstract, authors=["Fixture Author"], year=2025,
        doi=f"10.1234/mesh{i}", sources=[source], verified=True)


def observed_batch():
    """29 rows, provider completion order: DDPM-Polycube 22nd, DL-Polycube 27th."""
    rows = [notice(i, f"Unrelated learned classifier {i}", "Predicts labels for tabular data.", "openalex") for i in range(15)]
    rows += [notice(15+i, f"Generic surface remeshing study {i}", "Remeshes triangle surfaces.", "semantic_scholar") for i in range(14)]
    rows[21] = notice(21, "DDPM-Polycube: diffusion model for polycube structures",
        "A denoising diffusion model constructs polycubes for hexahedral mesh generation.", "semantic_scholar")
    rows[26] = notice(26, "DL-Polycube: deep learning polycube hexahedral meshing",
        "Deep learning identifies polycube structures and generates hexahedral meshes.", "semantic_scholar")
    return rows


def toolkit(rows=(), state=None):
    sources = Mock()
    sources.search.return_value = (list(rows), [])
    return ScholarTools(state or {"articles": {}, "messages": [{"role": "user", "content": "Travaux sur le maillage hexaédrique par IA"}]},
        sources, ScholarV4Config(api_key="mock"))


def search(t, query="hexahedral mesh generation neural network"):
    return t.execute("search_scientific", {"query": query, "sources": None, "limit_per_source": 15,
        "offset": 0, "family": "cad-to-simulation-mesh"}, time.monotonic()+30)


def ids_by_title(t):
    return {a.title.split(":")[0]: a.id for a in t.registry.values()}


def test_providers_alternate_by_their_own_rank_in_the_first_window():
    t = toolkit(observed_batch())
    result = search(t)
    providers = [t.registry[a["id"]].sources[0] for a in result["articles"]]
    assert providers[:4] == ["openalex", "semantic_scholar", "openalex", "semantic_scholar"]
    assert t.sources.search.call_count == 1


def test_titles_beyond_the_window_are_indexed_not_presented():
    t = toolkit(observed_batch())
    result = search(t)
    ids = ids_by_title(t)
    indexed = {row["id"] for row in result["batch_index"]}
    assert {ids["DL-Polycube"], ids["DDPM-Polycube"]} <= indexed
    # 29 results fit in one search's share (half of the remaining limit).
    assert result["batch_index_total"] == 29-len(result["articles"])
    assert all("abstract" not in row for row in result["batch_index"])
    t.delivered([result["window_id"]])
    assert ids["DL-Polycube"] not in t.store.exposed


def test_local_search_follows_the_model_query_and_enables_a_decision():
    t = toolkit(observed_batch())
    first = search(t)
    t.delivered([first["window_id"]])
    found = t.execute("search_collected_articles", {"query": "hexahedral polycube diffusion", "limit": 8,
        "exclude_presented": True, "family": None}, time.monotonic()+30)
    ids = ids_by_title(t)
    assert [a["id"] for a in found["articles"][:2]] == [ids["DDPM-Polycube"], ids["DL-Polycube"]]
    assert found["retrieval"]["scores_are_scientific_ranking"] is False
    assert all(a.get("abstract") for a in found["articles"][:2])
    t.delivered([found["window_id"]])
    saved = t.execute("record_candidate_decisions", {"decisions": [
        {"article_id": ids["DL-Polycube"], "decision": "retain", "justification": "Polycube hexahedral generation."},
        {"article_id": ids["DDPM-Polycube"], "decision": "retain", "justification": "Diffusion-based polycubes."}],
        "finalist_ids": [ids["DL-Polycube"], ids["DDPM-Polycube"]], "covered_families": [], "remaining_axes": []}, time.monotonic()+30)
    assert saved["saved"]
    assert t.sources.search.call_count == 1


def test_empty_local_query_keeps_storage_order():
    t = toolkit(observed_batch())
    search(t)
    found = t.execute("search_collected_articles", {"query": "", "limit": 8, "exclude_presented": False,
        "family": None}, time.monotonic()+30)
    assert [a["id"] for a in found["articles"]] == list(t.registry)[:8]


def test_long_session_index_shows_this_turn_candidates_first():
    old = [Article(title=f"Earlier topic paper {i}", abstract="Older subject.", authors=["A"], year=2020,
        doi=f"10.9999/old{i}", verified=True) for i in range(140)]
    t = toolkit(observed_batch())
    t.store.insert(old)
    search(t)
    t2 = t  # same toolkit = same message
    memory = json.loads(model_context(t2.state, t2, 60000)[0]["content"].split(" : ", 1)[1])
    titles = [row["title"] for row in memory["candidate_index"]]
    assert any(title.startswith("DL-Polycube") for title in titles)
    assert any(title.startswith("DDPM-Polycube") for title in titles)
