"""Lots 1-2: cache-stable requests, compact analysis output, parallel analyses,
critical-path time budget and intent-driven reading depth. Offline only.

Durations replay the real traces: an analysis save lasts 145 s (runs 5768bedd and
85a926db: 142-170 s), a synthesis 152 s (d8606428). A scaled clock (1 real second =
100 simulated seconds) lets concurrent calls really overlap.
"""
import json
import threading
import time as real_time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
import httpx
from openai import RateLimitError

from agents.EnnoScholarV4 import agent as module
from agents.EnnoScholarV4.agent import ScholarChatAgent, TURN_INSTRUCTION_PREFIX
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.scientific_reading import resolve_evidence_refs, evidence_refs
from agents.EnnoScholarV4.turn import call_estimate
from tests.ennoscholar_v4.test_finalization import Stream, call
from tests.ennoscholar_v4.test_scientific_reading import article, analysis_from_packet

SCALE = 100.0


@pytest.fixture
def clock(monkeypatch):
    from tests.ennoscholar_v4.replay_e7bae458 import Clock
    now = Clock()
    monkeypatch.setattr(module, "time", NS(monotonic=now))
    return now


class ScaledClient:
    """Each call lasts its simulated duration; concurrent calls overlap."""
    def __init__(self, respond, clock, goal=None):
        self.respond, self.clock, self.calls, self.lock = respond, clock, [], threading.Lock()
        self.active = self.max_active = 0
        self.goal, self.understanding = goal, []
    def with_options(self, **kw): return self
    @property
    def responses(self): return self
    def create(self, **kw):
        if kw.get("tool_choice") == {"type": "function", "name": "set_turn_goal"}:
            self.understanding.append(kw)
            self.clock.wait(5) if hasattr(self.clock, "wait") else real_time.sleep(5/SCALE)
            output = call("set_turn_goal", self.goal, "goal") if self.goal else []
            return Stream(NS(output=output, output_text="", status="completed", usage={"input_tokens": 3000, "output_tokens": 200}))
        with self.lock:
            self.calls.append(kw)
            n = len(self.calls)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            output, seconds, out = self.respond(n, kw)
            self.clock.wait(seconds) if hasattr(self.clock, "wait") else real_time.sleep(seconds/SCALE)
        finally:
            with self.lock:
                self.active -= 1
        return Stream(NS(output=output, output_text="", status="completed", usage={"input_tokens": 9000, "output_tokens": out}))


def memory(kw):
    return json.loads(next(i["content"] for i in kw["input"] if i.get("role") == "developer"
        and i.get("content", "").startswith("Mémoire")).split(" : ", 1)[1])


def corpus(n=3, request="Analyse en profondeur les méthodes, résultats et limites de ces articles."):
    papers = [article(f"Synthetic study {i}", f"10.1234/s{i}", "methods", f"Mechanism {i}") for i in range(n)]
    for i, p in enumerate(papers):
        p.id, p.selected = f"paper_s{i}", True
    sources = Mock()
    sources.verify.side_effect = lambda a, deadline: a.model_copy(update={"verified": True})
    return {"articles": {p.id: p.durable() for p in papers}, "messages": [{"role": "user", "content": request}]}, sources


def with_refs(packet):
    """The model's save written with the short references of the window."""
    analysis = analysis_from_packet(packet)
    refs = {row["evidence_id"]: row["ref"] for row in packet["passages"]}
    for claims in analysis.values():
        if isinstance(claims, list):
            for claim in claims:
                claim["evidence_ids"] = [refs.get(e, e) for e in claim["evidence_ids"]]
    return analysis


def respond_factory(state, saves=None, fail=()):
    def respond(n, kw):
        if kw.get("tool_choice") == {"type": "function", "name": "analyze_article"}:
            packet = memory(kw)["automatic_scientific_reading"]
            if packet["article_id"] in fail:
                fail.remove(packet["article_id"])
                raise RateLimitError("simulated 429", response=httpx.Response(429,
                    request=httpx.Request("POST", "https://example.invalid/responses")), body=None)
            if saves is not None:
                saves.append(packet["article_id"])
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "x",
                "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": with_refs(packet)}, f"r{n}"), 145, 2600
        ids = list(state["articles"])
        return call("finalize_response", {"answer": "Synthèse " + " ".join(f"[{a}]" for a in ids), "article_ids": ids,
            "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": [], "analyses": None,
            "displayed_article_ids": ids}, f"f{n}"), 152, 5100
    return respond


def run(state, sources, client, **config):
    return ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False, **config), client=client, sources=sources).run(state)


# --- Lot 1 -------------------------------------------------------------------

def test_requests_of_a_task_share_one_prefix_and_carry_the_round_instruction_last(clock):
    state, sources = corpus()
    client = ScaledClient(respond_factory(state), clock)
    run(state, sources, client)
    readings = [kw for kw in client.calls if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}]
    assert len(readings) == 3
    assert len({kw["instructions"] + json.dumps(kw["tools"]) for kw in readings}) == 1
    assert {kw["prompt_cache_key"] for kw in readings} == {"ennoscholar-v4-reading"}
    assert all(kw["input"][-1]["content"].startswith(TURN_INSTRUCTION_PREFIX) for kw in client.calls)
    # The varying part (which article, rounds left) is no longer in the instructions.
    assert not any("paper_s" in kw["instructions"] for kw in client.calls)


def test_short_references_are_saved_as_exact_evidence():
    focus = {"article_id": "paper_a", "evidence_ids": ["ev_1111", "ev_2222"]}
    assert evidence_refs(focus) == {"E1": "ev_1111", "E2": "ev_2222"}
    raw = {"action": "save", "article_ids": ["paper_a"], "reviewed_evidence_ids": ["E2"],
        "analysis": {"method": [{"statement": "x", "evidence_ids": ["E1", "ev_2222"], "origin": "source_statement"}],
            "results": [{"statement": "y", "evidence_ids": ["E9"], "origin": "source_statement"}], "method_family": "m"}}
    resolved = resolve_evidence_refs(raw, focus)
    assert resolved["analysis"]["method"][0]["evidence_ids"] == ["ev_1111", "ev_2222"]
    assert resolved["reviewed_evidence_ids"] == ["ev_2222"]
    # Unknown references stay as written: the existing evidence checks refuse them.
    assert resolved["analysis"]["results"][0]["evidence_ids"] == ["E9"]
    # Another article's save is never rewritten.
    assert resolve_evidence_refs({**raw, "article_ids": ["paper_b"]}, focus)["analysis"]["method"][0]["evidence_ids"] == ["E1", "ev_2222"]


def test_references_cut_analysis_output_tokens():
    tiktoken = pytest.importorskip("tiktoken")
    encoder = tiktoken.get_encoding("o200k_base")
    state, sources = corpus(1)
    from agents.EnnoScholarV4.toolkit import ScholarTools
    from agents.EnnoScholarV4.scientific_reading import reading_tasks, prepare_reading, reading_context
    tools = ScholarTools(state, sources, ScholarV4Config(api_key="mock"))
    task = reading_tasks(state, tools.registry)["tasks"][0]
    prepare_reading(state, tools, task, lambda n, a: tools.execute(n, a, float("inf")))
    packet = reading_context(state, tools)
    full = len(encoder.encode(json.dumps(analysis_from_packet(packet), ensure_ascii=False)))
    short = len(encoder.encode(json.dumps(with_refs(packet), ensure_ascii=False)))
    assert short < full


def test_calls_and_tool_outputs_are_timed_and_sized(clock):
    state, sources = corpus(1)
    events = []
    client = ScaledClient(respond_factory(state), clock)
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=client, sources=sources).run(state, emit=events.append)
    completed = [e for e in events if e["tool"] == "responses" and e["status"] == "completed" and e.get("phase") != "understanding"]
    assert completed and all(e["call_seconds"] >= 100 and e["task"] for e in completed)
    assert any("output_chars" in e for e in events if e["tool"] == "analyze_article" and e["status"] == "completed")
    assert result["timings"]["llm_calls"] == len(completed)+result["understanding_calls"] and result["timings"]["wall_seconds"] > 0


# --- Lot 2 -------------------------------------------------------------------

def test_three_analyses_run_in_parallel_and_are_saved_like_sequential_ones(clock, monkeypatch):
    state, sources = corpus()
    parallel = ScaledClient(respond_factory(state), clock)
    # A long deadline for both runs, so the sequential one also completes its 3 analyses.
    result = run(state, sources, parallel, timeout_seconds=1200)
    assert result["publication_accepted"] and parallel.max_active == 3
    assert result["timings"]["parallel_analyses"] == 3 and result["timings"]["max_concurrent_analyses"] == 3
    # 145 s analyses overlap: about 145 + 152 s instead of 3 x 145 + 152 = 587 s.
    assert result["timings"]["wall_seconds"] < 420
    cards = {aid: row["analysis"] for aid, row in state["articles"].items()}
    assert all(card.get("evidence") for card in cards.values())
    # Same cards as the sequential path (parallelism only changes timing).
    monkeypatch.setattr(module, "PARALLEL_ANALYSES", 1)
    state2, sources2 = corpus()
    sequential = ScaledClient(respond_factory(state2), clock)
    result2 = run(state2, sources2, sequential, timeout_seconds=1200)
    assert sequential.max_active == 1 and result2["timings"]["wall_seconds"] > 550
    strip = lambda card: {k: v for k, v in card.items() if k not in {"analysis_limits", "method_families"}}
    assert {aid: strip(c) for aid, c in cards.items()} == {aid: strip(r["analysis"]) for aid, r in state2["articles"].items()}


def test_cost_reservations_limit_how_many_analyses_start_together(clock):
    state, sources = corpus()
    client = ScaledClient(respond_factory(state), clock)
    # Fictitious test prices (USD / million: input 2, cache read 0.2, cache write 2.5, output
    # 10). Each analysis reserves ~0.089 $ (input bound at the highest input price, no cache
    # hit assumed, + 7 000 output tokens) and the publication keeps 0.25 $. 0.47 $ leaves
    # room for two, not three.
    result = run(state, sources, client, max_cost_usd=0.47, model="test-model", input_price=2.0, output_price=10.0,
        cache_read_price=.2, cache_write_price=2.5)
    assert client.max_active == 2
    assert result["usage"]["estimated_cost_usd"]+result["usage"]["unreported_reserved_cost_usd"] <= 0.47


def test_a_failed_background_analysis_is_retried_once_and_saved(clock):
    state, sources = corpus()
    fail = ["paper_s1"]
    events = []
    client = ScaledClient(respond_factory(state, fail=fail), clock)
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=client, sources=sources).run(state, emit=events.append)
    assert result["publication_accepted"] and state["articles"]["paper_s1"]["analysis"]
    assert any(e["status"] == "parallel_failed" for e in events)


def test_cancellation_keeps_in_flight_analyses_resumable_and_reserved(clock):
    state, sources = corpus()
    stop = threading.Event()
    def respond(n, kw):
        stop.set()
        return respond_factory(state)(n, kw)
    client = ScaledClient(respond, clock)
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=client,
        sources=sources).run(state, cancelled=stop.is_set)
    assert result["reason"] == "cancelled"
    pending = [t for t in state["scientific_reading"]["tasks"] if t.get("pending")]
    assert pending and all(t["status"] == "awaiting_analysis" for t in pending)
    assert result["usage"]["unreported_reserved_cost_usd"] > 0 or result["usage"]["estimated_cost_usd"] > 0


def test_time_estimate_never_drops_below_the_default_and_covers_the_final_call():
    # 85a926db: the first analysis (abstract only) took 88 s, the next 170 s.
    assert call_estimate([(88, 1759)], 120) == 120
    assert call_estimate([(88, 1759), (170, 2711)], 120) == 170
    assert call_estimate([(30, 400)], 120) == 120


def test_no_new_reading_when_it_and_the_final_answer_cannot_both_fit(clock):
    state, sources = corpus(4)
    saves = []
    client = ScaledClient(respond_factory(state, saves), clock)
    # 400 s: a first wave of three 145 s analyses, then the 152 s synthesis. A fourth
    # analysis (145 s more) would leave less than the synthesis needs: it is kept.
    result = run(state, sources, client, timeout_seconds=400)
    assert result["reason"] != "timeout" and result["publication_accepted"]
    assert len(saves) == 3 and client.max_active == 3
    assert state["scientific_reading"]["blocked_reason"] == "exploration_time_budget"


# --- Intent-driven depth -----------------------------------------------------

def plan_state(depth):
    from tests.ennoscholar_v4.test_turn_goal import linked_state
    # A plan from the works already retained (no new search requested).
    state = linked_state("Construis le plan détaillé à partir des articles retenus.")
    paper = article("Road boundary dynamic programming", "10.1234/dp", "roads", "Dynamic programming")
    paper.id = "paper_dp"
    paper.assessment = {"decision": "retain", "justification": "x", "lock_ids": ["4"], "basis": "abstract"}
    state["articles"] = {paper.id: paper.durable()}
    state["exposed_article_ids"] = state["examined_article_ids"] = [paper.id]
    state["evaluation"] = {"recommended_article_ids": [paper.id]}
    return state, depth


@pytest.mark.parametrize("depth, search_needed, reads", [("core", False, False), ("deep", False, True), ("deep", None, True)])
def test_a_plan_reads_full_texts_only_when_the_model_asks_for_depth(clock, depth, search_needed, reads):
    """A deep plan on the existing corpus reads the finalists before the plan. With
    search_needed null, the reading waits for the model's round; that round searches
    nothing here, so the current finalists are read."""
    from tests.ennoscholar_v4.test_turn_goal import goal, outline, plan_sections, calls
    state, _ = plan_state(depth)
    sources = Mock()
    sources.verify.side_effect = lambda a, deadline: a.model_copy(update={"verified": True})
    def respond(n, kw):
        if n == 1:
            return calls(goal("plan", depth, search_needed=search_needed)), 10, 300
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            packet = memory(kw)["automatic_scientific_reading"]
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "x",
                "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": with_refs(packet)}, n), 145, 2600
        sections = plan_sections(("4",))
        sections[0]["article_ids"] = ["paper_dp"]
        sections[0]["article_roles"] = [{"article_id": "paper_dp", "role": "Programmation dynamique"}]
        return calls(outline(sections=sections)), 60, 2000
    client = ScaledClient(respond, clock)
    result = run(state, sources, client)
    assert not result["partial"] and "plan proposé" in result["answer"]
    assert bool(result["tool_counts"].get("analyze_article")) is reads


def test_pending_finalists_are_not_read_automatically():
    from agents.EnnoScholarV4.toolkit import ScholarTools
    from agents.EnnoScholarV4.scientific_reading import reading_tasks
    state, sources = corpus(2)
    for row in state["articles"].values():
        row["selected"] = False
    state["articles"]["paper_s0"]["assessment"] = {"decision": "pending", "justification": "incertain"}
    state["articles"]["paper_s1"]["assessment"] = {"decision": "retain", "justification": "pertinent"}
    state["evaluation"] = {"recommended_article_ids": ["paper_s0", "paper_s1"]}
    tools = ScholarTools(state, sources, ScholarV4Config(api_key="mock"))
    ledger = reading_tasks(state, tools.registry)
    assert [t["article_id"] for t in ledger["tasks"]] == ["paper_s1"]


def test_the_cache_key_parameter_can_be_switched_off(clock, monkeypatch):
    monkeypatch.setattr(module, "CACHE_KEY_PREFIX", "")
    state, sources = corpus(1)
    client = ScaledClient(respond_factory(state), clock)
    run(state, sources, client)
    assert client.calls and all("prompt_cache_key" not in kw for kw in client.calls)
    # Stable prefixes do not depend on the parameter.
    assert all(kw["input"][-1]["content"].startswith(TURN_INSTRUCTION_PREFIX) for kw in client.calls)
