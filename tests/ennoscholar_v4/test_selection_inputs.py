"""What the model receives before selecting: abstracts, never-forced empty judgments, axes status."""
import json
import time
from unittest.mock import Mock

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.toolkit import ScholarTools
from tests.ennoscholar_v4.test_finalization import Client, call
from dataclasses import replace


def notices(abstract=True, count=4):
    return [Article(title=f"Learned mesh generator {i}", abstract="Generates tetrahedral meshes." if abstract else "",
        authors=["A"], year=2024, doi=f"10.1234/m{i}", sources=["openalex"]) for i in range(count)]


def run(rows, held=()):
    sources = Mock()
    sources.search.return_value = (rows, [])
    state = {"messages": [{"id": "u1", "role": "user", "content": "Quels sont les travaux les plus pertinents ? Propose un état de l'art."}],
        "articles": {}, "held_article_ids": list(held)}
    choices, submitted = [], []

    def respond(n, kw):
        choices.append(kw["tool_choice"])
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if kw["tool_choice"] == {"type": "function", "name": "record_candidate_decisions"}:
            submitted.extend(memory["selection_candidates"])
            return call("record_candidate_decisions", {"decisions": [{"article_id": row["id"], "decision": "pending",
                "justification": "Needs more evidence."} for row in memory["selection_candidates"][:1]],
                "finalist_ids": [], "covered_families": [], "remaining_axes": []}, n)
        if n == 1:
            return call("search_scientific", {"query": "learned tetrahedral mesh generation", "sources": None,
                "limit_per_source": 4, "offset": 0, "family": "tetra"}, n)
        if kw["tool_choice"] == "auto":
            return call("update_research_state", {"topic": "mesh", "consultant_decisions": []}, n)
        return call("finalize_response", {"answer": "Aucune sélection.", "article_ids": [], "evidence_ids": [],
            "coverage_sufficient": False, "remaining_uncertainties": ["x"], "analyses": None, "displayed_article_ids": None}, n)

    config = replace(ScholarV4Config(api_key="mock", web_search_enabled=False), max_tool_rounds=4)
    ScholarChatAgent(config, client=Client(respond), sources=sources).run(state)
    return state, choices, submitted


def test_unpresented_candidates_reach_the_checkpoint_with_their_abstracts():
    state, choices, submitted = run(notices(count=12))
    assert {"type": "function", "name": "record_candidate_decisions"} in choices
    # 8 were in the search window, 4 only in batch_index: all are judged on abstracts.
    assert len(submitted) == 12 and all(row.get("abstract") for row in submitted)
    assert all(aid in state["exposed_article_ids"] for aid in (row["id"] for row in submitted))


def test_no_forced_selection_without_any_abstract_and_held_works_excluded():
    _, choices, _ = run(notices(abstract=False))
    assert {"type": "function", "name": "record_candidate_decisions"} not in choices
    rows = notices()
    from agents.EnnoScholarV4.store import ArticleStore
    held_id = ArticleStore({"articles": {}}).insert(rows[:1])[0][0]
    _, _, submitted = run(rows, held=[held_id])
    assert held_id not in {row["id"] for row in submitted}


def test_undecided_candidates_are_never_counted_as_rejected():
    state, _, submitted = run(notices(count=6))
    from agents.EnnoScholarV4.catalogue import library_groups
    groups = library_groups(state)
    assert not groups["rejected"]
    assert len(groups["unexamined"]) + len(groups["examined_pending"]) == 6


def test_search_reports_declared_axes_next_to_searched_families():
    sources = Mock()
    sources.search.return_value = (notices(), [])
    t = ScholarTools({"articles": {}, "messages": [], "research_state": {"search_axes": [
        "hexahedral mesh generation by learning", "learned tetrahedral meshing"]}}, sources, ScholarV4Config(api_key="mock"))
    result = t.execute("search_scientific", {"query": "learned tetrahedral mesh generation", "sources": None,
        "limit_per_source": 4, "offset": 0, "family": "learned tetrahedral meshing"}, time.monotonic()+30)
    axes = {row["axis"]: row["lexical_match"] for row in result["search_axes_status"]["axes"]}
    assert axes["learned tetrahedral meshing"] and "lexical_match" in result["search_axes_status"]["axes"][0]
    assert "juge toi-même" in result["search_axes_status"]["guidance"]
