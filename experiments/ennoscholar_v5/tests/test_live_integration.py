"""Production Responses SDK + real V4 tools + application persistence; all HTTP simulated."""
import copy
import asyncio
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
from openai import OpenAI
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from fastapi import FastAPI
from fastapi.testclient import TestClient
from db.database import Base
from db.models import User
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.session import ScholarV4Session as V4Session
from agents.EnnoScholarV4.service import ScholarV4Service
from agents.EnnoScholarV5.session import ScholarV4Session, ScholarV4Run, sync_records
from agents.EnnoScholarV5.service import ScholarV5Service, SessionBusy
from agents.EnnoScholarV5.persistence import ApplicationRepository, RunMeter, Budget, Operation
from agents.EnnoScholarV5.runtime import ScientificAdapter, ResponsesSol
from experiments.ennoscholar_v5.checkpoints import sqlite_checkpoints
from experiments.ennoscholar_v5.graph import Runner
from experiments.ennoscholar_v5.progress import Scope
from .test_graph import case, read, payload, LI, STOKES, ARTIFACTS


class NoSocketLoop(asyncio.SelectorEventLoop):
    """ASGI test loop: poll local callbacks, never create even a loopback socket."""
    def _make_self_pipe(self):
        self._ssock = self._csock = None

    def _close_self_pipe(self):
        pass

    def __init__(self):
        super().__init__()
        original = self._selector.select
        def select_local(timeout=None):
            bounded = min(.01, timeout) if timeout is not None else .01
            if self._selector.get_map():
                return original(bounded)
            time.sleep(max(0, bounded))
            return []
        self._selector.select = select_local


class Transport:
    def __init__(self, choices):
        self.choices, self.requests = list(choices), []
        self.client = OpenAI(api_key="offline", max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(self.respond)))

    def respond(self, request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        sent = json.loads(request.content)
        self.requests.append(sent)
        assert sent["model"] == "gpt-6.1-sol" and sent["stream"] and sent["store"] is False
        if not self.choices:
            raise AssertionError("Unexpected Responses request")
        choice = self.choices.pop(0)
        if callable(choice):
            choice = choice(sent)
        if isinstance(choice, Exception):
            raise choice
        if isinstance(choice, httpx.Response):
            return choice
        output = choice if isinstance(choice, list) else [choice]
        response = {"id": "resp_"+str(len(self.requests)), "object": "response", "created_at": 1,
            "model": "gpt-6.1-sol", "service_tier": "default", "status": "completed", "output": output,
            "usage": {"input_tokens": 100, "output_tokens": 50, "input_tokens_details": {"cached_tokens": 20},
                "output_tokens_details": {"reasoning_tokens": 5}}}
        event = {"type": "response.completed", "sequence_number": 1, "response": response}
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
            text="event: response.completed\ndata: "+json.dumps(event)+"\n\ndata: [DONE]\n\n")


def call(tool, args, identifier="c1"):
    return {"type": "function_call", "id": "fc_"+identifier, "call_id": identifier, "name": tool,
        "arguments": json.dumps(args), "status": "completed"}


def finish(answer="La lecture reste partielle ; les documents sont conservés."):
    return call("finalize_response", {"answer": answer, "article_ids": [], "evidence_ids": [],
        "coverage_sufficient": False, "remaining_uncertainties": ["Lecture incomplète"],
        "analyses": None, "displayed_article_ids": None}, "final")


@pytest.fixture
def app_env(tmp_path):
    engine = create_engine("sqlite:///"+str(tmp_path/"application.sqlite3"), connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(User(id=1, full_name="Offline", email="offline@example.invalid", hashed_password="x", is_active=True))
        db.commit()
    @contextmanager
    def checkpoints(_):
        with sqlite_checkpoints(tmp_path/"graph.sqlite3") as saver:
            yield saver
    services = []
    def make(choices, state=None, config=None, sources=None):
        transport = Transport(choices)
        svc = ScholarV5Service(factory, config or ScholarV4Config(api_key="offline", web_search_enabled=False),
            client=transport.client, sources=sources or Mock(), checkpointer_factory=checkpoints)
        services.append(svc)
        sid = svc.create(1)["id"]
        if state is not None:
            with factory() as db:
                row = db.get(ScholarV4Session, sid)
                row.data = {**copy.deepcopy(state), "engine": "v5_langgraph"}
                sync_records(db, row)
                db.commit()
        return svc, sid, transport
    yield make, factory, checkpoints
    for svc in services:
        svc.pool.shutdown(wait=True)
    engine.dispose()


def run_sync(svc, sid, message="Continue la lecture scientifique des articles conservés."):
    rid = svc.open_run(1, sid, message)
    svc.slots.acquire()
    svc.execute(1, sid, rid)
    return svc.run(1, rid)


def scientific_state(factory, sid):
    with factory() as db:
        return copy.deepcopy(db.get(ScholarV4Session, sid).data)


def test_real_sdk_and_scientific_tools_cross_langgraph_more_than_six_turns(app_env):
    make, factory, _ = app_env
    s = case("chemistry")["state"]
    choices = [call("analyze_article", read(LI, passage_offset=i*4).arguments, str(i)) for i in range(7)]
    def save(sent):
        result = next(json.loads(x["output"]) for x in reversed(sent["input"]) if x.get("type") == "function_call_output")
        eid = result["reading"][0]["passages"][0]["evidence_id"]
        args = read(LI).arguments
        args.update(action="save", analysis=payload(eid))
        return call("analyze_article", args, "save")
    svc, sid, transport = make([*choices, save, finish()], s)
    result = run_sync(svc, sid)
    assert result["engine"] == "langgraph_v5", result
    assert len(transport.requests) == 9
    data = scientific_state(factory, sid)
    assert data["articles"][LI]["analysis_basis"] == "fulltext"
    assert result["usage"]["llm_calls"] == 9 and result["usage"]["input_tokens"] == 900
    assert data["research_progress"]["requested_count"] == 10
    assert data["research_progress"]["missing_count"] == 5
    assert any(t["kind"] == "complete_selection" for t in result["remaining_tasks"].values())
    assert all("max_tool_rounds" not in req["instructions"] and "rounds left" not in req["instructions"] and "six tours" not in req["instructions"] for req in transport.requests)
    # All original controls + writing/plan capabilities are exposed to Sol.
    names = {t.get("name") for t in transport.requests[0]["tools"]}
    assert {"search_scientific", "get_fulltext", "analyze_article", "compare_articles", "identify_research_gaps",
        "manage_outline", "write_section", "verify_bibliography"} <= names
    for sent in transport.requests[1:]:
        assert any(x.get("type") == "function_call_output" for x in sent["input"])


def test_stokes_remainder_uses_same_pdf_and_preserves_page_metadata(app_env, monkeypatch):
    make, factory, _ = app_env
    s = case("stokes")["state"]
    monkeypatch.setenv("ENNOSCHOLAR_V5_PDF_DIRECTORY", str(ARTIFACTS/"pdf"))
    pdf = ARTIFACTS/"pdf"/s["articles"][STOKES]["fulltext"]["pdf_file"]
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()
    choices = [call("get_fulltext", {"article_id": STOKES, "candidate_urls": None, "page_start": 21, "page_limit": 9}, "extract"),
        call("analyze_article", read(STOKES, passage_query="STAR+METHODS METHODS DETAILS", passages_per_article=8).arguments, "methods"), finish()]
    svc, sid, tr = make(choices, s)
    result = run_sync(svc, sid, "Continue l’extraction des neuf pages restantes de Stokes et examine ses méthodes scientifiques.")
    data = scientific_state(factory, sid)
    ft = data["articles"][STOKES]["fulltext"]
    assert ft["next_page"] is None and ft["page_count"] == 29, result
    assert all(ft["page_extraction"][str(p)]["status"] == "complete" for p in range(1, 30))
    assert data["articles"][STOKES]["analysis"] == s["articles"][STOKES]["analysis"]
    assert all(data["evidence"][eid] == e for eid, e in s["evidence"].items())
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == before
    assert any("METHOD" in chunk["section"].upper() for chunk in ft["chunks"])
    assert all("page" in chunk and "page_char_offset" in chunk for chunk in ft["chunks"])
    assert len(tr.requests) == 3


def test_search_reuse_and_native_web_sources_use_production_transport(app_env):
    from agents.EnnoScholarV5.models import Article
    make, factory, _ = app_env
    sources = Mock()
    sources.search.return_value = ([Article(title="Copper CO2 catalysis", doi="10.1234/test", authors=["A Author"], year=2024)], [])
    sources.last_provider_stats = {"openalex": {"returned": 1}}
    query = {"query": "copper CO2 catalysis", "sources": ["openalex", "semantic_scholar", "arxiv"], "limit_per_source": 8,
        "offset": 0, "family": "copper"}
    native = {"type": "web_search_call", "id": "web1", "status": "completed",
        "action": {"type": "search", "query": "CO2 copper", "sources": [{"type": "url", "url": "https://example.org/science", "title": "Science"}]}}
    svc, sid, tr = make([[native], call("search_scientific", query, "s1"), call("search_scientific", query, "s2"), finish()],
        config=ScholarV4Config(api_key="offline", web_search_enabled=True), sources=sources)
    result = run_sync(svc, sid, "Recherche dix publications sur la catalyse CO2 du cuivre.")
    assert sources.search.call_count == 1, result
    assert sources.search.call_args.args[1] == ["arxiv", "openalex", "semantic_scholar"] or set(sources.search.call_args.args[1]) == set(query["sources"])
    data = scientific_state(factory, sid)
    assert data["web_sources"][0]["url"] == "https://example.org/science"
    assert result["usage"]["tools"] == 3  # Web, one search, publication (repeated search was reused).
    assert result["usage"]["estimated_cost_usd"] > .01
    assert tr.requests[0]["tools"][0]["type"] == "web_search"


def test_unknown_provider_call_keeps_reservation_and_cannot_be_retried(app_env):
    make, factory, _ = app_env
    svc, sid, tr = make([httpx.ReadTimeout("simulated interruption")])
    result = run_sync(svc, sid)
    assert result["reason"] == "uncertain_model_call", result
    assert result["usage"]["uncertain_usd"] > 0 and result["usage"]["uncertain_tokens"] > 0
    with pytest.raises(SessionBusy, match="incertain"):
        svc.resume_run(1, result["id"])
    assert len(tr.requests) == 1


def test_hard_budget_refused_before_transport_and_not_refilled_by_new_node(app_env):
    make, factory, _ = app_env
    svc, sid, tr = make([finish()], config=ScholarV4Config(api_key="offline", max_cost_usd=.000001, web_search_enabled=False))
    result = run_sync(svc, sid)
    assert not tr.requests and result["reason"] == "admission_refused", result
    assert result['usage']['llm_calls']==0  # A refused request has consumed nothing.
    with factory() as db:
        budget = db.get(Budget, result["id"]).data
        assert budget["limits"]["usd"] == .000001 and not budget["entries"]


def test_application_receipt_survives_crash_after_commit_without_second_http(app_env):
    make, factory, checkpoints = app_env
    svc, sid, tr = make([call("get_article", {"identifiers": [LI]}, "meta"), finish()], case("chemistry")["state"])
    rid = svc.open_run(1, sid, "Continue la lecture.")
    scope = Scope(1, sid)
    repo = ApplicationRepository(svc, scope)
    meter = RunMeter(svc, scope, rid, svc.config)
    adapter = ScientificAdapter(svc.config, meter, sources=svc.sources)
    sol = ResponsesSol(svc.config, meter, client=tr.client)
    crashed = []
    def crash(opid):
        if repo.operation(scope, opid)["kind"] == "decision" and not crashed:
            crashed.append(opid)
            raise RuntimeError("power loss after commit")
    with checkpoints(None) as saver:
        runner = Runner(scope, repo, adapter, sol, saver, after_commit=crash)
        with pytest.raises(RuntimeError, match="power loss"):
            runner.start()
    with checkpoints(None) as saver:
        resumed = Runner(scope, repo, adapter, sol, saver).resume()
    assert len(tr.requests) == 2 and resumed["usage"]["llm_calls"] == 2
    assert repo.operation(scope, crashed[0])["status"] == "complete"


def test_resume_endpoint_preserves_budget_and_science(app_env, monkeypatch):
    make, factory, _ = app_env
    # The first notice is delivered knowledge; the three identical replays are the loop.
    svc, sid, tr = make([call("get_article", {"identifiers": [LI]}, "one"), call("get_article", {"identifiers": [LI]}, "two"),
        call("get_article", {"identifiers": [LI]}, "three"), call("get_article", {"identifiers": [LI]}, "four"), finish(),
        call('analyze_article', read(LI).arguments, 'resumed_read'), finish()], case("chemistry")["state"])
    result = run_sync(svc, sid)
    assert result["reason"] == "stagnation", result
    old = result["usage"]["estimated_cost_usd"]
    # Resume executes in the real service thread, retaining the same run ID.
    pending = svc.resume_run(1, result["id"])
    svc.pool.shutdown(wait=True)
    resumed = svc.run(1, pending["id"])
    assert resumed["usage"]["estimated_cost_usd"] > old
    assert resumed["id"] == result["id"] and len(tr.requests) == 7
    data = scientific_state(factory, sid)
    assert data["articles"][LI]["fulltext"]


def test_api_ownership_engine_selection_and_plan_manuscript_preservation(app_env, monkeypatch):
    from routers import scholar_v5
    from core.deps import get_current_user
    make, factory, _ = app_env
    svc, sid, tr = make([])
    v4 = ScholarV4Service(factory, svc.config)
    original = v4.create(1)["id"]
    app = FastAPI()
    app.include_router(scholar_v5.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    monkeypatch.setattr(scholar_v5, "get_service", lambda: svc)
    client = TestClient(app, backend_options={"loop_factory": NoSocketLoop})
    monkeypatch.delenv("ENNOSCHOLAR_V5_TEST_ENABLED", raising=False)
    assert client.get("/scholar-v5/sessions").status_code == 404
    monkeypatch.setenv("ENNOSCHOLAR_V5_TEST_ENABLED", "1")
    assert client.get("/scholar-v5/sessions/"+original).status_code == 404
    assert [x["id"] for x in client.get("/scholar-v5/sessions").json()] == [sid]
    assert client.get("/scholar-v5/sessions/"+sid+"/document").status_code == 200
    with factory() as db:
        row = db.get(ScholarV4Session, sid)
        row.data = {**row.data, "outline": {"version": 1, "sections": [{"id": "section_keep", "title": "Méthodes", "drafts": [{"id": "draft_keep", "text": "Texte scientifique conservé."}]}]},
            "document": {"revision": 1}}
        db.commit()
    before = scientific_state(factory, sid)
    response = client.get("/scholar-v5/sessions/"+sid)
    assert response.status_code == 200
    assert scientific_state(factory, sid)["outline"] == before["outline"]
    assert v4.read(1, original)["messages"] == [] and not tr.requests
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=2)
    assert client.get("/scholar-v5/sessions/"+sid).status_code == 404
    v4.pool.shutdown(wait=True)


@pytest.mark.parametrize("title", ["CO 2 reduction", "CO₂ reduction", "CO<sub>2</sub> reduction"])
def test_v5_chemical_identity_normalization_is_scoped(title):
    from agents.EnnoScholarV5.sources import title_key
    from agents.EnnoScholarV4.sources import title_key as v4_key
    assert title_key(title) == title_key("CO2 reduction")
    assert title_key("CO2 oxidation") != title_key(title)
    assert v4_key("CO 2 reduction") != v4_key("CO2 reduction")


@pytest.mark.parametrize("heading", ["STAR+METHODS", "METHODS DETAILS", "12 METHODS DETAILS"])
def test_v5_methods_headings(heading):
    from agents.EnnoScholarV5.tools.extraction import scientific_heading
    assert scientific_heading(heading) == heading


@pytest.mark.parametrize("line", ["Table S1 reports all parameters.", "Supplementary Table 2 shows accuracy", "Table S2", "Figure S3 displays results"])
def test_table_mentions_are_not_sections(line):
    from agents.EnnoScholarV5.tools.extraction import scientific_heading
    assert scientific_heading(line) is None


def test_article_batch_failure_does_not_discard_other_metadata_or_verification():
    from agents.EnnoScholarV5.toolkit import ScholarTools
    from agents.EnnoScholarV5.models import Article
    sources = Mock()
    good = Article(title="Useful scientific paper", authors=["A Author"], year=2024, doi="10.1234/good", verified=True)
    sources.metadata.side_effect = lambda identifier, *a: (_ for _ in ()).throw(RuntimeError("registry unavailable")) if identifier == "bad" else [good]
    state = {"articles": {}, "messages": []}
    t = ScholarTools(state, sources, ScholarV4Config(api_key="offline"))
    result = t.execute("get_article", {"identifiers": ["bad", "good"]}, 1e12)
    assert result["errors"] and result["articles"][0]["title"] == good.title
    sources.verify.side_effect = RuntimeError("DOI outage")
    checked = t.verify([good.id, "missing"], 1e12)
    assert len(checked["articles"]) == 2 and not checked["valid"]


def test_no_modification_to_existing_v4_sources():
    root = Path(__file__).resolve().parents[3]
    baseline = json.loads((ARTIFACTS/"integration-baseline.json").read_text())
    assert all(hashlib.sha256((root/path).read_bytes()).hexdigest() == digest for path, digest in baseline.items())


def test_real_scientific_provider_clients_use_simulated_http_and_reuse_search(app_env):
    from agents.EnnoScholarV5.sources import ScientificSources
    make, factory, _ = app_env
    calls = []
    def provider(request):
        calls.append(request.url.host)
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"results": [{"id": "https://openalex.org/W1", "title": "CO2 catalysis", "doi": "https://doi.org/10.1234/shared", "publication_year": 2024,
                "authorships": [{"author": {"display_name": "Ada Author"}}], "abstract_inverted_index": {"Copper": [0], "catalysis": [1]}, "primary_location": {"is_oa": True}}]})
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(200, json={"data": [{"paperId": "s2", "title": "CO2 catalysis", "authors": [{"name": "Ada Author"}], "year": 2024,
                "abstract": "Copper catalysis", "externalIds": {"DOI": "10.1234/shared"}}]})
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/2401.00001</id><title>Scientific catalyst method</title><published>2024-01-01</published><author><name>Ada Author</name></author><summary>Copper catalysis</summary></entry></feed>')
        raise AssertionError("Unexpected scientific endpoint")
    config = ScholarV4Config(api_key="offline", web_search_enabled=False)
    sources = ScientificSources(config=config)
    sources.client.client = httpx.Client(transport=httpx.MockTransport(provider))
    query = {"query": "copper catalysis stability", "sources": ["openalex", "semantic_scholar", "arxiv"], "limit_per_source": 8, "offset": 0, "family": "catalysis"}
    svc, sid, tr = make([call("search_scientific", query, "a"), call("search_scientific", query, "b"), finish()], sources=sources)
    result = run_sync(svc, sid, "Recherche dix articles de catalyse du cuivre.")
    assert sorted(calls) == sorted(["api.openalex.org", "api.semanticscholar.org", "export.arxiv.org"]), result
    articles = scientific_state(factory, sid)["articles"]
    assert len(articles) == 2  # OpenAlex and Semantic Scholar notices merged by DOI.
    assert any(set(a["sources"]) >= {"openalex", "semantic_scholar"} for a in articles.values())
    assert not any(a.get("analysis") for a in articles.values())


@pytest.mark.parametrize("conflict", ["doi", "title", None])
def test_real_identity_verifier_keeps_conflicts_and_normalizes_chemistry(conflict):
    from agents.EnnoScholarV5.sources import ScientificSources
    from agents.EnnoScholarV5.models import Article
    sources = ScientificSources(config=ScholarV4Config(api_key="offline"))
    record = {"DOI": "10.1234/wrong" if conflict == "doi" else "10.1234/test",
        "title": ["CO2 oxidation" if conflict == "title" else "CO<sub>2</sub> reduction"],
        "author": [{"given": "Ada", "family": "Author"}], "published": {"date-parts": [[2024]]}}
    sources.client.client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"message": record})))
    article = Article(title="CO 2 reduction", doi="10.1234/test")
    checked = sources.verify(article, 1e12)
    assert checked.verified == (conflict is None)
    assert bool(checked.metadata_errors) == (conflict is not None)


@pytest.mark.parametrize("limit", ["tokens", "tools", "seconds"])
def test_cumulative_limits_survive_new_meter_and_config_changes(app_env, limit):
    from agents.EnnoScholarV4.finance import BudgetRefused
    make, factory, _ = app_env
    svc, sid, tr = make([])
    rid = svc.open_run(1, sid, "Recherche scientifique")
    scope = Scope(1, sid)
    meter = RunMeter(svc, scope, rid, svc.config)
    with factory() as db:
        b = db.get(Budget, rid)
        data = copy.deepcopy(b.data)
        if limit == "tokens":
            data["entries"] = [{"id": "previous", "status": "settled", "cost_usd": .01, "input_tokens": svc.config.max_tokens, "output_tokens": 0, "reserved_usd": 1, "reserved_tokens": svc.config.max_tokens}]
        elif limit == "tools":
            data["tools"] = svc.config.max_tool_calls
        else:
            data["seconds"] = svc.config.timeout_seconds
        b.data = data
        db.commit()
    # Higher config values cannot replenish an existing execution's frozen limits.
    resumed = RunMeter(svc, scope, rid, replace(svc.config, max_tokens=800000, max_tool_calls=96, timeout_seconds=1200))
    with pytest.raises(BudgetRefused):
        resumed.admit("v5_science", "next", "", "gpt-6.1-sol", .01,
            {"estimated_input_tokens": 10, "max_output_tokens": 10})
    assert resumed.snapshot()["limits"] == meter.limits and not tr.requests


def test_plan_drafting_and_revision_use_existing_validators_through_langgraph(app_env):
    from .test_cir_workflow import confirm_after_preparation, preparation_refusals
    make, factory, _ = app_env
    s = case("stokes")["state"]
    claim = s["articles"][STOKES]["analysis"]["technical_mechanism"][0]
    eid = claim["evidence_ids"][0]
    text = claim["statement"]+f" [{STOKES}] [{eid}]"
    plan = {"action": "create", "title": "Méthodes scientifiques", "section_id": None, "target_section_id": None,
        "position": None, "sections": [{"title": "Apprentissage et validation", "description": "Méthode et limites de la source", "article_ids": [STOKES]}], "article_ids": None}
    draft = {"section_id": "1", "action": "save", "instructions": "Rédaction ciblée sur la méthode déjà lue.",
        "text": text, "article_ids": [STOKES], "evidence_ids": [eid]}
    def refused(sent):
        out = next(json.loads(i["output"]) for i in sent["input"] if i.get("call_id") == "write"
            and i.get("type") == "function_call_output")
        assert out["rejection_code"] == "plan_not_approved"  # The plan was just proposed.
        return finish("Plan proposé ; la rédaction attend sa validation.")
    svc, sid, tr = make([call("get_analysis_claims", {"article_id": STOKES, "fields": ["technical_mechanism"], "offset": 0}, "claims"),
        call("manage_outline", plan, "plan"), call("write_section", draft, "write"), refused], s)
    result = run_sync(svc, sid, "Crée un plan sur Stokes puis rédige sa méthode avec les preuves enregistrées, sans recherche ni téléchargement.")
    data = scientific_state(factory, sid)
    assert data["outline"]["sections"] and not data["outline"]["sections"][0].get("drafts"), result
    # The consultant approves the plan; the next request drafts with the same validators.
    from agents.EnnoScholarV4.manuscript import DocumentAction
    svc.document_action(1, sid, DocumentAction(action="approve_outline", expected_version=data["outline"].get("version", 0)))
    tr.choices[:] = [call("write_section", draft, "write2"), confirm_after_preparation(finish()),
        confirm_after_preparation(finish()), finish()]
    asked = len(tr.requests)
    result = run_sync(svc, sid, "Rédige maintenant la méthode de la section 1 avec les preuves enregistrées.")
    data = scientific_state(factory, sid)
    assert data["outline"]["sections"][0]["drafts"], result
    first = data["outline"]["sections"][0]["drafts"][0]
    # A card without results or limits gets bounded preparation refusals; the text is then confirmed.
    assert eid in first["evidence_ids"] and len(tr.requests)-asked == 1+len(preparation_refusals(factory))
    # The approved plan is not changed by Sol: a rename becomes a proposal to the consultant.
    old_analysis = copy.deepcopy(data["articles"][STOKES]["analysis"])
    rename = {**plan, "action": "rename", "section_id": "1", "title": "Méthode révisée", "sections": None}
    tr.choices[:] = [call("manage_outline", rename, "rename"), finish()]
    again = run_sync(svc, sid, "Renomme la première section du plan en Méthode révisée. Conserve le texte des brouillons.")
    final = scientific_state(factory, sid)
    assert final["outline"]["sections"][0]["drafts"][0] == first
    assert final["articles"][STOKES]["analysis"] == old_analysis
    assert final["outline"]["sections"][0]["title"] == "Apprentissage et validation", again
    assert final["outline_revision_proposal"]["consultant_approval_required"]


def test_new_search_does_not_automatically_inject_historical_analyses(app_env):
    make, factory, _ = app_env
    state = case("stokes")["state"]
    unique = state["articles"][STOKES]["analysis"]["technical_mechanism"][0]["statement"]
    svc, sid, tr = make([finish()], state)
    result = run_sync(svc, sid, "Recherche dix articles sur la corrosion des batteries au sodium.")
    assert len(tr.requests) == 1, result
    assert unique not in json.dumps(tr.requests[0]["input"], ensure_ascii=False)
    assert scientific_state(factory, sid)["articles"][STOKES]["analysis"] == state["articles"][STOKES]["analysis"]


def test_cancellation_settles_received_usage_and_keeps_acquisitions(app_env):
    make, factory, _ = app_env
    svc, sid, tr = make([])
    rid = svc.open_run(1, sid, "Recherche scientifique")
    def cancel_during_http(sent):
        svc.cancel(1, rid)
        return finish()
    tr.choices[:] = [cancel_during_http]
    svc.slots.acquire()
    svc.execute(1, sid, rid)
    result = svc.run(1, rid)
    assert result["status"] == "cancelled" and result["reason"] == "cancelled", result
    assert result["usage"]["input_tokens"] == 100 and result["usage"]["estimated_cost_usd"] > 0
    assert len(tr.requests) == 1


def test_explicit_reuse_of_v4_science_is_read_only_and_avoids_new_downloads(app_env):
    from agents.EnnoScholarV4.session import sync_records as v4_sync
    make, factory, _ = app_env
    svc, sid, tr = make([call("get_article", {"identifiers": [LI]}, "reuse"),
        call("get_fulltext", {"article_id": LI, "candidate_urls": None, "page_start": None, "page_limit": None}, "document"), finish()])
    v4 = ScholarV4Service(factory, svc.config)
    source_id = v4.create(1)["id"]
    s = case("chemistry")["state"]
    with factory() as db:
        source = db.get(V4Session, source_id)
        source.data = copy.deepcopy(s)
        v4_sync(db, source)
        db.commit()
    result = run_sync(svc, sid, "Consulte le document conservé pour "+LI+".")
    data = scientific_state(factory, sid)
    assert data["articles"][LI]["fulltext"]["text"] == s["articles"][LI]["fulltext"]["text"], result
    assert not svc.sources.metadata.called
    with factory() as db:
        assert db.get(V4Session, source_id).data == s
    assert any(n.get("kind") == "reused_science" for n in data["articles"][LI]["provider_notices"])
    v4.pool.shutdown(wait=True)


def test_expired_worker_is_resumable_in_ui_without_automatic_dispatch(app_env):
    make, factory, _ = app_env
    svc, sid, tr = make([])
    rid = svc.open_run(1, sid, "Recherche dix publications")
    meter = RunMeter(svc, Scope(1, sid), rid, svc.config)
    with factory() as db:
        row = db.get(ScholarV4Session, sid)
        row.lease_until = datetime.utcnow()-timedelta(seconds=1)
        db.get(ScholarV4Run, rid).status = "running"
        db.commit()
    interrupted = svc.run(1, rid)
    assert interrupted["status"] == "partial" and interrupted["resumable_run_id"] == rid
    assert not tr.requests
    assert svc.read(1, sid)["resumable_run_id"] == rid
    assert RunMeter(svc, Scope(1, sid), rid, svc.config).limits == meter.limits


def test_new_identical_message_does_not_resume_old_execution(app_env):
    make, factory, _ = app_env
    svc, sid, tr = make([])
    request = "Recherche dix articles"
    old = svc.open_run(1, sid, request)
    with factory() as db:
        row = db.get(ScholarV4Session, sid)
        row.active_run_id = None
        run = db.get(ScholarV4Run, old)
        run.status, run.data = "partial", {**run.data, "resumable_run_id": old}
        db.commit()
    new = svc.open_run(1, sid, request)
    with factory() as db:
        db.get(ScholarV4Session, sid).active_run_id = None
        db.get(ScholarV4Run, new).status = "partial"
        db.commit()
    with pytest.raises(SessionBusy, match="nouvelle demande"):
        svc.resume_run(1, old)
    assert not tr.requests
