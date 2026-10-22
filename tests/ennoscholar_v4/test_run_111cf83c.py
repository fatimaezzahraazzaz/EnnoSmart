"""Run 111cf83c (job job_5ab63649…), gpt-6-luna: « Recherche les 5 articles … maillages volumiques 3D ».

The second selection batch never left: the selection memory was capped at 36 000 characters
(85 000 available), the balanced comparison of 3 detailed finalists and 1 challenger needed
36 707, `balanced_comparison_context_budget` was raised, then two identical continuations failed
the same way without any model call, three identical fallbacks were saved and each announced a
continuation; 64 candidates (TetGAN's full abstract, NVMG's, NeurFrame's) were never judged. In
the first batch NeuralMesh had « maillage volumique 3D » met while its own profile said the
volumetric nature was not stated, and expected_output framed articles instead of a technical output.

Offline: the real session is read in mode=ro and worked in memory; no model, provider or network call.
"""
import copy
import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

from agents.EnnoScholarV4 import research
from tests.ennoscholar_v4.test_audit_91590987 import DB

SESSION, JOB = "111cf83c35de4ae989cef412ed951671", "job_5ab6364930624537b7bc6276b13b8e9f"
NEURALMESH = "paper_0cb3ff33f63ef9a68dbf"
_CACHE = {}


@pytest.fixture(autouse=True)
def offline_documents(monkeypatch):
    from agents.EnnoScholarV4.tools import fulltext

    def refuse(*args, **kwargs):
        raise ConnectionError("offline test: no download")
    monkeypatch.setattr(fulltext, "fetch_public", refuse)


def session():
    if "state" not in _CACHE:
        if not DB.is_file():
            pytest.skip("Base locale absente")
        db = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True)
        db.execute("PRAGMA query_only=ON")
        row = db.execute("select data from scholar_v4_sessions where id = ?", (SESSION,)).fetchone()
        db.close()
        if not row:
            pytest.skip("Session du run 111cf83c absente")
        _CACHE["state"] = json.loads(row[0])
    state = copy.deepcopy(_CACHE["state"])
    state["messages"] = state["messages"][:1]                                  # the request, before any fallback
    return state


def tools(state):
    from unittest.mock import Mock
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.toolkit import ScholarTools
    return ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))


def second_batch(state, t):
    """The batch the agent prepares after the first triage (same transition and window)."""
    decision = research.transition(state)
    assert decision["stage"] == "triage"
    state["selection_focus"] = {"candidate_ids": research.selection_ids(state, decision["article_ids"]),
        "presented_total": len(t.registry), "review": False, "scientific_stage": "triage", "evaluation_ids": decision["article_ids"]}
    return decision["article_ids"]


def title_id(state, prefix):
    return next(aid for aid, a in state["articles"].items() if a["title"].startswith(prefix))


def test_the_recorded_state_is_the_one_the_audit_describes():
    state = session()
    decisions = [(a.get("assessment") or {}).get("decision") for a in state["articles"].values()]
    assert len(state["articles"]) == 75 and decisions.count(None) == 64
    assert len(state["evaluation"]["recommended_article_ids"]) == 3
    for prefix, chars in (("TetGAN", 749), ("Neural Volumetric Mesh Generator", 1550), ("NeurFrame", None)):
        article = state["articles"][title_id(state, prefix)]
        assert article["abstract"] and (chars is None or len(article["abstract"]) == chars)
        assert (article.get("assessment") or {}).get("decision") in {None, "pending"}


def test_the_old_cap_and_no_reduction_reproduce_the_refused_comparison(monkeypatch):
    import agents.EnnoScholarV4.context as context
    monkeypatch.setattr(context, "selection_duplicates", lambda memory, state: [])
    monkeypatch.setattr(context, "compact_selection_dossiers", lambda memory, level: "")
    state = session()
    t = tools(state)
    second_batch(state, t)
    with pytest.raises(context.SelectionContextError) as blocked:
        context.model_context(state, t, 36000)
    assert 36_700 <= blocked.value.details["size"] <= 36_800                  # 36 707 in the run
    assert len(blocked.value.details["candidate_ids"]) == 4                     # 3 finalists, 1 challenger


def delivered(state, budget):
    from agents.EnnoScholarV4.context import model_context
    t = tools(state)
    evaluation = second_batch(state, t)
    inputs = model_context(state, t, budget)
    memory = json.loads(inputs[0]["content"].split(" : ", 1)[1])
    return t, evaluation, inputs, memory


def test_the_same_batch_now_fits_without_losing_finalists_criteria_or_full_abstracts():
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.agent import SELECTION_EXCHANGE_RESERVE
    state = session()
    finalists = list(state["evaluation"]["recommended_article_ids"])
    room = max(36000, ScholarV4Config(api_key="mock").max_context_chars - SELECTION_EXCHANGE_RESERVE)
    t, evaluation, inputs, memory = delivered(state, room)
    comparison = memory["selection_comparison"]
    assert comparison["incumbent_ids"] == finalists                             # no finalist removed
    candidates = {row["id"]: row for row in memory["selection_candidates"]}
    assert set(finalists) <= set(candidates)
    challengers = [aid for aid in candidates if aid not in finalists]
    assert len(challengers) >= 12                                               # was 1
    assert title_id(state, "TetGAN") in candidates
    for aid, row in candidates.items():                                          # every abstract whole, once
        assert not row.get("abstract_truncated") and row["abstract"] == state["articles"][aid]["abstract"]
    for dossier in comparison["incumbents"]:                                     # criteria, provenance kept
        assert dossier["relevance"]["criteria"] and dossier["provenance"] and dossier["qualification"]
    need = memory["research_need"]
    assert need["criteria"] == state["research_need"]["criteria"]                # criteria whole
    assert comparison["context_reductions"] and all(isinstance(r, str) for r in comparison["context_reductions"])
    assert len(json.dumps(inputs, ensure_ascii=False)) <= room
    assert len(json.dumps(inputs, ensure_ascii=False)) <= t.config.max_context_chars - 6000


def test_even_the_old_allocation_now_carries_the_finalists_and_several_challengers():
    state = session()
    _, _, inputs, memory = delivered(state, 36000)
    assert len(memory["selection_comparison"]["incumbent_ids"]) == 3
    assert len(memory["selection_candidates"]) >= 5 and len(json.dumps(inputs, ensure_ascii=False)) <= 36000


def test_an_irreducible_batch_is_blocked_once_explicitly_and_the_job_does_not_resume_into_it():
    from agents.EnnoScholarV4.context import model_context, SelectionContextError
    from agents.EnnoScholarV4.writing_job import FINAL
    state = session()
    t = tools(state)
    second_batch(state, t)
    with pytest.raises(SelectionContextError) as blocked:
        model_context(state, t, 9000)                                           # far below the finalists' dossiers
    details = blocked.value.details
    assert details["reductions"] and len(details["candidate_ids"]) >= 4         # every safe reduction tried first
    block = research.record_context_block(state, details)
    assert block["signature"] == research.record_context_block(state, details)["signature"]   # deterministic
    job = copy.deepcopy(_CACHE["state"]["writing_job"])
    job.update(status="running", current_run_id="run_x", segments=[], no_progress=0, technical_failures=0)
    job = research.job_evaluate(job, state, "run_x", "partial", {"reason": "selection_context_blocked", "ledger": []})
    assert job["status"] in FINAL and job["status"] != "waiting" and "aucune reprise identique" in job["error"]
    assert job["context_block"] == block["signature"]


def test_a_volumetric_criterion_is_not_met_when_the_profile_itself_says_it_is_not_established():
    state = session()
    need = state["research_need"]
    assessment = state["articles"][NEURALMESH]["assessment"]
    assert next(c for c in assessment["relevance"]["criteria"] if c["criterion_id"] == "c1")["status"] == "met"
    article = state["articles"][NEURALMESH]
    relevance, changes = research.consistent_criteria(article, assessment["relevance"], need)
    c1 = next(c for c in relevance["criteria"] if c["criterion_id"] == "c1")
    assert c1["status"] == "unknown" and c1["downgraded_from"] == "met"        # unknown, not rejected
    assert any(ch["criterion_id"] == "c1" for ch in changes)
    # A met judgment quoting the notice and not contradicted by its own profile stays met.
    quote = article["abstract"][:60]
    ok = {**assessment["relevance"], "uncertainties": [], "missing_information": [], "criteria": [
        {"criterion_id": "c2", "status": "met", "basis": "abstract", "note": "n", "evidence": quote}]}
    assert research.consistent_criteria(article, ok, need)[0]["criteria"][0]["status"] == "met"
    for wrong in ({"evidence": "", "basis": "abstract"}, {"evidence": quote, "basis": "inference"},
            {"evidence": "a sentence the notice never says", "basis": "abstract"}):
        bad = {**ok, "criteria": [{**ok["criteria"][0], **wrong}]}
        assert research.consistent_criteria(article, bad, need)[0]["criteria"][0]["status"] == "unknown"


def test_expected_output_names_a_technical_output_not_the_bibliographic_deliverable():
    state = session()
    need = copy.deepcopy(state["research_need"])
    assert need["expected_output"].startswith("Articles scientifiques")           # the recorded framing
    with pytest.raises(ValueError, match="sortie technique"):
        research.NeedProfile.model_validate(need)
    need["expected_output"] = "Un maillage volumique 3D (tétraédrique ou hexaédrique) généré par la méthode, utilisable en éléments finis."
    assert research.NeedProfile.model_validate(need)


def test_identical_fallbacks_are_saved_once_and_a_withdrawn_continuation_is_not_announced(tmp_path):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from db.database import Base
    from db.models import User
    from agents.EnnoScholarV4.agent import CONTINUATION_NOTE
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.service import ScholarV4Service
    from agents.EnnoScholarV4.session import ScholarV4Session, ScholarV4Run
    from agents.EnnoScholarV4.writing_job import stop
    engine = create_engine("sqlite:///" + str(tmp_path / "s.sqlite"), connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(User(id=1, full_name="T", email="t@e.org", hashed_password="x", is_active=True))
        db.commit()
    svc = ScholarV4Service(factory, ScholarV4Config(api_key="mock"), lambda: None)
    sid = svc.create(1)["id"]
    answer = "La rédaction finale n’a pas pu passer toutes ses vérifications." + CONTINUATION_NOTE
    for n in range(3):
        rid = f"run{n}"
        with factory() as db:
            row = db.get(ScholarV4Session, sid)
            row.active_run_id = rid
            row.data = {**row.data, "writing_job": {"id": JOB, "kind": "research", "status": "running"}}
            db.add(ScholarV4Run(id=rid, session_id=sid, owner_id=1, status="running",
                data={"segment": {"index": n}, "writing_job_id": JOB}))
            db.commit()
        svc.finish(1, sid, rid, None, {"answer": answer, "citations": []}, "partial")
    with factory() as db:
        messages = [m for m in db.get(ScholarV4Session, sid).data["messages"] if m.get("writing_job_id") == JOB]
    assert len(messages) == 1 and messages[0]["repeated_run_ids"] == ["run1", "run2"]
    with factory() as db:                                                        # the job stops after evaluation
        row = db.get(ScholarV4Session, sid)
        job = stop({**row.data["writing_job"], "segments": [], "spent": {"confirmed_usd": 0, "reserved_usd": 0, "uncertain_usd": 0},
            "budget_usd": 1.0, "stalled_segments": 2, "max_segments": 6}, "stalled")
        svc._write_job(db, row, job, announce=True)
    with factory() as db:
        messages = [m for m in db.get(ScholarV4Session, sid).data["messages"] if m.get("writing_job_id") == JOB]
    assert CONTINUATION_NOTE not in messages[0]["content"] and messages[0]["continuation_withdrawn"]
    assert messages[-1]["writing_job_status"] == "stalled"                     # the real state, stated once
    engine.dispose()


class Model:
    """Judges every presented notice (retain when its abstract states a volumetric/tetrahedral/hexahedral
    output, else reject), then answers any other forced request briefly. Records each request."""
    def __init__(self):
        self.requests = []

    def with_options(self, **kw):
        return self

    @property
    def responses(self):
        return self

    def create(self, **kw):
        from tests.ennoscholar_v4.test_finalization import Stream, call
        memory = json.loads(next(i for i in kw["input"] if i.get("role") == "developer")["content"].split(" : ", 1)[1])
        name = (kw.get("tool_choice") or {}).get("name") if isinstance(kw.get("tool_choice"), dict) else None
        self.requests.append({"tool": name, "chars": len(json.dumps(kw["input"], ensure_ascii=False)),
            "evaluation": list((memory.get("selection_comparison") or {}).get("evaluation_ids", []))})
        n = len(self.requests)
        if name == "record_candidate_decisions":
            ids = memory["selection_comparison"]["evaluation_ids"]
            rows = {row["id"]: row for row in memory["selection_candidates"]}
            decide = lambda aid: "retain" if any(w in (rows[aid].get("abstract") or "").lower() for w in ("tetrahedral", "hexahedral", "volumetric mesh")) else "reject"
            out = call("record_candidate_decisions", {"decisions": [{"article_id": aid, "decision": decide(aid),
                "justification": "Jugé sur l'abstract complet.", "lock_ids": [], "contribution": "related" if decide(aid) == "retain" else None,
                "relevance": None} for aid in ids], "finalist_ids": None, "covered_families": [], "remaining_axes": [], "replacements": []}, n)
        else:
            out = call("finalize_response", {"answer": "Synthèse provisoire.", "article_ids": [], "evidence_ids": [],
                "coverage_sufficient": False, "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": None}, n)
        return Stream(NS(output=out, output_text="", status="completed", usage={"input_tokens": 1000, "output_tokens": 300}))


def test_the_job_segment_now_judges_the_second_batch_and_the_following_ones():
    """The real path: the job's next segment on the recorded corpus. The selection requests leave,
    each with many challengers, and the never-judged notices (TetGAN, NVMG…) get decisions."""
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.config import ScholarV4Config
    from unittest.mock import Mock
    state = session()
    job = _CACHE["state"]["writing_job"]
    state["writing_segment"] = {"job_id": JOB, "kind": "research", "request_message_id": state["messages"][0]["id"],
        "progress_id": state["research_progress"]["id"], "goal": copy.deepcopy(job["goal"]), "blocked": {}}
    before = sum(1 for a in state["articles"].values() if not (a.get("assessment") or {}).get("decision"))
    model, providers = Model(), Mock()
    providers.search.return_value = ([], [])
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=model, sources=providers).run(state)
    selections = [r for r in model.requests if r["tool"] == "record_candidate_decisions"]
    assert selections and result.get("reason") not in {"selection_context_blocked", "context_budget", "internal_error"}
    assert len(selections) >= 2 and all(r["chars"] <= 85000 for r in model.requests)   # following batches too
    assert len(selections[0]["evaluation"]) >= 12                               # the second batch, whole
    after = sum(1 for a in state["articles"].values() if not (a.get("assessment") or {}).get("decision"))
    assert before - after >= 12
    for prefix in ("TetGAN", "Neural Volumetric Mesh Generator", "NeurFrame"):                # never judged in the run
        assert any(title_id(state, prefix) in r["evaluation"] for r in selections), prefix
        assert (state["articles"][title_id(state, prefix)].get("assessment") or {}).get("decision") == "retain"
    assert set(_CACHE["state"]["evaluation"]["recommended_article_ids"]) <= set(state["evaluation"]["recommended_article_ids"])
