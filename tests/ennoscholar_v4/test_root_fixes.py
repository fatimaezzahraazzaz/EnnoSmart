"""Root fixes of the real runs d71d17b0 (plan) and 2fd068ff (draft), session 23236f69.

Offline only: fake model, fake providers, simulated clock. No OpenAI call, no network,
no write to the local database (read-only, in-memory copies).
"""
import copy
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4 import agent as module
from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.context import model_context
from agents.EnnoScholarV4.diagnostic_link import lock_dossier, lock_records, make_link
from agents.EnnoScholarV4.toolkit import ScholarTools
from tests.ennoscholar_v4.test_context_memory import memory_of
from tests.ennoscholar_v4.test_scientific_reading import article
from tests.ennoscholar_v4.test_performance import with_refs
from tests.ennoscholar_v4.test_turn_goal import (TimedClient, calls, linked_state, search, sources, stored, part_payload,
    PAPERS)

DB = Path(__file__).resolve().parents[2] / "ennoscholar_v4_test.db"


def forced(kw):
    choice = kw.get("tool_choice")
    return choice.get("name") if isinstance(choice, dict) else None


# --- 1. Orchestration: stale finalists and a plan request ----------------------

def stale_state():
    """A linked conversation whose two earlier finalists are analysed works on another
    topic (as in session 23236f69 before run d71d17b0)."""
    state = linked_state("Recherche les meilleurs articles pour chacun des quatre verrous et construis le plan de mon état de l'art.")
    old = [article("Retinal vessel segmentation with polygon scanning", "10.9/old-a", "medical", "Polygon scanning"),
        article("Indonesian named entity recognition with HMM", "10.9/old-b", "nlp", "HMM tagging")]
    for i, paper in enumerate(old):
        paper.id = f"paper_old_{i}"
        paper.assessment = {"decision": "retain", "justification": "Ancien tour.", "basis": "abstract"}
    state["articles"] = {p.id: p.durable() for p in old}
    state["evaluation"] = {"recommended_article_ids": [p.id for p in old]}
    return state, [p.id for p in old]


def analyse_stale(state):
    """An earlier turn read the two old finalists: their cards exist and are comparable."""
    tools_state = state
    def respond(n, kw):
        if forced(kw) == "analyze_article":
            packet = memory_of(kw)["automatic_scientific_reading"]
            return calls(("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "x",
                "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": with_refs(packet)})), 30, 1500
        return [{"type": "message", "content": [{"type": "output_text", "text": "Analyse faite.", "annotations": []}]}], 5, 50
    state["messages"] = [{"id": "m0", "role": "user", "content": "Analyse en profondeur ces deux travaux."}]
    goal = {"deliverable": "deep_reading", "lock_ids": [], "focus_lock_ids": [], "exact_quote": "", "reading_depth": "deep",
        "understanding": "Lecture des deux travaux.", "search_needed": False}
    clock = [0.]
    module.time = NS(monotonic=lambda: clock[0])
    ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=TimedClient(respond, clock, goal),
        sources=sources()).run(tools_state)
    return state


@pytest.mark.parametrize("search_needed", [None, True])
def test_a_plan_request_never_compares_or_blueprints_stale_finalists(monkeypatch, search_needed):
    previous = module.time
    try:
        state, old_ids = stale_state()
        analyse_stale(state)
        assert all(state["articles"][aid]["analysis"] for aid in old_ids)
        state["messages"].append({"id": "msg_plan", "role": "user", "content":
            "Recherche les meilleurs articles pour chacun des quatre verrous et construis le plan de mon état de l'art."})
        for key in ("turn_goal", "automatic_reading_focus"):
            state.pop(key, None)
        clock = [0.]
        module.time = NS(monotonic=lambda: clock[0])
        trace, conversations = [], []

        def respond(n, kw):
            name, memory = forced(kw), memory_of(kw)
            trace.append((name or memory.get("context_task", "conversation"), clock[0]))
            if name == "analyze_article":
                packet = memory["automatic_scientific_reading"]
                assert packet["article_id"] not in old_ids, "A stale finalist is read again"
                return calls(("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "x",
                    "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": with_refs(packet)})), 40, 1800
            if name in {"compare_articles", "build_argument_blueprint"}:
                ids = memory["argumentation_focus"]["article_ids"]
                assert not set(ids) & set(old_ids), f"{name} on stale finalists {ids}"
                assert name != "build_argument_blueprint", "No automatic blueprint for a plan"
                return calls(("compare_articles", {"article_ids": ids, "research_context": "x", "conclusions": None,
                    "relations": [], "claim_offsets": []})), 20, 400
            if name == "record_candidate_decisions":
                decisions = [{"article_id": stored(state, lid), "decision": "retain", "justification": "Contribution au verrou.",
                    "lock_ids": [lid], "contribution": "direct"} for lid in "1234"]
                return calls(("record_candidate_decisions", {"decisions": decisions, "finalist_ids": [d["article_id"] for d in decisions],
                    "covered_families": [], "remaining_axes": []})), 20, 600
            if name == "propose_plan_sections":
                return part_payload(kw, state, n=n), 30, 1200
            conversations.append(n)
            return calls(search("1"), search("2"), search("3"), search("4")), 10, 300

        goal = {"deliverable": "plan", "lock_ids": [], "focus_lock_ids": [], "exact_quote": "", "reading_depth": "deep",
            "understanding": "Plan par verrou avec recherche.", "search_needed": search_needed}
        result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=TimedClient(respond, clock, goal),
            sources=sources()).run(state)
        assert not result["partial"], result["reason"]
        kinds = [k for k, _ in trace]
        # Search first, then this message's selection, then readings of the new finalists.
        assert kinds[0] == "conversation"
        first_selection = kinds.index("record_candidate_decisions")
        assert all(k not in {"analyze_article", "compare_articles", "build_argument_blueprint"} for k in kinds[:first_selection])
        assert "build_argument_blueprint" not in kinds
        assert result["tool_counts"].get("build_argument_blueprint") is None
        assert state["outline"]["sections"]
    finally:
        module.time = previous


# --- 2. EnnoDiagnostic context actually sent to the model (real LiDAR dossier) -----

def real_session():
    if not DB.is_file():
        pytest.skip("Base locale absente")
    db = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    row = db.execute("select data from scholar_v4_sessions where id like '23236f69%'").fetchone()
    plan = db.execute("select data from scholar_v4_outlines where session_id like '23236f69%' and version=1").fetchone()
    run = db.execute("select data from scholar_v4_runs where id='d71d17b007d149738df84896dfe62510'").fetchone()
    db.close()
    if not row:
        pytest.skip("Session LiDAR absente")
    state = json.loads(row[0])
    # Historical fixture: later live drafting must not change the eight finalists
    # or replace the 12-section plan this regression was written to exercise.
    if plan and run:
        state["outline"] = json.loads(plan[0])
        finalists = json.loads(run[0])["recommended_article_ids"]
        state["evaluation"]["recommended_article_ids"] = finalists
        for aid in finalists:
            state["articles"][aid].update(analysis={}, analysis_basis=None)
        historical_sources = set(finalists) | {aid for section in state["outline"]["sections"] for aid in section["article_ids"]}
        state["articles"] = {aid: row for aid, row in state["articles"].items() if aid in historical_sources}
        state["document"] = {}
    # As at the start of every run: foci left by an interrupted run are not resent.
    # Per-request state left by the last real run (the agent resets it at each request).
    for key in ("argumentation_focus", "writing_focus", "writing_planning", "turn_goal", "automatic_reading_focus", "selection_focus",
            "current_turn_article_ids", "conversation_references", "request_card_pages"):
        state.pop(key, None)
    # As in a real drafting or planning request, the latest message is the consultant's request
    # (the base now ends with a long answer of a later question, which is not this task's input).
    state.setdefault("messages", []).append({"id": "test_request", "role": "user",
        "content": "Continue la rédaction de mon état de l'art CIR."})
    return state


def sent_memory(state, tools, budget=None):
    from agents.EnnoScholarV4.context import PLAN_PART_CONTEXT_CHARS, SECTION_CONTEXT_CHARS
    budget = budget or (PLAN_PART_CONTEXT_CHARS if state.get("plan_part") else SECTION_CONTEXT_CHARS if state.get("writing_focus") else 18000)
    for item in model_context(state, tools, budget):
        if item.get("role") == "developer":
            return json.loads(item["content"].split(" : ", 1)[1])


def test_each_lock_part_receives_its_project_facts_and_evidence_from_the_real_dossier():
    state = real_session()
    tools = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    snapshot = state["diagnostic_link"]["snapshot"]
    assert [v["id"] for v in snapshot["locks"]] == ["1", "2", "3", "4"]
    state["writing_planning"] = True
    for lock in snapshot["locks"]:
        state["plan_part"] = {"kind": "lock", "lock_ids": [lock["id"]], "label": lock["title"]}
        memory = sent_memory(state, tools)
        dossier = memory["diagnostic_context"]
        row = next(r for r in dossier["locks"] if r["id"] == lock["id"])
        # Every fact documented for this lock and every evidence passage reach the model.
        assert row["facts_total"] >= 2 and row["facts_shown"] == row["facts_total"], (lock["id"], row["facts_shown"], row["facts_total"])
        assert row["passages_shown"] == row["passages_total"] >= 1
        assert row.get("justification")
        # Provenance: units linked by the diagnostic or the lock's own evidence passages; inside a
        # unit, a same-section fact is a documentary grouping, never more. Every fact has evidence.
        facts = [f for unit in row["work_units"] for v in unit["roles"].values() for f in v]
        assert {u["link"] for u in row["work_units"]} <= {"declared", "lock_evidence_passage", "lock_evidence_passage_partial"}
        assert {f["link"] for f in facts} <= {"declared", "lock_evidence_passage", "same_document_section"}
        assert all(f["evidence_ids"] and all(e in snapshot["evidence"] for e in f["evidence_ids"]) for f in facts)
        assert all(p["quote_verified"] is True and p["quote"] for p in row["evidence_passages"])
        # General facts stay labelled as not linked (no "link" key).
        assert all("link" not in f for f in dossier.get("general_context", []))
        assert len(json.dumps(dossier, ensure_ascii=False)) > 3000
    # The trials of each lock are those of its own evidence passages (Essai n).
    facts = {lid: " ".join(f["statement"] for _, f in lock_records(state, v)) for lid, v in ((v["id"], v) for v in snapshot["locks"])}
    assert "Essai 1" in facts["1"] and "Essai 4" in facts["2"] and "Essai 3" in facts["3"]
    # Lock 4's own method (read from the base, not written here) reaches its facts.
    assert title_words(snapshot, "4") & set(re.findall(r"\w{6,}", facts["4"]))
    assert "Essai 3" not in facts["1"] and "Essai 1" not in facts["3"]


def title_words(snapshot, lock_id):
    """Distinctive words of one lock's title in the real diagnostic (kept out of the sources)."""
    title = next(v["title"] for v in snapshot["locks"] if v["id"] == lock_id)
    common = set(re.findall(r"\w{6,}", " ".join(v["title"] for v in snapshot["locks"] if v["id"] != lock_id)))
    return set(re.findall(r"\w{6,}", title)) - common


def test_a_section_and_a_small_budget_keep_every_lock_with_its_first_facts():
    state = real_session()
    from agents.EnnoScholarV4.context import DOSSIER_FLOOR
    dossier = lock_dossier(state, None, DOSSIER_FLOOR)
    assert [r["id"] for r in dossier["locks"]] == ["1", "2", "3", "4"]
    # Before: 716 characters and no technical fact (active_packet shrunk to 1200).
    assert all(r["facts_shown"] >= 1 for r in dossier["locks"])
    assert dossier["partial"] and len(json.dumps(dossier, ensure_ascii=False)) <= DOSSIER_FLOOR
    tools = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    section = next(s for s in state["outline"]["sections"] if s.get("lock_ids") == ["4"])
    state["document"]["writing_progress"] = {"id": "w", "mode": "full", "target_ids": [section["id"]],
        "plan_revision": state["outline"].get("plan_revision"), "baseline_draft_ids": {}, "saved_draft_ids": {}, "errors": [],
        "section_status": {}, "status": "running", "approval_required": True}
    state["writing_focus"] = {"section_id": section["id"], "article_ids": section["article_ids"], "retrieval_progress": []}
    memory = sent_memory(state, tools)
    row = memory["diagnostic_context"]["locks"]
    assert [r["id"] for r in row] == ["4"] and row[0]["facts_shown"] == row[0]["facts_total"]
    assert title_words(state["diagnostic_link"]["snapshot"], "4") & set(re.findall(r"\w{6,}", json.dumps(row, ensure_ascii=False)))


# --- 3. Every finalist reaches the plan ---------------------------------------

def test_the_eight_lidar_finalists_reach_every_plan_preparation():
    state = real_session()
    finalists = state["evaluation"]["recommended_article_ids"]
    assert len(finalists) == 8
    tools = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    state["writing_planning"] = True
    state["plan_part"] = {"kind": "framing", "lock_ids": [], "label": "introduction et synthèse"}
    framing = sent_memory(state, tools)
    assert set(finalists) <= {r["id"] for r in framing["plan_sources"]}
    for lid in "1234":
        state["plan_part"] = {"kind": "lock", "lock_ids": [lid], "label": lid}
        memory = sent_memory(state, tools)
        own = {r["id"] for r in memory["plan_sources"]}
        others = {r["id"] for r in memory["other_plan_sources"]}
        assert set(finalists) <= own | others and not own & others
        assert all(lid in r["lock_ids"] for r in memory["plan_sources"])
    # Old analyses of works outside the plan no longer travel with a plan part.
    assert "conversation_science" not in memory


# --- 4. A work found several turns earlier is judged and used later --------------

def test_an_earlier_undecided_candidate_is_selected_and_reaches_its_plan_part(monkeypatch):
    clock = [0.]
    monkeypatch.setattr(module, "time", NS(monotonic=lambda: clock[0]))
    state = linked_state("Construis le plan de mon état de l'art par verrou.")
    # Turn 1 found this lock-1 work; nobody judged it (run d71d17b0: 60 such notices).
    early = article("Exploring label probability sequence to robustly learn road extraction", "10.1234/early", "roads",
        "Label probability sequence")
    early.id = "paper_early"
    # Finalists already exist (as in the real session), so the context shows them, not old notices.
    prior = article("Prior finalist on polygon topology", "10.1234/prior", "roads", "Polygon topology")
    prior.id = "paper_prior"
    prior.assessment = {"decision": "retain", "justification": "Tour précédent.", "lock_ids": ["2"], "basis": "abstract"}
    state["articles"] = {early.id: early.durable(), prior.id: prior.durable()}
    state["evaluation"] = {"recommended_article_ids": [prior.id]}
    state["exposed_article_ids"] = state["examined_article_ids"] = [prior.id]
    state["recent_searches"] = [{"key": ["old"], "query": "road extraction noisy labels", "family": "verrou 1", "lock_ids": ["1"],
        "article_ids": [early.id], "warnings": []}]
    seen = {}

    def respond(n, kw):
        name, memory = forced(kw), memory_of(kw)
        if name == "record_candidate_decisions":
            rows = memory["selection_candidates"]
            seen.update({r["id"]: r for r in rows})
            decisions = [{"article_id": r["id"], "decision": "retain", "justification": "Pertinent pour le verrou.",
                "lock_ids": ["1"] if r["id"] == early.id else [r["id"][-1]] if r["id"][-1] in "1234" else [],
                "contribution": "direct"} for r in rows]
            return calls(("record_candidate_decisions", {"decisions": decisions, "finalist_ids": [r["id"] for r in rows],
                "covered_families": [], "remaining_axes": []})), 15, 500
        if name == "propose_plan_sections":
            part = memory["plan_part"]
            if part["kind"] == "lock":
                seen.setdefault("parts", {})[part["lock_ids"][0]] = [r["id"] for r in memory["plan_sources"]]
                # The model cites the sources its part receives.
                sections = [{"title": f"Verrou {part['lock_ids'][0]}", "description": "Argument.", "article_ids":
                    [r["id"] for r in memory["plan_sources"]], "scientific_objective": "Question.", "questions": [],
                    "article_roles": [], "relation_ids": [], "experimental_results": [], "limitations": [], "evidence_ids": [],
                    "missing_information": [], "project_connection": "", "figure_suggestions": [], "lock_ids": part["lock_ids"]}]
                return calls(("propose_plan_sections", {"sections": sections, "closing_sections": [], "consultant_note": ""})), 30, 1200
            return part_payload(kw, state, n=n), 30, 1200
        # This message searches the other locks only.
        return calls(search("2"), search("3"), search("4")), 10, 300

    goal = {"deliverable": "plan", "lock_ids": [], "focus_lock_ids": [], "exact_quote": "", "reading_depth": "core",
        "understanding": "Plan par verrou.", "search_needed": True}
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=TimedClient(respond, clock, goal),
        sources=sources()).run(state)
    assert not result["partial"], result["reason"]
    assert seen[early.id].get("collected_in_earlier_message") is True
    assert early.id in seen["parts"]["1"]
    assert state["articles"][early.id]["assessment"]["decision"] == "retain"
    assert any(early.id in s["article_ids"] for s in state["outline"]["sections"])


def test_a_candidate_without_abstract_gets_its_provider_abstract_and_can_be_judged():
    from agents.EnnoScholarV4.sources import ScientificSources
    from agents.EnnoScholarV4.models import Article
    state = {"articles": {}, "messages": [{"id": "m", "role": "user", "content": "x"}]}
    provider = ScientificSources.__new__(ScientificSources)
    found = Article(title="Road Boundary Detection Based on the Dynamic Programming", doi="10.1234/rb", abstract="Exact provider abstract.",
        sources=["openalex"])
    provider.client = NS(lookup_doi=lambda doi, deadline=None: [found] if doi == "10.1234/rb" else [])
    tools = ScholarTools(state, provider, ScholarV4Config(api_key="mock"))
    rows = [Article(title="Road boundary detection based on the dynamic programming", doi="10.1234/rb", sources=["semantic_scholar"]),
        Article(title="Unrelated", doi="10.1234/none", sources=["openalex"])]
    ids, _ = tools.store.insert(rows, "verrou 4")
    assert len(ids) == 2
    tools.registry[ids[0]].assessment = {"decision": "pending", "justification": "Sans résumé."}
    added = tools.enrich_abstracts(ids, 10**12)
    assert added == [ids[0]]
    paper = tools.registry[ids[0]]
    assert paper.abstract == "Exact provider abstract." and paper.access_level == "abstract_only"
    assert paper.assessment["reopened_for_abstract"] and paper.provider_notices[-1]["provider"] == "openalex"
    # One lookup per work: a second request does not call the providers again.
    provider.client = NS(lookup_doi=lambda *a, **k: (_ for _ in ()).throw(AssertionError("looked up twice")))
    assert tools.enrich_abstracts(ids, 10**12) == []


# --- 5. Provider queries -------------------------------------------------------

def test_long_arxiv_queries_keep_two_required_concepts_and_alternatives():
    from agents.EnnoScholarV4.sources import arxiv_plain_query
    short = arxiv_plain_query(["road", "boundary", "programmation dynamique"])
    assert short == "(ti:road OR abs:road) AND (ti:boundary OR abs:boundary) AND (ti:programmation dynamique OR abs:programmation dynamique)"
    long = arxiv_plain_query(["road", "boundary", "programmation dynamique", "dynamic", "programming", "contours"])
    # Before: six required terms, 0 result in runs e7bae458 and d71d17b0.
    assert long.count(" AND ") == 2 and long.startswith("(ti:road OR abs:road) AND (ti:boundary OR abs:boundary) AND (")
    assert "ti:programmation dynamique OR abs:programmation dynamique OR ti:dynamic" in long


# --- 6. Plan rendering -----------------------------------------------------------

def test_plan_titles_are_numbered_once_and_publications_show_their_status():
    from agents.EnnoScholarV4.turn import strip_numbering, plan_answer
    assert strip_numbering("1. Cadrage de la chaîne") == "Cadrage de la chaîne"
    assert strip_numbering("2.3 — Polygonisation") == "Polygonisation"
    assert strip_numbering("IV. Verrou 4") == "Verrou 4"
    assert strip_numbering("C-shaped meshes") == "C-shaped meshes" and strip_numbering("Verrou 1 : labels") == "Verrou 1 : labels"
    state = linked_state()
    paper = PAPERS["1"].model_copy(deep=True)
    paper.assessment = {"decision": "retain", "justification": "x", "lock_ids": ["1"]}
    registry = {paper.id: paper}
    state["outline"] = {"title": "Plan", "version": 1, "sections": [{"id": "s1", "title": "1. Verrou 1", "article_ids": [paper.id],
        "article_roles": [], "lock_ids": ["1"]}]}
    text = plan_answer(state, registry)
    assert "### 1. Verrou 1" in text and "1. 1." not in text
    assert "retenue sur résumé, non analysée" in text


# --- 7. Drafting from the saved plan (replay of run 2fd068ff) ---------------------

@pytest.mark.parametrize("stubborn", [False, True])
def test_the_draft_replay_saves_every_section_within_the_deadline(stubborn):
    from tests.ennoscholar_v4 import replay_2fd068ff as replay
    if not replay.available():
        pytest.skip("Base locale absente")
    out = replay.run(stubborn)
    result, state, events = out["result"], out["state"], out["events"]
    progress = state["document"]["writing_progress"]
    # Real run: 0/12 sections, 11 refused project-context calls, upstream error at 146 s.
    assert len(progress["saved_draft_ids"]) == len(progress["target_ids"]) == 12
    assert not any(e.get("tool") == "get_project_context" and e.get("status") == "failed" for e in events)
    assert result["timings"]["wall_seconds"] < 600
    # One card per cited source before its sections; no comparison or blueprint stage.
    assert result["tool_counts"].get("compare_articles") is None and result["tool_counts"].get("build_argument_blueprint") is None
    saved = [s for s in state["outline"]["sections"] if s.get("drafts")]
    assert all(all(state["articles"][aid]["analysis"] for aid in s["drafts"][-1]["article_ids"]) for s in saved)
    assert result["timings"]["max_concurrent_analyses"] <= 3


def test_a_section_stuck_in_reading_rounds_is_asked_to_save(monkeypatch):
    """Run 2fd068ff: rounds of passage reads without any save attempt. After three
    rounds without new card claims, the round offers write_section(save) only."""
    from tests.ennoscholar_v4 import replay_2fd068ff as replay
    if not replay.available():
        pytest.skip("Base locale absente")
    monkeypatch.setattr(module, "PARALLEL_SECTIONS", 1)
    out = replay.run(stubborn=True)
    progress = out["state"]["document"]["writing_progress"]
    assert len(progress["saved_draft_ids"]) >= 2
    assert not any(e.get("tool") == "get_project_context" and e.get("status") == "failed" for e in out["events"])


# --- 8. A structured publication is analysed by dimension, without mixing works ----

def structured_paper(name, doi, metric):
    from agents.EnnoScholarV4.models import Article
    chunks = [
        {"section": "1 Introduction", "page": 1, "text": f"{name} addresses topology errors in vectorized road outlines."},
        {"section": "3 Method", "page": 3, "text": f"{name} couples a graph neural network with a polygon simplification step and a learned vertex classifier."},
        {"section": "4 Experiments", "page": 5, "text": f"{name} is trained for 50 epochs on the CityRoads dataset with a learning rate of 0.001."},
        {"section": "4.2 Results", "page": 6, "text": f"Table 2 reports for {name} a topological accuracy of {metric} % and a mean IoU of 71.3 % on CityRoads."},
        {"section": "5 Discussion", "page": 8, "text": f"{name} fails on occluded junctions and depends on accurate initial masks; generalization to rural roads is not evaluated."},
    ]
    return Article(title=f"{name}: topology-aware road vectorization", doi=doi, year=2024, authors=["Author"], verified=True,
        url=f"https://example.org/{doi[-4:]}", abstract=f"{name} improves topology of road outlines.", scientific_families=["roads"],
        fulltext={"url": f"https://example.org/{doi[-4:]}", "text": "\n\n".join(c["text"] for c in chunks), "chunks": chunks,
            "truncated": False, "access_level": "fulltext", "access_status": "fulltext_extracted"})


def test_a_structured_paper_card_keeps_method_protocol_table_results_and_limits_with_their_sources(monkeypatch):
    clock = [0.]
    monkeypatch.setattr(module, "time", NS(monotonic=lambda: clock[0]))
    papers = [structured_paper("RoadTopo", "10.1234/topo", "92.4"), structured_paper("VecNet", "10.1234/vecn", "88.1")]
    state = {"articles": {}, "messages": [{"id": "m", "role": "user", "content": "Analyse en profondeur RoadTopo et VecNet : méthode, expériences, résultats et limites."}]}
    for p in papers:
        p.id = "paper_" + p.doi[-4:]
        p.assessment = {"decision": "retain", "justification": "x", "basis": "abstract"}
        state["articles"][p.id] = p.durable()
    state["evaluation"] = {"recommended_article_ids": [p.id for p in papers]}
    windows = {}

    def respond(n, kw):
        if forced(kw) == "analyze_article":
            packet = memory_of(kw)["automatic_scientific_reading"]
            windows.setdefault(packet["article_id"], []).append({row["section"] for row in packet["passages"]})
            by = {row["section"]: row["ref"] for row in packet["passages"]}
            claim = lambda section: [{"statement": next(r["exact_quote"] for r in packet["passages"] if r["section"] == section),
                "evidence_ids": [by[section]], "origin": "source_statement"}] if section in by else []
            empty = {k: [] for k in ("problem", "objective", "contribution", "hypotheses", "generalization", "open_questions",
                "relevance", "equations", "validation_protocol", "datasets_materials", "tables_figures", "comparisons",
                "conditions_of_validity", "technical_mechanism")}
            analysis = {**empty, "method": claim("3 Method"), "experimental_setup": claim("4 Experiments"),
                "parameters": claim("4 Experiments"), "results": claim("4.2 Results"), "limitations": claim("5 Discussion"),
                "method_family": "graph", "analysis_limit": "Passages ciblés."}
            return calls(("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "x",
                "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": analysis})), 60, 1800
        return [{"type": "message", "content": [{"type": "output_text", "text": "Analyse disponible.", "annotations": []}]}], 5, 50

    goal = {"deliverable": "deep_reading", "lock_ids": [], "focus_lock_ids": [], "exact_quote": "", "reading_depth": "deep",
        "understanding": "Lecture approfondie.", "search_needed": False}
    sources_mock = Mock()
    sources_mock.verify.side_effect = lambda a, deadline: a.model_copy(update={"verified": True})
    ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=TimedClient(respond, clock, goal),
        sources=sources_mock).run(state)
    tasks = {t["article_id"]: t for t in state["scientific_reading"]["tasks"]}
    for p in papers:
        card = state["articles"][p.id]["analysis"]
        # The first window already spans method, protocol, table results and limitations.
        assert {"3 Method", "4 Experiments", "4.2 Results", "5 Discussion"} <= windows[p.id][0]
        for field, section in (("method", "3 Method"), ("experimental_setup", "4 Experiments"), ("results", "4.2 Results"),
                ("limitations", "5 Discussion")):
            claim = card[field][0]
            proof = state["evidence"][claim["evidence_ids"][0]]
            assert proof["article_id"] == p.id and proof["section"] == section, (p.id, field)
            assert claim["statement"] in proof["exact_quote"]
        # Numbers stay with their own publication.
        assert ("92.4" in card["results"][0]["statement"]) is (p.id == "paper_topo")
        dims = tasks[p.id]["dimensions"]
        assert all(dims[d]["status"] == "documented" for d in ("methods", "experimental_protocol", "results", "limits"))
        assert tasks[p.id]["basis"] == "fulltext"
        # A card made from part of the text is never declared exhaustive unless every passage was examined.
        assert tasks[p.id]["coverage_sufficient"] == (tasks[p.id]["remaining_passages"] == 0 and not tasks[p.id]["unincorporated_passages"])
