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
from routers import scholar_v4
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.service import ScholarV4Service
from agents.EnnoScholarV4.session import ScholarV4Session, ScholarV4Run


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
    service = ScholarV4Service(factory, ScholarV4Config(api_key="mock"), Agent)
    monkeypatch.setattr(scholar_v4, "get_service", lambda: service)
    app = FastAPI()
    app.include_router(scholar_v4.router)
    client = TestClient(app)
    yield app, client, service, factory
    service.pool.shutdown(wait=True)
    engine.dispose()


def auth(app, owner=1):
    app.dependency_overrides[get_current_user] = lambda: NS(id=owner)


def wait_run(client, rid):
    for _ in range(100):
        run = client.get("/scholar-v4/runs/" + rid).json()
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(.01)
    raise AssertionError("Mock worker did not finish")


def test_all_session_routes_require_auth_and_are_owner_scoped(setup):
    app, client, _, _ = setup
    assert client.get("/scholar-v4/sessions").status_code in {401, 403}
    auth(app)
    sid = client.post("/scholar-v4/sessions", json={}).json()["id"]
    assert client.get("/scholar-v4/sessions/" + sid).status_code == 200
    auth(app, 2)
    assert client.get("/scholar-v4/sessions").json() == []
    assert client.get("/scholar-v4/sessions/" + sid).status_code == 404
    assert client.post("/scholar-v4/chat", json={"session_id": sid, "message": "Lire"}).status_code == 404


def test_durable_multiturn_messages_articles_selection_and_annotations(setup):
    app, client, service, factory = setup
    auth(app)
    sid = client.post("/scholar-v4/sessions", json={}).json()["id"]
    run = client.post("/scholar-v4/chat", json={"session_id": sid, "message": "Recherche"})
    assert run.status_code == 202
    final = wait_run(client, run.json()["id"])
    assert final["status"] == "completed" and final["citations"][0]["url"] == "https://example.org/paper"
    assert client.patch(f"/scholar-v4/sessions/{sid}/articles/paper_12345678", json={"selected": True}).json()["selected"]
    assert client.get(f"/scholar-v4/sessions/{sid}/articles").json()["total"] == 1
    # A fresh service reads the same durable state without an OpenAI response ID.
    fresh = ScholarV4Service(factory, ScholarV4Config(api_key="mock"), Agent)
    try:
        snapshot = fresh.read(1, sid)
        assert len(snapshot["messages"]) == 2 and snapshot["articles"][0]["selected"]
        result = client.post("/scholar-v4/chat", json={"session_id": sid, "message": "Compare ces articles"})
        wait_run(client, result.json()["id"])
        current = fresh.read(1, sid)
        assert len(current["messages"]) == 4 and current["articles"][0]["selected"]
    finally:
        fresh.pool.shutdown()


def test_input_validation_rejects_client_history_project_and_internal_state(setup):
    app, client, _, _ = setup
    auth(app)
    for payload in ({"message": " "}, {"message": "Hello", "project_id": 1}, {"message": "Hello", "history": []}, {"message": "Hello", "session_id": "../secret"}):
        assert client.post("/scholar-v4/chat", json=payload).status_code == 422


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
    sid = client.post("/scholar-v4/sessions", json={}).json()["id"]
    rid = client.post("/scholar-v4/chat", json={"session_id": sid, "message": "Recherche"}).json()["id"]
    assert wait_run(client, rid)["status"] == "failed"
    snapshot = client.get("/scholar-v4/sessions/" + sid).json()
    assert snapshot["articles"][0]["id"] == "paper_checkpoint"
    assert len(snapshot["messages"]) == 2


def test_database_lease_rejects_concurrent_turn_and_recovers_worker_restart(setup):
    app, client, service, factory = setup
    auth(app)
    sid = client.post("/scholar-v4/sessions", json={}).json()["id"]
    with factory() as db:
        row = db.get(ScholarV4Session, sid)
        row.active_run_id, row.lease_until = "old_run_12345678", datetime.utcnow() + timedelta(seconds=60)
        db.add(ScholarV4Run(id=row.active_run_id, owner_id=1, session_id=sid, status="running", data={}))
        db.commit()
    assert client.post("/scholar-v4/chat", json={"session_id": sid, "message": "Concurrent"}).status_code == 409
    with factory() as db:
        db.get(ScholarV4Session, sid).lease_until = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    snapshot = client.get("/scholar-v4/sessions/" + sid).json()
    assert snapshot["active_run_id"] is None
    assert client.get("/scholar-v4/runs/old_run_12345678").json()["reason"] == "worker_interrupted"


def test_pdf_upload_is_owner_scoped_preserves_pages_and_does_not_invent_bibliography(setup, tmp_path, monkeypatch):
    import fitz
    monkeypatch.setenv("ENNOSCHOLAR_V4_PDF_DIRECTORY", str(tmp_path/"pdf"))
    app, client, _, _ = setup
    auth(app)
    sid = client.post("/scholar-v4/sessions", json={}).json()["id"]
    with fitz.open() as document:
        document.new_page().insert_text((50,50), "Scientific article title\nIntroduction\nA reproducible experiment.")
        document.new_page().insert_text((50,50), "Limitations\nA controlled experiment has limited scope.")
        content = document.tobytes()
    auth(app, 2)
    assert client.post(f"/scholar-v4/sessions/{sid}/pdf", files={"file": ("article.pdf", content, "application/pdf")}).status_code == 404
    assert not list(tmp_path.glob("pdf/*.pdf"))
    auth(app)
    upload = client.post(f"/scholar-v4/sessions/{sid}/pdf", files={"file": ("article.pdf", content, "application/pdf")})
    assert upload.status_code == 201
    article = upload.json()
    assert article["fulltext_available"] and not article["verified"] and article["analysis_basis"] is None
    assert article["doi"] == "" and article["source_version"].startswith("pdf_sha256:")
    assert len(list(tmp_path.glob("pdf/*.pdf"))) == 1
    memory = client.get(f"/scholar-v4/sessions/{sid}/memory").json()
    assert memory["exposed_article_ids"] == []


def test_human_corpus_search_does_not_count_as_gpt_exposure(setup):
    app, client, _, _ = setup
    auth(app)
    sid = client.post("/scholar-v4/sessions", json={}).json()["id"]
    rid = client.post("/scholar-v4/chat", json={"session_id": sid, "message": "Recherche"}).json()["id"]
    wait_run(client, rid)
    page = client.get(f"/scholar-v4/sessions/{sid}/articles?query=verified").json()
    assert page["total"] == 1
    assert client.get(f"/scholar-v4/sessions/{sid}/memory").json()["exposed_article_ids"] == []


def test_chat_is_blocked_before_worker_when_api_key_missing(setup):
    from dataclasses import replace
    app, client, service, _ = setup
    auth(app)
    service.config = replace(service.config, api_key="")
    assert client.post("/scholar-v4/chat", json={"message": "Recherche"}).status_code == 503


def test_text_document_upload_and_indexed_tables_are_persistent_and_owner_scoped(setup):
    from sqlalchemy import select
    from agents.EnnoScholarV4.session import ScholarV4Article, ScholarV4Message
    app, client, service, factory = setup
    auth(app)
    sid = client.post("/scholar-v4/sessions", json={}).json()["id"]
    uploaded = client.post(f"/scholar-v4/sessions/{sid}/documents", files={"file": ("notes.txt", b"An actual document supplied by the consultant.", "text/plain")})
    assert uploaded.status_code == 201 and uploaded.json()["access_status"] == "fulltext_extracted"
    with factory() as db:
        article = db.scalar(select(ScholarV4Article).where(ScholarV4Article.session_id == sid))
        assert article.data["fulltext"]["chunks"][0]["page"] is None
    auth(app, 2)
    assert client.get(f"/scholar-v4/sessions/{sid}/articles").status_code == 404
    assert client.post(f"/scholar-v4/sessions/{sid}/documents", files={"file": ("x.txt", b"private", "text/plain")}).status_code == 404


def test_cancellation_is_persistent_and_owned(setup):
    app, client, service, _ = setup
    auth(app)
    class SlowAgent:
        def run(self, data, emit, cancelled):
            for _ in range(200):
                if cancelled():
                    return {"answer": "Recherche arrêtée.", "reason": "cancelled", "partial": True}
                time.sleep(.01)
            return {"answer": "Terminé", "partial": False}
    service.agent_factory = SlowAgent
    sid = client.post("/scholar-v4/sessions", json={}).json()["id"]
    rid = client.post("/scholar-v4/chat", json={"session_id": sid, "message": "Recherche"}).json()["id"]
    auth(app, 2)
    assert client.post(f"/scholar-v4/runs/{rid}/cancel").status_code == 404
    auth(app)
    assert client.post(f"/scholar-v4/runs/{rid}/cancel").json()["cancel_requested"]
    assert wait_run(client, rid)["status"] == "cancelled"


def test_publication_order_secondary_library_and_refusals_survive_restart(setup):
    app, client, service, factory = setup
    auth(app)
    class PublishingAgent:
        def run(self, data, emit, cancelled):
            for number in range(6):
                aid = f"paper_saved_{number}"
                data["articles"][aid] = Article(id=aid, title=f"Saved publication {number}",
                    verified=True, doi=f"10.1234/saved{number}").durable()
            order = ["paper_saved_3", "paper_saved_1", "paper_saved_5", "paper_saved_0"]
            data["displayed_article_ids"] = order
            data["finalization_attempts"] = [{"published": False, "rejection_code": "unverified_url",
                "error": "The URL is not a validated publication or evidence source."}]
            data["web_sources"] = [{"url": "https://example.org/discovered", "cited": False}]
            emit({"tool": "finalize_response", "status": "rejected", "rejection_code": "unverified_url"})
            return {"answer": "A persisted scientific table.", "partial": False, "primary_article_ids": order,
                "displayed_article_ids": order, "complementary_count": 2, "discovered_sources_count": 1,
                "citations": [{"url": "https://doi.org/10.1234/saved3", "cited": True}]}
    service.agent_factory = PublishingAgent
    sid = client.post("/scholar-v4/sessions", json={}).json()["id"]
    rid = client.post("/scholar-v4/chat", json={"session_id": sid, "message": "Recherche"}).json()["id"]
    assert wait_run(client, rid)["status"] == "completed"
    fresh = ScholarV4Service(factory, ScholarV4Config(api_key="mock"), Agent)
    try:
        snapshot = fresh.read(1, sid)
        assert snapshot["displayed_article_ids"][3] == "paper_saved_0"
        message = snapshot["messages"][-1]
        assert message["primary_article_ids"] == snapshot["displayed_article_ids"]
        assert message["complementary_count"] == 2 and len(message["citations"]) == 1
        params = [("exclude_ids", aid) for aid in message["primary_article_ids"]]
        secondary = client.get(f"/scholar-v4/sessions/{sid}/articles", params=params).json()
        assert secondary["total"] == 2
        assert {a["id"] for a in secondary["articles"]} == {"paper_saved_2", "paper_saved_4"}
        assert client.get(f"/scholar-v4/sessions/{sid}/articles").json()["total"] == 6
        memory = client.get(f"/scholar-v4/sessions/{sid}/memory").json()
        assert memory["finalization_attempts"][0]["rejection_code"] == "unverified_url"
        assert len(memory["web_sources"]) == 1
        assert client.get(f"/scholar-v4/sessions/{sid}/articles?exclude_ids=../bad").status_code == 422
        auth(app, 2)
        assert client.get(f"/scholar-v4/sessions/{sid}/memory").status_code == 404
        assert client.get(f"/scholar-v4/sessions/{sid}/articles", params=params).status_code == 404
    finally:
        fresh.pool.shutdown()


def test_library_categories_and_publication_coverage_are_independent(setup):
    app, client, service, factory = setup
    auth(app)
    class ScopedAgent:
        def run(self, data, emit, cancelled):
            for number, decision in enumerate(("retain", "retain", "pending", "reject", None)):
                aid = f"paper_group_{number}"
                article = Article(id=aid, title=f"Scoped paper {number}", verified=True)
                if decision:
                    article.assessment = {"decision": decision, "justification": "Recorded model judgment"}
                data["articles"][aid] = article.durable()
            data["examined_article_ids"] = [f"paper_group_{i}" for i in range(4)]
            data["displayed_article_ids"] = ["paper_group_0"]
            return {"answer": "Preliminary publication with insufficient coverage.", "partial": False,
                "primary_article_ids": ["paper_group_0"], "publication_accepted": True, "coverage_sufficient": False,
                "research_status": "incomplete", "complementary_count": 1,
                "library_counts": {"relevant": 2, "retained_complementary": 1, "unexamined": 1, "examined_pending": 1, "rejected": 1}}
    service.agent_factory = ScopedAgent
    sid = client.post("/scholar-v4/sessions", json={}).json()["id"]
    rid = client.post("/scholar-v4/chat", json={"session_id": sid, "message": "Find scientific papers"}).json()["id"]
    run = wait_run(client, rid)
    assert run["status"] == "completed" and run["publication_accepted"] and not run["coverage_sufficient"]
    assert run["research_status"] == "incomplete"
    message = client.get(f"/scholar-v4/sessions/{sid}").json()["messages"][-1]
    assert message["research_status"] == "incomplete" and message["library_counts"]["unexamined"] == 1
    for category, aid in (("retained_complementary", "paper_group_1"), ("unexamined", "paper_group_4"),
            ("examined_pending", "paper_group_2"), ("rejected", "paper_group_3")):
        page = client.get(f"/scholar-v4/sessions/{sid}/articles", params={"category": category, "exclude_ids": "paper_group_0"}).json()
        assert page["total"] == 1 and page["articles"][0]["id"] == aid
    assert client.get(f"/scholar-v4/sessions/{sid}/articles?category=all").json()["total"] == 5
    assert client.get(f"/scholar-v4/sessions/{sid}/articles?category=made_up").status_code == 422
    auth(app, 2)
    assert client.get(f"/scholar-v4/sessions/{sid}/articles?category=retained_complementary").status_code == 404
