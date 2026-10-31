"""Safety of the minimal real trial « Finalise uniquement la section 1 » (block 1).

Launcher: only per-run caps set in the launching shell and lower than the V4
defaults reach the backend. Then, on a copy of the real lithium session with its
plan NOT approved, the caps are applied, kept after a restart, and bound the
number of Responses calls whatever the scripted Sol does. Responses SDK with a
simulated transport whose usage is derived from the size of each request.
"""
import copy
import gzip
import json
import os
import httpx
import pytest
from sqlalchemy import select
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV5.config import runtime_config
from agents.EnnoScholarV5.persistence import Operation, RunMeter, Budget
from agents.EnnoScholarV5.startup import RUN_CAP_FIELDS
from experiments.ennoscholar_v5.progress import Scope
from .test_graph import ARTIFACTS
from .test_startup import local_install, configure
from .test_live_integration import app_env, call, run_sync, scientific_state

TRIAL_CAPS = {"ENNOSCHOLAR_V4_MAX_TOKENS": "150000", "ENNOSCHOLAR_V4_MAX_COST_USD": "0.5",
    "ENNOSCHOLAR_V4_MAX_TOOL_CALLS": "6", "ENNOSCHOLAR_V4_TIMEOUT_SECONDS": "240",
    "ENNOSCHOLAR_V4_MAX_WEB_CALLS": "1", "ENNOSCHOLAR_V4_WEB_SEARCH_ENABLED": "false"}
REQUEST = "Finalise uniquement la section 1"


# --- 1. Launcher: transmission of lowered caps only ---------------------------------------

def test_lowered_run_caps_from_the_shell_reach_the_backend(local_install):
    environ = {**TRIAL_CAPS, "ENNOSCHOLAR_V4_REASONING_EFFORT": "high"}
    info = configure(local_install, environ)
    assert {k: environ[k] for k in TRIAL_CAPS} == {"ENNOSCHOLAR_V4_MAX_TOKENS": "150000", "ENNOSCHOLAR_V4_MAX_COST_USD": "0.5",
        "ENNOSCHOLAR_V4_MAX_TOOL_CALLS": "6", "ENNOSCHOLAR_V4_TIMEOUT_SECONDS": "240",
        "ENNOSCHOLAR_V4_MAX_WEB_CALLS": "1", "ENNOSCHOLAR_V4_WEB_SEARCH_ENABLED": "false"}
    assert "ENNOSCHOLAR_V4_REASONING_EFFORT" not in environ  # Any other V4 setting is still removed.
    assert info["run_caps"]["max_tokens"] == "150000" and info["run_caps"]["web_search_enabled"] == "false"
    # The shared profile's ENNOSCHOLAR_V4_MAX_COST_USD=100 never crosses: only the shell value does.
    assert environ["ENNOSCHOLAR_V4_MAX_COST_USD"] == "0.5"


def test_without_shell_caps_the_v4_defaults_stay(local_install):
    environ = {}
    info = configure(local_install, environ)
    assert info["run_caps"] == {} and not any(k.startswith("ENNOSCHOLAR_V4_MAX") for k in environ)


@pytest.mark.parametrize("key,value", [("ENNOSCHOLAR_V4_MAX_TOKENS", "500000"), ("ENNOSCHOLAR_V4_MAX_COST_USD", "5"),
    ("ENNOSCHOLAR_V4_WEB_SEARCH_ENABLED", "true")])
def test_a_raised_cap_is_ignored_and_reported_never_applied(local_install, key, value):
    environ = {key: value, "KEEP": "1"}
    info = configure(local_install, environ)
    assert key not in environ and environ["KEEP"] == "1"  # The V4 default applies, as before.
    assert RUN_CAP_FIELDS[key] in info["ignored_run_caps"] and RUN_CAP_FIELDS[key] not in info["run_caps"]


@pytest.mark.parametrize('key,value',[('ENNOSCHOLAR_V4_MAX_TOOL_CALLS','0'),('ENNOSCHOLAR_V4_MAX_TOKENS','1.5'),
    ('ENNOSCHOLAR_V4_TIMEOUT_SECONDS','nan'),('ENNOSCHOLAR_V4_MAX_WEB_CALLS','-1'),
    ('ENNOSCHOLAR_V4_MAX_COST_USD','dix'),('ENNOSCHOLAR_V4_MAX_WEB_CALLS','0')])
def test_invalid_caps_are_refused_without_environment_mutation(local_install,key,value):
    environ={key:value,'KEEP':'1'}
    with pytest.raises(ValueError):configure(local_install,environ)
    assert environ=={key:value,'KEEP':'1'}


# --- 2. Applied, and kept after a restart ----------------------------------------------------

def trial_config(local_install, monkeypatch):
    environ = dict(TRIAL_CAPS)
    configure(local_install, environ)
    for key in [k for k in os.environ if k.startswith("ENNOSCHOLAR_V4_") or k == "ENNOSCHOLAR_V5_MISSION_BUDGET_USD"]:
        monkeypatch.delenv(key)
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    return runtime_config(ScholarV4Config.from_env())


def test_caps_are_applied_to_the_run_and_survive_a_restart(local_install, monkeypatch, app_env):
    config = trial_config(local_install, monkeypatch)
    assert (config.max_tokens, config.max_cost_usd, config.max_tool_calls, config.timeout_seconds, config.max_web_calls,
        config.web_search_enabled) == (150000, 0.5, 6, 240, 1, False)
    config.validate()  # Accepted by V4 as it is at the first request (run 1 of the trial failed here before).
    assert not config.writing_job_enabled  # No mission without an authorised mission budget.
    make, factory, _ = app_env
    svc, sid, tr = make([], config=config)
    rid = svc.open_run(1, sid, REQUEST)
    meter = RunMeter(svc, Scope(1, sid), rid, svc.config)
    assert meter.limits == {"usd": 0.5, "tokens": 150000, "tools": 6, "seconds": 240, "web": 1}
    # A restarted process with the default (higher) ceilings resumes the same frozen limits.
    restarted = RunMeter(svc, Scope(1, sid), rid, ScholarV4Config(api_key="offline"))
    assert restarted.limits == meter.limits
    with factory() as db:
        assert db.get(Budget, rid).data["limits"] == meter.limits


# --- 3. Maximum number of Responses calls, whatever Sol does ----------------------------------

def trial_session():
    with gzip.open(ARTIFACTS/"real-lithium-session.json.gz", "rt", encoding="utf-8") as f:
        state = json.load(f)["state"]
    state["outline"].pop("approval", None)  # The test session's plan is NOT approved.
    state["outline"]["status"] = "proposed"
    return state


def metered(choice, divisor):
    """Usage from the request actually sent: about one token per `divisor` characters
    (4 is typical for this JSON; 6 under-counts tokens, so it allows more calls)."""
    def respond(sent):
        out = choice(sent)
        output = out if isinstance(out, list) else [out]
        response = {"id": "resp_t", "object": "response", "created_at": 1, "model": "gpt-6.1-sol", "service_tier": "default",
            "status": "completed", "output": output, "usage": {"input_tokens": len(json.dumps(sent["input"], ensure_ascii=False))//divisor,
                "output_tokens": 300, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 100}}}
        event = {"type": "response.completed", "sequence_number": 1, "response": response}
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
            text="event: response.completed\ndata: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")
    return respond


def section_ids(state):
    return [s["id"] for s in state["outline"]["sections"]]


def behaviours(state):
    s1, s2 = section_ids(state)[:2]
    claim = state["articles"]["paper_a39c5c7db2f6111f9728"]["analysis"]["results"][0]
    save = lambda sid, tag: call("write_section", {"section_id": sid, "action": "save", "instructions": "Finaliser.",
        "text": claim["statement"] + " [paper_a39c5c7db2f6111f9728] [" + claim["evidence_ids"][0] + "]",
        "article_ids": ["paper_a39c5c7db2f6111f9728"], "evidence_ids": claim["evidence_ids"][:1]}, tag)
    return {
        # Sol writes a text instead of calling a tool, again and again.
        "text_only": lambda sent: {"type": "message", "role": "assistant", "id": "m", "status": "completed",
            "content": [{"type": "output_text", "text": "Je finalise la section 1.", "annotations": []}]},
        # Sol keeps reading the section's sources.
        "reads": lambda sent: call("analyze_article", {"article_ids": ["paper_a39c5c7db2f6111f9728"], "action": "read",
            "research_context": "Section 1.", "passage_query": "results", "passage_offset": 0, "passages_per_article": 2,
            "analysis": None, "revisit": True}, "r"),
        # Sol tries to save section 1, then section 2 (outside the scope).
        "saves": lambda sent: save(s1 if not any(i.get("call_id") == "w1" for i in sent["input"]) else s2,
            "w1" if not any(i.get("call_id") == "w1" for i in sent["input"]) else "w2"),
        # Sol declares a draft goal over every planned work (reconcile_draft_goal path).
        "wide_goal": lambda sent: call("set_turn_goal", {"requested_article_count": None, "deliverable": "draft", "lock_ids": [],
            "focus_lock_ids": [], "exact_quote": "", "reading_depth": "core", "understanding": "Rédiger tout l'état de l'art.",
            "comparison_requested": None, "search_needed": True, "article_ids": [a for s in state["outline"]["sections"]
                for a in s["article_ids"]][:12], "article_scope": "exclusive", "references_ambiguous": False, "constraints": None,
            "research_need": None}, "g"),
    }


calls = []


@pytest.mark.parametrize("divisor", [4, 6])
@pytest.mark.parametrize("behaviour", ["text_only", "reads", "saves", "wide_goal"])
def test_the_trial_stays_budget_bounded_never_drafts_nor_opens_a_mission(local_install, monkeypatch, app_env, behaviour, divisor):
    config = trial_config(local_install, monkeypatch)
    make, factory, _ = app_env
    state = trial_session()
    choice = behaviours(state)[behaviour]
    svc, sid, tr = make([metered(choice, divisor)]*20, state, config=config)
    result = run_sync(svc, sid, REQUEST)
    data = scientific_state(factory, sid)
    assert not str(result.get("reason")).startswith("v5_interrupted"), result  # The run really ran.
    # Bounded calls, within the dollar and token caps.
    calls.append((behaviour, divisor, len(tr.requests), result.get("reason"), result["usage"]["input_tokens"],
        result["usage"]["estimated_cost_usd"]))
    print("TRIAL", calls[-1])
    # The reservation depends on the exact request, including tool schemas.
    # Compact schemas can admit a third call without raising any ceiling.
    # A fixed two-call expectation would enforce the arbitrary turn limit V5
    # explicitly removed. This deliberately idle script must still stop early.
    assert 1 <= len(tr.requests) < 20, calls[-1]
    assert result["usage"]["estimated_cost_usd"] <= 0.5 and result["usage"]["input_tokens"] + result["usage"]["output_tokens"] <= 150000
    assert result['usage']['tools'] <= config.max_tool_calls
    # No hosted web search was ever offered to Sol.
    assert not any(t.get("type") == "web_search" for r in tr.requests for t in r["tools"])
    # Scope: section 1 only; no mission; no section saved (plan not approved); others untouched.
    progress = data["document"]["writing_progress"]
    assert progress["mode"] == "section" and progress["target_ids"] == section_ids(state)[:1]
    assert not data.get("writing_job")
    assert [s.get("drafts") for s in data["outline"]["sections"]] == [s.get("drafts") for s in state["outline"]["sections"]]
    with factory() as db:
        saves = [o for o in db.scalars(select(Operation).where(Operation.kind == "action"))
            if o.request["tool"] == "write_section" and o.request["arguments"].get("action") == "save"]
    assert all(not o.response["output"].get("saved") for o in saves)
    assert {o.response["output"].get("rejection_code") for o in saves} <= {"plan_not_approved"} or all(
        "périmètre" in str(o.response["output"].get("error")) or o.response["output"].get("rejection_code") == "plan_not_approved"
        for o in saves)
    assert len([r for r in data.get("messages", []) if r["role"] == "user"]) == len([r for r in state["messages"] if r["role"] == "user"]) + 1


def test_a_mission_exhausted_at_the_trial_budget_could_not_continue():
    """No fixed .50 USD floor: admission uses actual reservations. A genuinely
    exhausted .40 USD mission still cannot refill itself in another segment."""
    from agents.EnnoScholarV5 import writing_job
    job = writing_job.new_job(ScholarV4Config(api_key="offline", writing_job_budget_usd=0.4), "m", "r", {})
    job["progress"] = {"id": "writing_x", "target_ids": ["s1"]}
    assert writing_job.can_start_segment(job) == (True,None)
    job['spent']['confirmed_usd'] = .4
    assert writing_job.can_start_segment(job) == (False, "budget_exhausted")
