"""Second Codex review of automatic drafting: offline, no provider, temporary databases.

1. Every segment sends the model the consultant's complete original request (not the last
   assistant answer), with the constraints at the end of a long request.
2. A backend stopped after the first run was created but before the progress was opened is
   resumed from the original request (same job, same budget, spending kept).
3. The watchdog starts with FastAPI, without any request.
4. The watchdog rediscovers active jobs from the database; several processes never resume
   the same job concurrently.
5. The configuration fingerprint covers the effective model routing and the price catalogue.
"""
import copy
import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from sqlalchemy.orm import sessionmaker

from agents.EnnoScholarV4 import writing_job
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.session import ScholarV4Run, ScholarV4Session
from tests.ennoscholar_v4.test_writing_job import (FULL, SECTIONS, Writer, data_of, drafts, ledger_total, planned_session,
    runs_of, service, wait_job)

TAIL = ("Contrainte finale indispensable : terminer chaque section par un paragraphe « Limites pour le projet » "
    "qui cite le verrou 2 et reste en anglais.")


# --- 1. The complete original request reaches the model ----------------------------------------

def long_request():
    body = " ".join(f"Point {i} : comparer précisément méthodes, protocoles, métriques et limites des travaux retenus."
        for i in range(60))
    return "Rédige l’état de l’art complet en suivant le plan. " + body + " " + TAIL


def test_model_context_sends_the_original_request_not_the_last_assistant_answer():
    from agents.EnnoScholarV4.context import model_context
    from tests.ennoscholar_v4.test_context_memory import rich_corpus
    t, ids = rich_corpus(2)
    request = long_request()
    t.state["messages"] = [{"id": "m1", "role": "user", "content": request},
        {"id": "a1", "role": "assistant", "content": "Brouillon partiel enregistré : 2/5 sections."},
        {"id": "a2", "role": "assistant", "content": "Brouillon partiel enregistré : 4/5 sections."}]
    t.state["writing_segment"] = {"request_message_id": "m1"}
    inputs = model_context(t.state, t, 60000)
    messages = [item for item in inputs if item.get("role") in {"user", "assistant"}]
    assert messages[-1] == {"role": "user", "content": request}                         # complete, constraint at the end
    assert not any("Brouillon partiel" in m["content"] for m in messages)               # later automatic answers left out
    t.state.pop("writing_segment")
    assert [m for m in model_context(t.state, t, 60000) if m.get("role") in {"user", "assistant"}][-1]["role"] == "assistant"


def test_the_real_agent_sends_the_complete_request_in_every_continuation():
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.writing_progress import open_writing
    from tests.ennoscholar_v4.replay_b52f582a import sources
    from tests.ennoscholar_v4.test_run_dcf5d05b import Model, SECTION_1, before_run, tools
    state, proposals, goal_args = before_run()
    request = [m for m in state["messages"] if m["role"] == "user"][-1]
    request["content"] = request["content"] + " " + long_request()                       # a long request, constraint last
    goal = tools(state).execute("set_turn_goal", goal_args, float("inf"))["goal"]
    state.pop("turn_goal", None)
    second = state["outline"]["sections"][1]["id"]
    progress = open_writing(state, "full", [SECTION_1, second])
    sent = []
    for n in range(2):                                                                   # two successive continuations
        state["messages"].append({"id": f"answer_{n}", "role": "assistant", "content": f"Brouillon partiel {n}."})
        state["writing_segment"] = {"job_id": "job_x", "request_message_id": request["id"], "progress_id": progress["id"],
            "target_ids": [SECTION_1, second], "plan_revision": state["outline"].get("plan_revision"), "goal": goal,
            "blocked": {}}
        model = Model(goal_args, proposals[-1])
        ScholarChatAgent(ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False),
            client=model, sources=sources()).run(state, emit=lambda e: None)
        state.pop("writing_segment", None)
        sent.extend(model.calls)
    assert sent
    for kw in sent:
        conversation = [item for item in kw["input"] if item.get("role") in {"user", "assistant"}]
        assert conversation and conversation[-1] == {"role": "user", "content": request["content"]}
        assert TAIL in json.dumps(kw["input"], ensure_ascii=False)
        assert not any(item.get("content", "").startswith("Brouillon partiel") for item in conversation)


# --- 2. Interrupted initialisation -----------------------------------------------------------------

def test_a_crash_before_the_progress_exists_is_resumed_from_the_original_request(service):
    build, factory = service
    Writer.per_run = 2
    svc = build()
    sid = planned_session(svc, factory)
    rid = svc.open_run(1, sid, FULL)                         # first run created; the process stops before the agent
    from agents.EnnoScholarV4.job_ledger import JobMeter
    meter = JobMeter(factory, sid, rid, 10.0, writing_job.EMPTY)
    meter.ledger.settle(meter.admit("understanding", "m", "m", "gpt-5.6-sol", 0.4, {}), 0.3)   # spent before the crash
    with factory() as db:
        db.get(ScholarV4Run, rid).status = "running"
        db.get(ScholarV4Session, sid).lease_until = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    assert "writing_progress" not in data_of(factory, sid).get("document", {})
    job_id = data_of(factory, sid)["writing_job"]["id"]
    restarted = build()
    restarted.recover_all_jobs()
    job = wait_job(factory, sid)
    assert job["status"] == "completed" and job["id"] == job_id and job["budget_usd"] == 10.0
    assert job["segments"][0]["reason"] == "worker_interrupted" and job["segments"][0]["spent"]["confirmed_usd"] == 0.3
    assert job["spent"]["confirmed_usd"] == pytest.approx(0.3 + SECTIONS * Writer.cost)
    assert all(len(ids) == 1 for ids in drafts(data_of(factory, sid)).values())
    users = [m for m in data_of(factory, sid)["messages"] if m["role"] == "user"]
    assert [m["content"] for m in users] == [FULL]                                         # resumed from it, no new message
    assert all(row["request_id"] == users[0]["id"] for row in Writer.log)


def test_a_request_the_model_did_not_read_as_a_draft_is_still_not_applicable(service, monkeypatch):
    build, factory = service

    class Answer(Writer):
        def run(self, data, emit, cancelled):
            Writer.log.append({"answered": True})
            return {"answer": "Réponse sans rédaction.", "partial": False}
    svc = writing_job_service(build, Answer)
    sid = planned_session(svc, factory)
    svc.start(1, NS(session_id=sid, message=FULL))
    job = wait_job(factory, sid)
    assert job["status"] == "not_applicable" and len(Writer.log) == 1


def writing_job_service(build, agent):
    svc = build()
    svc.agent_factory = agent
    return svc


def test_repeated_interrupted_initialisations_stop_explicitly_without_a_new_budget(service):
    build, factory = service
    svc = build()
    sid = planned_session(svc, factory)
    svc.open_run(1, sid, FULL)
    for _ in range(3):
        with factory() as db:
            row = db.get(ScholarV4Session, sid)
            run = db.get(ScholarV4Run, row.active_run_id)
            run.status = "running"
            row.lease_until = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        svc.recover_expired(1, sid)
        job = svc.after_run(1, sid, data_of(factory, sid)["writing_job"]["current_run_id"], dispatch=False)
        if job["status"] in writing_job.FINAL:
            break
        svc.open_run(1, sid, None, job_id=job["id"], executor="thread")
    assert job["status"] == "failed" and job["budget_usd"] == 10.0 and len(job["segments"]) == 2
    assert job["no_progress"] == 0 and job["technical_failures"] == 2
    assert "techniques" in job["error"] and "scientifique" in job["error"]


# --- 3. Watchdog at FastAPI start-up -------------------------------------------------------------

def test_the_watchdog_starts_with_fastapi_and_resumes_a_job_without_any_request(service, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import db.database
    from agents.EnnoScholarV4 import agent as agent_module, service as module
    from routers import scholar_v4
    build, factory = service
    Writer.per_run = 2
    svc = build()
    sid = planned_session(svc, factory)
    rid = svc.open_run(1, sid, FULL)
    svc.slots.acquire()
    svc.after_run, original = (lambda *a, **k: None), svc.after_run
    svc.execute(1, sid, rid, driven=True)                     # first segment done; the backend then stops
    svc.after_run = original
    svc.after_run(1, sid, rid, dispatch=False)
    assert data_of(factory, sid)["writing_job"]["status"] == "waiting"
    # A restarted backend process: its own service, built by FastAPI's start-up.
    monkeypatch.setattr(db.database, "SessionLocal", factory)
    monkeypatch.setattr(agent_module, "ScholarChatAgent", Writer)
    monkeypatch.setattr(module, "_service", None)
    monkeypatch.setattr(module, "WATCHDOG_FIRST_DELAY", .05)
    monkeypatch.setattr(module, "WATCHDOG_INTERVAL", .05)
    monkeypatch.setenv("OPENAI_API_KEY", "mock")
    monkeypatch.setenv("ENNOSCHOLAR_V4_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("ENNOSCHOLAR_V4_PROMPT_CACHE_MODE", svc.config.prompt_cache_mode)
    app = FastAPI()
    app.include_router(scholar_v4.router)
    try:
        with TestClient(app):                                 # start-up only: no EnnoScholar request at all
            job = wait_job(factory, sid)
    finally:
        module.stop_watchdog()
        if module._service is not None:
            module._service.pool.shutdown(wait=True)
    assert job["status"] == "completed" and len(job["segments"]) == 3


# --- 4. Discovery from the database and several processes ------------------------------------------

def test_the_watchdog_rediscovers_jobs_from_the_database_and_two_processes_resume_once(service):
    build, factory = service
    Writer.per_run = 1
    svc = build()
    sid = planned_session(svc, factory)
    rid = svc.open_run(1, sid, FULL)
    svc.slots.acquire()
    svc.after_run, original = (lambda *a, **k: None), svc.after_run
    svc.execute(1, sid, rid, driven=True)
    svc.after_run = original
    svc.after_run(1, sid, rid, dispatch=False)
    # Two other processes: neither knows the job in memory (it was opened elsewhere).
    first, second = build(), build()
    assert not first._job_sessions and not second._job_sessions
    ticks = [threading.Thread(target=process.watchdog_tick, kwargs={"rescan": True}) for process in (first, second)]
    for tick in ticks:
        tick.start()
    for tick in ticks:
        tick.join(10)
    job = wait_job(factory, sid)
    assert job["status"] == "completed" and len(job["segments"]) == SECTIONS
    written = [s for row in Writer.log for s in row["written"]]
    assert sorted(written) == sorted(set(written)) and len(written) == SECTIONS            # no double segment
    assert ledger_total(factory, sid)["confirmed_usd"] == pytest.approx(SECTIONS * Writer.cost)


def test_a_dispatch_claimed_by_another_process_is_not_repeated(service):
    build, factory = service
    Writer.per_run, Writer.block = 1, threading.Event()
    svc = build()
    sid = planned_session(svc, factory)
    rid = svc.open_run(1, sid, FULL)
    svc.slots.acquire()
    Writer.block.set()
    svc.after_run, original = (lambda *a, **k: None), svc.after_run
    svc.execute(1, sid, rid, driven=True)
    svc.after_run = original
    svc.after_run(1, sid, rid, dispatch=False)
    job = data_of(factory, sid)["writing_job"]
    first, second = build(), build()
    assert first.claim_dispatch(1, sid, job["id"]) is True
    assert second.claim_dispatch(1, sid, job["id"]) is False                              # another process holds it
    assert first.claim_dispatch(1, sid, job["id"]) is True                                # its own claim is renewable


# --- 5. Configuration fingerprint ---------------------------------------------------------------

def test_the_fingerprint_covers_model_routing_and_the_price_catalogue(tmp_path):
    from agents.EnnoScholarV4.writing_job import fingerprint, mismatch
    base = ScholarV4Config(api_key="mock")
    reference = fingerprint(base, "sqlite:///x")
    other_writer = fingerprint(replace(base, task_models=json.dumps({"writing": "gpt-6.1-sol"})), "sqlite:///x")
    assert mismatch(reference, other_writer) == []                           # different drafting model
    # Same catalogue version, different prices: still incompatible.
    from agents.EnnoScholarV4.pricing import DEFAULT_CATALOG
    raw = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
    raw["prices"][0]["output"] = 99.0
    altered = tmp_path / "catalog.json"
    altered.write_text(json.dumps(raw), encoding="utf-8")
    assert mismatch(reference, fingerprint(replace(base, pricing_file=str(altered)), "sqlite:///x")) == ["price_catalog"]
    assert mismatch(reference, fingerprint(base, "sqlite:///x")) == []
