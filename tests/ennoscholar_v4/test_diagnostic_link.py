import copy
import json
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db.models import User, Project, DiagnosticRun, Verrou, ProjectAccessRequest
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import SessionCreate, DiagnosticContextUpdate
from agents.EnnoScholarV4.service import ScholarV4Service, SessionBusy
from agents.EnnoScholarV4.session import ScholarV4Session
from agents.EnnoScholarV4.diagnostic_link import (active_packet, active_records, scope_from_message,
    retrieve, project_facts_view, LINKED_GUIDANCE)
from agents.EnnoScholarV4.toolkit import ScholarTools, definitions
from agents.EnnoScholarV4.context import model_context
from agents.EnnoScholarV4.argumentation import project_fact_valid


@pytest.fixture
def linked(tmp_path):
    engine = create_engine("sqlite:///"+str(tmp_path/"diagnostic.sqlite"), connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        for i in (1, 2, 3):
            db.add(User(id=i, full_name=f"User {i}", email=f"{i}@example.org", hashed_password="offline", role="admin" if i == 3 else "consultant"))
        db.flush()
        for i in (1, 2):
            db.add(Project(id=i, consultant_id=i, organisme=f"Client {i}", project_name=f"Projet {i}", year="2024"))
        db.flush()
        for pid in (1, 2):
            facts = []
            for lid in (1, 2):
                for kind, text in (("objective", "Réduire l'erreur"), ("method", "Solveur éléments finis"),
                                    ("parameter", "Température 37.25 °C ± 0.01"), ("result", "Échec à 1.2 MPa")):
                    eid = f"diag_{pid}_{lid}_{kind}"
                    facts.append({"entity_id": f"entity_{pid}_{lid}_{kind}", "kind": kind,
                        "statement": f"{text} pour verrou {lid}", "metadata": {"temporal_status": "performed", "source_role": "project"},
                        "evidences": [{"evidence_id": eid, "document_id": f"doc_{pid}", "document_name": f"Client {pid} essais",
                            "chunk_id": "chunk_4", "quote": f"Mesure source : {text}, verrou {lid}.", "page": 7,
                            "quote_verified": True, "char_start": 12, "char_end": 100}]})
            db.add(DiagnosticRun(id=pid, project_id=pid, status="completed", raw_result_json={"diagnostic_snapshot": {
                "entities": facts, "project_synthesis": {"main_objective": "Stabilité du procédé", "technical_context": "Milieu fortement non linéaire"}}}))
        db.flush()
        for pid in (1, 2):
            for lid in (1, 2):
                db.add(Verrou(id=pid*10+lid, diagnostic_run_id=pid, title=f"Verrou {lid} du client {pid}",
                    consultant_status="garde", justification="Décision consultant",
                    source_json={"member_entity_ids": [f"entity_{pid}_{lid}_{k}" for k in ("objective", "method", "parameter", "result")]}))
        db.commit()
    service = ScholarV4Service(factory, ScholarV4Config(api_key="mock"))
    yield service, factory
    service.pool.shutdown(wait=True)
    engine.dispose()


def create_link(service, owner=1, ids=None, allow=False):
    preview = service.diagnostic_preview(owner, 1)
    args = SessionCreate(mode="diagnostic_linked", project_id=1, diagnostic_id=preview["diagnostic_id"],
        source_version=preview["source_version"], selected_lock_ids=ids or ["11", "12"], confirmed=True, allow_unkept=allow)
    return service.create(owner, options=args)


def data_for(factory, sid):
    with factory() as db:
        return copy.deepcopy(db.get(ScholarV4Session, sid).data)


def test_express_is_default_and_never_converted(linked):
    service, factory = linked
    row = service.create(1)
    assert row["mode"] == "express" and "diagnostic_context" not in row
    with factory() as db:
        stored = db.get(ScholarV4Session, row["id"])
        stored.data = {**stored.data, "project_id": 1}
        db.commit()
    assert service.read(1, row["id"])["mode"] == "express"
    assert not any(t["name"] == "get_project_context" for t in definitions())


def test_linked_snapshot_reopens_without_diagnostic_rerun(linked):
    service, factory = linked
    row = create_link(service)
    fresh = ScholarV4Service(factory, ScholarV4Config(api_key="mock"))
    try:
        assert fresh.read(1, row["id"])["diagnostic_context"] == row["diagnostic_context"]
        assert row["active_run_id"] is None
    finally:
        fresh.pool.shutdown()


def test_authorized_projects_admin_and_accepted_collaboration(linked):
    service, factory = linked
    assert [r["project"]["id"] for r in service.diagnostic_projects(1)] == [1]
    assert len(service.diagnostic_projects(3)) == 2
    with factory() as db:
        db.add(ProjectAccessRequest(project_id=1, requester_id=2, owner_id=1, status="accepted"))
        db.commit()
    assert len(service.diagnostic_projects(2)) == 2


def test_one_project_per_latest_usable_diagnostic(linked):
    service, factory = linked
    with factory() as db:
        db.add(DiagnosticRun(id=9, project_id=1, status="failed", created_at=datetime.utcnow()+timedelta(days=1), raw_result_json={}))
        db.commit()
    rows = service.diagnostic_projects(1)
    assert len(rows) == 1 and rows[0]["diagnostic_id"] == 1


def test_kept_locks_and_single_selection(linked):
    service, factory = linked
    with factory() as db:
        db.get(Verrou, 12).consultant_status = "rejete"
        db.commit()
    preview = service.diagnostic_preview(1, 1)
    assert [v["id"] for v in preview["locks"] if v["decision"] == "garde"] == ["11"]
    assert create_link(service, ids=["11"])["diagnostic_context"]["active_lock_ids"] == ["11"]
    with pytest.raises(ValueError):
        create_link(service, ids=["12"], allow=True)


def test_ordinal_scope_filters_other_lock_but_keeps_objectives_and_technical_facts(linked):
    service, factory = linked
    state = data_for(factory, create_link(service)["id"])
    scope_from_message(state, "Je veux commencer seulement par le premier")
    assert state["diagnostic_link"]["active_lock_ids"] == ["11"]
    packet = active_packet(state)
    text = json.dumps(packet, ensure_ascii=False)
    assert "Stabilité du procédé" in text and "37.25 °C ± 0.01" in text and "Échec à 1.2 MPa" in text
    assert "pour verrou 2" not in text and "Verrou 2 du client" not in text
    scope_from_message(state, "Compare le premier article")
    assert state["diagnostic_link"]["active_lock_ids"] == ["11"]


def test_no_cross_client_preview_or_conversation_access(linked):
    service, _ = linked
    with pytest.raises(HTTPException) as exc:
        service.diagnostic_preview(1, 2)
    assert exc.value.status_code == 403
    row = create_link(service)
    from agents.EnnoScholarV4.service import SessionNotFound
    with pytest.raises(SessionNotFound):
        service.read(2, row["id"])


def test_revoked_project_access_hides_session_and_context(linked):
    service, factory = linked
    with factory() as db:
        db.add(ProjectAccessRequest(project_id=1, requester_id=2, owner_id=1, status="accepted"))
        db.commit()
    row = create_link(service, owner=2)
    with factory() as db:
        db.scalar(select(ProjectAccessRequest)).status = "rejected"
        db.commit()
    assert service.sessions(2) == []
    with pytest.raises(HTTPException):
        service.project_context(2, row["id"])


def test_scope_change_preserves_records_and_source_db(linked):
    service, factory = linked
    row = create_link(service)
    with factory() as db:
        stored = db.get(ScholarV4Session, row["id"])
        stored.data = {**stored.data, "research_gaps": [{"test": "retain"}], "document": {"versions": ["version1"]}}
        original = copy.deepcopy(db.get(DiagnosticRun, 1).raw_result_json)
        db.commit()
    result = service.update_project_context(1, row["id"], DiagnosticContextUpdate(action="scope", expected_revision=1, lock_ids=["12"]))
    state = data_for(factory, row["id"])
    assert result["diagnostic_context"]["active_lock_ids"] == ["12"]
    assert state["document"]["versions"] == ["version1"] and state["research_gaps"] == [{"test": "retain"}]
    with factory() as db:
        assert db.get(DiagnosticRun, 1).raw_result_json == original
        assert db.get(Verrou, 11).consultant_status == "garde"


def test_updated_source_requires_explicit_refresh_and_archives_previous(linked):
    service, factory = linked
    row = create_link(service)
    old = row["diagnostic_context"]["source_version"]
    with factory() as db:
        db.get(Verrou, 11).justification = "Nouvelle justification"
        db.commit()
    assert service.project_context(1, row["id"])["source_changed"] is True
    assert service.read(1, row["id"])["diagnostic_context"]["source_version"] == old
    preview = service.diagnostic_preview(1, 1)
    result = service.update_project_context(1, row["id"], DiagnosticContextUpdate(action="refresh", expected_revision=1,
        lock_ids=["11"], source_version=preview["source_version"], confirmed=True))
    assert result["diagnostic_context"]["source_version"] != old
    assert data_for(factory, row["id"])["diagnostic_link"]["source_history"][0]["source_version"] == old


def test_source_changed_between_preview_and_confirmation_rejected(linked):
    service, factory = linked
    preview = service.diagnostic_preview(1, 1)
    with factory() as db:
        db.get(Verrou, 11).consultant_status = "rejete"
        db.commit()
    with pytest.raises(SessionBusy):
        service.create(1, options=SessionCreate(mode="diagnostic_linked", project_id=1, diagnostic_id=1,
            source_version=preview["source_version"], selected_lock_ids=["11"], confirmed=True))


def test_no_kept_lock_requires_explicit_nonvalidated_selection(linked):
    service, factory = linked
    with factory() as db:
        for row in db.scalars(select(Verrou).where(Verrou.diagnostic_run_id == 1)):
            row.consultant_status = "en_attente"
        db.commit()
    with pytest.raises(ValueError):
        create_link(service, ids=["11"])
    row = create_link(service, ids=["11"], allow=True)
    assert row["diagnostic_context"]["locks"][0]["decision"] == "en_attente"


def test_creation_requires_confirmation_and_matching_diagnostic(linked):
    service, _ = linked
    with pytest.raises(ValueError):
        SessionCreate(mode="diagnostic_linked", project_id=1, diagnostic_id=1, source_version="x", selected_lock_ids=["11"])
    with pytest.raises(ValueError):
        service.create(1, options=SessionCreate(mode="diagnostic_linked", project_id=1, diagnostic_id=2,
            source_version="x", selected_lock_ids=["11"], confirmed=True))


def test_context_budget_and_exact_paginated_recovery(linked):
    service, factory = linked
    state = data_for(factory, create_link(service, ids=["11"])["id"])
    for i in range(100):
        fact = copy.deepcopy(state["diagnostic_link"]["snapshot"]["facts"][0])
        fact["id"] = f"large_{i}"
        fact["declared_lock_ids"] = ["11"]
        fact["statement"] = "Paramètre détaillé " * 200
        state["diagnostic_link"]["snapshot"]["facts"].append(fact)
    packet = active_packet(state)
    assert len(json.dumps(packet, ensure_ascii=False)) <= 4300 and packet["partial"]
    rows = []
    offset = 0
    while True:
        page = retrieve(state, "facts", offset, 3)
        rows.extend(page["items"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert len(rows) == len(active_records(state)[1])
    assert rows[-1]["statement"] == "Paramètre détaillé " * 200


def test_proof_provenance_and_project_publication_separation(linked):
    service, factory = linked
    state = data_for(factory, create_link(service, ids=["11"])["id"])
    fact = project_facts_view(state)[0]
    assert fact["source_type"] == "diagnostic_snapshot" and fact["proof_reference"]["page"] == 7
    assert fact["proof_reference"]["document_id"] == "doc_1"
    assert fact["exact_quote"].startswith("Mesure source") and project_fact_valid(state, {}, fact)
    altered = {**fact, "exact_quote": "Inventé"}
    assert not project_fact_valid(state, {}, altered)
    assert not state.get("evidence")
    assert "date de démarrage" in LINKED_GUIDANCE and "absence de résultats" in LINKED_GUIDANCE


def test_active_context_reaches_model_and_tool_without_express_overhead(linked):
    service, factory = linked
    state = data_for(factory, create_link(service, ids=["11"])["id"])
    state["messages"] = [{"id": "msg", "role": "user", "content": "Recherche ce verrou"}]
    tools = ScholarTools(state, None, service.config)
    context = model_context(state, tools, service.config.max_context_chars)
    rendered = json.dumps(context, ensure_ascii=False)
    assert "diagnostic_context" in rendered and "pour verrou 2" not in rendered
    result = tools.execute("get_project_context", {"kind": "facts", "offset": 0, "limit": 2, "ids": None}, 9999999999)
    assert len(result["items"]) == 2 and result["project_facts"]
    assert any(t["name"] == "get_project_context" for t in definitions(linked=True))


def test_scope_update_conflict_and_active_run_are_rejected(linked):
    service, factory = linked
    row = create_link(service)
    payload = DiagnosticContextUpdate(action="scope", expected_revision=9, lock_ids=["11"])
    with pytest.raises(SessionBusy):
        service.update_project_context(1, row["id"], payload)
    with factory() as db:
        db.get(ScholarV4Session, row["id"]).active_run_id = "busy"
        db.commit()
    with pytest.raises(SessionBusy):
        service.update_project_context(1, row["id"], payload.model_copy(update={"expected_revision": 1}))


def test_scope_restores_previous_research_without_reusing_old_axes(linked):
    from agents.EnnoScholarV4.diagnostic_link import set_scope
    service, factory = linked
    state = data_for(factory, create_link(service)["id"])
    set_scope(state, ["11"])
    state["research_state"] = {"topic": "Premier verrou", "search_axes": ["Axe réservé au premier"]}
    state["scientific_scope"] = {"start_date": "2022-03-01", "reference_date": "2022-03-01", "cir_year": 2024}
    set_scope(state, ["12"])
    assert "Axe réservé au premier" not in str(state["research_state"])
    assert state["scientific_scope"]["start_date"] == "2022-03-01" and state["scientific_scope"]["cir_year"] == 2024
    set_scope(state, ["11"])
    assert state["research_state"]["search_axes"] == ["Axe réservé au premier"]


def test_http_creation_project_context_and_scope(linked, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from core.deps import get_current_user
    from routers import scholar_v4
    service, _ = linked
    monkeypatch.setattr(scholar_v4, "get_service", lambda: service)
    app = FastAPI()
    app.include_router(scholar_v4.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    client = TestClient(app)
    projects = client.get("/scholar-v4/diagnostic-projects").json()
    source = projects[0]
    created = client.post("/scholar-v4/sessions", json={"mode": "diagnostic_linked", "project_id": 1,
        "diagnostic_id": source["diagnostic_id"], "source_version": source["source_version"],
        "selected_lock_ids": ["11", "12"], "confirmed": True})
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    assert client.get(f"/scholar-v4/sessions/{sid}/project-context?kind=evidence").json()["items"]
    response = client.patch(f"/scholar-v4/sessions/{sid}/project-context", json={"action": "scope", "expected_revision": 1, "lock_ids": ["11"]})
    assert response.status_code == 200 and response.json()["diagnostic_context"]["active_lock_ids"] == ["11"]


def test_roles_and_temporal_status_are_not_relabelled_as_project_experiments(linked):
    service, factory = linked
    state = data_for(factory, create_link(service, ids=["11"])["id"])
    fact = state["diagnostic_link"]["snapshot"]["facts"][0]
    fact["source"]["metadata"] = {"source_role": "literature", "temporal_status": "planned"}
    packet = active_packet(state)
    roles = packet["facts"][0]["source_roles"]
    assert roles["source_role"] == "literature" and roles["temporal_status"] == "planned"
    assert all(f["source_fact_id"] != fact["id"] for f in project_facts_view(state))
    assert retrieve(state, "facts", 0, 1)["items"][0]["source"]["metadata"]["source_role"] == "literature"


def test_linked_targeted_revision_preserves_existing_drafts_and_budgets(linked):
    from tests.ennoscholar_v4.test_targeted_revision import revision_case, save_from_revision
    from tests.ennoscholar_v4.test_context_memory import Client, agent, memory_of
    service, factory = linked
    source_state = data_for(factory, create_link(service, ids=["11"])["id"])
    tools, _ = revision_case()
    tools.state.update(mode="diagnostic_linked", diagnostic_link=source_state["diagnostic_link"])
    before = copy.deepcopy(tools.state["outline"]["sections"])
    def respond(n, kw):
        assert memory_of(kw)["diagnostic_context"]["active_lock_ids"] == ["11"]
        assert "lecture seule" in kw["instructions"]
        return save_from_revision(n, kw)
    result = agent(Client(respond)).run(tools.state)
    assert not result["partial"] and result["rounds"] == 1
    assert result["tool_counts"]["write_section"] == 1
    assert all(section == before[i] for i, section in enumerate(tools.state["outline"]["sections"]) if i != 4)
    assert tools.state["outline"]["sections"][4]["drafts"][:-1] == before[4]["drafts"]


def test_offline_token_overhead_stays_bounded_when_snapshot_grows(linked):
    import tiktoken
    encoder = tiktoken.get_encoding("o200k_base")
    service, factory = linked
    state = data_for(factory, create_link(service, ids=["11"])["id"])
    state["messages"] = [{"id": "msg", "role": "user", "content": "Recherche ce verrou"}]
    def tokens(value):
        return len(encoder.encode(json.dumps(value, ensure_ascii=False)))
    express = copy.deepcopy(state)
    express.pop("diagnostic_link")
    express["mode"] = "express"
    base = tokens(model_context(express, ScholarTools(express, None, service.config), service.config.max_context_chars))
    linked_tokens = tokens(model_context(state, ScholarTools(state, None, service.config), service.config.max_context_chars))
    source = state["diagnostic_link"]["snapshot"]
    for i in range(1000):
        source["facts"].append({"id": f"fact_{i}", "kind": "parameter", "statement": "Paramètre exact à récupérer. "*100,
                                "evidence_ids": [], "source": {"needs_review": True}})
    large = tokens(model_context(state, ScholarTools(state, None, service.config), service.config.max_context_chars))
    tool_overhead = tokens(definitions(linked=True))-tokens(definitions())
    guidance = len(encoder.encode(LINKED_GUIDANCE))
    assert large-base < 1800 and tool_overhead < 500 and guidance < 400
    assert tokens(source) > large * 50
    print(f"TOKEN_MEASURE express={base} linked={linked_tokens} large={large} tool={tool_overhead} guidance={guidance} source={tokens(source)}")


def test_explicit_lock_relations_precede_shared_family_and_evidence(linked):
    service, factory = linked
    state = data_for(factory, create_link(service, ids=["11"])["id"])
    source = state["diagnostic_link"]["snapshot"]
    source["locks"][0]["source"].update(entity_id="lock_a", family_id="shared")
    source["locks"][1]["source"].update(entity_id="lock_b", family_id="shared")
    source["facts"].append({"id": "method_b", "kind": "method", "statement": "Méthode réservée au verrou B", "evidence_ids": [],
        "source": {"family_id": "shared", "metadata": {"related_lock_ids": ["lock_b"]}}})
    assert "Méthode réservée" not in str(active_records(state))


def test_unverified_diagnostic_quote_cannot_satisfy_project_fact_requirement(linked):
    service, factory = linked
    state = data_for(factory, create_link(service, ids=["11"])["id"])
    source = state["diagnostic_link"]["snapshot"]
    eid = source["facts"][0]["evidence_ids"][0]
    source["evidence"][eid]["quote_verified"] = False
    assert all(f["diagnostic_evidence_id"] != eid for f in project_facts_view(state))
    assert any(r["evidence_id"] == eid for r in retrieve(state, "evidence", limit=10)["items"])
    source["evidence"][eid]["quote_verified"] = True
    source["facts"][0]["source"]["needs_review"] = True
    assert all(f["diagnostic_evidence_id"] != eid for f in project_facts_view(state))


def test_out_of_scope_context_remains_retrievable_without_activating_it(linked):
    service, factory = linked
    state = data_for(factory, create_link(service, ids=["11"])["id"])
    assert "pour verrou 2" not in str(retrieve(state, "facts", limit=20))
    assert "pour verrou 2" in str(retrieve(state, "facts", limit=20, scope="all"))
    assert state["diagnostic_link"]["active_lock_ids"] == ["11"]


def test_linked_search_uses_active_technical_context_with_existing_tool_budget(linked):
    from unittest.mock import Mock
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from tests.ennoscholar_v4.test_finalization import Client, call
    from tests.ennoscholar_v4.test_context_memory import memory_of
    from dataclasses import replace
    service, factory = linked
    state = data_for(factory, create_link(service)["id"])
    state["messages"] = [{"id": "msg", "role": "user", "content": "Je veux commencer seulement par le premier"}]
    sources = Mock()
    sources.last_provider_stats = None
    sources.search.return_value = ([], [])
    def respond(n, kw):
        packet = memory_of(kw)["diagnostic_context"]
        assert packet["active_lock_ids"] == ["11"]
        assert "37.25 °C" in str(packet) and "pour verrou 2" not in str(packet)
        if n == 1:
            return call("search_scientific", {"query": "finite element nonlinear stability pressure temperature", "sources": ["openalex"],
                "limit_per_source": 8, "offset": 0, "family": "Verrou 1"}, n)
        return call("finalize_response", {"answer": "Aucun résultat dans la recherche effectuée. Cela ne démontre aucune nouveauté.",
            "article_ids": [], "evidence_ids": [], "coverage_sufficient": False,
            "remaining_uncertainties": ["Couverture à approfondir"], "analyses": None, "displayed_article_ids": []}, n)
    config = replace(service.config, web_search_enabled=False)
    result = ScholarChatAgent(config, client=Client(respond), sources=sources).run(state)
    assert not result["partial"], result["reason"]
    assert result["rounds"] == 2 and sources.search.call_count == 1
    assert not result["tool_counts"].get("analyze_article")


def test_project_recovery_during_blueprint_does_not_skip_the_scientific_stage(linked):
    from tests.ennoscholar_v4.test_context_memory import rich_corpus, seven_section_plan, REQUEST_A, Client, Model, memory_of, agent
    from tests.ennoscholar_v4.test_finalization import call
    service, factory = linked
    linked_state = data_for(factory, create_link(service, ids=["11"])["id"])
    tools, ids = rich_corpus(2)
    seven_section_plan(tools, ids)
    tools.state.update(mode="diagnostic_linked", diagnostic_link=linked_state["diagnostic_link"])
    tools.state["messages"] = [{"id": "write", "role": "user", "content": REQUEST_A}]
    model = Model(ids, [])
    recovered = []
    def respond(n, kw):
        memory = memory_of(kw)
        focus = memory.get("argumentation_focus")
        if focus and focus["stage"] == "blueprint" and not recovered:
            assert kw["tool_choice"] == "required"
            recovered.append(True)
            return call("get_project_context", {"kind": "facts", "scope": "active", "offset": 0, "limit": 2, "ids": None}, n)
        if kw["tool_choice"] == "required":
            name = {"comparison": "compare_articles", "blueprint": "build_argument_blueprint"}.get((focus or {}).get("stage"), "manage_outline")
            kw = {**kw, "tool_choice": {"type": "function", "name": name}}
        return model(n, kw)
    result = agent(Client(respond)).run(tools.state)
    assert recovered and not result["partial"], result["reason"]
    assert result["tool_counts"]["get_project_context"] == 1
    assert result["tool_counts"]["build_argument_blueprint"] >= 1
    assert result["writing_progress"]["status"] == "draft_complete"
