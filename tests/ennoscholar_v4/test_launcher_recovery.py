"""End to end, offline: drafting in progress, backend crash, restart by the REAL local launcher
(C:/Users/hp/.codex/runtime/ennoscholar-v4/start_backend.py), automatic recovery, remaining
sections drafted, manuscript budget kept.

The launcher runs in its own process on a temporary database, profile and attestation
directory (ENNOSCHOLAR_V4_LAUNCH_*), through launcher_harness.py: simulated writer, no
provider call, no network. The real database and the real launcher directory are never used.
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from db.database import Base
import db.models  # noqa: F401  (tables the launcher counts: users, projects)
from db.models import User
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.job_ledger import JobMeter, totals
from agents.EnnoScholarV4.service import ScholarV4Service
from agents.EnnoScholarV4.session import ScholarV4Run, ScholarV4Session
from agents.EnnoScholarV4.writing_progress import refresh_writing

LAUNCHER = Path(r"C:/Users/hp/.codex/runtime/ennoscholar-v4/start_backend.py")
HARNESS = Path(__file__).with_name("launcher_harness.py")
FULL = "Rédige l’état de l’art complet en suivant le plan, section par section."
SECTIONS = 5

pytestmark = pytest.mark.skipif(not LAUNCHER.is_file() or os.name != "nt", reason="Lanceur local absent")


class FirstSegmentWriter:
    """The run before the crash: two sections through the real ledger (1 $ each)."""
    def __init__(self, config=None, **_):
        self.config, self.meter = config, None

    def run(self, data, emit, cancelled):
        from agents.EnnoScholarV4.writing_progress import begin_writing
        progress = begin_writing(data, data["messages"][-1]["content"])
        data["turn_goal"] = {"deliverable": "draft", "reading_depth": "cards", "references": {"message_id": data["messages"][-1]["id"]}}
        for sid in progress["target_ids"][:2]:
            self.meter.ledger.settle(self.meter.admit("section", sid, sid, "gpt-5.6-sol", 1.2, {}), 1.0)
            next(s for s in data["outline"]["sections"] if s["id"] == sid)["drafts"].append({"id": uuid.uuid4().hex, "text": sid})
            refresh_writing(data)
            emit({"tool": "write_section", "status": "completed"})
        refresh_writing(data, final=True)
        return {"answer": "Brouillon partiel.", "partial": True, "reason": "exploration_time_budget"}


def copy_schema(database):
    """The application's complete schema (every table main.py expects at start-up), read from
    the local database opened read-only: structure only, no row is copied."""
    import sqlite3
    real = Path(__file__).resolve().parents[2] / "ennoscholar_v4_test.db"
    if not real.is_file():
        pytest.skip("Base locale absente (schéma de référence)")
    source = sqlite3.connect(real.as_uri() + "?mode=ro", uri=True)
    statements = [sql for kind, sql in source.execute("SELECT type, sql FROM sqlite_master WHERE sql IS NOT NULL "
        "AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END")]
    source.close()
    target = sqlite3.connect(database)
    for sql in statements:
        target.execute(sql)
    target.commit()
    target.close()


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def dead_pid():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


@pytest.fixture
def crashed(tmp_path):
    """A temporary installation where the backend crashed in the middle of segment 2:
    sections 1-2 saved by segment 1, section 3 saved and paid by segment 2, one call of
    segment 2 in flight (reserved, never settled), the run still « running »."""
    database = tmp_path / "v4.db"
    copy_schema(database)
    engine = create_engine("sqlite:///" + database.as_posix(), connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(User(id=1, full_name="Test", email="test@example.org", hashed_password="x", is_active=True))
        db.commit()
    svc = ScholarV4Service(factory, ScholarV4Config(api_key="sk-offline-test", model="gpt-6-luna", prompt_cache_mode="explicit",
        input_price=0.0, output_price=0.0),                    # the configuration the launcher gives the backend
        FirstSegmentWriter)
    sid = svc.create(1)["id"]
    with factory() as db:
        row = db.get(ScholarV4Session, sid)
        row.data = {**row.data, "outline": {"title": "État de l'art", "version": 1, "plan_revision": "r1", "approval": None,
            "sections": [{"id": f"section_{i}aaaaaaaa", "title": f"Section {i}", "article_ids": [], "drafts": []}
                for i in range(1, SECTIONS+1)]}}
        db.commit()
    first = svc.open_run(1, sid, FULL)
    svc.slots.acquire()
    svc.execute(1, sid, first, driven=True)
    with factory() as db:                                     # read directly: no recovery from this process
        job = db.get(ScholarV4Session, sid).data["writing_job"]
    assert job["status"] == "waiting"
    crashed_run = svc.open_run(1, sid, None, job_id=job["id"], executor="thread")
    meter = JobMeter(factory, sid, crashed_run, 10.0, job["spent"])
    meter.ledger.settle(meter.admit("section", "s3", "s3", "gpt-5.6-sol", 1.2, {}), 1.0)
    meter.admit("section", "s4", "s4", "gpt-5.6-sol", 1.2, {})                        # in flight at the crash
    with factory() as db:
        db.get(ScholarV4Run, crashed_run).status = "running"
        row = db.get(ScholarV4Session, sid)
        data = json.loads(json.dumps(row.data))
        data["outline"]["sections"][2]["drafts"].append({"id": "draft_before_crash", "text": "section 3"})
        refresh_writing(data)
        row.data = data                                                                # its last checkpoint
        db.commit()
    svc.pool.shutdown(wait=True)
    engine.dispose()

    launch = tmp_path / "launch"
    launch.mkdir()
    (launch / "openai.local.env").write_text("OPENAI_API_KEY=sk-offline-test\nENNOSCHOLAR_V4_MODEL=gpt-6-luna\n"
        "ENNOSCHOLAR_V4_INPUT_PRICE=4\nENNOSCHOLAR_V4_OUTPUT_PRICE=20\n", encoding="utf-8")
    (launch / "backend-attestation.json").write_text(json.dumps({"pid": dead_pid()}), encoding="utf-8")
    profile = tmp_path / "profile.env"
    profile.write_text("ALGORITHM=HS256\n", encoding="utf-8")
    return {"database": database, "launch": launch, "profile": profile, "session": sid, "first": first,
        "crashed": crashed_run, "job": job["id"]}


def set_lease(database, sid, seconds):
    engine = create_engine("sqlite:///" + database.as_posix())
    with sessionmaker(bind=engine)() as db:
        db.get(ScholarV4Session, sid).lease_until = datetime.utcnow() + timedelta(seconds=seconds)
        db.commit()
    engine.dispose()


def launch(case, port):
    env = {**os.environ, "HARNESS_LAUNCHER": str(LAUNCHER), "ENNOSCHOLAR_V4_LAUNCH_DIR": str(case["launch"]),
        "ENNOSCHOLAR_V4_LAUNCH_DB": str(case["database"]), "ENNOSCHOLAR_V4_LAUNCH_PROFILE": str(case["profile"]),
        "ENNOSCHOLAR_V4_LAUNCH_PORT": str(port), "PYTHONIOENCODING": "utf-8"}
    log = open(case["launch"] / "launcher.log", "wb")
    return subprocess.Popen([sys.executable, "-I", "-B", str(HARNESS)], env=env, stdout=log, stderr=subprocess.STDOUT), log


def read(database, sid):
    engine = create_engine("sqlite:///" + database.as_posix())
    with sessionmaker(bind=engine)() as db:
        data = db.get(ScholarV4Session, sid).data
        runs = {r.id: (r.status, r.data) for r in db.scalars(select(ScholarV4Run).where(ScholarV4Run.session_id == sid))}
    engine.dispose()
    return data, runs


def wait_completed(case, process, seconds=180):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if process.poll() is not None:
            raise AssertionError("Lanceur arrêté : " + (case["launch"] / "launcher.log").read_text("utf-8", "replace")[-3000:])
        data, runs = read(case["database"], case["session"])
        if data["writing_job"]["status"] not in {"running", "waiting"}:
            return data, runs
        time.sleep(.5)
    raise AssertionError("Reprise non terminée : " + json.dumps(data["writing_job"], ensure_ascii=False)[:2000])


def check_recovered(case, data, runs):
    job = data["writing_job"]
    assert job["status"] == "completed" and job["id"] == case["job"] and job["budget_usd"] == 10.0
    drafts = {s["id"]: [d["id"] for d in s.get("drafts", [])] for s in data["outline"]["sections"]}
    assert all(len(ids) == 1 for ids in drafts.values())                               # every section once, none redone
    assert drafts["section_3aaaaaaaa"] == ["draft_before_crash"]                       # saved before the crash, kept
    segments = job["segments"]
    assert [s["run_id"] for s in segments[:2]] == [case["first"], case["crashed"]]
    assert segments[1]["reason"] == "worker_interrupted"
    assert segments[1]["spent"] == {"confirmed_usd": 1.0, "reserved_usd": 0.0, "uncertain_usd": 1.2}
    assert job["spent"]["confirmed_usd"] == pytest.approx(5.0)                         # 2 + 1 + 2 sections at 1 $
    assert job["spent"]["uncertain_usd"] == pytest.approx(1.2)                         # in-flight call kept, not replayed
    ledgers = [e for status, run in runs.values() for e in run.get("ledger", [])]
    assert sum(e["status"] == "settled" for e in ledgers) == 5 and sum(e["status"] == "uncertain" for e in ledgers) == 1
    assert [m["content"] for m in data["messages"] if m["role"] == "user"] == [FULL]   # no message in the consultant's name


def test_the_real_launcher_restarts_after_a_crash_and_the_job_resumes_after_its_checks(crashed):
    """Lease already expired at restart: recovery could write immediately, so the launcher's
    own check (database unchanged until the end of preparation) proves the watchdog waited."""
    set_lease(crashed["database"], crashed["session"], -1)
    before = hashlib.sha256(crashed["database"].read_bytes()).hexdigest()
    process, log = launch(crashed, free_port())
    try:
        data, runs = wait_completed(crashed, process)
    finally:
        process.terminate()
        process.wait(30)
        log.close()
    attestation = json.loads((crashed["launch"] / "backend-attestation.json").read_text(encoding="utf-8"))
    assert attestation["status"] == "startup-verified" and attestation["database_sha256_before"] == before
    assert attestation["database_sha256_after_startup"] == before                     # nothing written during preparation
    assert attestation["writing_job_recovery"] == "held until the end of the read-only preparation"
    assert attestation["previous_backend"]["previous_pid_alive"] is False
    assert [r["state"] for r in attestation["active_runs_at_start"]] == ["abandoned"]
    check_recovered(crashed, data, runs)


def test_an_abandoned_run_with_a_live_lease_is_recovered_once_its_lease_expires(crashed):
    # Keep the lease live throughout cold imports/startup, then expire it in this
    # temporary database. An eight-second wall-clock lease raced process startup.
    set_lease(crashed["database"], crashed["session"], 600)
    process, log = launch(crashed, free_port())
    try:
        deadline = time.monotonic() + 180
        attestation_path = crashed["launch"] / "backend-attestation.json"
        while time.monotonic() < deadline:
            if attestation_path.is_file():
                try:
                    attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    attestation = {}  # The temporary launcher's write may still be in progress.
                if attestation.get("status") == "startup-verified":
                    break
            if process.poll() is not None:
                raise AssertionError("Temporary launcher stopped before startup verification")
            time.sleep(.1)
        else:
            raise AssertionError("Temporary launcher did not complete startup verification")
        set_lease(crashed["database"], crashed["session"], -1)
        data, runs = wait_completed(crashed, process)
    finally:
        process.terminate()
        process.wait(30)
        log.close()
    attestation = json.loads((crashed["launch"] / "backend-attestation.json").read_text(encoding="utf-8"))
    assert [r["state"] for r in attestation["active_runs_at_start"]] == ["abandoned_lease_pending"]
    check_recovered(crashed, data, runs)


def test_a_run_still_held_by_a_living_backend_blocks_the_start(crashed):
    set_lease(crashed["database"], crashed["session"], 600)
    (crashed["launch"] / "backend-attestation.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    before = hashlib.sha256(crashed["database"].read_bytes()).hexdigest()
    port = free_port()
    with socket.socket() as other_backend:                                            # the API port is still served
        other_backend.bind(("127.0.0.1", port))
        other_backend.listen()
        process, log = launch(crashed, port)
        try:
            code = process.wait(120)
        finally:
            log.close()
    raw = (crashed["launch"] / "launcher.log").read_bytes()
    try:
        output = raw.decode("utf-8")
    except UnicodeDecodeError:
        output = raw.decode("cp1252", "replace")                   # -I ignores PYTHONIOENCODING
    assert code != 0 and "Démarrage refusé : run EnnoScholar V4 réellement actif" in output
    assert crashed["crashed"] in output
    assert hashlib.sha256(crashed["database"].read_bytes()).hexdigest() == before       # nothing touched
    data, runs = read(crashed["database"], crashed["session"])
    assert runs[crashed["crashed"]][0] == "running" and data["writing_job"]["status"] == "running"
