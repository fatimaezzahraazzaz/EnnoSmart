"""Regression of job_494f7a3d: eleven real responses, no provider or real-data writes.

Real receipts are read only when the local audit database is available. Synthetic
tests run independently of it. All saves below affect detached in-memory states.
"""
import copy
import json
import sqlite3
from types import SimpleNamespace as NS

import httpx
import pytest
from openai import APIConnectionError, BadRequestError, InternalServerError

from agents.EnnoScholarV4 import agent as module, writing_job
from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.stops import StopResearch, require_tools, technical_diagnostic
from agents.EnnoScholarV4.turn import TurnGoalArgs, record_goal
from tests.ennoscholar_v4.test_audit_91590987 import DB
from tests.ennoscholar_v4.test_finalization import Stream, call, sources

RUN = "2d7cb05befaf410fa12d8477b3fbc4f2"
EXECUTION = "f2afdf7330644571bc6f2f58d5b188af"
REQUEST_ID = "d58b484ce9ea46a09b06957878827fcc"
SECTION = "section_3905aabdae56451f8b400c5e49392161"
MISSING = "section_be058ccf31dd479a8a951e385408e4d1"
REQUEST = ("Rédige les sections encore manquantes et finalise l'état de l'art. "
    "Conserve le plan existant et toutes les sections déjà rédigées.")


def goal_args(**changes):
    return TurnGoalArgs.model_validate({"deliverable": "draft", "lock_ids": [], "focus_lock_ids": [],
        "exact_quote": "", "reading_depth": "deep", "understanding": "Compléter les sections manquantes.",
        "comparison_requested": True, "search_needed": False, "constraints": ["no_search", "no_manuscript_change"],
        **changes})


def planned():
    return {"outline": {"plan_revision": 4, "sections": [
        {"id": f"section_{i}", "title": f"Part {i}", "article_ids": [],
            "drafts": [{"id": f"draft_{i}", "text": f"Saved {i}", "article_ids": [], "evidence_ids": []}] if i < 9 else []}
        for i in range(11)]}}


def test_preservation_is_additive_and_not_a_blanket_write_prohibition():
    state = planned()
    before = copy.deepcopy(state["outline"])
    result = record_goal(state, goal_args(), REQUEST, {})
    refs = result["goal"]["references"]
    assert "no_manuscript_change" not in refs["constraints"]
    assert refs["constraints"] == ["no_search"]
    permissions = refs["manuscript_permissions"]
    assert permissions["protected_section_ids"] == [f"section_{i}" for i in range(9)]
    assert permissions["target_section_ids"] == ["section_9", "section_10"]
    assert state["outline"] == before


@pytest.mark.parametrize("prohibition", ["Ne rédige rien.", "Ne rédige pas de section.",
    "Aucune nouvelle rédaction.", "Aucune modification de mon état de l'art.",
    "Ne modifie pas le manuscrit.", "Ne modifie rien.", "Ne modifie pas.", "Do not write."])
def test_explicit_prohibition_wins_even_against_a_draft_goal(prohibition):
    state = planned()
    result = record_goal(state, goal_args(), REQUEST + " " + prohibition, {})
    assert "no_manuscript_change" in result["goal"]["references"]["constraints"]
    assert "manuscript_permissions" not in result["goal"]["references"]


def test_plan_preservation_does_not_cancel_an_explicit_request_to_fill_missing_sections():
    state = planned()
    state["conversation_references"] = {"constraints": ["no_manuscript_change"]}
    refs = record_goal(state, goal_args(), REQUEST + " Ne modifie pas le plan.", {})["goal"]["references"]
    assert "no_manuscript_change" not in refs["constraints"]
    assert refs["manuscript_permissions"]["preserve_plan"]


@pytest.mark.parametrize("required", [None, "write_section"])
def test_empty_tools_are_a_local_explicit_permission_conflict(required):
    with pytest.raises(StopResearch) as err:
        require_tools([], required=required, forbidden={"write_section"})
    assert err.value.reason == "permission_conflict"
    assert err.value.diagnostic["retryable"] is False
    assert err.value.diagnostic["required_tool"] == required


@pytest.mark.parametrize("exc,reason,category", [
    (IndexError("secret-key"), "internal_error", "internal"),
    (KeyError("offset"), "internal_error", "internal"),
    (APIConnectionError(request=httpx.Request("POST", "https://example.invalid")), "upstream_error", "provider"),
    (httpx.ReadTimeout("secret-key", request=httpx.Request("POST", "https://example.invalid")), "upstream_error", "provider"),
    (BadRequestError("secret-key", response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")), body=None), "configuration_error", "provider"),
    (InternalServerError("secret-key", response=httpx.Response(503, request=httpx.Request("POST", "https://example.invalid")), body=None), "upstream_error", "provider"),
])
def test_technical_errors_are_classified_without_exception_text(exc, reason, category):
    d = technical_diagnostic(exc)
    assert d["reason"] == reason and d["category"] == category
    assert "secret-key" not in json.dumps(d)


def test_bibliography_review_records_no_result_without_hiding_a_missing_scientific_field():
    from agents.EnnoScholarV4.scientific_reading import bibliography_only, reading_tasks
    from tests.ennoscholar_v4.test_scientific_reading import article
    a = article("Method", "10.1234/test", "axis", "Method")
    bibliography = {"section": "References", "page": 9, "text": "[1] Author A. A method. Journal, 2020.\n[2] Author B. A result. Journal, 2021."}
    a.fulltext["chunks"].append(bibliography)
    a.fulltext["text"] += "\n\n" + bibliography["text"]
    state = {}
    reading_tasks(state, {a.id: a}, priority_ids=[a.id])
    task = state["scientific_reading"]["tasks"][0]
    from agents.EnnoScholarV4.scientific_reading import source_rows, refresh_task
    row = next(r for r in source_rows(a) if r["section"] == "References")
    task["examined_evidence_ids"] = [row["evidence_id"]]
    analysis = copy.deepcopy(a.analysis)
    refresh_task(task, a)
    assert row["evidence_id"] in task["reviewed_without_result_evidence_ids"]
    assert task["unincorporated_passages"] == 0
    assert task["dimensions"]["results"]["status"] != "documented"
    assert "results" in task["core_missing"] and not task["core_covered"]
    assert a.analysis == analysis
    assert not bibliography_only({**row, "section": "Results"})
    assert not bibliography_only({**row, "text": row["text"] + "\nOur method achieved 95% accuracy."})
    assert not bibliography_only({**row, "text": "Results remain undocumented."})


def job_state():
    state = planned()
    from agents.EnnoScholarV4.writing_progress import begin_writing
    begin_writing(state, REQUEST)
    record_goal(state, goal_args(), REQUEST, {})
    job = writing_job.new_job(ScholarV4Config(api_key="mock"), "m", "r1", {})
    return job, state


def test_late_internal_failure_pins_goal_scope_and_budget_without_a_second_understanding():
    job, state = job_state()
    expected_goal = copy.deepcopy(state["turn_goal"])
    before = writing_job.saved_ids(state)
    data = {"reason": "internal_error", "diagnostic": {"category": "internal", "retryable": False},
        "ledger": [{"id": "call1", "status": "settled", "cost_usd": .449521, "reserved_usd": .5}]}
    writing_job.evaluate(job, state, "r1", "partial", data, before)
    assert job["goal"] == expected_goal and job["progress"]["id"] == state["document"]["writing_progress"]["id"]
    assert job["no_progress"] == 0 and job["status"] == "failed"
    assert job["spent"]["confirmed_usd"] == .449521 and writing_job.remaining(job) == pytest.approx(9.550479)
    # Settlement is idempotent even if the completion callback is delivered twice.
    writing_job.evaluate(job, state, "r1", "partial", data, before)
    assert len(job["segments"]) == 1 and job["spent"]["confirmed_usd"] == .449521


def test_transient_provider_failure_does_not_consume_the_scientific_stall_counter():
    job, state = job_state()
    for n in (1, 2):
        job["current_run_id"] = f"r{n}"
        writing_job.evaluate(job, state, f"r{n}", "partial", {"reason": "upstream_error"}, writing_job.saved_ids(state))
        assert job["no_progress"] == 0 and job["goal"] == state["turn_goal"]
    assert job["status"] == "failed" and job["technical_failures"] == 2
    assert job["last_cause"] == "upstream_error" and job["stop_reason"] != "stalled"


def real_trace(run_id=RUN):
    if not DB.is_file():
        pytest.skip("Local audit database unavailable: synthetic regressions still run")
    with sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        row = db.execute("select data from scholar_v4_runs where id=?", (run_id,)).fetchone()
        if not row:
            pytest.skip("Real run unavailable")
        run = json.loads(row[0])
        state = json.loads(db.execute("select data from scholar_v4_sessions where id like 'ace44bf8%'").fetchone()[0])
    execution = EXECUTION if run_id == RUN else "a6f082f381ca42539c6649094acc8e40"
    attempts = {a.get("ledger", {}).get("call_id"): a for t in state["task_results"].values() for a in t["attempts"]
        if a.get("execution") == execution}
    responses = [copy.deepcopy(attempts[r["id"]]) for r in run["ledger"]]
    index = next(i for i, m in enumerate(state["messages"]) if m.get("id") == REQUEST_ID)
    state["messages"] = state["messages"][:index+1]
    for task in state["task_results"].values():
        task["attempts"] = [a for a in task["attempts"] if a.get("execution") not in {EXECUTION, "a6f082f381ca42539c6649094acc8e40"}]
    state.get("document", {}).pop("writing_progress", None)
    # The two sections still missing when the run started were saved by later real runs
    # (job_494f7a3d): this in-memory copy restores the manuscript as the run found it.
    for section in state["outline"]["sections"]:
        if section["id"] in {SECTION, MISSING}:
            section["drafts"] = []
    for k in ("writing_segment", "turn_goal", "writing_focus", "argumentation_focus", "automatic_reading_focus",
        "request_card_pages", "section_received_claims", "section_read_receipts", "section_read_rounds"):
        state.pop(k, None)
    return state, responses


class Replay:
    def __init__(self, responses):
        self.recorded, self.calls = responses, []
    def with_options(self, **_):
        return self
    @property
    def responses(self):
        return self
    def create(self, **kw):
        self.next_request = copy.deepcopy(kw)
        if len(self.calls) == len(self.recorded):
            raise StopResearch("offline_replay_complete")
        receipt = self.recorded[len(self.calls)]
        self.calls.append(copy.deepcopy(kw))
        # Replay the historical proposals/usage under the current simulated
        # transport. Historical receipts themselves remain unchanged.
        return Stream(NS(output=receipt["content"], status="completed", usage=receipt["usage"],
            model=kw["model"], id=receipt["response_id"], service_tier="default"))


@pytest.mark.parametrize("run_id,expected", [(RUN, 7), ("315818910fae49a0a6779897113b0e77", 4)])
def test_original_segment_responses_replay_without_offset_or_empty_tool_crashes(monkeypatch, run_id, expected):
    state, recorded = real_trace(run_id)
    assert len(recorded) == expected
    assert [a["content"][0]["name"] for a in recorded] == ["set_turn_goal", "compare_articles", "compare_articles",
        *(["get_analysis_claims"] * (expected-3))]
    monkeypatch.setattr(module, "PARALLEL_SECTIONS", 1)
    before = {s["id"]: copy.deepcopy(s["drafts"]) for s in state["outline"]["sections"] if s.get("drafts")}
    client, events = Replay(recorded), []
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False),
        client=client, sources=sources()).run(state, emit=events.append)
    assert len(client.calls) == expected, (result["reason"], result.get("diagnostic"))
    assert result["reason"] != "internal_error", result.get("diagnostic")
    assert not any(e.get("error_type") in {"KeyError", "IndexError"} for e in events)
    assert "no_manuscript_change" not in state["turn_goal"]["references"]["constraints"]
    assert any("write_section" in [t.get("name") for t in kw["tools"]] for kw in client.calls[1:])
    assert before == {s["id"]: s["drafts"] for s in state["outline"]["sections"] if s["id"] in before}
    reads = [e for e in events if e.get("tool") == "get_analysis_claims" and e.get("status") == "completed"]
    assert reads and all("offset" in e and "claims_delivered" in e for e in reads)
    if expected == 7:
        assert any(e["claims_delivered"] == 0 and e["claims_returned"] > 0 for e in reads)


def test_real_additive_permissions_protect_all_nine_saved_sections_and_allow_section_31():
    from tests.ennoscholar_v4.test_run_dcf5d05b import tools
    from tests.ennoscholar_v4.test_run_95f3507d import refused_proposal
    state, recorded = real_trace()
    before = {s["id"]: copy.deepcopy(s["drafts"]) for s in state["outline"]["sections"] if s.get("drafts")}
    assert len(before) == 9
    args = TurnGoalArgs.model_validate(json.loads(recorded[0]["content"][0]["arguments"]))
    record_goal(state, args, state["messages"][-1]["content"], {})
    permissions = state["turn_goal"]["references"]["manuscript_permissions"]
    assert set(permissions["target_section_ids"]) == {SECTION, MISSING}
    toolkit = tools(state)
    _, _, proposal = refused_proposal()
    result = toolkit.execute("write_section", proposal, float("inf"))
    assert result["saved"], result
    assert before == {s["id"]: s["drafts"] for s in state["outline"]["sections"] if s["id"] in before}
    protected = copy.deepcopy(proposal)
    protected["section_id"] = next(iter(before))
    with pytest.raises(ValueError, match="protégée"):
        toolkit.execute("write_section", protected, float("inf"))


def test_real_bibliographic_passage_is_accounted_without_becoming_a_scientific_claim():
    from agents.EnnoScholarV4.models import Article
    from agents.EnnoScholarV4.scientific_reading import refresh_task
    state, _ = real_trace()
    eid = "ev_fa6c71640f0387f85952b2e1"
    task = next(t for t in state["scientific_reading"]["tasks"] if eid in t.get("examined_evidence_ids", []))
    article = Article.model_validate(state["articles"][task["article_id"]])
    before = copy.deepcopy(article.analysis)
    refresh_task(task, article)
    assert eid in task["bibliographic_evidence_ids"] and eid not in task["unincorporated_evidence_ids"]
    assert article.analysis == before


def receipt(output):
    return {"content": output, "usage": {"input_tokens": 100, "output_tokens": 50},
        "model_resolved": "gpt-5.6-sol", "response_id": "offline_response"}


def test_explicit_prohibition_stops_the_agent_before_any_reading_or_comparison_call():
    state = planned()
    state["messages"] = [{"id": "m", "role": "user", "content": REQUEST + " Ne rédige rien."}]
    before = copy.deepcopy(state["outline"])
    client, providers = Replay([receipt(call("set_turn_goal", goal_args().model_dump(), 1))]), sources()
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=client, sources=providers).run(state)
    assert result["reason"] == "permission_conflict" and result["diagnostic"]["required_tool"] == "write_section"
    assert len(client.calls) == 1 and not providers.mock_calls and state["outline"] == before


def test_an_internal_understanding_failure_is_not_swallowed_and_retried_as_a_paid_conversation():
    state = planned()
    state["messages"] = [{"id": "m", "role": "user", "content": REQUEST}]
    client = Replay([])
    def fail(**kw):
        client.calls.append(kw)
        raise IndexError("do-not-expose-this-secret")
    client.create = fail
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=client, sources=sources()).run(state)
    assert len(client.calls) == 1 and result["reason"] == "internal_error"
    assert result["diagnostic"]["frames"] and "do-not-expose" not in json.dumps(result)


def test_known_missing_comparison_prerequisites_cost_no_comparison_call_across_continuations(monkeypatch):
    from agents.EnnoScholarV4.scientific_reading import comparison_blockers
    from agents.EnnoScholarV4.scientific_reading import source_rows, bibliography_only, refresh_task
    from agents.EnnoScholarV4.models import Article
    state, recorded = real_trace()
    monkeypatch.setattr(module, "PARALLEL_SECTIONS", 1)
    # A known, still pending scientific passage, distinct from bibliography. The
    # existing cards remain readable; the comparison tool's own prerequisite fails.
    state["scientific_reading"].pop("blocked_reason", None)
    for task in state["scientific_reading"]["tasks"]:
        article = Article.model_validate(state["articles"][task["article_id"]])
        used = {r["evidence_id"] for r in article.analysis.get("evidence", [])}
        row = next((r for r in source_rows(article) if r["evidence_id"] not in used and not bibliography_only(r)), None)
        if row:
            task.setdefault("examined_evidence_ids", []).append(row["evidence_id"])
            task["reviewed_without_result_evidence_ids"] = [eid for eid in task.get("reviewed_without_result_evidence_ids", [])
                if eid != row["evidence_id"]]
        task.update(core_covered=False, coverage_sufficient=False, exhausted=False, status="awaiting_analysis")
        refresh_task(task, article)
    before = copy.deepcopy(state["outline"])
    client, providers = Replay(recorded[:1]), sources()
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False),
        client=client, sources=providers).run(state)
    assert result["reason"] == "offline_replay_complete", result.get("diagnostic")
    assert len(client.calls) == 1 and not providers.mock_calls
    assert client.next_request["tool_choice"] != {"type": "function", "name": "compare_articles"}
    assert not any(t.get("name") == "compare_articles" for t in client.next_request["tools"])
    outcomes = copy.deepcopy(state["comparison_outcomes"])
    blocked = [r for r in outcomes.values() if r.get("status") == "prerequisites_missing"]
    assert blocked and comparison_blockers(state, blocked[0]["article_ids"])
    progress = state["document"]["writing_progress"]
    state["writing_segment"] = {"job_id": "offline_job", "request_message_id": REQUEST_ID,
        "progress_id": progress["id"], "target_ids": progress["target_ids"], "plan_revision": progress["plan_revision"],
        "goal": copy.deepcopy(state["turn_goal"]), "blocked": {}}
    continued = Replay([])
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False),
        client=continued, sources=providers).run(state)
    assert not continued.calls and state["comparison_outcomes"] == outcomes
    assert continued.next_request["tool_choice"] != {"type": "function", "name": "set_turn_goal"}
    assert continued.next_request["tool_choice"] != {"type": "function", "name": "compare_articles"}
    assert state["outline"] == before and not providers.mock_calls


def test_an_internal_parallel_failure_keeps_finished_siblings_and_its_technical_receipt():
    from tests.ennoscholar_v4.test_task_results import PlanClient, plan_run
    from tests.ennoscholar_v4.test_turn_goal import linked_state
    from tests.ennoscholar_v4.test_performance import memory
    class BrokenPart(PlanClient):
        def create(self, **kw):
            if kw["tool_choice"] == {"type": "function", "name": "propose_plan_sections"}:
                if memory(kw)["plan_part"]["lock_ids"] == ["4"]:
                    raise IndexError("secret-not-to-store")
            return super().create(**kw)
    state = linked_state("Construis un plan sans rédiger.")
    result = plan_run(state, BrokenPart())
    assert result["reason"] == "internal_error" and result["diagnostic"]["category"] == "internal"
    parts = [t for t in state["task_results"].values() if t["kind"] == "plan_part"]
    assert sum(t["status"] == "saved" for t in parts) == 4
    failed = next(t for t in parts if t["status"] == "failed")
    assert failed["attempts"][-1]["diagnostic"]["category"] == "internal"
    assert "secret-not-to-store" not in json.dumps(state)
    assert state["outline"]["generation"]["complete"] is False
