"""Explicit reconciliation of an uncertain paid call (run b10ab481: APIConnectionError on a
sent call), then a single explicit resume. Offline transport, temporary databases only."""
import copy
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.EnnoScholarV5.models import Article
from agents.EnnoScholarV5.persistence import Budget
from agents.EnnoScholarV5.resources import ACTIVE, balance
from agents.EnnoScholarV5.service import SessionBusy
from agents.EnnoScholarV5.session import ScholarV4Session
from .test_live_integration import app_env, call, finish, run_sync, scientific_state, NoSocketLoop

QUERY = {"query": "enzymatic PET recycling", "sources": ["openalex", "semantic_scholar", "arxiv"], "limit_per_source": 8,
    "offset": 0, "family": "PET hydrolases"}


def interrupted(app_env, after=()):
    """One saved search, then the next call is sent and its connection drops (APIConnectionError)."""
    make, factory, _ = app_env
    sources = Mock()
    sources.search.return_value = ([Article(title="Engineered PET hydrolase", doi="10.1234/pet", authors=["A Author"], year=2024)], [])
    sources.last_provider_stats = {"openalex": {"returned": 1}}
    drop = httpx.RemoteProtocolError("peer closed connection without sending complete message body")
    svc, sid, tr = make([call("search_scientific", QUERY, "s1"), drop, *after], sources=sources)
    result = run_sync(svc, sid, "Fais un état de l'art sur le recyclage enzymatique du PET.")
    return svc, sid, tr, factory, sources, result


def test_connection_drop_is_uncertain_and_resume_stays_refused_without_reconciliation(app_env):
    svc, sid, tr, factory, sources, result = interrupted(app_env)
    assert result["reason"] == "uncertain_model_call" and result["diagnostic"]["code"] == "APIConnectionError"
    assert result["usage"]["uncertain_usd"] > 0 and len(tr.requests) == 2
    with pytest.raises(SessionBusy, match="incertain"):
        svc.resume_run(1, result["id"])
    assert len(tr.requests) == 2 and sources.search.call_count == 1


def test_reconciliation_counts_the_maximum_and_starts_nothing(app_env):
    svc, sid, tr, factory, sources, result = interrupted(app_env)
    with factory() as db:
        before = copy.deepcopy(db.get(Budget, result["id"]).data)
    science = {k: v for k, v in scientific_state(factory, sid).items() if k in {"articles", "recent_searches", "evidence"}}
    done = svc.reconcile_run(1, result["id"])
    assert done["counted_usd"] == pytest.approx(result["usage"]["uncertain_usd"]) and done["resumable"]
    with factory() as db:
        after = db.get(Budget, result["id"]).data
        session = db.get(ScholarV4Session, sid).data
    assert not [e for e in after["entries"] if e["status"] in ACTIVE]
    closed = [e for e in after["entries"] if e.get("reconciliation")]
    assert len(closed) == 1 and closed[0]["cost_usd"] == closed[0]["reserved_usd"] and closed[0]["response_received"] is False
    # Same commitment: the uncertain maximum becomes counted, never forgotten.
    assert balance(after)["committed_usd"] == pytest.approx(balance(before)["committed_usd"])
    assert balance(after)["tokens"] == balance(before)["tokens"]
    assert "v5_provider_failure" not in session
    assert {k: v for k, v in session.items() if k in science} == science       # saved science untouched
    run = svc.run(1, result["id"])
    assert run["status"] == "partial" and run["budget_status"]["unresolved_usd"] == 0
    assert run["v5_reconciliations"][0]["provider_failure"]["code"] == "APIConnectionError"
    assert len(tr.requests) == 2                                                  # nothing was called
    assert svc.reconcile_run(1, result["id"])["reconciled_calls"] == []          # idempotent


def test_reconciled_run_resumes_once_without_repeating_saved_operations(app_env):
    svc, sid, tr, factory, sources, result = interrupted(app_env, after=[finish("Synthèse après reprise explicite.")])
    svc.reconcile_run(1, result["id"])
    with factory() as db:
        reconciled = copy.deepcopy(db.get(Budget, result["id"]).data)
    svc.resume_run(1, result["id"])
    svc.pool.shutdown(wait=True)
    current = svc.run(1, result["id"])
    assert len(tr.requests) == 3 and sources.search.call_count == 1               # one new call, no new search
    assert "Synthèse après reprise" in current["answer"] and "APIConnectionError" not in current["answer"]
    with factory() as db:
        after = db.get(Budget, result["id"]).data
    assert after["entries"][:len(reconciled["entries"])] == reconciled["entries"]   # history kept
    assert len(after["entries"]) == len(reconciled["entries"]) + 1
    assert after["limits"] == reconciled["limits"]                                  # budget not refilled


def test_reconciliation_is_refused_while_a_run_is_active_and_for_another_owner(app_env):
    svc, sid, tr, factory, sources, result = interrupted(app_env)
    with factory() as db:
        db.get(ScholarV4Session, sid).active_run_id = result["id"]
        db.commit()
    with pytest.raises(SessionBusy):
        svc.reconcile_run(1, result["id"])
    with factory() as db:
        db.get(ScholarV4Session, sid).active_run_id = None
        db.commit()
    from agents.EnnoScholarV5.service import SessionNotFound
    with pytest.raises(SessionNotFound):
        svc.reconcile_run(2, result["id"])
    with factory() as db:
        assert [e for e in db.get(Budget, result["id"]).data["entries"] if e["status"] == "uncertain"]


def test_graph_refuses_an_uncertain_resume_when_the_ledger_is_not_settled(app_env):
    svc, sid, tr, factory, sources, result = interrupted(app_env)
    with factory() as db:
        # A forged reconciliation record without settling the ledger does not unlock the graph.
        from agents.EnnoScholarV5.session import ScholarV4Run
        run = db.get(ScholarV4Run, result["id"])
        run.data = {**run.data, "v5_reconciliations": [{"calls": []}]}
        db.get(ScholarV4Session, sid).active_run_id = result["id"]
        run.status = "queued"
        db.commit()
    svc.slots.acquire()
    svc.execute(1, sid, result["id"])
    assert len(tr.requests) == 2                                                  # no paid call


def test_reconcile_endpoint(app_env, monkeypatch):
    from routers import scholar_v5
    from core.deps import get_current_user
    svc, sid, tr, factory, sources, result = interrupted(app_env)
    monkeypatch.setenv("ENNOSCHOLAR_V5_TEST_ENABLED", "1")
    monkeypatch.setattr(scholar_v5, "get_service", lambda: svc)
    app = FastAPI()
    app.include_router(scholar_v5.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    client = TestClient(app, backend_options={"loop_factory": NoSocketLoop})
    response = client.post(f"/scholar-v5/runs/{result['id']}/reconcile", json={})
    assert response.status_code == 200 and response.json()["counted_usd"] > 0
    assert len(tr.requests) == 2
