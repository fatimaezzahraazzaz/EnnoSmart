"""Automatic full-manuscript drafting (writing_job.py, job_ledger.py): offline, no provider.

The simulated writer follows the real writing progress (begin_writing for the consultant's
request, resume_writing for a continuation) and spends through the real per-call ledger:
each section is one admitted call (protective reservation) settled at its cost. The
service, its leases, locks, checkpoints and the job are the real ones, on a temporary
SQLite database. Scenarios of the Codex audit are reproduced one by one.
"""
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db.models import User
from agents.EnnoScholarV4 import writing_job
from agents.EnnoScholarV4.config import ConfigurationError, ScholarV4Config
from agents.EnnoScholarV4.finance import BudgetRefused
from agents.EnnoScholarV4.job_ledger import totals
from agents.EnnoScholarV4.service import ScholarV4Service, SessionBusy
from agents.EnnoScholarV4.session import ScholarV4Run, ScholarV4Session

FULL = "Rédige l’état de l’art complet en suivant le plan, section par section."
SECTIONS = 5


def sid_of(n):
    return f"section_{n}aaaaaaaa"


class Writer:
    """Saves ``per_run`` sections per run (time limit reached), never one already saved.
    Each section is one provider call: admitted for ``reserve`` $, settled at ``cost`` $."""
    per_run, cost, reserve, refuse, block, crash_after, log = 2, 1.0, 1.2, (), None, None, None

    def __init__(self, config=None, **_):
        self.config, self.meter = config, None

    def run(self, data, emit, cancelled):
        from agents.EnnoScholarV4.instructions import applies_now, capture_instructions, current_request
        from agents.EnnoScholarV4.writing_progress import begin_writing, refresh_writing, resume_writing
        segment = data.get("writing_segment")
        request = current_request(data)
        capture_instructions(data, request)
        resuming = bool(segment and segment.get("progress_id"))
        progress = resume_writing(data, segment) if resuming else begin_writing(data, request["content"])
        if not resuming:
            data["turn_goal"] = {"deliverable": "draft", "reading_depth": "cards", "references": {"message_id": request["id"]}}
        written = []
        Writer.log.append({"request_id": request["id"], "segment": segment, "written": written,
            "max_cost_usd": getattr(self.config, "max_cost_usd", None), "targets": list((progress or {}).get("target_ids", [])),
            "instructions": {row["text"]: applies_now(data, row) for row in data.get("consultant_instructions", [])}})
        if Writer.block is not None:
            Writer.block.wait(10)
        if cancelled():
            return {"answer": "Interrompu.", "partial": True, "reason": "cancelled"}
        for sid in list((progress or {}).get("target_ids", [])):
            if len(written) >= Writer.per_run:
                break
            if sid in progress["saved_draft_ids"] or sid in progress.get("blocked", {}):
                continue
            try:
                ticket = self.meter.admit("section", sid, sid, "gpt-5.6-sol", Writer.reserve, {}) if self.meter else None
            except BudgetRefused:
                return {"answer": "Budget.", "partial": True, "reason": "budget_refused"}
            if Writer.crash_after is not None and not isinstance(Writer.crash_after, threading.Event)                     and len(written) == Writer.crash_after:
                dead = Writer.crash_after = threading.Event()
                dead.wait(10)                                   # the process « dies » with this call in flight
                try:                                            # woken long after: it may not spend any more
                    self.meter.admit("section", sid, sid, "gpt-5.6-sol", Writer.reserve, {})
                    Writer.log.append({"stale_admitted": True})
                except BudgetRefused:
                    Writer.log.append({"stale_refused": True})
                return {"answer": "Fin tardive.", "partial": True, "reason": "exploration_time_budget"}
            if ticket is not None:
                self.meter.ledger.settle(ticket, Writer.cost)
            if sid in Writer.refuse:
                task = data.setdefault("task_results", {}).setdefault("task_" + sid, {"kind": "section", "target": sid,
                    "status": "refused", "attempts": []})
                task["attempts"].append({"status": "refused"})
                progress.setdefault("errors", []).append({"section_id": sid, "message": "Attribution non vérifiable : X."})
                continue
            section = next(s for s in data["outline"]["sections"] if s["id"] == sid)
            section.setdefault("drafts", []).append({"id": uuid.uuid4().hex, "text": f"Texte {sid}"})
            written.append(sid)
            refresh_writing(data)
            emit({"tool": "write_section", "status": "completed"})
        progress = refresh_writing(data, final=True)
        done = progress["status"] == "draft_complete"
        return {"answer": "Brouillon complet." if done else "Brouillon partiel.", "partial": not done,
            "reason": None if done else "exploration_time_budget"}


@pytest.fixture
def service(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "jobs.sqlite"), connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(User(id=1, full_name="Test", email="test@example.org", hashed_password="x", is_active=True))
        db.commit()
    Writer.per_run, Writer.cost, Writer.reserve, Writer.refuse, Writer.block, Writer.crash_after, Writer.log = (
        2, 1.0, 1.2, (), None, None, [])
    made = []

    def build(**config):
        svc = ScholarV4Service(factory, replace(ScholarV4Config(api_key="mock"), **config), Writer)
        made.append(svc)
        return svc
    yield build, factory
    if isinstance(Writer.block, threading.Event):
        Writer.block.set()
    if isinstance(Writer.crash_after, threading.Event):
        Writer.crash_after.set()
    for svc in made:
        svc.pool.shutdown(wait=True)
    engine.dispose()


def planned_session(svc, factory, revision="r1"):
    sid = svc.create(1)["id"]
    with factory() as db:
        row = db.get(ScholarV4Session, sid)
        row.data = {**row.data, "outline": {"title": "État de l'art", "version": 1, "plan_revision": revision, "approval": None,
            "sections": [{"id": sid_of(i), "title": f"Section {i}", "article_ids": [], "drafts": []}
                for i in range(1, SECTIONS+1)]}}
        db.commit()
    return sid


def data_of(factory, sid):
    with factory() as db:
        return db.get(ScholarV4Session, sid).data


def runs_of(factory, sid):
    with factory() as db:
        return {r.id: (r.status, r.data) for r in db.scalars(select(ScholarV4Run).where(ScholarV4Run.session_id == sid))}


def wait_job(factory, sid, statuses=writing_job.FINAL | {"paused"}, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        job = data_of(factory, sid).get("writing_job")
        with factory() as db:
            busy = db.get(ScholarV4Session, sid).active_run_id
        if job and job["status"] in statuses and not busy:
            return job
        time.sleep(.02)
    raise AssertionError(f"Job not finished: {data_of(factory, sid).get('writing_job')}")


def drafts(data):
    return {s["id"]: [d["id"] for d in s.get("drafts", [])] for s in data["outline"]["sections"]}


def ledger_total(factory, sid):
    """Spending rebuilt from every run's durable ledger (independently of the job)."""
    total = {"confirmed_usd": 0, "reserved_usd": 0, "uncertain_usd": 0}
    for status, data in runs_of(factory, sid).values():
        for key, value in totals(data.get("ledger"), run_over=status not in {"queued", "running"}).items():
            total[key] += value
    return total


# --- Full draft, scope and request -----------------------------------------------------------

def test_a_full_request_is_written_to_the_end_in_several_segments_without_any_message_in_the_consultants_name(service):
    build, factory = service
    svc = build()
    sid = planned_session(svc, factory)
    svc.start(1, NS(session_id=sid, message=FULL))
    job = wait_job(factory, sid)
    data = data_of(factory, sid)
    assert job["status"] == "completed" and len(job["segments"]) == 3                     # 2 + 2 + 1 sections
    assert all(len(ids) == 1 for ids in drafts(data).values())                            # each section written once
    users = [m for m in data["messages"] if m["role"] == "user"]
    assert [m["content"] for m in users] == [FULL]                                         # no automatic user message
    # Every segment answered the consultant's request, with the goal of its first segment.
    assert {row["request_id"] for row in Writer.log} == {users[0]["id"]}
    assert all(row["segment"]["goal"]["deliverable"] == "draft" for row in Writer.log[1:])
    assert job["spent"] == {"confirmed_usd": 5.0, "reserved_usd": 0.0, "uncertain_usd": 0.0}
    assert ledger_total(factory, sid)["confirmed_usd"] == 5.0
    partial = [m["content"] for m in data["messages"] if m["role"] == "assistant" and m.get("partial")]
    assert not any("Demandez « continue" in text for text in partial)


def test_the_consultants_one_off_instructions_apply_to_every_segment():
    """Codex: « En anglais. » and « Ne modifie pas le plan. » became inactive once an
    automatic user message was inserted. Continuations keep the original request."""
    from agents.EnnoScholarV4.instructions import applies_now, capture_instructions, current_request
    state = {"messages": [{"id": "m1", "role": "user", "content": "Rédige tout l'état de l'art complet. En anglais. "
        "Ne modifie pas le plan."}]}
    capture_instructions(state, state["messages"][0])
    texts = {row["text"] for row in state["consultant_instructions"]}
    assert {"En anglais.", "Ne modifie pas le plan."} <= texts
    state["messages"].append({"id": "a1", "role": "assistant", "content": "Brouillon partiel enregistré : 2/5."})
    state["writing_segment"] = {"request_message_id": "m1"}
    assert current_request(state)["id"] == "m1"
    assert all(applies_now(state, row) for row in state["consultant_instructions"])
    # A later consultant message is a new request: outside a segment it takes over, as before.
    state["messages"].append({"id": "m2", "role": "user", "content": "Quelles sont les limites de l'article 2 ?"})
    assert all(applies_now(state, row) for row in state["consultant_instructions"])     # still the job's request
    state.pop("writing_segment")
    assert not any(applies_now(state, row) for row in state["consultant_instructions"])


def test_instructions_stay_active_through_the_service_segments(service):
    build, factory = service
    Writer.per_run = 2
    svc = build()
    sid = planned_session(svc, factory)
    svc.start(1, NS(session_id=sid, message=FULL + " En anglais. Ne modifie pas le plan."))
    assert wait_job(factory, sid)["status"] == "completed"
    assert len(Writer.log) == 3
    for row in Writer.log:
        assert row["instructions"].get("En anglais.") is True and row["instructions"].get("Ne modifie pas le plan.") is True


def test_a_bounded_request_resumes_sections_3_to_5_only_with_its_blocked_section(service):
    """Codex: the generic continuation re-targeted sections 1-5 and dropped the block."""
    build, factory = service
    Writer.per_run, Writer.refuse = 1, (sid_of(4),)
    svc = build(writing_job_max_section_attempts=2, writing_job_stalled_segments=5)
    sid = planned_session(svc, factory)
    svc.start(1, NS(session_id=sid, message="Rédige les sections 3 à 5 de l’état de l’art."))
    job = wait_job(factory, sid)
    data = data_of(factory, sid)
    assert job["progress"]["target_ids"] == [sid_of(3), sid_of(4), sid_of(5)]
    assert all(row["targets"] == [sid_of(3), sid_of(4), sid_of(5)] for row in Writer.log)   # never widened
    assert job["status"] == "failed" and list(job["blocked"]) == [sid_of(4)]
    assert not drafts(data)[sid_of(1)] and not drafts(data)[sid_of(2)]                         # outside the request
    assert drafts(data)[sid_of(3)] and drafts(data)[sid_of(5)] and not drafts(data)[sid_of(4)]
    # Once blocked after its 2 refused attempts, section 4 is not attempted again by a continuation.
    assert len(data["task_results"]["task_" + sid_of(4)]["attempts"]) == 2


def test_a_changed_plan_stops_the_job_before_any_call(service):
    build, factory = service
    Writer.per_run = 1
    svc = build()
    sid = planned_session(svc, factory)
    rid = svc.open_run(1, sid, FULL)
    svc.slots.acquire()
    svc.execute(1, sid, rid, driven=True)
    assert data_of(factory, sid)["writing_job"]["status"] == "waiting"
    with factory() as db:
        row = db.get(ScholarV4Session, sid)
        row.data = {**row.data, "outline": {**row.data["outline"], "plan_revision": "r2"}}
        db.commit()
    calls = len(Writer.log)
    svc.drive_job(1, sid, data_of(factory, sid)["writing_job"]["id"])
    job = data_of(factory, sid)["writing_job"]
    assert job["status"] == "failed" and "plan a changé" in job["error"]
    assert len(Writer.log) == calls                                                     # no segment agent ran
    assert sum(ledger_total(factory, sid).values()) == pytest.approx(1.0)              # only the first call


# --- Budget: durable, confirmed / reserved / uncertain ------------------------------------------

def test_a_crash_after_receipts_keeps_the_spending_and_the_call_in_flight_stays_reserved(service):
    """Codex: interrupted after a 1.25 $ receipt, before finish(): the job counted 0 $."""
    build, factory = service
    Writer.per_run, Writer.cost, Writer.reserve, Writer.crash_after = 3, 1.25, 1.5, 1
    svc = build()
    sid = planned_session(svc, factory)
    svc.start(1, NS(session_id=sid, message=FULL))
    for _ in range(200):                                                              # first receipt settled, second in flight
        entries = [e for _, data in runs_of(factory, sid).values() for e in data.get("ledger", [])]
        if len(entries) == 2:
            break
        time.sleep(.02)
    first = next(rid for rid, (status, _) in runs_of(factory, sid).items())
    assert [e["status"] for e in runs_of(factory, sid)[first][1]["ledger"]] == ["settled", "reserved"]
    # The process is gone; its lease expires. A restarted service takes over.
    with factory() as db:
        db.get(ScholarV4Session, sid).lease_until = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    dead = Writer.crash_after
    Writer.crash_after = None
    restarted = build()
    restarted.recover_all_jobs()
    job = wait_job(factory, sid)
    first_segment = job["segments"][0]
    assert first_segment["run_id"] == first and first_segment["reason"] == "worker_interrupted"
    # 1.25 $ confirmed and the in-flight call counted at its reservation (uncertain), never lost.
    assert first_segment["spent"] == {"confirmed_usd": 1.25, "reserved_usd": 0.0, "uncertain_usd": 1.5}
    assert job["spent"]["confirmed_usd"] == pytest.approx(1.25 * (SECTIONS))          # every section once
    assert job["spent"]["uncertain_usd"] == pytest.approx(1.5)
    assert job["status"] == "completed"
    # The interrupted call is never replayed blindly: its section was drafted by a new, admitted call.
    entries = runs_of(factory, sid)[first][1]["ledger"]
    assert [e["status"] for e in entries] == ["settled", "uncertain"] and entries[1]["uncertain_reason"] == "worker_interrupted"
    # When the « dead » thread wakes up, it can neither spend nor overwrite: lease lost, run closed.
    before = runs_of(factory, sid)[first][1]["ledger"]
    dead.set()
    for _ in range(200):
        if any("stale_refused" in row or "stale_admitted" in row for row in Writer.log):
            break
        time.sleep(.02)
    assert {"stale_refused": True} in Writer.log and {"stale_admitted": True} not in Writer.log
    time.sleep(.2)
    assert runs_of(factory, sid)[first][1]["ledger"] == before
    assert data_of(factory, sid)["writing_job"]["status"] == "completed"


def test_the_first_segment_is_capped_by_the_manuscript_budget_before_any_call(service):
    """Codex: with a 0.50 $ manuscript budget the first agent still received a 4 $ cap and a
    0.75 $ segment was counted after the overrun."""
    build, factory = service
    Writer.cost, Writer.reserve = 0.75, 0.75
    svc = build(writing_job_budget_usd=0.5, max_cost_usd=4.0)
    sid = planned_session(svc, factory)
    svc.start(1, NS(session_id=sid, message=FULL))
    job = wait_job(factory, sid)
    assert Writer.log[0]["max_cost_usd"] == pytest.approx(0.5)                          # capped from the first segment
    assert job["status"] == "budget_exhausted"
    spent = ledger_total(factory, sid)
    assert spent == {"confirmed_usd": 0, "reserved_usd": 0, "uncertain_usd": 0}         # refused before being sent
    assert not any(drafts(data_of(factory, sid)).values())


def test_the_manuscript_budget_holds_end_to_end_with_confirmed_reserved_and_uncertain(service):
    build, factory = service
    Writer.per_run, Writer.cost, Writer.reserve = 2, 0.8, 1.3
    svc = build(writing_job_budget_usd=3.0)
    sid = planned_session(svc, factory)
    svc.start(1, NS(session_id=sid, message=FULL))
    job = wait_job(factory, sid)
    spent = ledger_total(factory, sid)
    # Admission keeps confirmed + reserved + uncertain + the next reservation within the budget.
    assert spent["confirmed_usd"] + spent["uncertain_usd"] <= 3.0
    assert job["status"] == "budget_exhausted" and job["spent"]["confirmed_usd"] == pytest.approx(spent["confirmed_usd"])
    assert "engagés sur 3.00 $" in job["error"]
    assert data_of(factory, sid)["messages"][-1]["content"] == job["error"]
    # Never renewed automatically: same job, nothing after the stop.
    calls = len(Writer.log)
    time.sleep(.2)
    assert len(Writer.log) == calls and data_of(factory, sid)["writing_job"]["id"] == job["id"]


# --- Concurrency -------------------------------------------------------------------------------

def test_two_concurrent_finalizations_cannot_desynchronise_the_job(service, monkeypatch):
    """Codex: evaluation A reads the old state and waits; B evaluates and opens the next
    segment; A then saved its stale state (current_run_id lost, message erased)."""
    build, factory = service
    Writer.per_run = 1
    svc = build()
    sid = planned_session(svc, factory)
    rid = svc.open_run(1, sid, FULL)
    original_after_run = svc.after_run
    svc.after_run = lambda *a, **k: None                                               # finished, not yet evaluated
    svc.slots.acquire()
    svc.execute(1, sid, rid, driven=True)
    svc.after_run = original_after_run
    real = writing_job.evaluate

    def slow(*args, **kwargs):
        time.sleep(.6)                                                                # A holds the lock meanwhile
        return real(*args, **kwargs)
    monkeypatch.setattr(writing_job, "evaluate", slow)
    opened = {}

    def first():
        svc.after_run(1, sid, rid, dispatch=False)

    def second():
        time.sleep(.15)
        svc.after_run(1, sid, rid, dispatch=False)
        job = data_of(factory, sid)["writing_job"]
        opened["rid"] = svc.open_run(1, sid, None, job_id=job["id"], executor="thread")
    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    data = data_of(factory, sid)
    job = data["writing_job"]
    with factory() as db:
        active = db.get(ScholarV4Session, sid).active_run_id
    assert active == opened["rid"] == job["current_run_id"] and job["status"] == "running"
    assert [s["run_id"] for s in job["segments"]] == [rid]                             # evaluated once
    assert runs_of(factory, sid)[opened["rid"]][1]["segment"]["request_message_id"] == job["request_message_id"]


def test_a_second_driver_never_runs_a_segment_twice(service):
    build, factory = service
    Writer.per_run, Writer.block = 1, threading.Event()
    svc = build()
    sid = planned_session(svc, factory)
    svc.start(1, NS(session_id=sid, message=FULL))
    time.sleep(.2)                                                                     # first segment holds the lease
    job = data_of(factory, sid)["writing_job"]
    with pytest.raises(SessionBusy):
        svc.open_run(1, sid, None, job_id=job["id"])
    outcomes = []
    threads = [threading.Thread(target=lambda: outcomes.append(svc.drive_job(1, sid, job["id"]))) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert all(o["status"] == "running" and o["retry_after"] > 600 for o in outcomes)   # wait for the live lease
    Writer.block.set()
    job = wait_job(factory, sid)
    assert job["status"] == "completed" and len(job["segments"]) == SECTIONS
    written = [s for row in Writer.log for s in row["written"]]
    assert sorted(written) == sorted(set(written)) and len(written) == SECTIONS
    assert len(Writer.log) == SECTIONS                                                 # one writer call per segment


def test_a_stale_worker_cannot_spend_once_its_lease_is_taken_over(service):
    from agents.EnnoScholarV4.job_ledger import JobMeter
    build, factory = service
    svc = build()
    sid = planned_session(svc, factory)
    rid = svc.open_run(1, sid, FULL)
    meter = JobMeter(factory, sid, rid, 10.0, writing_job.EMPTY)
    meter.ledger.settle(meter.admit("section", "s", "s", "m", 1.0, {}), 0.5)
    with factory() as db:
        db.get(ScholarV4Session, sid).active_run_id = "another_run_123"
        db.commit()
    with pytest.raises(BudgetRefused):
        meter.admit("section", "s", "s", "m", 1.0, {})
    assert [e["status"] for e in runs_of(factory, sid)[rid][1]["ledger"]] == ["settled"]


def test_checkpoints_never_overwrite_a_cancellation_written_meanwhile(service):
    build, factory = service
    Writer.per_run, Writer.block = 1, threading.Event()
    svc = build()
    sid = planned_session(svc, factory)
    svc.start(1, NS(session_id=sid, message=FULL))
    time.sleep(.2)
    svc.cancel_job(1, sid)
    Writer.block.set()                                                                # the segment checkpoints afterwards
    job = wait_job(factory, sid)
    assert job["status"] == "cancelled" and job["cancel_requested"] and len(Writer.log) == 1
    assert "annulée" in data_of(factory, sid)["messages"][-1]["content"]


# --- Persistence: executor, lease expiry, configuration -------------------------------------------

def test_explicit_celery_refuses_to_start_without_a_worker_able_to_run_v4(service, monkeypatch):
    from agents.EnnoScholarV4 import service as module
    build, factory = service

    class Unreachable:
        def send_task(self, *a, **k):
            raise ConnectionRefusedError("broker down")
    monkeypatch.setattr(module, "celery_client", lambda: Unreachable())
    svc = build(writing_job_executor="celery")
    sid = planned_session(svc, factory)
    with pytest.raises(ConfigurationError, match="worker Celery injoignable"):
        svc.start(1, NS(session_id=sid, message=FULL))
    assert "writing_job" not in data_of(factory, sid) and not runs_of(factory, sid)     # nothing started
    # A worker that answers with another configuration (e.g. the root .env model) is refused too.
    class Mismatched:
        def send_task(self, name, kwargs, retry):
            fingerprint = {**svc.fingerprint(), "model": "gpt-6.1-sol"}
            return NS(get=lambda timeout: fingerprint)
    monkeypatch.setattr(module, "celery_client", lambda: Mismatched())
    with pytest.raises(ConfigurationError, match="model"):
        svc.start(1, NS(session_id=sid, message=FULL))
    assert not runs_of(factory, sid)
    # A request that opens no job does not need the worker.
    svc.start(1, NS(session_id=sid, message="Quelles sont les limites de l'article 2 ?"))


def test_a_vanished_worker_stops_the_job_explicitly_without_thread_fallback(service, monkeypatch):
    from agents.EnnoScholarV4 import service as module
    build, factory = service
    sent = []
    state = {"alive": True}

    class Broker:
        def send_task(self, name, kwargs, retry):
            if not state["alive"]:
                raise ConnectionRefusedError("worker gone")
            sent.append(name)
            return NS(get=lambda timeout: svc.fingerprint())
    monkeypatch.setattr(module, "celery_client", lambda: Broker())
    Writer.per_run = 1
    svc = build(writing_job_executor="celery")
    sid = planned_session(svc, factory)
    svc.start(1, NS(session_id=sid, message=FULL))                                    # worker verified, first segment here
    for _ in range(200):
        if module.DRIVE_TASK in sent:
            break
        time.sleep(.02)
    assert module.DRIVE_TASK in sent                                                   # continuation handed to Celery
    job = data_of(factory, sid)["writing_job"]
    assert job["status"] == "waiting" and job["executor"] == "celery"
    # The worker disappears: the next dispatch stops the job, nothing runs in a thread.
    state["alive"] = False
    svc._worker_verified = None
    svc.dispatch_job(1, sid, job["id"], force=True)
    job = data_of(factory, sid)["writing_job"]
    assert job["status"] == "executor_unavailable" and "Aucun repli" in job["error"]
    time.sleep(.2)
    assert len(Writer.log) == 1


def test_an_expired_lease_is_recovered_by_the_watchdog_without_any_browser(service):
    build, factory = service
    Writer.per_run = 2
    svc = build()
    sid = planned_session(svc, factory)
    rid = svc.open_run(1, sid, FULL)
    svc.slots.acquire()
    svc.execute(1, sid, rid, driven=True)                                             # waiting, nobody drives it
    job = data_of(factory, sid)["writing_job"]
    lost = svc.open_run(1, sid, None, job_id=job["id"], executor="celery")           # a worker took it, then died
    with factory() as db:
        db.get(ScholarV4Run, lost).status = "running"
        db.commit()
    restarted = build()
    # Lease still live: the driver schedules a retry after its expiry instead of returning for good.
    outcome = restarted.drive_job(1, sid, job["id"])
    assert outcome["status"] == "running" and outcome["retry_after"] > 600
    with factory() as db:
        db.get(ScholarV4Session, sid).lease_until = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    stop = threading.Event()
    watchdog = threading.Thread(target=restarted.watchdog, kwargs={"interval": .05, "stop_event": stop})
    watchdog.start()
    try:
        job = wait_job(factory, sid)
    finally:
        stop.set()
        watchdog.join(5)
    assert job["status"] == "completed" and [s["reason"] for s in job["segments"]][1] == "worker_interrupted"
    assert all(len(ids) == 1 for ids in drafts(data_of(factory, sid)).values())


def test_a_process_with_another_configuration_never_continues_the_job(service):
    build, factory = service
    Writer.per_run = 1
    svc = build()
    sid = planned_session(svc, factory)
    rid = svc.open_run(1, sid, FULL)
    svc.slots.acquire()
    svc.execute(1, sid, rid, driven=True)
    # A real historical job, not a newly normalized legacy env selector.
    with factory() as db:
        row = db.get(ScholarV4Session, sid)
        data = dict(row.data)
        data["writing_job"] = {**data["writing_job"], "config_fingerprint": {
            **data["writing_job"]["config_fingerprint"], "model": "gpt-5.6-sol"}}
        row.data = data
        db.commit()
    other = build(model="gpt-6.1-sol")
    other.drive_job(1, sid, data_of(factory, sid)["writing_job"]["id"])
    job = data_of(factory, sid)["writing_job"]
    assert job["status"] == "configuration_mismatch" and "model" in job["error"]
    assert len(Writer.log) == 1


# --- Other stops and the consultant's own messages ------------------------------------------------

def test_no_progress_stops_with_an_explicit_cause(service):
    build, factory = service
    Writer.refuse = tuple(sid_of(i) for i in range(1, SECTIONS+1))
    svc = build(writing_job_max_section_attempts=10, writing_job_budget_usd=100.0)
    sid = planned_session(svc, factory)
    svc.start(1, NS(session_id=sid, message=FULL))
    job = wait_job(factory, sid)
    assert job["status"] == "stalled" and len(job["segments"]) == 2
    assert "aucune nouvelle section" in job["error"] and "Attribution" in job["last_cause"]


def test_a_message_that_is_not_a_full_draft_opens_no_job(service):
    build, factory = service
    svc = build()
    sid = planned_session(svc, factory)
    svc.start(1, NS(session_id=sid, message="Quelles sont les limites du deuxième article ?"))
    time.sleep(.3)
    assert "writing_job" not in data_of(factory, sid)
    disabled = build(writing_job_enabled=False)
    sid = planned_session(disabled, factory)
    disabled.start(1, NS(session_id=sid, message=FULL))
    time.sleep(.3)
    assert "writing_job" not in data_of(factory, sid)


def test_a_consultant_message_pauses_the_job_until_an_explicit_resume(service):
    build, factory = service
    Writer.per_run = 1
    svc = build()
    sid = planned_session(svc, factory)
    rid = svc.open_run(1, sid, FULL)
    svc.slots.acquire()
    svc.execute(1, sid, rid, driven=True)
    svc.start(1, NS(session_id=sid, message="Peux-tu préciser la méthode de l'article 2 ?"))
    job = wait_job(factory, sid)
    assert job["status"] == "paused" and len(job["segments"]) == 1
    budget = job["budget_usd"]
    svc.resume_job(1, sid)
    job = wait_job(factory, sid, writing_job.FINAL)
    assert job["status"] == "completed" and job["budget_usd"] == budget
    # Every continuation answered the original request, not the later question.
    first_request = data_of(factory, sid)["messages"][0]["id"]
    assert all(row["request_id"] == first_request for row in Writer.log if row["segment"])
    with pytest.raises(ValueError):
        svc.resume_job(1, sid)


def test_the_public_api_reads_and_cancels_the_job(service):
    build, factory = service
    Writer.per_run, Writer.block = 1, threading.Event()
    svc = build()
    sid = planned_session(svc, factory)
    svc.start(1, NS(session_id=sid, message=FULL))
    time.sleep(.2)
    state = svc.writing_job(1, sid)
    assert state["job"]["status"] == "running" and state["job"]["remaining_usd"] == 10.0
    assert set(state["job"]["spent"]) == {"confirmed_usd", "reserved_usd", "uncertain_usd"}
    with factory() as db:
        assert svc.session_read(db.get(ScholarV4Session, sid), detailed=False)["writing_job"]["id"] == state["job"]["id"]
    with pytest.raises(Exception):
        svc.writing_job(2, sid)                                                       # another consultant
    svc.cancel_job(1, sid)
    Writer.block.set()
    assert wait_job(factory, sid)["status"] == "cancelled"


def test_the_real_agent_resumes_the_goal_request_and_progress_of_the_job_without_a_new_understanding_call():
    """Real session (read-only copy): a continuation applies the goal the model declared for
    the consultant's request; it makes no set_turn_goal call and reads no generic message."""
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.writing_progress import open_writing
    from tests.ennoscholar_v4.replay_b52f582a import sources
    from tests.ennoscholar_v4.test_run_dcf5d05b import Model, SECTION_1, before_run, tools
    state, proposals, goal_args = before_run()
    request = [m for m in state["messages"] if m["role"] == "user"][-1]
    toolkit = tools(state)
    goal = toolkit.execute("set_turn_goal", goal_args, float("inf"))["goal"]
    state.pop("turn_goal", None)
    progress = open_writing(state, "full", [SECTION_1])
    state["messages"].append({"id": "segment_answer", "role": "assistant", "content": "Brouillon partiel enregistré : 0/1."})
    state["writing_segment"] = {"job_id": "job_x", "request_message_id": request["id"], "progress_id": progress["id"],
        "target_ids": [SECTION_1], "plan_revision": state["outline"].get("plan_revision"), "goal": goal, "blocked": {}}
    model, events = Model(goal_args, proposals[-1]), []
    ScholarChatAgent(ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False),
        client=model, sources=sources()).run(state, emit=events.append)
    forced = [(kw.get("tool_choice") or {}).get("name") for kw in model.calls if isinstance(kw.get("tool_choice"), dict)]
    assert "set_turn_goal" not in forced                                               # no new understanding call
    assert any(e.get("tool") == "turn_goal" and e.get("status") == "resumed" for e in events)
    after = state["document"]["writing_progress"]
    assert after["id"] == progress["id"] and after["target_ids"] == [SECTION_1]         # same progress, same scope
    assert SECTION_1 in after["saved_draft_ids"]
    assert state["turn_goal"]["references"].get("message_id") in (None, request["id"])
