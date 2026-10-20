"""Partial results, exact refusals and restart semantics through real validators."""
import copy
import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.task_results import TaskResults, collect_finished
from tests.ennoscholar_v4.test_finalization import Stream, call
from tests.ennoscholar_v4.test_turn_goal import linked_state
from tests.ennoscholar_v4.test_performance import memory


def ledger(state=None):
    usage = dict(input_tokens=0, output_tokens=0, estimated_cost_usd=0., unreported_reserved_cost_usd=0.)
    durations = []
    return TaskResults(state if state is not None else {}, ScholarV4Config(api_key="mock"), usage, durations)


def test_accounting_is_identical_for_all_tasks_and_exactly_once():
    results = ledger()
    for kind in ("section", "plan_part", "reading", "understanding"):
        task = results.task(kind, "target", "sources-v1")
        attempt = results.begin(task)
        response = NS(output=[], status="completed", usage=dict(input_tokens=300000, output_tokens=100,
            input_tokens_details={"cached_tokens": 5000}, output_tokens_details={"reasoning_tokens": 80}))
        results.receive(task, attempt, response, seconds=3, web_calls=1)
        results.receive(task, attempt, response, seconds=3, web_calls=1)
    assert results.usage["input_tokens"] == 1200000
    assert results.usage["reasoning_tokens"] == 320
    assert results.usage["cached_input_tokens"] == 20000
    assert len(results.durations) == 4
    # Catalogue prices by category; above 272 000 input tokens the long-context factors apply.
    from agents.EnnoScholarV4.pricing import resolve
    p = resolve(results.config)
    expected = 4*((295000*p.input*2+5000*p.cache_read*2+100*p.output*1.5)/1e6+p.web_call)
    assert results.usage["estimated_cost_usd"] == pytest.approx(expected)
    task = results.task("section", "failed", "v1")
    attempt = results.begin(task)
    results.receive(task, attempt, seconds=7, reserve=.12, error=RuntimeError("transport"))
    assert results.usage["unreported_reserved_cost_usd"] == .12
    assert len(results.durations) == 5 and task["status"] == "failed"


def test_refusal_content_proofs_and_proposal_survive_serialization():
    state = {}
    results = ledger(state)
    task = results.task("section", "s1", "v1")
    attempt = results.begin(task, evidence_ids=["ev_1"], input_signature="exact-projection")
    output = call("write_section", {"action": "save", "section_id": "s1", "text": "old"}, 1)
    results.receive(task, attempt, NS(output=output, usage={"input_tokens": 10}, status="completed"))
    refusal = {"error": "exact validator message "*80, "rejected_claims": [{"statement": "unsupported", "evidence_ids": ["ev_1"]}]}
    results.reject(task, attempt, refusal)
    restored = ledger(json.loads(json.dumps(state)))
    recovered = restored.rows[task["id"]]
    exchange = restored.recovery_exchange(recovered)
    assert json.loads(exchange[-1]["output"]) == refusal
    assert recovered["evidence_ids"] == ["ev_1"]
    assert exchange[0]["arguments"] == output[0]["arguments"]
    assert not restored.complete([recovered])


def test_transport_receipts_keep_identifiers_without_credentials():
    results = ledger()
    task = results.task("reading", "paper", "v1")
    attempt = results.begin(task)
    error = RuntimeError("credential sk-private-do-not-store at https://upstream.invalid/private")
    error.request_id = "req_test"
    error.status_code = 429
    results.receive(task, attempt, error=error, reserve=.1)
    assert attempt["request_id"] == "req_test" and attempt["http_status"] == 429
    assert "sk-private" not in json.dumps(task) and "upstream.invalid" not in json.dumps(task)


def test_finished_siblings_are_drained_before_cancellation():
    first, second, slow = Future(), Future(), Future()
    first.set_result("first")
    second.set_result("second")
    saved = []
    def interrupt():
        raise RuntimeError("cancelled")
    with pytest.raises(RuntimeError, match="cancelled"):
        collect_finished([slow, first, second], lambda f: saved.append(f.result()), interrupt)
    assert saved == ["first", "second"] and not slow.done()


class PlanClient:
    def __init__(self, invalid=None, delay=False):
        self.invalid, self.delay = invalid, delay
        self.parts = []
        self.lock = threading.Lock()
    def with_options(self, **kw): return self
    @property
    def responses(self): return self
    def create(self, **kw):
        choice = kw["tool_choice"]
        if choice == {"type": "function", "name": "set_turn_goal"}:
            output = call("set_turn_goal", dict(deliverable="plan", reading_depth="abstracts", lock_ids=[],
                focus_lock_ids=[], exact_quote="", understanding="Plan", search_needed=False), "goal")
        elif choice == {"type": "function", "name": "propose_plan_sections"}:
            part = memory(kw)["plan_part"]
            key = tuple(part["lock_ids"])
            with self.lock:
                self.parts.append(key)
            if self.delay and key in {("2",), ("3",), ("4",)}:
                time.sleep(.4)
            args = dict(sections=[dict(title=part["label"], description="Aucune affirmation scientifique inventée.",
                article_ids=[], lock_ids=part["lock_ids"])], closing_sections=[], consultant_note="")
            if key == ("4",) and self.invalid == "schema":
                args["sections"] = "invalid"
            if key == ("4",) and self.invalid == "proof":
                args["sections"][0]["evidence_ids"] = ["ev_unknown"]
            output = call("propose_plan_sections", args, str(key))
            if key == ("4",) and self.invalid == "json":
                output[0]["arguments"] = "{invalid"
            if key == ("4",) and self.invalid == "failed":
                return Stream(NS(output=output, status="failed", error={"code": "test"}, usage={"input_tokens": 100, "output_tokens": 10}))
        else:
            output = call("finalize_response", dict(answer="x", article_ids=[], evidence_ids=[], coverage_sufficient=False,
                remaining_uncertainties=[], analyses=None, displayed_article_ids=None), "final")
        return Stream(NS(output=output, output_text="", status="completed", usage={"input_tokens": 100,
            "output_tokens": 10, "input_tokens_details": {"cached_tokens": 20}, "output_tokens_details": {"reasoning_tokens": 3}}))


def plan_run(state, client, **kw):
    return ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=client, sources=Mock()).run(state, **kw)


@pytest.mark.parametrize("invalid", ["json", "schema", "proof", "failed"])
def test_bad_part_is_isolated_persisted_and_only_that_part_is_retried(invalid):
    state = linked_state("Construis un plan sans rédiger.")
    client = PlanClient(invalid)
    result = plan_run(state, client)
    assert result["partial"] and result["reason"] == "plan_parts_incomplete"
    assert not state["outline"]["generation"]["complete"]
    parts = [t for t in state["task_results"].values() if t["kind"] == "plan_part"]
    assert sum(t["status"] == "saved" for t in parts) == 4
    assert result["usage"]["reasoning_tokens"] == (18 if invalid == "failed" else 21)
    original = {t["id"]: copy.deepcopy(t["content"]) for t in parts if t["status"] == "saved"}
    # A real restart: JSON is the only memory transferred to the next agent.
    state = json.loads(json.dumps(state))
    state["messages"].append({"id": "resume", "role": "user", "content": "Continue le plan."})
    retry = PlanClient()
    result = plan_run(state, retry)
    assert retry.parts == [("4",)]
    assert not result["partial"] and state["outline"]["generation"]["complete"]
    assert all(state["task_results"][key]["content"] == content for key, content in original.items())
    assert len(state["outline"]["sections"]) == 5


def test_interruption_saves_finished_parts_and_resume_does_not_duplicate():
    state = linked_state("Construis un plan sans rédiger.")
    client = PlanClient(delay=True)
    saved_count = lambda: sum(t["kind"] == "plan_part" and t["status"] == "saved" for t in state.get("task_results", {}).values())
    result = plan_run(state, client, cancelled=lambda: saved_count() >= 2)
    assert result["reason"] == "cancelled" and result["partial"]
    assert saved_count() == 2 and state["outline"]["generation"]["complete"] is False
    assert result["usage"]["unreported_reserved_cost_usd"] > 0
    time.sleep(.45)  # Old workers never write into the session after the agent returns.
    assert saved_count() == 2
    state = json.loads(json.dumps(state))
    state["messages"].append({"id": "resume", "role": "user", "content": "Continue le plan."})
    retry = PlanClient()
    result = plan_run(state, retry)
    assert set(retry.parts) == {("2",), ("3",), ("4",)}
    assert not result["partial"] and len(state["outline"]["sections"]) == 5


def test_parallel_proposals_and_assembly_keep_the_existing_tool_cap():
    state = linked_state("Construis un plan sans rédiger.")
    client = PlanClient()
    config = ScholarV4Config(api_key="mock", web_search_enabled=False, max_tool_calls=5)
    result = ScholarChatAgent(config, client=client, sources=Mock()).run(state)
    assert result["tool_calls"] <= 5 and result["partial"]
    assert len(client.parts) < 5


def test_local_save_is_idempotent_and_keeps_the_real_validators():
    from tests.ennoscholar_v4.test_writing import drafted_document, write
    tools, ids = drafted_document()
    eid = next(e for e in tools.shown_evidence if tools.evidence[e]["article_id"] == ids[1])
    assert write(tools, "1", f"Ces résultats restent conditionnels [{ids[1]}] [{eid}].", [ids[1]], [eid])["saved"]
    section = next(section for section in tools.state["outline"]["sections"] if section.get("drafts"))
    draft = section["drafts"][-1]
    args = dict(action="save", section_id=section["id"], instructions="", text=draft["raw_text"],
        article_ids=draft["article_ids"], evidence_ids=draft["evidence_ids"])
    tools.check = lambda: (_ for _ in ()).throw(RuntimeError("deadline"))
    n = len(section["drafts"])
    one = tools.execute("write_section", args, 0, settle=True, operation_id="op1")
    two = tools.execute("write_section", args, 0, settle=True, operation_id="op1")
    assert one["saved"] and two["draft_id"] == one["draft_id"] and len(section["drafts"]) == n+1
    with pytest.raises(ValueError, match="changer de contenu"):
        tools.execute("write_section", {**args, "text": "changed"}, 0, settle=True, operation_id="op1")
    with pytest.raises(ValueError):
        tools.execute("write_section", {**args, "evidence_ids": ["ev_unknown"]}, 0, settle=True, operation_id="op2")
    assert len(section["drafts"]) == n+1
    with pytest.raises(ValueError):
        tools.execute("get_fulltext", {}, 0, settle=True)


def test_repeated_claim_pages_cannot_postpone_a_save_indefinitely(monkeypatch):
    from agents.EnnoScholarV4 import agent as module
    from tests.ennoscholar_v4.test_section_claims import prepared_seven
    from tests.ennoscholar_v4.test_document_orchestration import agent
    from tests.ennoscholar_v4.test_finalization import Client
    state, ids, eids = prepared_seven()
    template = state["outline"]["sections"][0]
    state["outline"]["sections"] = [{**copy.deepcopy(template), "id": f"section_test_{i}",
        "title": f"Section {i}"} for i in range(1, 15)]
    state["messages"] = [{"id": "write", "role": "user", "content": "Rédige uniquement la section 1 du plan."}]
    others = copy.deepcopy(state["outline"]["sections"][1:])
    forced = []
    def respond(n, kw):
        sid = memory(kw)["section_drafting"]["focus"]["section_id"]
        save = kw["tool_choice"] == {"type": "function", "name": "write_section"}
        forced.append(save)
        if not save:
            # Different rounds return the same complete page, exactly the loop
            # observed in 843fbe5d. It must not reset the save progress counter.
            return call("get_analysis_claims", {"article_id": ids[1], "fields": ["method", "results"],
                "offset": 0}, n)
        sentence = f"La méthode conserve la topologie documentée [{ids[1]}] [{eids[1]}]."
        return call("write_section", dict(section_id=sid, action="save", instructions="",
            text=sentence, article_ids=[ids[1]], evidence_ids=[eids[1]]), n)
    result = agent(Client(respond)).run(state)
    assert not result["partial"] and forced[-1] is True
    assert len(forced) <= module.SECTION_READ_ROUNDS+2
    assert len(state["outline"]["sections"][0]["drafts"]) == 1
    assert state["outline"]["sections"][1:] == others


def test_section_receipt_resumes_a_generated_proposal_without_a_model_save(monkeypatch):
    from tests.ennoscholar_v4.test_section_claims import prepared_seven
    from tests.ennoscholar_v4.test_document_orchestration import agent
    from tests.ennoscholar_v4.test_finalization import Client
    state, ids, eids = prepared_seven()
    state["messages"] = [{"id": "write", "role": "user", "content": "Rédige uniquement la section 1."}]
    emitted = []
    def respond(n, kw):
        sid = memory(kw)["section_drafting"]["focus"]["section_id"]
        return call("write_section", dict(section_id=sid, action="save", instructions="",
            text=f"La méthode reste documentée [{ids[1]}] [{eids[1]}].", article_ids=[ids[1]], evidence_ids=[eids[1]]), n)
    class Crash(Exception): pass
    def crash_after_proposal(event):
        if event.get("tool") == "task_result" and event.get("task_kind") == "section" and event.get("task_status") == "generated":
            emitted.append(copy.deepcopy(state))
            raise Crash("simulated worker loss after persisted response")
    agent(Client(respond)).run(state, emit=crash_after_proposal)
    assert emitted
    restored = json.loads(json.dumps(emitted[0]))
    # Evidence delivery is part of the receipt checkpoint, not a later event.
    restored["messages"].append({"id": "retry", "role": "user", "content": "Rédige uniquement la section 1."})
    client = Client(lambda *a: (_ for _ in ()).throw(AssertionError("Saved proposal must not be regenerated")))
    result = agent(client).run(restored)
    assert not result["partial"] and len(restored["outline"]["sections"][0]["drafts"]) == 1
