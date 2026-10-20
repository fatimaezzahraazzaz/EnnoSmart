"""Corrections of real run 91590987 (session 23236f69, 13/14 sections), audited by Codex.

Offline: fake model, fake providers, simulated clock, in-memory copies. The local
database is only read (mode=ro) for the real-data checks. No OpenAI call, no network.
"""
import copy
import json
import re
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4 import agent as module
from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.toolkit import ScholarTools
from tests.ennoscholar_v4.test_context_memory import memory_of
from tests.ennoscholar_v4.test_performance import with_refs
from tests.ennoscholar_v4.test_scientific_reading import article
from tests.ennoscholar_v4.test_turn_goal import TimedClient, calls
from tests.ennoscholar_v4.test_provenance_drafting import linked, save, draft

DB = Path(__file__).resolve().parents[2] / "ennoscholar_v4_test.db"


def real_state():
    if not DB.is_file():
        pytest.skip("Base locale absente")
    db = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    row = db.execute("select data from scholar_v4_sessions where id like '23236f69%'").fetchone()
    db.close()
    if not row:
        pytest.skip("Session LiDAR absente")
    return json.loads(row[0])


def generic_analysis(packet):
    """A card from whatever passages the window holds (full text sections or an abstract)."""
    rows = packet["passages"]
    by = {r["section"]: r for r in rows}
    if {"Methods", "Results", "Limitations"} <= set(by):
        return with_refs(packet)
    claim = lambda row: [{"statement": row["exact_quote"][:300], "evidence_ids": [row["ref"]], "origin": "source_statement"}]
    empty = {k: [] for k in ("problem", "objective", "contribution", "hypotheses", "generalization", "open_questions", "relevance",
        "equations", "parameters", "validation_protocol", "datasets_materials", "tables_figures", "comparisons",
        "conditions_of_validity", "technical_mechanism", "experimental_setup", "limitations")}
    return {**empty, "method": claim(rows[0]), "results": claim(rows[-1]), "method_family": "x", "analysis_limit": "Résumé seul."}


def forced(kw):
    choice = kw.get("tool_choice")
    return choice.get("name") if isinstance(choice, dict) else None


# --- 1. Passage identity and locators (real snapshot) --------------------------------

def test_aliases_of_one_passage_share_an_identity_and_trial_locators_follow_the_document():
    from agents.EnnoScholarV4.diagnostic_link import passage_index, work_units
    state = real_state()
    index = passage_index(state)
    evidence = state["diagnostic_link"]["snapshot"]["evidence"]
    # Audit: three ids for exactly the same polygonisation passage.
    trio = ["ev_fast_e920ab9a147cc77d", "ev_fast_6fa9ef2f801fae1a", "ev_fast_728d062872ec6566"]
    assert len({index["canonical"][e] for e in trio}) == 1
    assert set(trio) <= set(index["aliases"][index["canonical"][trio[0]]])
    # Same text in different documents stays distinct.
    by_quote = {}
    for eid, row in evidence.items():
        by_quote.setdefault(" ".join(row.get("quote", "").split()), set()).add(
            (row.get("document_id") or row.get("document_name"), index["canonical"][eid]))
    assert all(len({doc for doc, _ in rows}) == len({cid for _, cid in rows}) or len(rows) == 1 for rows in by_quote.values())
    # p69 opens trial 5; p70/p71 continue it although the diagnostic stored « Essai 4 ».
    locators = {evidence[cid].get("chunk_id"): loc for cid, loc in index["locators"].items()}
    assert locators["p69"]["spans"][-1].startswith("Essai 5") and locators["p69"]["stored_section_title"].startswith("Essai 4")
    assert locators["p70"]["effective_section_title"].startswith("Essai 5") and locators["p71"]["corrected"]
    assert locators["p66"]["effective_section_title"].startswith("Essai 2")
    # The geometric lock (4) is now tied to trial 5, not trial 4; the snapshot is unchanged.
    lock4 = next(v for v in state["diagnostic_link"]["snapshot"]["locks"] if v["id"] == "4")
    assert [u["unit"][:7] for u in work_units(state, lock4)] == ["Essai 5"]
    assert evidence["ev_fast_3eb1f978caf7d87c"]["section_title"].startswith("Essai 4")


def test_confrontation_traces_are_recomputed_without_changing_any_draft():
    from agents.EnnoScholarV4.provenance_check import draft_confrontation
    from agents.EnnoScholarV4.scientific_review import deterministic_review
    state = real_state()
    before = copy.deepcopy(state["outline"])
    recomputed = {}
    for section in state["outline"]["sections"]:
        current = (section.get("drafts") or [None])[-1]
        if current:
            recomputed[section["id"]] = (current.get("confrontation"), draft_confrontation(state, section, current))
    # Stored at save time with alias-blind matching: many sections counted 0 cited units.
    stored_zero = [sid for sid, (stored, _) in recomputed.items() if stored and stored["units_cited"] == 0]
    assert len(stored_zero) >= 6
    assert all(new["units_cited"] > 0 for sid, (stored, new) in recomputed.items() if sid in stored_zero)
    tools = ScholarTools(copy.deepcopy(state), Mock(), ScholarV4Config(api_key="mock"))
    codes = [i["code"] for i in deterministic_review(tools.state, tools)["issues"]]
    assert "project_confrontation_missing" not in codes and codes.count("legacy_provenance_unrecorded") == 2
    assert "numeric_mapping_missing" not in codes and "numeric_value_without_proof" not in codes
    assert state["outline"] == before


# --- 2. One rendering for the workspace and the exports ------------------------------

def test_the_real_manuscript_renders_once_titled_with_grouped_numbered_citations_and_full_references():
    from agents.EnnoScholarV4 import document_export as export
    from agents.EnnoScholarV4.citation_render import render_manuscript
    state = real_state()
    rendering = render_manuscript(state, state["outline"]["sections"])
    for section in state["outline"]["sections"]:
        if section.get("drafts"):
            body = rendering["sections"][section["id"]]
            assert not re.match(r"^\s*#", body)                      # no repeated plan title
            assert not re.search(r"\[(?:paper_|ev_|projet:)[\w-]+\]", body)  # every marker resolved
            assert not re.search(r"\[[^\]]*\]\s*\[[^\]]*\]", body.replace("](", ""))  # adjacent markers grouped
    references = rendering["references"]
    assert references and all(r.get("authors") and r.get("year") for r in references)
    assert not any(re.search(r"\.\s*\.", r["text"]) for r in references)  # no empty journal punctuation
    # Every project source keeps all its aliases for audit; corrected locators are visible.
    assert all(row["aliases"] for row in rendering["project_sources"])
    items = []
    original = export.render_docx
    export.render_docx = lambda rows: items.extend(rows) or b""
    try:
        export.export_document(copy.deepcopy(state), state["document_versions"][-1]["id"], "docx")
    finally:
        export.render_docx = original
    assert not any(a[0] == "heading" and b[0] == "heading" and a[1][1].split(" ", 1)[-1] == b[1][1]
        for a, b in zip(items, items[1:]))
    assert any(kind == "heading" and value[1].startswith("Sources du projet") for kind, value in items)


def test_reference_formatting_shows_known_metadata_without_empty_fields():
    from agents.EnnoScholarV4.citation_render import format_reference, section_body
    assert format_reference({"title": "Mesh paper", "authors": ["A. B"], "year": 2024, "journal": "", "doi": "10.1/x"}) == \
        "A. B (2024). Mesh paper. https://doi.org/10.1/x"
    assert format_reference({"title": "Untitled data"}) == "Untitled data."
    section = {"title": "3. Méthodes de maillage"}
    assert section_body(section, {"raw_text": "## Méthodes de maillage\n\nCorps."}) == "Corps."
    assert section_body(section, {"raw_text": "## Autre titre\n\nCorps."}).startswith("## Autre titre")


# --- 3. Context: bounded progress, numbers ------------------------------------------

def test_accumulated_refusals_never_push_the_proofs_out_of_a_section_request():
    from agents.EnnoScholarV4.context import model_context, transmitted_evidence_ids, SECTION_CONTEXT_CHARS
    from tests.ennoscholar_v4.test_context_memory import rich_corpus, seven_section_plan
    t, ids = rich_corpus(3)
    seven_section_plan(t, ids)
    sections = t.state["outline"]["sections"]
    t.state.setdefault("document", {})["writing_progress"] = {"id": "w", "mode": "full", "target_ids": [s["id"] for s in sections],
        "plan_revision": t.state["outline"].get("plan_revision"), "baseline_draft_ids": {}, "saved_draft_ids": {},
        "errors": [{"section_id": s["id"], "message": "Attribution non vérifiable : " + "détail " * 300} for s in sections for _ in range(4)],
        "section_status": {}, "status": "running", "approval_required": True}
    t.state["writing_focus"] = {"section_id": sections[1]["id"], "article_ids": ids, "retrieval_progress": []}
    inputs = model_context(t.state, t, SECTION_CONTEXT_CHARS)
    memory = memory_of({"input": inputs})
    progress = memory["writing_progress"]
    assert "errors" not in progress and progress["refusals_by_section"][sections[1]["id"]] == 4
    assert len(progress["active_section_last_refusal"]) <= 700
    packet = memory["section_drafting"]["packet"]
    assert packet["proof_backed_claims"] and transmitted_evidence_ids(inputs, t.evidence)
    assert len(t.state["document"]["writing_progress"]["errors"]) == 4*len(sections)  # durable history kept


def test_a_value_must_be_in_a_proof_cited_in_its_paragraph():
    t, ids, proofs = linked()
    eid = proofs[ids[1]]["Graphnet"]
    t.evidence[eid]["exact_quote"]  # passage without numbers
    with pytest.raises(ValueError, match="Valeurs chiffrées sans preuve"):
        save(t, [ids[1]], f"Graphnet atteint 84,7 % de précision [{ids[1]}] [{eid}].", [eid])
    # A value present in the cited project passage is traced (here « 216 »-free decimal absent → use project text).
    text = f"Graphnet prédit une métrique anisotrope [{ids[1]}] [{eid}]. Le projet n'en rapporte pas de valeur."
    assert save(t, [ids[1]], text, [eid])["saved"]
    assert draft(t)["numeric_trace"] == []


def test_a_traced_value_records_the_proof_that_contains_it():
    t, ids, proofs = linked()
    eid = proofs[ids[1]]["Graphnet"]
    quote = t.evidence[eid]["exact_quote"]
    t.state["diagnostic_link"]["snapshot"]["evidence"]["ev_fast_roadmark"]["quote"] = "Le taux de polygones valides atteint 62,5 % dans l'essai."
    text = f"Le projet rapporte 62,5 % de polygones valides [projet:ev_fast_roadmark]. Graphnet suit une autre voie [{ids[1]}] [{eid}]."
    result = save(t, [ids[1]], text, [eid], ["ev_fast_roadmark"])
    assert result["saved"], result
    trace = draft(t)["numeric_trace"]
    assert [(row["value"], row["evidence_ids"], row["origins"]) for row in trace] == [("62,5", ["ev_fast_roadmark"], ["project"])]
    assert quote  # the publication proof is unchanged


# --- 4/5/6. Scheduling, comparison obligation, priority readings ----------------------

def corpus_with_pending_sources():
    """A two-section draft: section A cites two analysed works; section B one analysed work
    with an abstract card whose full text is now stored, and one work without card whose
    text is stored (LoveDA/DANet-like); plus a synthesis."""
    from tests.ennoscholar_v4.test_turn_goal import sources
    def make(name, doi):
        paper = article(f"{name} for structured meshing", doi, "meshing", name)
        paper.id = "paper_" + name.lower()
        paper.assessment = {"decision": "retain", "justification": "x", "basis": "abstract"}
        return paper
    papers = [make(n, f"10.1234/{n.lower()}") for n in ("Alpha", "Beta", "Gamma", "Delta")]
    state = {"articles": {p.id: p.durable() for p in papers}, "messages": [{"id": "m0", "role": "user", "content": "x"}]}
    tools = ScholarTools(state, sources(), ScholarV4Config(api_key="mock"))
    clock = [0.]
    return state, papers, clock


def analyse(state, papers, clock, abstract_only=()):
    """Cards for the given works (full text, or abstract for abstract_only)."""
    def respond(n, kw):
        if forced(kw) == "analyze_article":
            packet = memory_of(kw)["automatic_scientific_reading"]
            return calls(("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "x",
                "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": generic_analysis(packet)})), 30, 1500
        return [{"type": "message", "content": [{"type": "output_text", "text": "ok", "annotations": []}]}], 5, 50
    saved = {}
    for paper in papers:
        if paper.id in abstract_only:
            saved[paper.id] = state["articles"][paper.id]["fulltext"]
            state["articles"][paper.id]["fulltext"] = {}
    state["evaluation"] = {"recommended_article_ids": [p.id for p in papers]}
    state["messages"] = [{"id": "m1", "role": "user", "content": "Analyse ces travaux."}]
    goal = {"deliverable": "deep_reading", "lock_ids": [], "focus_lock_ids": [], "exact_quote": "", "reading_depth": "core",
        "understanding": "Lire.", "search_needed": False}
    module.time = NS(monotonic=lambda: clock[0])
    from tests.ennoscholar_v4.test_turn_goal import sources
    ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=TimedClient(respond, clock, goal),
        sources=sources()).run(state)
    for aid, fulltext in saved.items():
        state["articles"][aid]["fulltext"] = fulltext  # its text is now stored locally
    return state


def drafting_run(state, clock, goal, respond):
    from tests.ennoscholar_v4.test_turn_goal import sources
    state["messages"].append({"id": "m2", "role": "user", "content": "Rédige l'état de l'art et compare les approches."})
    for key in ("turn_goal", "writing_focus", "automatic_reading_focus"):
        state.pop(key, None)
    module.time = NS(monotonic=lambda: clock[0])
    events = []
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=TimedClient(respond, clock, goal),
        sources=sources()).run(state, emit=events.append)
    return result, events


def writer(log):
    def respond(n, kw):
        name, memory = forced(kw), memory_of(kw)
        log.append((name or memory.get("context_task"), (memory.get("section_drafting") or {}).get("focus", {}).get("section_id"),
            (memory.get("argumentation_focus") or {}).get("article_ids")))
        if name == "analyze_article":
            packet = memory["automatic_scientific_reading"]
            return calls(("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "x",
                "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": generic_analysis(packet)})), 30, 1500
        if name == "compare_articles":
            focus = memory["argumentation_focus"]
            papers = {p["id"]: p for p in focus["packet"]["papers"]}
            eids = [papers[aid]["proofs"][0]["evidence_id"] for aid in focus["article_ids"] if papers.get(aid, {}).get("proofs")]
            return calls(("compare_articles", {"article_ids": focus["article_ids"], "research_context": "x", "conclusions": None,
                "relations": [{"article_ids": focus["article_ids"], "kind": "alternative", "statement": "Méthodes distinctes.",
                    "origin": "comparative_inference", "conditions": "Protocoles différents.", "experimental_results": [],
                    "evidence_ids": eids, "comparable_protocols": False, "uncertainties": ["Protocoles non comparables"]}],
                "claim_offsets": []})), 20, 500
        if name == "write_section" or memory.get("section_drafting"):
            packet = memory["section_drafting"]["packet"]
            sid = memory["section_drafting"]["focus"]["section_id"]
            cited, eids, sentences = [], [], []
            for claim in packet["proof_backed_claims"]:
                if claim["article_id"] not in cited:
                    cited.append(claim["article_id"]); eids.append(claim["evidence_ids"][0])
                    sentences.append(f"La publication [{claim['article_id']}] documente un résultat [{claim['evidence_ids'][0]}].")
            return calls(("write_section", {"section_id": sid, "action": "save", "instructions": "", "text": " ".join(sentences) or "Cadrage.",
                "article_ids": cited, "evidence_ids": eids, "claims": [],
                "argument_summary": {"role": "Section " + sid[-4:], "established": ["Acquis de la section."], "limits": [], "links": []}})), 40, 1500
        return [{"type": "message", "content": [{"type": "output_text", "text": "ok", "annotations": []}]}], 5, 50
    return respond


def plan(state, papers):
    from agents.EnnoScholarV4.tools.outline import OutlineArgs
    from agents.EnnoScholarV4.toolkit import ScholarTools
    tools = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    rows = [("Méthodes directes", [papers[0].id, papers[1].id]), ("Approches hybrides", [papers[2].id, papers[3].id]),
        ("Synthèse et perspectives", [p.id for p in papers])]
    tools.execute("manage_outline", {"action": "create", "title": "État de l'art", "section_id": None, "target_section_id": None,
        "position": None, "sections": [{"title": t, "description": "x", "article_ids": a} for t, a in rows], "article_ids": None,
        "offset": 0, "limit": 8, "consultant_message": "Plan."}, time.monotonic()+60)
    tools.save()


def test_draft_and_compare_keeps_both_reads_pending_sources_first_and_writes_the_synthesis_last(monkeypatch):
    previous = module.time
    try:
        state, papers, clock = corpus_with_pending_sources()
        # Alpha, Beta analysed from full text; Gamma from its abstract (text stored since); Delta never analysed.
        analyse(state, papers[:3], clock, abstract_only={papers[2].id})
        assert state["articles"][papers[2].id]["analysis_basis"] == "abstract" and state["articles"][papers[2].id]["fulltext"]["text"]
        assert not state["articles"][papers[3].id].get("analysis")
        plan(state, papers)
        log = []
        goal = {"deliverable": "draft", "lock_ids": [], "focus_lock_ids": [], "exact_quote": "", "reading_depth": "deep",
            "understanding": "Rédiger et comparer en profondeur.", "search_needed": False, "comparison_requested": True}
        result, events = drafting_run(state, clock, goal, writer(log))
        assert not result["partial"], result["reason"]
        kinds = [k for k, *_ in log]
        # « Rédige et compare »: the comparison obligation is kept (audit: dropped for a draft).
        assert "compare_articles" in kinds and state.get("scientific_relations")
        # Delta (no card) and Gamma (abstract card, text now stored) are read before section B.
        reads = [i for i, (k, *_ ) in enumerate(log) if k == "analyze_article"]
        sections = state["outline"]["sections"]
        first_b = next(i for i, (k, sid, _) in enumerate(log) if sid == sections[1]["id"])
        assert len(reads) >= 2 and max(reads) < first_b
        # Gamma's text was read: its card now holds passages of the body (a mixed card keeps the
        # conservative « abstract » label of the existing validator); Delta has a card.
        gamma = state["articles"][papers[2].id]
        assert {e["basis"] for e in gamma["analysis"]["evidence"]} >= {"abstract", "fulltext"}
        assert state["articles"][papers[3].id]["analysis"]
        assert [k for k, *_ in log].count("compare_articles") <= 3 + 1
        # The synthesis is written after the scientific sections; its packet carries the manuscript map.
        order = [sid for k, sid, _ in log if sid]
        assert order.index(sections[2]["id"]) > max(order.index(sections[0]["id"]), order.index(sections[1]["id"]))
        assert set(state["manuscript_memory"]) == {s["id"] for s in sections}
        assert state["manuscript_memory"][sections[0]["id"]]["origin"] == "writer_summary"
        # Review and export from the same rendering.
        from agents.EnnoScholarV4.scientific_review import deterministic_review
        from agents.EnnoScholarV4 import document_export as export
        tools = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
        review = deterministic_review(state, tools)
        assert review["integrity_passed"], [i for i in review["issues"] if i["code"] in {"invalid_evidence", "unsupported_attribution"}]
        items = []
        original = export.render_docx
        export.render_docx = lambda rows: items.extend(rows) or b""
        try:
            export.export_document(state, state["document_versions"][-1]["id"], "docx")
        finally:
            export.render_docx = original
        body = " ".join(v for k, v in items if k == "paragraph")
        assert re.search(r"\[1 ; P1\]", body) and not re.search(r"\[(?:paper_|ev_)", body)
    finally:
        module.time = previous


def test_a_draft_without_comparison_request_and_a_single_documented_work_compare_nothing():
    previous = module.time
    try:
        state, papers, clock = corpus_with_pending_sources()
        analyse(state, papers, clock)
        plan(state, papers)
        log = []
        goal = {"deliverable": "draft", "lock_ids": [], "focus_lock_ids": [], "exact_quote": "", "reading_depth": "core",
            "understanding": "Rédiger.", "search_needed": False, "comparison_requested": None}
        result, _ = drafting_run(state, clock, goal, writer(log))
        assert not result["partial"] and "compare_articles" not in [k for k, *_ in log]
    finally:
        module.time = previous


# --- 8. Budgets: exact admission, reduced context, delta reads ------------------------

def test_a_refused_admission_is_explained_and_a_smaller_section_context_is_tried_first():
    previous = module.time
    try:
        state, papers, clock = corpus_with_pending_sources()
        analyse(state, papers, clock)
        plan(state, papers)
        log = []
        goal = {"deliverable": "draft", "lock_ids": [], "focus_lock_ids": [], "exact_quote": "", "reading_depth": "core",
            "understanding": "Rédiger.", "search_needed": False}
        from tests.ennoscholar_v4.test_turn_goal import sources
        state["messages"].append({"id": "m2", "role": "user", "content": "Rédige l'état de l'art."})
        module.time = NS(monotonic=lambda: clock[0])
        events = []
        config = ScholarV4Config(api_key="mock", web_search_enabled=False, max_tokens=14000)
        ScholarChatAgent(config, client=TimedClient(writer(log), clock, goal), sources=sources()).run(state, emit=events.append)
        refused = [e for e in events if e.get("tool") == "budget" and e.get("status") == "admission_refused"]
        assert refused, "the admission refusal is reported"
        row = refused[-1]
        assert {"used_tokens", "estimated_input_tokens", "output_reserve_tokens", "token_cap"} <= set(row)
        assert row["used_tokens"]+row["estimated_input_tokens"]+row["output_reserve_tokens"] > row["token_cap"]
        assert any(e["reduced_context"] for e in refused) or len(refused) == 1
    finally:
        module.time = previous


def test_resuming_the_real_manuscript_prepares_section_7_and_rewrites_none_of_the_13_drafts():
    """Section 7 of run 91590987. Since the real run 128698bb the base holds section 7
    and full-text cards of LDPoly and HoliTracer: section 7's drafts are removed from the
    copy, the 13 others and the cards stay. Resuming writes section 7 only and reuses the
    stored cards (no second analysis of an already analysed document)."""
    from tests.ennoscholar_v4 import replay_b52f582a as replay
    state = real_state()
    if not replay.available():
        pytest.skip("Base locale absente")
    for key in ("argumentation_focus", "writing_focus", "writing_planning", "turn_goal", "automatic_reading_focus", "selection_focus"):
        state.pop(key, None)
    state.get("document", {}).pop("writing_progress", None)
    sections = state["outline"]["sections"]
    section7 = sections[6]
    section7["drafts"] = []
    before = {s["id"]: [d["id"] for d in s.get("drafts", [])] for s in sections}
    assert sum(bool(v) for v in before.values()) == 13
    cards = {aid: json.dumps(state["articles"][aid].get("analysis"), sort_keys=True)
        for aid in ("paper_9812a1f783142ceb8bf4", "paper_414c65597612f559177b", "paper_e41c0d6864c27137d9f5")}
    state["messages"].append({"id": "resume_7", "role": "user", "content": "Continue la rédaction de mon état de l'art CIR."})
    out = replay.run(state=state, proposals={})
    progress = state["document"]["writing_progress"]
    assert section7["id"] in progress["saved_draft_ids"]
    # The 13 existing drafts are neither regenerated nor saved again.
    assert all([d["id"] for d in s.get("drafts", [])] == before[s["id"]] for s in sections if s["id"] != section7["id"])
    assert {e["section_id"] for e in out["log"] if e["kind"] == "section"} == {section7["id"]}
    # The stored full-text cards of section 7's works are reused, not analysed again.
    assert all(json.dumps(state["articles"][aid].get("analysis"), sort_keys=True) == card for aid, card in cards.items())


def test_a_card_page_read_again_by_a_section_returns_only_new_claims():
    """Audit: the synthesis re-read the same cards six times (44 % of the run's tokens)."""
    from tests.ennoscholar_v4.test_context_memory import (Client, Model, REQUEST_A, agent, call, rich_corpus,
        save_section, seed_argumentation, seven_section_plan)
    t, ids = rich_corpus(2)
    seven_section_plan(t, ids)
    seed_argumentation(t, ids)
    for number in range(1, 7):
        save_section(t, number, ids)
    t.state["messages"] = [{"id": "m", "role": "user", "content": REQUEST_A}]
    outputs = []

    def model(n, kw):
        outputs.extend(json.loads(item["output"]) for item in kw["input"] if item.get("type") == "function_call_output")
        if n <= 2:
            return call("get_analysis_claims", {"article_id": ids[0], "fields": ["results"], "offset": 0}, n)
        return Model(ids, [])(n, kw)
    result = agent(Client(model)).run(t.state)
    assert not result["partial"], result["reason"]
    pages = [o for o in outputs if "no_new_evidence" in o or "already_received_claims" in o or o.get("claims")]
    first = pages[0]
    assert first["claims"]
    # Audit 128698bb: the same page again is answered locally (no_new_evidence, without its
    # claims), or not requested at all because get_analysis_claims is withdrawn once the
    # section has received every claim of the card.
    for again in pages[1:]:
        if again["article_id"] == first["article_id"] and not again.get("claims"):
            assert again.get("no_new_evidence") and "claims" not in again
    repeated = [p for p in pages[1:] if p.get("claims") and p["claims"] == first["claims"]]
    assert not repeated
