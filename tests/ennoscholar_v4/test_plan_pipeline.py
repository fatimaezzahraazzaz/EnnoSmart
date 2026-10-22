"""Understanding before execution, selection after the searches, a plan generated
by parts in parallel within its own time budget, and a provisional plan that is
always useful. LiDAR (run e7bae458) and 3D meshing scenarios. Offline only."""
import json
import time as real_time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4 import agent as module
from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.toolkit import ScholarTools
from agents.EnnoScholarV4.turn import provisional_plan
from tests.ennoscholar_v4.test_finalization import call
from tests.ennoscholar_v4.test_performance import ScaledClient, SCALE, memory
from tests.ennoscholar_v4.test_turn_goal import linked_state, PAPERS, LOCKS, plan_sections, stored, goal, search, sources, calls
from tests.ennoscholar_v4 import replay_e7bae458 as replay


@pytest.fixture
def clock(monkeypatch):
    now = replay.Clock()
    monkeypatch.setattr(module, "time", NS(monotonic=now))
    return now


def goal_args(deliverable="plan", depth="core", locks=()):
    return {"deliverable": deliverable, "lock_ids": list(locks), "focus_lock_ids": [], "exact_quote": "",
        "reading_depth": depth, "understanding": "Compréhension de la demande."}


def publish_now(n):
    """A conversation round where the model goes straight to publishing (deferred to the plan)."""
    return call("finalize_response", {"answer": "x", "article_ids": [], "evidence_ids": [], "coverage_sufficient": False,
        "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": None}, n), 5, 300


def part_answer(kw, state, seconds=45):
    part = memory(kw)["plan_part"]
    if part["kind"] == "framing":
        frame = lambda title: {**plan_sections(("1",), state)[0], "title": title, "article_ids": [], "article_roles": [],
            "lock_ids": [], "missing_information": []}
        return call("propose_plan_sections", {"sections": [frame("Introduction")], "closing_sections": [frame("Synthèse")],
            "consultant_note": "Plan construit par verrou."}, "f"), seconds, 1200
    if part["kind"] == "axis":
        section = {**plan_sections(("1",), state)[0], "title": part["label"], "article_ids": [], "article_roles": [], "lock_ids": []}
        return call("propose_plan_sections", {"sections": [section], "closing_sections": [], "consultant_note": ""}, "a"), seconds, 1200
    return call("propose_plan_sections", {"sections": plan_sections(tuple(part["lock_ids"]), state), "closing_sections": [],
        "consultant_note": ""}, part["lock_ids"][0]), seconds, 1200


# --- 1. Understanding before execution ------------------------------------------------

def analysed_express_state(request):
    from tests.ennoscholar_v4.test_context_memory import rich_corpus
    tools, ids = rich_corpus(2)
    tools.state["messages"] = [{"id": "m1", "role": "user", "content": request}]
    tools.state["evaluation"] = {"recommended_article_ids": ids}
    return tools.state, ids


@pytest.mark.parametrize("deliverable, compares", [("plan", False), ("comparison", True)])
def test_words_alone_never_start_a_comparison(clock, deliverable, compares):
    # « état de l'art », « plan », « compar… » used to start comparisons and blueprints
    # before the model read the message (377 s in run e7bae458).
    state, ids = analysed_express_state("Construis le plan de mon état de l'art en comparant les méthodes retenues.")
    seen = []
    def respond(n, kw):
        name = (kw["tool_choice"] or {}).get("name") if isinstance(kw["tool_choice"], dict) else kw["tool_choice"]
        seen.append(name)
        if name == "compare_articles":
            focus = memory(kw)["argumentation_focus"]
            return call("compare_articles", {"article_ids": focus["article_ids"], "research_context": "c", "conclusions": None,
                "relations": [], "claim_offsets": []}, n), 20, 400
        if name == "build_argument_blueprint":
            focus = memory(kw)["argumentation_focus"]
            return call("build_argument_blueprint", {"action": "save", "merge": False, "global_question": "Q", "narrative_logic": "L",
                "axes": [], "remaining_questions": [], "claim_offsets": []}, n), 20, 400
        if name == "manage_outline":
            return call("manage_outline", {"action": "create", "title": "Plan", "section_id": None, "target_section_id": None,
                "position": None, "sections": [{**plan_sections(("1",), None)[0], "article_ids": ids[:1], "article_roles": [],
                "lock_ids": []}], "article_ids": None, "offset": 0, "limit": 8, "consultant_message": "Plan."}, n), 30, 1000
        return call("finalize_response", {"answer": "Comparaison des méthodes [" + ids[0] + "].", "article_ids": ids[:1], "evidence_ids": [],
            "coverage_sufficient": False, "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": None}, n), 30, 800
    client = ScaledClient(respond, clock, goal=goal_args(deliverable, "core"))
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=client, sources=Mock()).run(state)
    assert len(client.understanding) == 1 and result["understanding_calls"] == 1
    assert ("compare_articles" in seen) is compares
    if not compares:
        assert "build_argument_blueprint" not in seen and "plan proposé" in result["answer"]


def test_a_drafting_word_does_not_open_a_draft_when_a_plan_is_understood(clock):
    state, ids = analysed_express_state("Rédige l'état de l'art complet")
    def respond(n, kw):
        assert kw["tool_choice"] != {"type": "function", "name": "write_section"}
        return call("manage_outline", {"action": "create", "title": "Plan", "section_id": None, "target_section_id": None,
            "position": None, "sections": [{**plan_sections(("1",), None)[0], "article_ids": ids[:1], "article_roles": [], "lock_ids": []}],
            "article_ids": None, "offset": 0, "limit": 8, "consultant_message": "Voici d'abord le plan."}, n), 30, 1000
    client = ScaledClient(respond, clock, goal=goal_args("plan", "core"))
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=client, sources=Mock()).run(state)
    assert "plan proposé" in result["answer"] and not state.get("document", {}).get("writing_progress")
    assert not result["tool_counts"].get("write_section")


# --- 2. Selection and finalists -------------------------------------------------------

def test_an_uncertain_candidate_is_never_a_finalist():
    rows = [PAPERS["1"].model_copy(deep=True), PAPERS["2"].model_copy(deep=True)]
    state = {"articles": {}, "messages": [{"role": "user", "content": "x"}]}
    t = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    t.store.insert(rows)
    window = t.store.window(list(t.registry.values()), "q", ordered=True)
    t.delivered([window["window_id"]])
    a, b = list(t.registry)
    result = t.execute("record_candidate_decisions", {"decisions": [
        {"article_id": a, "decision": "retain", "justification": "Pertinent."},
        {"article_id": b, "decision": "pending", "justification": "Incertain."}],
        "finalist_ids": [a, b], "covered_families": [], "remaining_axes": []}, 1e12)
    assert state["evaluation"]["recommended_article_ids"] == [a]
    assert {"article_id": b, "reason": "not_retained", "as": "finalist"} in result["ignored"]


# --- 3. Plan by parts, in parallel ----------------------------------------------------

def test_the_plan_is_generated_by_lock_in_parallel_and_framed(clock):
    state = linked_state("Construis le plan détaillé à partir des articles retenus.")
    for lid, paper in PAPERS.items():
        paper = paper.model_copy(deep=True)
        if lid != "4":
            paper.assessment = {"decision": "retain", "justification": "Pertinent.", "lock_ids": [lid], "basis": "abstract"}
        state["articles"][paper.id] = paper.durable()
    state["evaluation"] = {"recommended_article_ids": [f"paper_lock_{l}" for l in "123"]}
    def respond(n, kw):
        if kw["tool_choice"] == {"type": "function", "name": "propose_plan_sections"}:
            return part_answer(kw, state)
        return publish_now(n)
    client = ScaledClient(respond, clock, goal=goal_args("plan", "core", LOCKS))
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=client, sources=Mock()).run(state)
    assert not result["partial"] and client.max_active == 5
    assert [s["lock_ids"] for s in state["outline"]["sections"]] == [[], ["1"], ["2"], ["3"], ["4"], []]
    # Five 45 s parts in parallel: the plan stage takes about one part, not five.
    assert result["timings"]["stages"]["plan"] < 100 and result["timings"]["wall_seconds"] < 150
    assert result["tool_counts"]["propose_plan_sections"] == 5 and result["tool_counts"]["manage_outline"] == 1


def test_a_failed_part_becomes_a_provisional_section_and_candidates_are_labelled(clock):
    state = linked_state("Construis le plan détaillé à partir des articles retenus.")
    for lid, paper in PAPERS.items():
        paper = paper.model_copy(deep=True)
        if lid == "1":
            paper.assessment = {"decision": "retain", "justification": "Pertinent.", "lock_ids": ["1"], "basis": "abstract"}
        state["articles"][paper.id] = paper.durable()
    def respond(n, kw):
        if kw["tool_choice"] != {"type": "function", "name": "propose_plan_sections"}:
            return publish_now(n)
        part = memory(kw)["plan_part"]
        if part["lock_ids"] == ["3"]:
            raise RuntimeError("simulated provider error")
        if part["lock_ids"] == ["2"]:
            # The model cites a notice it never retained: shown as a candidate, not a selected work.
            sections = plan_sections(("2",), state)
            return call("propose_plan_sections", {"sections": sections, "closing_sections": [], "consultant_note": ""}, n), 45, 1200
        return part_answer(kw, state)
    client = ScaledClient(respond, clock, goal=goal_args("plan", "core", LOCKS))
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=client, sources=Mock()).run(state)
    lock3 = next(s for s in state["outline"]["sections"] if s["lock_ids"] == ["3"])
    assert "provisoire" in lock3["description"] and "partie(s) du plan restent provisoires" in result["answer"]
    assert "candidat non évalué, à vérifier" in result["answer"]


# --- 4. Time budget and the provisional plan -----------------------------------------

def test_without_time_for_the_plan_a_useful_provisional_plan_is_returned(clock):
    state = linked_state("Construis le plan détaillé à partir des articles retenus.")
    retained = PAPERS["1"].model_copy(deep=True)
    retained.assessment = {"decision": "retain", "justification": "Adaptation de domaine routière.", "lock_ids": ["1"], "basis": "abstract"}
    candidate = PAPERS["2"].model_copy(deep=True)
    state["articles"] = {retained.id: retained.durable(), candidate.id: candidate.durable()}
    state["recent_searches"] = [{"query": "polygon topology", "lock_ids": ["2"], "article_ids": [candidate.id], "family": "v2"}]
    calls_seen = []
    def respond(n, kw):
        calls_seen.append(kw["tool_choice"])
        return call("finalize_response", {"answer": "x", "article_ids": [], "evidence_ids": [], "coverage_sufficient": False,
            "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": None}, n), 10, 300
    client = ScaledClient(respond, clock, goal=goal_args("plan", "core", LOCKS))
    # 60 s left after understanding: less than a plan part (90 s).
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False, timeout_seconds=70), client=client,
        sources=Mock()).run(state)
    answer = result["answer"]
    assert not calls_seen and result["partial"] and not result["fallback"]
    assert answer.startswith("**Plan provisoire.**") and "il n'affirme aucun résultat" in answer
    assert "Publications retenues :**" in answer and "Topology-preserving polygonization" in answer.split("Candidats non évalués")[1]
    assert "retenue sur résumé, non analysée" in answer and "Lacunes :" in answer


def test_a_plan_cut_by_the_deadline_still_answers_with_the_provisional_plan(clock):
    state = linked_state("Construis le plan détaillé à partir des articles retenus.")
    def respond(n, kw):
        if kw["tool_choice"] != {"type": "function", "name": "propose_plan_sections"}:
            return publish_now(n)
        return part_answer(kw, state, seconds=400)
    client = ScaledClient(respond, clock, goal=goal_args("plan", "core", LOCKS))
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False, timeout_seconds=250), client=client,
        sources=Mock()).run(state)
    assert result["reason"] == "timeout" and result["answer"].startswith("**Plan provisoire.**")
    assert result["usage"]["unreported_reserved_cost_usd"] > 0


def test_provisional_plan_never_presents_a_candidate_as_validated():
    from agents.EnnoScholarV4.models import Article
    state = linked_state()
    pending = PAPERS["4"].model_copy(deep=True)
    pending.assessment = {"decision": "pending", "justification": "Incertain.", "lock_ids": ["4"]}
    state["recent_searches"] = [{"query": "road boundary", "lock_ids": ["4"], "article_ids": [pending.id]}]
    registry = {pending.id: pending}
    text = provisional_plan(state, registry)
    lock4 = text.split("### 4.")[1]
    assert "Publications retenues :** aucune" in lock4 and "Candidats non évalués" in lock4


# --- 5. Searches of one round in parallel ---------------------------------------------

def test_the_searches_of_a_round_run_concurrently_with_identical_results(clock, monkeypatch):
    from agents.EnnoScholarV4 import toolkit as toolkit_module
    def slow_sources():
        mock = sources()
        base = mock.search.side_effect
        def search(query, *a, **k):
            clock.wait(8)  # Exact simulated provider time, unaffected by machine load.
            return base(query, *a, **k)
        mock.search.side_effect = search
        return mock
    def run(parallel):
        state = linked_state("Trouve des articles pour chaque verrou.")
        if not parallel:
            monkeypatch.setattr(toolkit_module.ScholarTools, "prefetch_searches", lambda *a, **k: None)
        def respond(n, kw):
            if n == 1:
                return calls(*(search(lid) for lid in "1234")), 5, 300
            return call("finalize_response", {"answer": "Recherche faite.", "article_ids": [], "evidence_ids": [],
                "coverage_sufficient": False, "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": None}, n), 5, 300
        client = ScaledClient(respond, clock, goal=goal_args("answer", "abstracts"))
        start = clock()
        result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=client, sources=slow_sources()).run(state)
        return clock()-start, [s["article_ids"] for s in state["recent_searches"]]
    parallel_time, parallel_ids = run(True)
    sequential_time, sequential_ids = run(False)
    assert parallel_ids == sequential_ids
    assert sequential_time-parallel_time > 15  # 4 x 8 s searches overlap


def test_provider_statistics_are_kept_per_thread():
    import threading
    from agents.EnnoScholarV4.sources import ScientificSources
    sources_obj = ScientificSources.__new__(ScientificSources)
    seen = {}
    def work(name):
        sources_obj.last_provider_stats = {name: 1}
        real_time.sleep(.02)
        seen[name] = sources_obj.last_provider_stats
    threads = [threading.Thread(target=work, args=(n,)) for n in ("a", "b")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert seen == {"a": {"a": 1}, "b": {"b": 1}}


# --- 6. 3D meshing (Express) ----------------------------------------------------------

def test_meshing_plan_is_generated_by_axis_in_parallel(clock):
    state = {"articles": {}, "messages": [{"id": "m", "role": "user", "content": "Propose un plan d'état de l'art sur le maillage 3D par IA."}],
        "research_state": {"topic": "Maillage volumique par IA", "search_axes": ["hexahedral polycube meshing",
            "tetrahedral mesh generation", "adaptive mesh refinement learning"]}}
    def respond(n, kw):
        if kw["tool_choice"] == {"type": "function", "name": "propose_plan_sections"}:
            return part_answer(kw, state)
        return publish_now(n)
    client = ScaledClient(respond, clock, goal=goal_args("plan", "abstracts"))
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=client, sources=Mock()).run(state)
    titles = [s["title"] for s in state["outline"]["sections"]]
    assert titles == ["Introduction", "hexahedral polycube meshing", "tetrahedral mesh generation", "adaptive mesh refinement learning", "Synthèse"]
    assert client.max_active >= 2 and result["timings"]["stages"]["plan"] < 100
    assert result["tool_counts"]["propose_plan_sections"] == 4


def test_meshing_plan_without_axes_uses_one_plan_call(clock):
    state = {"articles": {}, "messages": [{"id": "m", "role": "user", "content": "Propose un plan d'état de l'art sur le maillage 3D par IA."}],
        "research_state": {"topic": "Maillage volumique par IA"}}
    def respond(n, kw):
        assert kw["tool_choice"] == {"type": "function", "name": "manage_outline"} or kw["tool_choice"] == "auto"
        return call("manage_outline", {"action": "create", "title": "Plan", "section_id": None, "target_section_id": None, "position": None,
            "sections": [{**plan_sections(("1",), None)[0], "article_ids": [], "article_roles": [], "lock_ids": []}],
            "article_ids": None, "offset": 0, "limit": 8, "consultant_message": "Plan."}, n), 150, 4000
    client = ScaledClient(respond, clock, goal=goal_args("plan", "abstracts"))
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=client, sources=Mock()).run(state)
    assert "plan proposé" in result["answer"] and not result["tool_counts"].get("propose_plan_sections")


# --- 7. Replay of the real run e7bae458 -----------------------------------------------

@pytest.mark.skipif(not replay.available(), reason="local database absent")
def test_replay_of_run_e7bae458_delivers_the_plan_well_before_the_deadline():
    out = replay.run()
    result, state = out["result"], out["state"]
    # Real run: 377 s of automatic comparison and blueprint, plan cut at 600 s, fallback.
    assert not result["partial"] and not result["fallback"] and result["reason"] is None
    assert (state.get("outline") or {}).get("sections")
    assert "argumentation" not in out["model_seconds_by_kind"]
    stages = result["timings"]["stages"]
    assert set(stages) >= {"understanding", "conversation", "selection", "plan"}
    assert result["timings"]["wall_seconds"] < 300
    assert result["usage"]["estimated_cost_usd"] < 0.834


def test_the_replay_clock_is_immune_to_standby_load_and_oversleep(monkeypatch):
    """b52f582a failed only in full suites run while the machine went to (modern) standby:
    a real hour of standby during one simulated call was charged as 100 simulated hours."""
    from types import SimpleNamespace
    from tests.ennoscholar_v4 import replay_e7bae458 as replay_module
    wall, cpu = [0.0], [0.0]
    def sleep(seconds):
        wall[0] += seconds + 3600          # the machine sleeps for an hour during the call
    monkeypatch.setattr(replay_module, "real_time", SimpleNamespace(perf_counter=lambda: wall[0],
        process_time=lambda: cpu[0], sleep=sleep))
    clock = replay_module.Clock()
    cpu[0] += 2.0                          # two seconds of local processing
    clock.wait(30)                         # a model call stated at 30 s
    assert clock() == pytest.approx(32.0)
    # Overlapping calls are counted once: the window lasts as long as the longest call.
    import threading
    threads = [threading.Thread(target=clock.wait, args=(d,)) for d in (10, 25)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert 32.0+25.0 <= clock() <= 32.0+35.0
