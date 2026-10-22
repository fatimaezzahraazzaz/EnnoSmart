"""Autonomous literature search and state-of-the-art quality (completion of Codex's work).

Offline: simulated model and sources, document downloads blocked, temporary databases; the
real sessions (3D meshing run 94599ea8, road-surface dossier) are read in read-only mode
and only copied in memory. No provider call, no write to any real data.
"""
import copy
import json
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.EnnoScholarV4 import research, writing_job
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from tests.ennoscholar_v4.test_audit_91590987 import DB
from tests.ennoscholar_v4.test_finalization import Stream, call
from tests.ennoscholar_v4.replay_2fd068ff import analysis


@pytest.fixture(autouse=True)
def offline_documents(monkeypatch):
    """No document is ever downloaded by these tests."""
    from agents.EnnoScholarV4.tools import fulltext

    def refuse(*args, **kwargs):
        raise ConnectionError("offline test: no download")
    monkeypatch.setattr(fulltext, "fetch_public", refuse)


# --- research.py: objective, coverage, providers -------------------------------------------

@pytest.mark.parametrize("message,count", [
    ("Recherche les 10 articles scientifiques les plus pertinents sur ce sujet.", 10),
    ("Find the 12 most relevant papers on graph neural operators.", 12),
    ("Trouve 5 publications sur la corrosion des aciers inoxydables.", 5),
    ("Quelles méthodes existent pour la segmentation ?", None)])
def test_the_requested_number_is_read_in_any_field(message, count):
    assert research.requested_count(message) == count


def test_a_search_request_opens_research_and_an_explicit_refusal_does_not():
    assert research.wants_research("Recherche des articles sur la détection d'anomalies sismiques.")
    assert research.wants_research("Find papers about protein folding with diffusion models.")
    assert not research.wants_research("Compare ces articles sans nouvelle recherche.")
    assert not research.wants_research("Explique la méthode de l'article 2.")


def corpus_state(n_ready=0, n_pending=0, target=None, review=None):
    """Cards with exact, valid proofs come from the real tool chain (rich_corpus)."""
    from tests.ennoscholar_v4.test_context_memory import rich_corpus
    t, ids = rich_corpus(max(1, n_ready + n_pending))
    state = t.state
    for aid in ids[n_ready:n_ready+n_pending]:
        state["articles"][aid]["analysis"] = {}
    for aid in ids:
        state["articles"][aid]["verified"] = True
    state["evaluation"] = {"recommended_article_ids": ids[:n_ready+n_pending]}
    state["turn_goal"] = {"deliverable": "literature_search", "search_needed": True}
    state["research_progress"] = {"id": "research_x", "requested_count": target, "reviews": [] if review is None else [review]}
    return t, ids


def test_a_requested_count_is_reached_only_by_verified_cards_with_their_own_proofs():
    t, ids = corpus_state(n_ready=2, n_pending=1, target=3)
    observed = research.status(t.state)
    assert observed["verified_analyzed_count"] == 2 and observed["pending_article_ids"] == [ids[2]]
    assert not observed["complete"]                                         # the third card is still needed
    # Run 53292dc4: with the requested number of analysed works, another recommendation stays
    # available in the library without blocking the answer (it is not counted, not pending).
    t.state["research_progress"]["requested_count"] = 2
    observed = research.status(t.state)
    assert observed["target_reached"] and not observed["complete"] and not observed["pending_article_ids"] and observed["available_article_ids"] == [ids[2]]
    t.state["evaluation"]["recommended_article_ids"] = ids[:2]
    assert research.status(t.state)["target_reached"] and not research.status(t.state)["complete"]
    # A proof belonging to another work does not count.
    card = t.state["articles"][ids[0]]["analysis"]
    card["evidence"] = [{**card["evidence"][0], "article_id": ids[1]}]
    assert research.status(t.state)["verified_analyzed_count"] == 1


def test_without_a_target_the_current_coverage_review_decides_and_verification_does_not_outdate_it():
    t, ids = corpus_state(n_ready=2)
    from tests.ennoscholar_v4.test_decision_policy import comparison
    comparison(t, ids)
    t.state["research_progress"]["ranking_review"] = {"card_signature": research.card_signature(t.state),
        "corpus_ids": sorted(t.state["articles"]), "reviewed_ids": ids}
    research.save_review(t.state, research.ResearchReviewArgs(sufficient=True, analysis_sufficient=True,
        scientific_selection_justified=True, reason="Familles couvertes et méthodes comparées."))
    assert research.status(t.state)["complete"]
    t.state["articles"][ids[0]]["verified"] = False                         # final bibliographic check
    t.state["articles"][ids[0]]["verified"] = True
    assert research.status(t.state)["review_current"]
    t.state["recent_searches"] = [{"key": ["q", ["arxiv"], 0, 10], "query": "q"}]   # new knowledge
    assert not research.status(t.state)["complete"]


def test_a_review_cannot_claim_success_or_exhaustion_it_has_not_established():
    t, ids = corpus_state(n_ready=1, n_pending=1, target=3)
    with pytest.raises(ValueError, match="Couverture insuffisante"):
        research.save_review(t.state, research.ResearchReviewArgs(sufficient=True, reason="ok"))
    with pytest.raises(ValueError, match="Épuisement non établi"):
        research.save_review(t.state, research.ResearchReviewArgs(exhausted=True, reason="rien", next_queries=["autre requête"]))
    saved = research.save_review(t.state, research.ResearchReviewArgs(gaps=["méthodes spectrales"], next_queries=["spectral"],
        actions=[research.ResearchAction(kind="search", query="spectral", reason="Famille manquante")],
        reason="Lacune identifiée."))
    assert saved["saved"] and saved["research_coverage"]["gaps"] == ["méthodes spectrales"]


def test_collected_pages_and_providers_in_backoff_are_never_requested_again():
    state = {"recent_searches": [{"key": ["graph neural operator", ["arxiv", "openalex"], 0, 10], "query": "graph neural operator",
        "providers": {"arxiv": {"returned": 10}, "openalex": {"returned": 10}}}]}
    assert research.source_plan(state, "graph neural operator", ["arxiv", "openalex"], 0, 10) == []
    assert research.source_plan(state, "graph  neural operator", ["arxiv"], 10, 10) == ["arxiv"]   # the next page
    research.record_providers(state, [{"source": "semantic_scholar", "error": "HTTPStatusError", "http_status": 429,
        "retry_after": "40"}], {"arxiv": {"returned": 3}})
    health = state["research_provider_health"]
    assert health["semantic_scholar"]["retry_at"] > time.time() + 30 and health["arxiv"]["failures"] == 0
    assert research.source_plan(state, "new query", ["semantic_scholar", "arxiv"], 0, 10) == ["arxiv"]
    research.record_providers(state, [], {"semantic_scholar": {"returned": 4}})
    assert state["research_provider_health"]["semantic_scholar"] == {"failures": 0, "retry_at": 0}


def test_exploration_options_offer_next_pages_and_web_fallback_from_the_stored_searches():
    state = {"recent_searches": [
        {"key": ["q1", ["arxiv", "openalex"], 0, 10], "query": "q1", "family": "f1",
            "providers": {"arxiv": {"returned": 10}, "openalex": {"returned": 3}}},
        {"key": ["q2", ["arxiv"], 0, 10], "query": "q2", "family": "f2", "providers": {"arxiv": {"returned": 4}}},
        {"key": ["q3", ["arxiv"], 0, 5], "query": "q3", "family": "f3", "providers": {"arxiv": {"returned": 5}}},
        {"key": ["q3", ["arxiv"], 5, 5], "query": "q3", "family": "f3", "providers": {"arxiv": {"returned": 2}}}],
        "research_provider_health": {"semantic_scholar": {"retry_at": time.time() + 60}}}
    options = research.exploration_options(state)
    assert options["next_pages"] == [{"query": "q1", "sources": ["arxiv"], "next_offset": 10, "limit_per_source": 10,
        "family": "f1"}]                                                    # full page only, next page not collected
    assert options["providers_unavailable"] == ["semantic_scholar"] and options["web_search_fallback"]
    assert options["families_searched"] == ["f1", "f2", "f3"]


def test_concurrent_pages_keep_their_own_offset_in_provider_threads(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from agents.EnnoScholarV4.sources import ScientificSources
    sources = ScientificSources(config=ScholarV4Config(api_key="mock"))
    barrier, observed = threading.Barrier(2), []

    def provider(query, limit, deadline):
        barrier.wait(timeout=5)
        observed.append((query, sources.client.search_offset))
        return []

    monkeypatch.setattr(sources.client, "openalex", provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(sources.search, "alpha", ["openalex"], 10, time.monotonic()+10, offset=10)
        second = pool.submit(sources.search, "beta", ["openalex"], 10, time.monotonic()+10, offset=20)
        assert first.result() == second.result() == ([], [])
    assert sorted(observed) == [("alpha", 10), ("beta", 20)]


def test_reading_time_is_estimated_from_measured_analyses_within_bounds():
    assert research.reading_seconds([]) == 60.0
    assert research.reading_seconds([(10.0, 2000), (30.0, 3000), (5.0, 100)]) == pytest.approx(30*1.35+5)
    assert research.reading_seconds([(400.0, 5000)]) == 120.0 and research.reading_seconds([(1.0, 900)]) == 20.0


# --- research job decisions ----------------------------------------------------------------

def research_job(**changes):
    job = writing_job.new_job(ScholarV4Config(api_key="mock"), "m1", "r1", {}, kind="research")
    job.update(changes)
    return job


def research_state(goal="literature_search"):
    return {"turn_goal": {"deliverable": goal, "search_needed": True}, "articles": {},
        "research_progress": {"id": "research_1", "request_message_id": "m1", "requested_count": 10, "reviews": []}}


def test_research_job_completes_continues_or_stops_explicitly(monkeypatch):
    def evaluated(observed, reason=None, status="partial", **job_fields):
        monkeypatch.setattr(research, "status", lambda state: {"verified_analyzed_count": 3, "requested_count": 10,
            "complete": False, "exhausted": False, "review": {}, **observed})
        job = research_job(**job_fields)
        return research.job_evaluate(job, research_state(), "r1", status, {"reason": reason,
            "ledger": [{"status": "settled", "cost_usd": 0.4}]})
    job = evaluated({})
    assert job["status"] == "waiting" and job["spent"]["confirmed_usd"] == 0.4 and job["requested_count"] == 10
    assert job["progress"]["id"] == "research_1" and job["goal"]["deliverable"] == "literature_search"
    assert evaluated({"complete": True})["status"] == "completed"
    exhausted = evaluated({"exhausted": True, "review": {"reason": "Sources consultées sans autre travail vérifiable."}})
    assert exhausted["status"] == "exhausted" and "couverture partielle" in exhausted["error"]
    assert evaluated({}, reason="cancelled")["status"] == "cancelled"
    assert evaluated({}, reason="budget_refused")["status"] == "budget_exhausted"
    assert evaluated({}, reason="internal_error")["status"] == "failed"
    # No new knowledge twice in a row: an explicit stop, not an endless continuation.
    monkeypatch.setattr(research, "signature", lambda state: "same")
    job = research_job(corpus_signature=research.progress_signature(research_state()), no_progress=1)
    monkeypatch.setattr(research, "status", lambda state: {"verified_analyzed_count": 3, "requested_count": 10,
        "complete": False, "exhausted": False, "review": {}})
    assert research.job_evaluate(job, research_state(), "r1", "partial", {})["status"] == "stalled"


def test_a_request_the_model_did_not_read_as_a_search_ends_the_research_job():
    job = research.job_evaluate(research_job(), research_state("answer"), "r1", "completed", {})
    assert job["status"] == "not_applicable"


# --- versions.py: verified correspondence of open versions ----------------------------------

def paper(**changes):
    base = dict(title="Graph neural operators for elastic wave propagation in heterogeneous media at scale",
        authors=["Ada Lovelace", "Alan Turing"], year=2024, doi="10.1016/j.jcp.2024.1", verified=True)
    return Article(**{**base, **changes})


def test_an_open_preprint_is_an_alternative_only_with_the_same_authors_title_and_period():
    from agents.EnnoScholarV4.versions import corresponding_preprint
    published = paper()
    preprint = paper(doi="", arxiv_id="2401.01234", year=2023)
    assert corresponding_preprint(published, preprint)
    assert not corresponding_preprint(published, preprint.model_copy(update={"authors": ["Ada Lovelace", "Grace Hopper"]}))
    assert not corresponding_preprint(published, preprint.model_copy(update={"title": "Graph neural operators for waves"}))
    assert not corresponding_preprint(published, preprint.model_copy(update={"year": 2019}))
    assert not corresponding_preprint(published, preprint.model_copy(update={"verified": False}))
    assert not corresponding_preprint(paper(doi="10.48550/arxiv.2401.01234"), preprint)  # already the arXiv record


# --- Codex's targeted corrections ---------------------------------------------------------

def test_section_number_ranges_are_structure_and_percentage_pairs_need_their_own_proof():
    from agents.EnnoScholarV4.provenance_check import numeric_trace
    from tests.ennoscholar_v4.test_context_memory import rich_corpus
    t, ids = rich_corpus(1)
    eid = next(e for e in t.shown_evidence if t.evidence[e]["article_id"] == ids[0])
    t.evidence[eid] = {**t.evidence[eid], "exact_quote": t.evidence[eid]["exact_quote"] + " Accuracy reached 15 % on the test set."}
    traced, untraced = numeric_trace(t, f"Les sections 3.1 à 3.3 reprennent ce point [{eid}].", [eid], [])
    assert not untraced                                                       # section numbers are not values
    traced, untraced = numeric_trace(t, f"L'exactitude atteint 15 % puis 5 % [{eid}].", [eid], [])
    assert [row["value"] for row in untraced] == ["5"]                        # « 5 » is not found inside « 15 »


def test_both_sides_of_a_slash_percentage_pair_must_be_proved():
    from agents.EnnoScholarV4.provenance_check import numeric_trace
    from tests.ennoscholar_v4.test_context_memory import rich_corpus
    t, ids = rich_corpus(1)
    eid = next(e for e in t.shown_evidence if t.evidence[e]["article_id"] == ids[0])
    t.evidence[eid] = {**t.evidence[eid], "exact_quote": "Accuracy: 82.6% and 87.4%."}
    for text in ("82,6 %/87,4 %", "82.6%/87.4%"):
        _, missing = numeric_trace(t, f"Exactitude {text} [{eid}].", [eid], [])
        assert missing == []
    _, missing = numeric_trace(t, f"Exactitude 82,6 %/88,4 % [{eid}].", [eid], [])
    assert [row["value"] for row in missing] == ["88,4"]


def test_an_explicit_count_does_not_erase_known_scientific_gaps():
    t, _ = corpus_state(n_ready=2, target=2)
    t.state["evaluation"]["remaining_axes"] = ["conditions de généralisation"]
    observed = research.status(t.state)
    assert observed["target_reached"] and not observed["complete"] and not observed["scientific_coverage_sufficient"]


def test_new_quantitative_evidence_requests_a_review_of_old_unavailability_claims():
    from agents.EnnoScholarV4.editorial import manuscript_review
    t, ids, proofs = manuscript(n_sections=1)
    section = t.state["outline"]["sections"][0]
    claim = {"statement": "Les extraits ne fournissent pas de valeur numérique.", "evidence_ids": [proofs[0]]}
    section["drafts"][-1]["claims"].append(claim)
    t.state["articles"][ids[0]]["analysis"]["results"] = [{"statement": "Accuracy 88 %.", "evidence_ids": [proofs[0]]}]
    before = copy.deepcopy(t.state["outline"])
    issues = manuscript_review(t.state)
    assert any(i["code"] == "quantitative_availability_to_review" and i["article_id"] == ids[0] for i in issues)
    assert t.state["outline"] == before


def test_an_exact_doi_label_is_accepted_only_for_the_publication_it_identifies():
    from agents.EnnoScholarV4.publication import label_matches
    article = Article(title="MeshingNet3D: learned volumetric meshing", doi="10.1234/verified", verified=True)
    assert label_matches("10.1234/verified", article) and label_matches("https://doi.org/10.1234/VERIFIED", article)
    assert not label_matches("10.1234/other", article)
    assert not label_matches("Different scientific paper", article)


def test_a_save_error_already_resolved_is_no_longer_reported():
    from agents.EnnoScholarV4.writing_progress import open_writing, writing_answer
    state = {"outline": {"plan_revision": 1, "sections": [{"id": "s1", "title": "Une", "drafts": [{"id": "d1", "text": "x"}]},
        {"id": "s2", "title": "Deux", "drafts": []}]}}
    progress = open_writing(state, "full", ["s1", "s2"])
    progress["errors"] = [{"section_id": "s1", "message": "Valeur sans preuve (résolue)."}]
    assert "Dernière erreur" not in writing_answer(state)
    progress["errors"].append({"section_id": "s2", "message": "Attribution non vérifiable."})
    assert "Dernière erreur de sauvegarde : Attribution non vérifiable." in writing_answer(state)


def test_an_interrupted_download_is_retried_twice_at_most_and_an_unavailable_one_never():
    from agents.EnnoScholarV4.scientific_reading import prepare_reading, reading_tasks
    from tests.ennoscholar_v4.test_multi_article_reading import corpus
    state, toolkit = corpus(1)
    paper_ = toolkit.registry["paper_long_0"]
    paper_.fulltext, paper_.abstract = {}, "We propose a learned mesher; validation is limited to simple parts."
    toolkit.save()
    calls = []

    def run(name, args):
        calls.append(name)
        if name == "get_fulltext":
            return {"article_id": paper_.id, "available": None, "warning": "Lecture interrompue ; accès intégral inconnu."}
        return toolkit.execute(name, args, time.monotonic()+30)
    task = reading_tasks(state, toolkit.registry)["tasks"][0]
    for _ in range(4):
        state.pop("automatic_reading_focus", None)
        task["pending"] = None
        prepare_reading(state, toolkit, task, run)
    assert calls.count("get_fulltext") == 3 and task.get("access_attempted")     # 2 interruptions, then attempted


# --- State-of-the-art quality ----------------------------------------------------------------

def manuscript(n_sections=3, shared=None):
    from tests.ennoscholar_v4.test_context_memory import rich_corpus, seven_section_plan
    t, ids = rich_corpus(2)
    seven_section_plan(t, ids)
    proofs = [e for e in t.shown_evidence if t.evidence[e]["article_id"] == ids[0]]
    for number in range(n_sections):
        section = t.state["outline"]["sections"][number]
        eids = shared or proofs[number*2:number*2+2]
        text = " ".join(f"La publication [{ids[0]}] établit ce point [{eid}]." for eid in eids)
        result = t.execute("write_section", {"section_id": section["id"], "action": "save", "instructions": "x", "text": text,
            "article_ids": [ids[0]], "evidence_ids": eids}, float("inf"))
        assert result["saved"], result
    return t, ids, proofs


def test_section_requests_receive_a_bounded_coordination_with_the_other_sections():
    from agents.EnnoScholarV4.context import SECTION_CONTEXT_CHARS, model_context
    from agents.EnnoScholarV4.editorial import coordination
    t, ids, proofs = manuscript()
    target = t.state["outline"]["sections"][3]
    t.state["document"]["writing_progress"] = {"id": "w", "mode": "full", "target_ids": [target["id"]], "saved_draft_ids": {},
        "baseline_draft_ids": {}, "errors": [], "status": "running"}
    t.state["writing_focus"] = {"section_id": target["id"], "article_ids": target["article_ids"], "retrieval_progress": []}
    inputs = model_context(t.state, t, SECTION_CONTEXT_CHARS)
    memory = json.loads(inputs[0]["content"].split(" : ", 1)[1])
    rows = memory["manuscript_coordination"]["other_sections"]                # reaches the model (was filtered out)
    assert [r["section_id"] for r in rows if r["established_claims"]] == [s["id"] for s in t.state["outline"]["sections"][:3]]
    assert memory["section_drafting"]["packet"]                               # the scientific packet is still there
    assert len(json.dumps(coordination(t.state, target["id"], budget_chars=1500), ensure_ascii=False)) <= 1500


def test_repetitions_between_sections_are_reported_to_the_review_without_rewriting_anything():
    from agents.EnnoScholarV4.scientific_review import deterministic_review
    t, ids, proofs = manuscript(shared=None)
    before = copy.deepcopy(t.state["outline"])
    t2, ids2, proofs2 = manuscript(n_sections=2, shared=None)
    shared = proofs2[:3]
    for section in t2.state["outline"]["sections"][:2]:
        section["drafts"][-1]["evidence_ids"] = shared
    review = deterministic_review(t2.state, t2)
    assert any(i["code"] == "shared_evidence_to_review" for i in review["issues"])
    assert review["integrity_passed"] in {True, False} and t.state["outline"] == before


def test_eleven_of_eleven_sections_is_not_reported_as_sufficient_coverage():
    from agents.EnnoScholarV4.editorial import document_quality
    from agents.EnnoScholarV4.writing_progress import open_writing, writing_answer
    t, ids, proofs = manuscript(n_sections=7)
    open_writing(t.state, "full")
    quality = document_quality(t.state)
    assert quality["structurally_complete"] and not quality["bibliography_sufficient"] and not quality["consultant_approved"]
    answer = writing_answer(t.state)
    assert "Brouillon complet enregistré : 7/7" in answer and "Couverture scientifique : non établie" in answer
    assert "revue scientifique du manuscrit : à refaire" in answer
    assert not t.state["outline"].get("approval") and not t.state.get("document", {}).get("approval")


def test_the_consultant_edits_an_approved_plan_through_the_interface_and_the_model_only_proposes(tmp_path):
    from db.database import Base
    from db.models import User
    from agents.EnnoScholarV4.manuscript import DocumentAction, approve_outline
    from agents.EnnoScholarV4.service import ScholarV4Service
    from agents.EnnoScholarV4.session import ScholarV4Session
    from tests.ennoscholar_v4.test_context_memory import rich_corpus, seven_section_plan
    t, ids = rich_corpus(2)
    seven_section_plan(t, ids)
    approve_outline(t.state, 1)
    engine = create_engine("sqlite:///" + str(tmp_path / "plan.sqlite"), connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(User(id=1, full_name="T", email="t@e.org", hashed_password="x", is_active=True))
        db.commit()
    svc = ScholarV4Service(factory, ScholarV4Config(api_key="mock"))
    sid = svc.create(1)["id"]
    with factory() as db:
        row = db.get(ScholarV4Session, sid)
        row.data = {**row.data, **json.loads(json.dumps(t.state))}
        db.commit()
    section = t.state["outline"]["sections"][0]["id"]
    payload = DocumentAction.model_validate({"action": "outline_edit", "expected_version": t.state["outline"]["version"],
        "outline": {"action": "rename", "section_id": section, "target_section_id": None, "title": "Titre choisi par le consultant",
            "sections": None, "article_ids": None, "position": None, "offset": 0, "limit": 8}})
    try:
        svc.document_action(1, sid, payload)
    except Exception as exc:  # pragma: no cover - shows the precise refusal
        pytest.fail(f"Consultant edit refused: {exc}")
    with factory() as db:
        outline = db.get(ScholarV4Session, sid).data["outline"]
    assert outline["sections"][0]["title"] == "Titre choisi par le consultant" and not outline.get("approval")
    svc.pool.shutdown(wait=True)


# --- Integrated: research job, real service and agent, simulated model and sources ----------

FAMILIES = {"tetrahedral": "learned tetrahedral mesh generation", "hexahedral": "neural hexahedral mesh generation",
    "adaptation": "learned mesh size field adaptation"}


def notice(family, i, relevant):
    if relevant:
        return Article(title=f"Volumetric mesh generation by {family} learning, study {i}", doi=f"10.1234/{family}{i}r",
            abstract=(f"We propose a {family} learning method for 3D volumetric mesh generation in mechanics. "
                f"Results: element quality improved by {10+i} percent on {20+i} benchmark parts. "
                "Limitations: validation restricted to simple geometries."),
            authors=["A. Author", "B. Author"], year=2024, url=f"https://example.org/{family}{i}", verified=True)
    return Article(title=f"Image segmentation survey {family} {i}", doi=f"10.1234/{family}{i}x",
        abstract="An unrelated survey of image segmentation.", authors=["C. Author"], year=2020, verified=True)


class Sources:
    """Three method families; two relevant notices per page; Semantic Scholar answers 429."""
    def __init__(self, relevant_per_page=2):
        self.calls, self.last_provider_stats, self.relevant = [], {}, relevant_per_page

    def search(self, query, planned, limit, deadline, check, offset=0):
        self.calls.append((query, tuple(planned), offset))
        family = next((f for f in FAMILIES if f in query), None)
        owner = "arxiv" if family == "hexahedral" else "openalex"
        rows = [notice(family, offset + i, relevant=i < self.relevant).model_copy(update={"sources": [owner]})
            for i in range(4)] if family and owner in planned else []
        self.last_provider_stats = {name: {"returned": len(rows) if name == owner else 0}
            for name in planned if name != "semantic_scholar"}
        warnings = ([{"source": "semantic_scholar", "error": "HTTPStatusError", "http_status": 429, "retry_after": "60"}]
            if "semantic_scholar" in planned else [])
        return rows, warnings

    def verify(self, article, deadline):
        return article.model_copy(update={"verified": True})

    def metadata(self, *a, **k):
        return []


class Model:
    """A careful simulated model: declares its goal, searches one family at a time while the
    exploration focus shows missing coverage (then the next pages it offers), keeps a
    cumulative ranking of relevant notices, and analyses the passages it receives."""
    def __init__(self, target):
        self.calls, self.lock, self.log, self.target = [], threading.Lock(), [], target
        self.retained, self.searched = [], []

    def with_options(self, **kw):
        return self

    @property
    def responses(self):
        return self

    def create(self, **kw):
        with self.lock:
            self.calls.append(kw)
            n = len(self.calls)
        choice = kw.get("tool_choice")
        name = choice.get("name") if isinstance(choice, dict) else None
        memory = json.loads(next(i for i in kw["input"] if i.get("role") == "developer"
            and str(i.get("content", "")).startswith("Mémoire"))["content"].split(" : ", 1)[1])
        out = self.respond(n, name, memory, [t.get("name") for t in kw.get("tools", [])])
        self.log.append({"forced": name, "calls": [c["name"] for c in out], "adaptive": bool(memory.get("adaptive_discovery")),
            "progress": (memory.get("research_objective_progress") or {}).get("verified_analyzed_count"), "input": kw["input"]})
        return Stream(NS(output=out, output_text="", status="completed", usage={"input_tokens": 1000, "output_tokens": 200}))

    def goal(self, n):
        return call("set_turn_goal", {"requested_article_count": self.target, "deliverable": "literature_search", "lock_ids": [],
            "focus_lock_ids": [], "exact_quote": "", "reading_depth": "core", "understanding": "Trouver et analyser les publications.",
            "comparison_requested": False, "search_needed": True, "article_ids": None, "article_scope": "open",
            "references_ambiguous": False, "constraints": None}, n)

    def respond(self, n, name, memory, tools):
        if name == "set_turn_goal":
            return self.goal(n)
        if name == "record_candidate_decisions":
            rows = memory.get("selection_candidates", [])
            decisions = [{"article_id": r["id"], "decision": "retain" if "Volumetric" in r["title"] else "reject",
                "justification": "Génération volumique 3D." if "Volumetric" in r["title"] else "Hors sujet.", "lock_ids": []}
                for r in rows]
            # Cumulative ranking: the works already retained (shown in the research progress) stay.
            progress = memory.get("research_objective_progress") or {}
            for aid in [*progress.get("verified_analyzed_ids", []), *progress.get("pending_article_ids", [])]:
                if aid not in self.retained:
                    self.retained.append(aid)
            self.retained += [d["article_id"] for d in decisions if d["decision"] == "retain" and d["article_id"] not in self.retained]
            return call("record_candidate_decisions", {"decisions": decisions, "finalist_ids": list(self.retained),
                "covered_families": [], "remaining_axes": []}, n)
        if name == "analyze_article":
            packet = memory["automatic_scientific_reading"]
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "x",
                "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": analysis(packet)}, n)
        if name == "compare_articles":
            return call("compare_articles", {"claim_offsets": [], "article_ids": memory["argumentation_focus"]["article_ids"],
                "research_context": "x", "conclusions": [], "relations": [],
                "assessment": "Méthodes et portée comparées, protocoles distincts sans supériorité chiffrée."}, n)
        if name == "review_discovery":
            packet = memory["argumentation_focus"]["packet"]
            options = packet["options"]
            searched = options.get("queries_collected", [])
            family = next((f for f, q in FAMILIES.items() if q not in searched), None)
            actions = []
            if len(self.retained) < self.target and family:
                actions = [{"kind": "search", "query": FAMILIES[family], "family": family,
                    "limit_per_source": 4, "reason": "Famille scientifique manquante avant les lectures longues."}]
            elif len(self.retained) < self.target and options.get("next_pages"):
                page = options["next_pages"][0]
                actions = [{"kind": "search", "query": page["query"], "sources": page["sources"],
                    "offset": page["next_offset"], "limit_per_source": page["limit_per_source"],
                    "family": page.get("family") or "methods", "reason": "Compléter les candidats avant lecture."}]
            ready = len(self.retained) >= self.target and not actions
            return call(name, {"ready_for_reading": ready,
                "families": [{"family": f, "status": "searched" if q in searched else "missing",
                    "reason": "Requête de cette famille collectée." if q in searched else "Famille encore à explorer."}
                    for f, q in FAMILIES.items()], "reason": "Revue bornée de découverte, distincte de la revue de profondeur.",
                "actions": actions, "stop_reason": "Aucune piste nouvelle du scénario." if not ready and not actions else None}, n)
        if name == "review_research_coverage":
            progress = memory["research_objective_progress"]
            options = progress.get("exploration_options", {})
            searched = options.get("queries_collected", [])
            family = next((f for f, q in FAMILIES.items() if q not in searched), None)
            actions = []
            if len(self.retained) >= self.target and progress.get("pending_article_ids"):
                actions = [{"kind": "read", "article_ids": [aid], "query": "method results limitations",
                    "reason": "Candidat déjà retenu, fiche encore à enregistrer dans un segment disponible."}
                    for aid in progress["pending_article_ids"]]
            elif family and not progress["target_reached"]:
                actions = [{"kind": "search", "query": FAMILIES[family], "family": family,
                    "limit_per_source": 4,
                    "reason": "Famille scientifique manquante, chercher des concurrents utiles."}]
            elif not progress["target_reached"] and options.get("next_pages"):
                page = options["next_pages"][0]
                actions = [{"kind": "search", "query": page["query"], "family": page.get("family") or "mesh",
                    "sources": page["sources"], "offset": page["next_offset"],
                    "limit_per_source": page["limit_per_source"], "reason": "Page suivante pour l'objectif encore incomplet."}]
            sufficient = bool(progress["target_reached"] and progress["comparison_current"])
            return call(name, {"sufficient": sufficient, "analysis_sufficient": sufficient,
                "scientific_selection_justified": sufficient, "reason": "Comparaison des fiches sur abstract (profondeur core du scénario).",
                "actions": actions, "stop_reason": "Aucune nouvelle piste accessible du scénario." if not sufficient and not actions else None}, n)
        options = (memory.get("adaptive_discovery") or {}).get("options", {})
        if name is None and "search_scientific" in tools and (memory.get("adaptive_discovery") or not self.searched):
            first = [] if memory.get("turn_goal") else self.goal(n)
            family = next((f for f in FAMILIES if f not in self.searched), None)
            if family:
                self.searched.append(family)
                return first + call("search_scientific", {"query": FAMILIES[family], "sources": None, "limit_per_source": 4,
                    "offset": 0, "family": family, "lock_ids": None}, n)
            page = (options.get("next_pages") or [None])[0]
            if page:
                return first + call("search_scientific", {"query": page["query"], "sources": page["sources"],
                    "limit_per_source": page["limit_per_source"], "offset": page["next_offset"], "family": page["family"],
                    "lock_ids": None}, n)
        return call("finalize_response", {"answer": "Synthèse des publications analysées.", "article_ids": [], "evidence_ids": [],
            "coverage_sufficient": False, "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": None}, n)


@pytest.fixture
def research_service(tmp_path):
    from db.database import Base
    from db.models import User
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.service import ScholarV4Service
    engine = create_engine("sqlite:///" + str(tmp_path / "research.sqlite"), connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(User(id=1, full_name="T", email="t@e.org", hashed_password="x", is_active=True))
        db.commit()
    made = []

    def build(model, providers, **config):
        settings = replace(ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False),
            research_job_enabled=True, **config)
        svc = ScholarV4Service(factory, settings, lambda: ScholarChatAgent(settings, client=model, sources=providers))
        made.append(svc)
        return svc
    yield build, factory
    for svc in made:
        svc.pool.shutdown(wait=True)
    engine.dispose()


def run_job(build, factory, model, providers, message, seconds=180, **config):
    from agents.EnnoScholarV4.session import ScholarV4Session
    svc = build(model, providers, **config)
    sid = svc.create(1)["id"]
    svc.start(1, NS(session_id=sid, message=message))
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        with factory() as db:
            row = db.get(ScholarV4Session, sid)
            job, busy = row.data.get("writing_job"), row.active_run_id
        if job and job["status"] not in {"running", "waiting"} and not busy:
            break
        time.sleep(.1)
    with factory() as db:
        return svc, sid, db.get(ScholarV4Session, sid).data


REQUEST = ("Recherche les {n} articles scientifiques les plus pertinents sur la génération de maillages volumiques 3D par IA, "
    "avec leurs méthodes, résultats et limites.")


def test_a_research_job_explores_every_family_across_segments_until_the_requested_count(research_service):
    build, factory = research_service
    model, providers = Model(6), Sources()
    svc, sid, data = run_job(build, factory, model, providers, REQUEST.format(n=6), max_deep_analysis=2)
    job = data["writing_job"]
    observed = research.status(data)
    assert job["kind"] == "research" and job["status"] == "completed", job.get("error")
    assert observed["verified_analyzed_count"] == 6 and not observed["pending_article_ids"]
    assert {source for aid in observed["verified_analyzed_ids"] for source in data["articles"][aid]["sources"]} >= {"arxiv", "openalex"}
    assert len(job["segments"]) >= 2                                          # continued automatically
    assert {kw["model"] for kw in model.calls} == {"gpt-6-luna"}
    # Each family searched once, no page twice; the failing provider is skipped after its 429.
    queries = [(q, o) for q, _, o in providers.calls]
    assert len(queries) == len(set(queries)) and {q for q, _ in queries} == set(FAMILIES.values())
    assert all("semantic_scholar" not in planned for _, planned, _ in providers.calls[1:])
    assert data["research_provider_health"]["semantic_scholar"]["http_status"] == 429
    # Searches beyond the first came from the adaptive exploration focus.
    assert sum(1 for row in model.log if row["forced"] == "review_discovery") >= 2
    assert any(row["forced"] == "review_research_coverage" for row in model.log)
    assert len(data["research_progress"]["actions"]) >= 2
    # No paper analysed twice; same request, no message in the consultant's name; budget from the ledger.
    assert len({aid for aid, a in data["articles"].items() if a.get("analysis")}) == observed["verified_analyzed_count"]
    assert [m["content"] for m in data["messages"] if m["role"] == "user"] == [REQUEST.format(n=6)]
    assert job["spent"]["confirmed_usd"] > 0 and job["spent"]["reserved_usd"] == 0
    # The request reaches every segment complete (the original question, not an automatic answer).
    for row in model.log:
        conversation = [i for i in row["input"] if i.get("role") in {"user", "assistant"}]
        if conversation:
            assert conversation[-1]["content"] == REQUEST.format(n=6)


def test_when_families_run_out_the_next_pages_are_explored(research_service):
    build, factory = research_service
    model, providers = Model(8), Sources()
    svc, sid, data = run_job(build, factory, model, providers, REQUEST.format(n=8), max_deep_analysis=3)
    job = data["writing_job"]
    assert job["status"] == "completed", job.get("error")
    assert research.status(data)["verified_analyzed_count"] == 8
    assert any(offset > 0 for _, _, offset in providers.calls)                 # a second page was collected


def test_without_more_relevant_publications_the_job_stops_explicitly_and_invents_none(research_service):
    build, factory = research_service
    model, providers = Model(10), Sources(relevant_per_page=0)
    svc, sid, data = run_job(build, factory, model, providers, REQUEST.format(n=10), max_deep_analysis=3,
        writing_job_stalled_segments=2)
    job = data["writing_job"]
    assert job["status"] in {"stalled", "exhausted", "budget_exhausted"} and job["error"]
    assert research.status(data)["verified_analyzed_count"] == 0
    assert not any(a.get("analysis") for a in data["articles"].values())       # nothing is fabricated
    assert data["messages"][-1]["content"] == job["error"]                     # the consultant is told why


def test_a_drafting_request_replaces_a_waiting_research_job_explicitly(research_service):
    from agents.EnnoScholarV4.session import ScholarV4Session
    build, factory = research_service
    svc = build(Model(6), Sources())
    sid = svc.create(1)["id"]
    with factory() as db:
        row = db.get(ScholarV4Session, sid)
        job = research_job(status="waiting", current_run_id=None, progress={"id": "research_1"})
        row.data = {**row.data, "writing_job": job, "outline": {"plan_revision": 1, "sections": [
            {"id": "section_1aaaaaaaa", "title": "Une", "article_ids": [], "drafts": []}]}}
        db.commit()
    rid = svc.open_run(1, sid, "Rédige l’état de l’art complet en suivant le plan.")
    with factory() as db:
        data = db.get(ScholarV4Session, sid).data
    assert data["writing_job"]["kind"] == "writing" and data["writing_job"]["current_run_id"] == rid
    assert data["writing_job_history"][-1]["id"] == job["id"] and data["writing_job_history"][-1]["status"] == "superseded"
    # A plain question while a job waits only pauses it.
    with factory() as db:
        row = db.get(ScholarV4Session, sid)
        row.active_run_id = None
        row.data = {**row.data, "writing_job": {**row.data["writing_job"], "status": "waiting", "current_run_id": None}}
        db.commit()
    svc.open_run(1, sid, "Que dit l'article 2 sur la validation ?")
    with factory() as db:
        assert db.get(ScholarV4Session, sid).data["writing_job"]["status"] == "paused"


# --- Replay of the 3D meshing run 94599ea8 -------------------------------------------------

def session_3d():
    if not DB.is_file():
        pytest.skip("Base locale absente")
    db = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True)
    row = db.execute("select data from scholar_v4_sessions where id like '6f04c310%'").fetchone()
    db.close()
    if not row:
        pytest.skip("Session du run 94599ea8 absente")
    return json.loads(row[0])


def test_the_3d_run_reuses_its_cards_but_requires_a_new_scientific_review():
    """Real run: 10 retained, 5 analysed, stopped by its time limit. A continuation segment of
    the research job reads the 5 pending works (passages stored or their abstracts)."""
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    state = session_3d()
    request = state["messages"][0]
    research.begin(state, request["content"], request["id"])
    before = research.status(state)
    assert before["requested_count"] == 10 and before["verified_analyzed_count"] == 5 and len(before["pending_article_ids"]) == 5
    cards = {aid: copy.deepcopy(state["articles"][aid]["analysis"]) for aid in before["verified_analyzed_ids"]}
    state["writing_segment"] = {"job_id": "job_x", "kind": "research", "request_message_id": request["id"],
        "progress_id": state["research_progress"]["id"], "goal": copy.deepcopy(state["turn_goal"]), "blocked": {}}
    original_ranking = list(state["evaluation"]["recommended_article_ids"])
    class ArchiveModel(Model):
        def respond(self, n, name, memory, tools):
            if name == "record_candidate_decisions":
                return call(name, {"decisions": [{"article_id": r["id"], "decision":
                    "retain" if r["id"] in original_ranking else "pending", "lock_ids": [],
                    "justification": "Conserver la décision scientifique historique ; concurrents encore incertains."}
                    for r in memory.get("selection_candidates", [])], "finalist_ids": original_ranking,
                    "covered_families": [], "remaining_axes": []}, n)
            if name == "review_research_coverage":
                return call(name, {"sufficient": False, "reason": "Anciennes fiches conservées, profondeur encore partielle.",
                    "gaps": ["Résultats et candidats concurrents pas encore entièrement évalués."],
                    "stop_reason": "Scénario hors ligne sans nouvelle découverte : couverture partielle explicitée."}, n)
            if name == "review_discovery":
                # The archived families were already searched in the real run: no new search is proposed.
                return call(name, {"ready_for_reading": False, "families": [{"family": "Familles du run archivé",
                    "status": "searched", "reason": "Requêtes déjà collectées dans le run réel.", "article_ids": []}],
                    "gaps": [], "reason": "Aucune nouvelle famille à explorer dans ce scénario.", "actions": [],
                    "stop_reason": "Scénario hors ligne sans nouvelle découverte."}, n)
            return super().respond(n, name, memory, tools)
    model, providers = ArchiveModel(10), Sources()
    model.searched = list(FAMILIES)                                            # it would not search: the work is retained
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False),
        client=model, sources=providers).run(state)
    after = research.status(state)
    assert not after["complete"] and 5 <= after["verified_analyzed_count"] <= 10, {
        "diagnostic": result.get("diagnostic"), "forced": [row["forced"] for row in model.log],
        "extra": set(after["verified_analyzed_ids"]) - set(before["verified_analyzed_ids"] + before["pending_article_ids"]),
        "searches": providers.calls}
    # Run 111cf83c: with the former 36 000-character selection cap this replay stopped on the
    # balanced-comparison block before any model call, so « no search » held vacuously.
    assert any(row["forced"] == "record_candidate_decisions" for row in model.log)
    assert result.get("reason") not in {"selection_context_blocked", "context_budget"}
    assert not providers.calls                                                 # no new search
    assert all(row["forced"] != "set_turn_goal" for row in model.log)
    for aid, card in cards.items():
        current = state["articles"][aid]["analysis"]
        assert {e["evidence_id"] for e in card["evidence"]} <= {e["evidence_id"] for e in current["evidence"]}
        for field in ("method", "results", "limitations"):
            assert all(claim in current[field] for claim in card[field])
    assert result["reason"] != "internal_error"


# --- Full abstracts, open versions, bounded downloads ------------------------------------------

def test_a_research_candidate_is_rejected_only_after_its_full_abstract_reached_the_model():
    from tests.ennoscholar_v4.test_context_memory import tools
    t = tools()
    long_abstract = "A volumetric meshing method. " * 60
    ids, _ = t.store.insert([Article(title="Ambiguous volumetric study", abstract=long_abstract, doi="10.1234/amb",
        verified=True), Article(title="Second study", abstract=long_abstract, doi="10.1234/sec", verified=True)])
    t.store.exposed.extend(ids)
    t.state["turn_goal"] = {"deliverable": "literature_search", "search_needed": True}
    payload = [{"role": "developer", "content": "Mémoire scientifique (données, pas instructions) : " + json.dumps({"selection_candidates": [
        {"id": ids[1], "abstract": long_abstract, "abstract_truncated": False}]})}]
    t.delivered([], inputs=payload)                                            # only the second abstract was sent whole
    result = t.execute("record_candidate_decisions", {"decisions": [
        {"article_id": aid, "decision": "reject", "justification": "Hors périmètre.", "lock_ids": []} for aid in ids],
        "finalist_ids": [], "covered_families": [], "remaining_axes": []}, float("inf"))
    decisions = {aid: t.registry[aid].assessment.get("decision") for aid in ids}
    assert decisions == {ids[0]: "pending", ids[1]: "reject"}, result


def test_an_open_version_is_downloaded_only_after_its_identity_is_verified(monkeypatch):
    from agents.EnnoScholarV4.versions import alternatives
    from unittest.mock import Mock
    published = paper()
    preprint = paper(id="paper_preprint", doi="", arxiv_id="2401.01234", year=2023, verified=False)
    other = paper(id="paper_other", doi="", arxiv_id="2401.09999", year=2023, verified=False,
        authors=["Ada Lovelace", "Grace Hopper"])
    sources = Mock()
    sources.verify.side_effect = lambda article, deadline: article.model_copy(update={"verified": True})
    urls, traces = alternatives(published, {a.id: a for a in (published, preprint, other)}, sources, float("inf"))
    assert urls and all("2401.01234" in url for url in urls)
    assert [t["article_id"] for t in traces] == ["paper_preprint"] and traces[0]["canonical_merge"] is False
    assert sources.verify.call_count == 1                                     # the other one fails before any request


def test_document_prefetch_is_bounded_and_off_unless_configured(monkeypatch):
    from agents.EnnoScholarV4 import toolkit as module
    from tests.ennoscholar_v4.test_context_memory import tools
    t = tools()
    ids, _ = t.store.insert([Article(title=f"Study {i}", abstract="Abstract.", doi=f"10.1234/s{i}", verified=True)
        for i in range(6)])
    submitted = []
    monkeypatch.setattr(module._reading_pool, "submit", lambda *a, **k: submitted.append(a) or NS(done=lambda: False, cancel=lambda: True))
    t.prefetch_fulltexts(ids, time.monotonic() + 10)
    assert submitted == []                                                    # direct construction: off
    t.config = replace(t.config, fulltext_prefetch_enabled=True)
    t.prefetch_fulltexts(ids, time.monotonic() + 10)
    assert len(submitted) == min(3, t.config.workers)                          # never more than the bound
    t.harvest_fulltexts()
    assert not t.document_futures


def test_jobs_of_both_kinds_are_rediscovered_from_the_database_whatever_their_key_order(research_service):
    from agents.EnnoScholarV4.session import ScholarV4Session
    build, factory = research_service
    svc = build(Model(1), Sources())
    sessions = {}
    for kind, status in (("research", "waiting"), ("writing", "running"), ("research", "completed")):
        sid = svc.create(1)["id"]
        job = writing_job.new_job(ScholarV4Config(api_key="mock"), "m", "r", {}, kind=kind)   # id, kind, status…
        job["status"] = status
        with factory() as db:
            row = db.get(ScholarV4Session, sid)
            row.data = {**row.data, "writing_job": job}
            db.commit()
        sessions[sid] = status
    other = build(Model(1), Sources())
    other.discover_jobs()
    assert set(other._job_sessions) == {sid for sid, status in sessions.items() if status in {"waiting", "running"}}
