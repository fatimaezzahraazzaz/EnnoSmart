import time
from types import SimpleNamespace as NS
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.deps import get_current_user
from db.database import Base
from db.models import User
from routers import scholar_v3
from agents.EnnoScholarV3.config import ScholarV3Config
from agents.EnnoScholarV3.models import Article
from agents.EnnoScholarV3.service import ScholarV3Service
from agents.EnnoScholarV3.session import ScholarV3Session, ScholarV3Run


class Agent:
    def run(self, data, emit, cancelled):
        emit({"tool": "search_scientific", "status": "completed", "found": 1})
        row = Article(id="paper_12345678", title="Verified paper", doi="10.1234/paper", verified=True, abstract="Real mocked abstract")
        data["articles"][row.id] = row.durable()
        return {"answer": "Une publication vérifiée.", "citations": [{"url": "https://example.org/paper", "title": "Paper", "cited": True, "start_index": 0, "end_index": 3}], "partial": False}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "sessions.sqlite"), connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(User(id=1, full_name="Test", email="test@example.org", hashed_password="not-a-password", is_active=True))
        db.commit()
    service = ScholarV3Service(factory, ScholarV3Config(api_key="mock"), Agent)
    monkeypatch.setattr(scholar_v3, "get_service", lambda: service)
    app = FastAPI()
    app.include_router(scholar_v3.router)
    client = TestClient(app)
    yield app, client, service, factory
    service.pool.shutdown(wait=True)
    engine.dispose()


def auth(app, owner=1):
    app.dependency_overrides[get_current_user] = lambda: NS(id=owner)


def wait_run(client, rid):
    for _ in range(100):
        run = client.get("/scholar-v3/runs/" + rid).json()
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(.01)
    raise AssertionError("Mock worker did not finish")


def test_all_session_routes_require_auth_and_are_owner_scoped(setup):
    app, client, _, _ = setup
    assert client.get("/scholar-v3/sessions").status_code in {401, 403}
    auth(app)
    sid = client.post("/scholar-v3/sessions", json={}).json()["id"]
    assert client.get("/scholar-v3/sessions/" + sid).status_code == 200
    auth(app, 2)
    assert client.get("/scholar-v3/sessions").json() == []
    assert client.get("/scholar-v3/sessions/" + sid).status_code == 404
    assert client.post("/scholar-v3/chat", json={"session_id": sid, "message": "Lire"}).status_code == 404


def test_durable_multiturn_messages_articles_selection_and_annotations(setup):
    app, client, service, factory = setup
    auth(app)
    sid = client.post("/scholar-v3/sessions", json={}).json()["id"]
    run = client.post("/scholar-v3/chat", json={"session_id": sid, "message": "Recherche"})
    assert run.status_code == 202
    final = wait_run(client, run.json()["id"])
    assert final["status"] == "completed" and final["citations"][0]["url"] == "https://example.org/paper"
    assert client.patch(f"/scholar-v3/sessions/{sid}/articles/paper_12345678", json={"selected": True}).json()["selected"]
    assert client.get(f"/scholar-v3/sessions/{sid}/articles").json()["total"] == 1
    # A fresh service reads the same durable state without an OpenAI response ID.
    fresh = ScholarV3Service(factory, ScholarV3Config(api_key="mock"), Agent)
    try:
        snapshot = fresh.read(1, sid)
        assert len(snapshot["messages"]) == 2 and snapshot["articles"][0]["selected"]
        result = client.post("/scholar-v3/chat", json={"session_id": sid, "message": "Compare ces articles"})
        wait_run(client, result.json()["id"])
        current = fresh.read(1, sid)
        assert len(current["messages"]) == 4 and current["articles"][0]["selected"]
    finally:
        fresh.pool.shutdown()


def test_input_validation_rejects_client_history_project_and_internal_state(setup):
    app, client, _, _ = setup
    auth(app)
    for payload in ({"message": " "}, {"message": "Hello", "project_id": 1}, {"message": "Hello", "history": []}, {"message": "Hello", "session_id": "../secret"}):
        assert client.post("/scholar-v3/chat", json=payload).status_code == 422


def test_new_discoveries_survive_a_worker_failure(setup):
    app, client, service, _ = setup
    auth(app)
    class FailingAgent:
        def run(self, data, emit, cancelled):
            row = Article(id="paper_checkpoint", title="Retrieved before failure", doi="10.1234/checkpoint", verified=True)
            data["articles"][row.id] = row.durable()
            emit({"tool": "search_scientific", "status": "completed", "found": 1})
            raise RuntimeError("Upstream disconnected")
    service.agent_factory = FailingAgent
    sid = client.post("/scholar-v3/sessions", json={}).json()["id"]
    rid = client.post("/scholar-v3/chat", json={"session_id": sid, "message": "Recherche"}).json()["id"]
    assert wait_run(client, rid)["status"] == "failed"
    snapshot = client.get("/scholar-v3/sessions/" + sid).json()
    assert snapshot["articles"][0]["id"] == "paper_checkpoint"
    assert len(snapshot["messages"]) == 2


def test_database_lease_rejects_concurrent_turn_and_recovers_worker_restart(setup):
    app, client, service, factory = setup
    auth(app)
    sid = client.post("/scholar-v3/sessions", json={}).json()["id"]
    with factory() as db:
        row = db.get(ScholarV3Session, sid)
        row.active_run_id, row.lease_until = "old_run_12345678", datetime.utcnow() + timedelta(seconds=60)
        db.add(ScholarV3Run(id=row.active_run_id, owner_id=1, session_id=sid, status="running", data={}))
        db.commit()
    assert client.post("/scholar-v3/chat", json={"session_id": sid, "message": "Concurrent"}).status_code == 409
    with factory() as db:
        db.get(ScholarV3Session, sid).lease_until = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    snapshot = client.get("/scholar-v3/sessions/" + sid).json()
    assert snapshot["active_run_id"] is None
    assert client.get("/scholar-v3/runs/old_run_12345678").json()["reason"] == "worker_interrupted"
