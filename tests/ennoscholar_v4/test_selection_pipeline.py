"""Discovery → presentation → evaluation → selection → plan, on the real failures.

LiDAR (run d8606428, session ed686234): the 80-candidate limit was spent by the
first searches (8 later searches refused), « Road Boundary Extraction from LiDAR
Point Clouds Using Dynamic Programming Algorithm » was the 13th of 20 results and
never presented, one unknown identifier rejected the whole decision batch, the plan
stage was diverted and the token check counted bytes as tokens.
3D meshing (run 6f5f57f4): DL-Polycube (27th of 29) and DDPM-Polycube (22nd) were
collected but only their titles were shown. No network, no OpenAI call.
"""
import copy
import json
import time
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4.agent import ScholarChatAgent, request_tokens
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.toolkit import ScholarTools
from tests.ennoscholar_v4.test_finalization import Client, call
from tests.ennoscholar_v4.test_context_memory import memory_of
from tests.ennoscholar_v4.test_turn_goal import linked_state, calls, goal, TimedClient

DP_TITLE = "Road Boundary Extraction from LiDAR Point Clouds Using Dynamic Programming Algorithm"


def notice(title, abstract, doi, source="openalex"):
    return Article(title=title, abstract=abstract, authors=["Fixture Author"], year=2024, doi=doi, sources=[source], verified=True)


def lock_results(lid):
    """20 results per lock in provider order; for lock 4 the dynamic-programming paper is 13th."""
    rows = [notice(f"LiDAR point cloud vehicle detection study {lid}-{i}", "Detects vehicles in LiDAR point clouds for driving.",
        f"10.1234/l{lid}-{i}", "openalex" if i % 2 else "semantic_scholar") for i in range(20)]
    if lid == "4":
        rows[12] = notice(DP_TITLE, "Road boundaries are traced in LiDAR point clouds by a dynamic programming search "
            "that enforces smooth, continuous boundary paths.", "10.1234/dp-boundary")
    return rows


def sources():
    mock = Mock()
    mock.last_provider_stats = None
    mock.verify.side_effect = lambda a, deadline: a.model_copy(update={"verified": True})
    mock.search.side_effect = lambda query, *a, **k: ([r.model_copy(deep=True) for r in lock_results(query.split()[-1])], [])
    return mock


def search(lid, query="road boundary extraction probabilistic smoothing"):
    return ("search_scientific", {"query": f"{query} lock {lid}", "sources": None, "limit_per_source": 10, "offset": 0,
        "family": f"verrou {lid}", "lock_ids": [lid]})


def dp_id(state):
    return next(aid for aid, row in state["articles"].items() if row["title"] == DP_TITLE)


def tools_with_presented(state, rows):
    t = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    t.store.insert(rows)
    window = t.store.window(list(t.registry.values()), "fixture", ordered=True)
    t.delivered([window["window_id"]])
    return t


# --- P0-1: decisions --------------------------------------------------------

def test_unknown_identifier_no_longer_rejects_the_batch_nor_half_applies_it():
    rows = [notice(f"Candidate {i}", "Relevant abstract.", f"10.1234/c{i}") for i in range(3)]
    state = {"articles": {}, "messages": [{"role": "user", "content": "Sélectionne"}]}
    t = tools_with_presented(state, rows)
    a, b, c = list(t.registry)
    t.store.exposed.remove(c)          # c was never presented to the model
    result = t.execute("record_candidate_decisions", {"decisions": [
        {"article_id": a, "decision": "retain", "justification": "Méthode directement pertinente."},
        {"article_id": "paper_typo", "decision": "retain", "justification": "Identifiant mal recopié."},
        {"article_id": b, "decision": "reject", "justification": "Hors sujet."},
        {"article_id": c, "decision": "retain", "justification": "Jamais présenté."}],
        "finalist_ids": [a, "paper_typo", b], "covered_families": [], "remaining_axes": []}, 1e12)
    assert result["saved"] and result["recorded_decisions"] == 2
    reasons = {(row["article_id"], row["reason"], row.get("as")) for row in result["ignored"]}
    assert reasons == {("paper_typo", "unknown_id", None), (c, "not_presented", None),
        ("paper_typo", "unknown_id", "finalist"), (b, "rejected", "finalist")}
    assert state["evaluation"]["recommended_article_ids"] == [a]
    assert c not in t.store.examined and not t.registry[c].assessment


def test_a_batch_without_any_applicable_decision_changes_nothing():
    rows = [notice("Candidate", "Relevant abstract.", "10.1234/only")]
    state = {"articles": {}, "messages": [{"role": "user", "content": "Sélectionne"}], "evaluation": {"recommended_article_ids": []}}
    t = tools_with_presented(state, rows)
    before = copy.deepcopy({k: state.get(k) for k in ("evaluation", "candidate_decisions", "examined_article_ids")})
    with pytest.raises(ValueError, match="paper_typo"):
        t.execute("record_candidate_decisions", {"decisions": [{"article_id": "paper_typo", "decision": "retain",
            "justification": "x"}], "finalist_ids": ["paper_typo"], "covered_families": [], "remaining_axes": []}, 1e12)
    assert {k: state.get(k) for k in before} == before


def test_an_unknown_lock_is_dropped_but_the_decision_is_kept():
    state = linked_state()
    t = tools_with_presented(state, [notice("LiDAR curb extraction", "Curbs.", "10.1234/curb")])
    aid = next(iter(t.registry))
    result = t.execute("record_candidate_decisions", {"decisions": [{"article_id": aid, "decision": "retain",
        "justification": "Bords de chaussée.", "lock_ids": ["4", "9"]}], "finalist_ids": [aid],
        "covered_families": [], "remaining_axes": []}, 1e12)
    assert t.registry[aid].assessment["lock_ids"] == ["4"]
    assert result["ignored"] == [{"article_id": aid, "reason": "unknown_lock_ids", "lock_ids": ["9"]}]


# --- P1-6 / P0-4: presentation and the shared limit ------------------------

def test_dynamic_programming_paper_is_stored_and_presented_with_its_abstract():
    state = linked_state()
    t = ScholarTools(state, sources(), ScholarV4Config(api_key="mock"))
    windows = [t.execute(*search(lid), time.monotonic()+30) for lid in "1234"]
    # Each lock received its part of the unchanged 80-candidate limit.
    assert [s["new_candidates"] for s in state["recent_searches"]] == [20, 20, 20, 10]
    assert not state.get("refused_searches")
    lock4 = windows[-1]
    # Stored first despite the last search's smaller share, and shown with its abstract.
    assert lock4["articles"][0]["title"] == DP_TITLE and "dynamic programming" in lock4["articles"][0]["abstract"]
    assert all(a.get("abstract") for a in lock4["articles"])


def test_a_query_without_lock_leaves_room_for_the_unsearched_locks():
    state = linked_state()
    t = ScholarTools(state, sources(), ScholarV4Config(api_key="mock"))
    t.execute("search_scientific", {**search("1")[1], "lock_ids": None}, time.monotonic()+30)
    # Four active locks still unsearched: 80 // 5 = 16, instead of half the limit (40).
    assert state["recent_searches"][0]["new_candidates"] == 16


def test_mesh_axes_share_the_limit_and_polycube_reaches_the_model():
    from tests.ennoscholar_v4.test_candidate_visibility import observed_batch
    state = {"articles": {}, "messages": [{"role": "user", "content": "Maillage hexaédrique et tétraédrique par IA"}],
        "research_state": {"search_axes": ["tetrahedral mesh generation", "hexahedral polycube meshing", "adaptive mesh refinement"]}}
    mock = Mock()
    mock.last_provider_stats = None
    big = [notice(f"Generic tetrahedral study {i}", "Tetrahedral meshes.", f"10.1234/tet{i}") for i in range(50)]
    mock.search.side_effect = lambda query, *a, **k: (big if "tetrahedral" in query else observed_batch(), [])
    t = ScholarTools(state, mock, ScholarV4Config(api_key="mock"))
    t.execute("search_scientific", {"query": "tetrahedral mesh generation deep learning", "sources": None, "limit_per_source": 15,
        "offset": 0, "family": "tetrahedral mesh generation"}, time.monotonic()+30)
    # Two declared axes not yet searched keep their part: 80 // 3 = 26 instead of 40.
    assert state["recent_searches"][0]["new_candidates"] == 26
    result = t.execute("search_scientific", {"query": "hexahedral polycube mesh generation", "sources": None, "limit_per_source": 15,
        "offset": 0, "family": "hexahedral polycube meshing"}, time.monotonic()+30)
    titles = [a["title"] for a in result["articles"]]
    # Collected 27th and 22nd by the providers, now stored and presented first with their abstracts.
    assert set(titles[:2]) == {"DL-Polycube: deep learning polycube hexahedral meshing",
        "DDPM-Polycube: diffusion model for polycube structures"}
    assert all(a.get("abstract") for a in result["articles"][:2])


def test_refused_searches_do_not_spend_the_tool_budget():
    from dataclasses import replace
    state = {"articles": {}, "messages": [{"role": "user", "content": "Cherche des articles sur les bords de chaussée LiDAR"}]}
    express = [(name, {**args, "lock_ids": None}) for name, args in (search("1"), search("2"))]
    def respond(n, kw):
        if n == 1:
            return calls(*express)
        return call("finalize_response", {"answer": "Recherche limitée.", "article_ids": [], "evidence_ids": [],
            "coverage_sufficient": False, "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": None}, n)
    config = replace(ScholarV4Config(api_key="mock", web_search_enabled=False), max_search_results=10)
    result = ScholarChatAgent(config, client=Client(respond), sources=sources()).run(state)
    assert state["refused_searches"] and result["tool_counts"]["search_scientific"] == 2
    assert result["tool_calls"] == result["tool_counts"]["search_scientific"]-1+result["tool_counts"]["finalize_response"]


# --- P0-3: selection before the plan, then a plan that cannot be diverted --

def test_lidar_plan_request_selects_the_dynamic_programming_paper_before_the_plan():
    from dataclasses import replace
    state = linked_state()
    seen = {}
    def respond(n, kw):
        memory = memory_of(kw)
        names = [t.get("name") for t in kw["tools"]]
        if n == 1:
            return calls(goal("plan", "abstracts"), *(search(lid) for lid in "1234")), 20, 600
        if n == 2:
            # Round budget spent without any decision: a selection precedes the plan.
            assert kw["tool_choice"] == {"type": "function", "name": "record_candidate_decisions"}
            candidates = [row["id"] for row in memory["selection_candidates"]]
            seen["candidates"] = candidates
            dp = dp_id(state)
            assert dp in candidates
            # The window alternates between the locks whose searches found the notices.
            first_locks = [next(s["lock_ids"][0] for s in state["recent_searches"] if aid in s["article_ids"]) for aid in candidates[:4]]
            assert sorted(first_locks) == ["1", "2", "3", "4"]
            decisions = [{"article_id": dp, "decision": "retain", "justification": "Programmation dynamique des bords : verrou 4.",
                "lock_ids": ["4"]}, {"article_id": "paper_typo", "decision": "retain", "justification": "ID erroné", "lock_ids": ["2"]}]
            return calls(("record_candidate_decisions", {"decisions": decisions, "finalist_ids": [dp, "paper_typo"],
                "covered_families": [], "remaining_axes": []})), 20, 500
        # The plan is generated by part: the lock-4 part places the selected paper.
        assert names == ["propose_plan_sections"] and kw["tool_choice"] == {"type": "function", "name": "propose_plan_sections"}
        part = memory["plan_part"]
        if part["lock_ids"] != ["4"]:
            return calls(("propose_plan_sections", {"sections": [], "closing_sections": [], "consultant_note":
                "Plan provisoire centré sur ce qui a été sélectionné." if part["kind"] == "framing" else ""})), 30, 300
        assert any(row["id"] == dp_id(state) and row["lock_ids"] == ["4"] for row in memory["plan_sources"])
        section = {"title": "Bords de chaussée : programmation dynamique et lissage", "description": "Verrou 4.",
            "article_ids": [dp_id(state)], "scientific_objective": "Comment obtenir des bords fidèles sans biais systématique des contours ?",
            "questions": [], "article_roles": [{"article_id": dp_id(state), "role": "Chemins de bord par programmation dynamique"}],
            "relation_ids": [], "experimental_results": [], "limitations": [], "evidence_ids": [],
            "missing_information": ["Lecture du texte intégral à faire"], "project_connection": "Comparer au lissage par programmation dynamique du projet.",
            "figure_suggestions": [], "lock_ids": ["4"]}
        return calls(("propose_plan_sections", {"sections": [section], "closing_sections": [], "consultant_note": ""})), 30, 1500
    clock = [0.]
    config = replace(ScholarV4Config(api_key="mock", web_search_enabled=False), max_tool_rounds=1)
    result = ScholarChatAgent(config, client=TimedClient(respond, clock), sources=sources()).run(state)
    # The other required parts explicitly returned no sections. They must remain
    # incomplete even though the selected lock-4 section was saved and rendered.
    assert result["partial"] and result["reason"] == "plan_parts_incomplete" and DP_TITLE in result["answer"]
    assert state["evaluation"]["recommended_article_ids"] == [dp_id(state)]
    lock4 = next(sec for sec in state["outline"]["sections"] if "4" in sec["lock_ids"])
    assert lock4["article_ids"] == [dp_id(state)]
    assert result["tool_counts"].get("finalize_response") is None and result["tool_counts"].get("get_project_context") is None


# --- P0-2: tokens --------------------------------------------------------

def test_token_check_uses_estimated_tokens_and_stays_protective():
    # Before round 9 of d8606428: 232 066 tokens used, next request ~131 700 bytes.
    used, request = 232_066, 131_700
    config = ScholarV4Config(api_key="mock")
    assert used+request+config.max_output_tokens+config.finalization_reserved_tokens > config.max_tokens  # old false stop
    assert used+request_tokens(request)+config.max_output_tokens+config.finalization_reserved_tokens <= config.max_tokens
    # Still above the measured size: round 1 of d8606428, 79 170 characters → 20 585 tokens.
    assert request_tokens(79_170) >= 20_585*1.2
    assert request_tokens(0) == 0


def test_parallel_searches_are_not_compacted_to_one_notice_each():
    """d8606428: 6 parallel searches reached the next request with 1 notice each;
    the dynamic-programming paper (4th of its search) was never presented."""
    long_abstract = " Road boundary continuity, curb geometry and point density are discussed in detail." * 14
    def results(query, *a, **k):
        lid = query.split()[-1]
        rows = [notice(f"LiDAR road study {lid}-{i}", "LiDAR road scene processing." + long_abstract, f"10.1234/p{lid}-{i}")
            for i in range(20)]
        if lid == "4":
            rows[3] = notice(DP_TITLE, "Dynamic programming traces smooth road boundary paths." + long_abstract, "10.1234/dp-boundary")
        return rows, []
    mock = sources()
    mock.search.side_effect = results
    state = linked_state()
    seen = {}
    def respond(n, kw):
        if n == 1:
            # A direct answer from the abstracts: no forced selection, the next round shows the windows.
            return calls(goal("answer", "abstracts"), *(search(lid, "road boundary extraction") for lid in "1234"),
                search("1", "road boundary curb geometry"), search("2", "road boundary point density"))
        outputs = [json.loads(item["output"]) for item in kw["input"] if item.get("type") == "function_call_output"]
        seen["windows"] = [len(o["articles"]) for o in outputs if "window_id" in o]
        seen["size"] = len(json.dumps(kw["input"], ensure_ascii=False))
        return call("finalize_response", {"answer": "Candidats présentés.", "article_ids": [], "evidence_ids": [],
            "coverage_sufficient": False, "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": None}, n)
    config = ScholarV4Config(api_key="mock", web_search_enabled=False)
    ScholarChatAgent(config, client=Client(respond), sources=mock).run(state)
    assert seen["size"] <= config.max_context_chars
    assert len(seen["windows"]) == 6 and min(seen["windows"]) >= 10, seen["windows"]
    assert dp_id(state) in state["exposed_article_ids"]
