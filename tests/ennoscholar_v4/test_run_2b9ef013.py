"""Run 2b9ef013: a comparison request recorded project facts of an earlier message.

The consultant asked to compare LDPoly and HoliTracer from stored analyses only. The run
opened a project_facts stage on message 8ab62b91 (the earlier section-7 request, which
says « mon projet ») and recorded two facts from it. The current request decides; earlier
messages are context. Offline: the real session is read from the local base in read-only
mode, the model is simulated, nothing is written to the base.
"""
import copy
import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

from agents.EnnoScholarV4.argumentation import project_observation_source
from tests.ennoscholar_v4.test_audit_91590987 import DB, real_state

RUN = "2b9ef013424649038f791ddd3b97922a"
CURRENT = "8cb8a37cbc4f4ec7be617a389f1ffed0"      # the comparison request of the run
EARLIER = "8ab62b91bb7d4113b7571faa9b6f3c69"      # the section-7 request (« mon projet »)


def run_row():
    if not DB.is_file():
        pytest.skip("Base locale absente")
    db = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    row = db.execute("select data from scholar_v4_runs where id = ?", (RUN,)).fetchone()
    db.close()
    if not row:
        pytest.skip("Run 2b9ef013 absent")
    return json.loads(row[0])


def state_at_start():
    """The session as run 2b9ef013 found it: its answer removed, and the two facts it
    recorded from the earlier message removed (the facts recorded before are kept as stored)."""
    state = real_state()
    # Later runs of the same conversation (6c6af07b, ce08ad31…) are removed with their answers.
    index = next(i for i, m in enumerate(state["messages"]) if m.get("role") == "assistant" and m.get("run_id") == RUN)
    later_answers = sum(m.get("role") == "assistant" for m in state["messages"][index+1:])
    state["messages"] = state["messages"][:index]
    assert state["messages"][-1]["id"] == CURRENT
    history = state.get("response_history", [])
    del history[max(0, len(history)-later_answers-1):]
    state["last_response"] = copy.deepcopy(history[-1]) if history else {}
    state["project_facts"] = [f for f in state.get("project_facts", []) if f["source_id"] != EARLIER]
    for key in ("argumentation_focus", "writing_focus", "writing_planning", "turn_goal", "automatic_reading_focus", "selection_focus"):
        state.pop(key, None)
    return state


def test_the_recorded_run_opened_a_project_facts_stage_on_an_earlier_message():
    """Reproduction of the defect from the run's own trace (read-only)."""
    data = run_row()
    assert data["tool_counts"].get("record_project_facts") == 1
    assert any(e.get("tool") == "argumentation" and e.get("stage") == "project_facts" for e in data["activity"])
    state = real_state()
    assert {f["source_id"] for f in state["project_facts"]} >= {EARLIER}
    assert "mon projet" not in next(m for m in state["messages"] if m["id"] == CURRENT)["content"]


def test_the_current_comparison_request_records_no_fact_of_an_earlier_message():
    state = state_at_start()
    assert project_observation_source(state) is None


def test_a_current_request_with_project_observations_is_still_recorded():
    state = state_at_start()
    state["messages"].append({"id": "new", "role": "user", "content":
        "Dans mon projet, l'essai 5 donne des polygones sans auto-intersection ; compare-le à LDPoly."})
    assert project_observation_source(state) == "new"


def test_an_explicit_request_to_take_up_earlier_observations_remains_possible():
    state = state_at_start()
    state["messages"].append({"id": "resume", "role": "user", "content":
        "Enregistre les faits du projet que j'ai donnés dans mon message précédent sur la section 7."})
    assert project_observation_source(state) == EARLIER
    # Once recorded, the same earlier message is not proposed again.
    state["project_facts"].append({"id": "f", "source_id": EARLIER})
    assert project_observation_source(state) != EARLIER


class Probe:
    """Simulated provider: the real understanding of the run, then the first exploration
    request is captured and the run stops (no further model call is needed)."""
    def __init__(self, goal):
        self.goal, self.requests = goal, []

    def with_options(self, **kw):
        return self

    @property
    def responses(self):
        return self

    def create(self, **kw):
        from tests.ennoscholar_v4.test_finalization import Stream, call
        if kw.get("tool_choice") == {"type": "function", "name": "set_turn_goal"}:
            return Stream(NS(output=call("set_turn_goal", self.goal, 1), output_text="", status="completed",
                usage={"input_tokens": 10, "output_tokens": 5}))
        self.requests.append(kw)
        raise RuntimeError("probe: first exploration request captured")


def test_replaying_run_2b9ef013_starts_no_project_facts_stage_and_preserves_the_manuscript():
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.config import ScholarV4Config
    from tests.ennoscholar_v4.replay_b52f582a import sources
    state = state_at_start()
    # The real understanding of this run (its stored task receipt).
    goal = next(json.loads(item["arguments"]) for task in state["task_results"].values()
        if task["kind"] == "understanding" and task["target"] == CURRENT
        for attempt in task["attempts"] for item in attempt["content"] if item.get("name") == "set_turn_goal")
    assert goal["deliverable"] == "comparison" and goal["reading_depth"] == "none"
    facts = copy.deepcopy(state["project_facts"])
    evidence = copy.deepcopy(state.get("evidence", {}))
    drafts = {s["id"]: [d["id"] for d in s.get("drafts", [])] for s in state["outline"]["sections"]}
    assert len(drafts) == 14 and all(drafts.values())
    events, probe = [], Probe(goal)
    ScholarChatAgent(ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False),
        client=probe, sources=sources()).run(state, emit=events.append)
    # The real run's first exploration request forced record_project_facts; now it does not.
    assert not any(e.get("tool") == "argumentation" and e.get("stage") == "project_facts" for e in events)
    assert all(kw.get("tool_choice") != {"type": "function", "name": "record_project_facts"} for kw in probe.requests)
    assert all("record_project_facts" not in {t.get("name") for t in kw.get("tools", [])} for kw in probe.requests[:1])
    # Facts, proofs and the 14 sections are untouched.
    assert state["project_facts"] == facts
    assert all(state["evidence"][k] == v for k, v in evidence.items())
    assert {s["id"]: [d["id"] for d in s.get("drafts", [])] for s in state["outline"]["sections"]} == drafts
