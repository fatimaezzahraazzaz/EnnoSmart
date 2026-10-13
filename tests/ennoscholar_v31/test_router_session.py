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
from routers import scholar_v31
from agents.EnnoScholarV31.config import ScholarV31Config
from agents.EnnoScholarV31.models import Article
from agents.EnnoScholarV31.service import ScholarV31Service
from agents.EnnoScholarV31.session import ScholarV31Session, ScholarV31Run


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
    service = ScholarV31Service(factory, ScholarV31Config(api_key="mock", paid_calls_authorized=True), Agent)
    monkeypatch.setattr(scholar_v31, "get_service", lambda: service)
    app = FastAPI()
    app.include_router(scholar_v31.router)
    client = TestClient(app)
    yield app, client, service, factory
    service.pool.shutdown(wait=True)
    engine.dispose()


def auth(app, owner=1):
    app.dependency_overrides[get_current_user] = lambda: NS(id=owner)


def wait_run(client, rid):
    for _ in range(100):
        run = client.get("/scholar-v3-1/runs/" + rid).json()
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(.01)
    raise AssertionError("Mock worker did not finish")


def test_all_session_routes_require_auth_and_are_owner_scoped(setup):
    app, client, _, _ = setup
    assert client.get("/scholar-v3-1/sessions").status_code in {401, 403}
    auth(app)
    sid = client.post("/scholar-v3-1/sessions", json={}).json()["id"]
    assert client.get("/scholar-v3-1/sessions/" + sid).status_code == 200
    auth(app, 2)
    assert client.get("/scholar-v3-1/sessions").json() == []
    assert client.get("/scholar-v3-1/sessions/" + sid).status_code == 404
    assert client.post("/scholar-v3-1/chat", json={"session_id": sid, "message": "Lire"}).status_code == 404


def test_durable_multiturn_messages_articles_selection_and_annotations(setup):
    app, client, service, factory = setup
    auth(app)
    sid = client.post("/scholar-v3-1/sessions", json={}).json()["id"]
    run = client.post("/scholar-v3-1/chat", json={"session_id": sid, "message": "Recherche"})
    assert run.status_code == 202
    final = wait_run(client, run.json()["id"])
    assert final["status"] == "completed" and final["citations"][0]["url"] == "https://example.org/paper"
    assert client.patch(f"/scholar-v3-1/sessions/{sid}/articles/paper_12345678", json={"selected": True}).json()["selected"]
    assert client.get(f"/scholar-v3-1/sessions/{sid}/articles").json()["total"] == 1
    # A fresh service reads the same durable state without an OpenAI response ID.
    fresh = ScholarV31Service(factory, ScholarV31Config(api_key="mock", paid_calls_authorized=True), Agent)
    try:
        snapshot = fresh.read(1, sid)
        assert len(snapshot["messages"]) == 2 and snapshot["articles"][0]["selected"]
        result = client.post("/scholar-v3-1/chat", json={"session_id": sid, "message": "Compare ces articles"})
        wait_run(client, result.json()["id"])
        current = fresh.read(1, sid)
        assert len(current["messages"]) == 4 and current["articles"][0]["selected"]
    finally:
        fresh.pool.shutdown()


def test_input_validation_rejects_client_history_project_and_internal_state(setup):
    app, client, _, _ = setup
    auth(app)
    for payload in ({"message": " "}, {"message": "Hello", "project_id": 1}, {"message": "Hello", "history": []}, {"message": "Hello", "session_id": "../secret"}):
        assert client.post("/scholar-v3-1/chat", json=payload).status_code == 422


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
    sid = client.post("/scholar-v3-1/sessions", json={}).json()["id"]
    rid = client.post("/scholar-v3-1/chat", json={"session_id": sid, "message": "Recherche"}).json()["id"]
    assert wait_run(client, rid)["status"] == "failed"
    snapshot = client.get("/scholar-v3-1/sessions/" + sid).json()
    assert snapshot["articles"][0]["id"] == "paper_checkpoint"
    assert len(snapshot["messages"]) == 2


def test_database_lease_rejects_concurrent_turn_and_recovers_worker_restart(setup):
    app, client, service, factory = setup
    auth(app)
    sid = client.post("/scholar-v3-1/sessions", json={}).json()["id"]
    with factory() as db:
        row = db.get(ScholarV31Session, sid)
        row.active_run_id, row.lease_until = "old_run_12345678", datetime.utcnow() + timedelta(seconds=60)
        db.add(ScholarV31Run(id=row.active_run_id, owner_id=1, session_id=sid, status="running", data={}))
        db.commit()
    assert client.post("/scholar-v3-1/chat", json={"session_id": sid, "message": "Concurrent"}).status_code == 409
    with factory() as db:
        db.get(ScholarV31Session, sid).lease_until = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    snapshot = client.get("/scholar-v3-1/sessions/" + sid).json()
    assert snapshot["active_run_id"] is None
    assert client.get("/scholar-v3-1/runs/old_run_12345678").json()["reason"] == "worker_interrupted"


def test_pdf_upload_is_owner_scoped_preserves_pages_and_does_not_invent_bibliography(setup, tmp_path, monkeypatch):
    import fitz
    monkeypatch.setenv("ENNOSCHOLAR_V31_PDF_DIRECTORY", str(tmp_path/"pdf"))
    app, client, _, _ = setup
    auth(app)
    sid = client.post("/scholar-v3-1/sessions", json={}).json()["id"]
    with fitz.open() as document:
        document.new_page().insert_text((50,50), "Scientific article title\nIntroduction\nA reproducible experiment.")
        document.new_page().insert_text((50,50), "Limitations\nA controlled experiment has limited scope.")
        content = document.tobytes()
    auth(app, 2)
    assert client.post(f"/scholar-v3-1/sessions/{sid}/pdf", files={"file": ("article.pdf", content, "application/pdf")}).status_code == 404
    assert not list(tmp_path.glob("pdf/*.pdf"))
    auth(app)
    upload = client.post(f"/scholar-v3-1/sessions/{sid}/pdf", files={"file": ("article.pdf", content, "application/pdf")})
    assert upload.status_code == 201
    article = upload.json()
    assert article["fulltext_available"] and not article["verified"] and article["analysis_basis"] is None
    assert article["doi"] == "" and article["source_version"].startswith("pdf_sha256:")
    assert len(list(tmp_path.glob("pdf/*.pdf"))) == 1
    memory = client.get(f"/scholar-v3-1/sessions/{sid}/memory").json()
    assert memory["exposed_article_ids"] == []


def test_human_corpus_search_does_not_count_as_gpt_exposure(setup):
    app, client, _, _ = setup
    auth(app)
    sid = client.post("/scholar-v3-1/sessions", json={}).json()["id"]
    rid = client.post("/scholar-v3-1/chat", json={"session_id": sid, "message": "Recherche"}).json()["id"]
    wait_run(client, rid)
    page = client.get(f"/scholar-v3-1/sessions/{sid}/articles?query=verified").json()
    assert page["total"] == 1
    assert client.get(f"/scholar-v3-1/sessions/{sid}/memory").json()["exposed_article_ids"] == []


def test_chat_is_blocked_before_worker_when_paid_budget_not_authorized(setup):
    from dataclasses import replace
    app, client, service, _ = setup
    auth(app)
    service.config = replace(service.config, paid_calls_authorized=False)
    assert client.post("/scholar-v3-1/chat", json={"message": "Recherche"}).status_code == 503
