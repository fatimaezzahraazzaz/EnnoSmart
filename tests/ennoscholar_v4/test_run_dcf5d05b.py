"""Run dcf5d05b: « Tu peux rédiger la première section de l'état de l'art … ? »

Real run: 30 model calls, ~2.86 $, eleven sections attempted in parallel, 0 saved. The first
section (Introduction, five sources in the plan) was refused for an upper/lower-case initial
(« La méthode … » declared, « la méthode … » written), and other sections for « LiDAR »
attributed to one article and for a heading number « 3.1 » read as a value. Offline: the real
session is read in read-only mode; the simulated model returns the real proposals.
"""
import copy
import json
import threading
from types import SimpleNamespace as NS

import pytest

from tests.ennoscholar_v4.test_audit_91590987 import DB

RUN = "dcf5d05b70a94adb9ddafe4fc6338982"
EXECUTION = "d130f63417ad4af79be567b45c795277"
SECTION_1 = "section_e4320e4747414953a4b2df22f485311d"
REQUEST = ("Tu peux rédiger la première section de l’état de l’art à partir des articles trouvés ? Je veux un texte bien "
    "détaillé, avec les méthodes, les résultats et les limites des travaux, en les comparant et en citant les sources.")


def session():
    import sqlite3
    if not DB.is_file():
        pytest.skip("Base locale absente")
    db = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    row = db.execute("select data from scholar_v4_sessions where id like 'ace44bf8%'").fetchone()
    db.close()
    if not row:
        pytest.skip("Session du run dcf5d05b absente")
    return json.loads(row[0])


def real_proposals(state):
    return [copy.deepcopy(a["arguments"]) for t in state["task_results"].values() if t["kind"] == "section"
        and t["target"] == SECTION_1 for a in t["attempts"] if a.get("execution") == EXECUTION and a.get("arguments")]


def real_goal(state):
    return next(json.loads(c["arguments"]) for t in state["task_results"].values() for a in t["attempts"]
        if a.get("execution") == EXECUTION for c in a["content"] if c.get("name") == "set_turn_goal")


def before_run():
    """The session as the run found it: its answer, receipts and writing progress removed; the
    cards and proofs saved since are kept (the « existing proofs »)."""
    state = session()
    index = next(i for i, m in enumerate(state["messages"]) if m.get("role") == "assistant" and m.get("run_id") == RUN)
    state["messages"] = state["messages"][:index]
    assert state["messages"][-1]["content"].startswith("Tu peux rédiger la première section")
    proposals, goal = real_proposals(state), real_goal(state)
    state["task_results"] = {k: t for k, t in state["task_results"].items()
        if not any(a.get("execution") == EXECUTION for a in t["attempts"])}
    state.get("document", {}).pop("writing_progress", None)
    # No section had a draft when the run started; the drafts saved by the later real runs
    # (first section, then nine sections) are left out of this in-memory copy.
    for section in state["outline"]["sections"]:
        section["drafts"] = []
    for key in ("turn_goal", "conversation_references", "current_turn_article_ids", "request_card_pages", "writing_focus",
            "automatic_reading_focus", "argumentation_focus", "selection_focus", "section_received_claims"):
        state.pop(key, None)
    return state, proposals, goal


# --- 1. « première section » targets exactly one section ---------------------------------

@pytest.mark.parametrize("text,index", [
    (REQUEST, 0), ("Rédige la deuxième section.", 1), ("Peux-tu rédiger la dernière section ?", -1),
    ("Rédige la section 3 de l'état de l'art.", 2), ("Rédige la 2e section, en détail.", 1)])
def test_a_named_section_is_the_only_target(text, index):
    from agents.EnnoScholarV4.writing_progress import begin_writing, requested_section
    state, _, _ = before_run()
    sections = state["outline"]["sections"]
    assert requested_section(state, text) == sections[index]["id"]
    progress = begin_writing(state, text)
    assert progress["mode"] == "section" and progress["target_ids"] == [sections[index]["id"]]


def test_a_full_draft_request_still_targets_every_section():
    from agents.EnnoScholarV4.writing_progress import begin_writing
    state, _, _ = before_run()
    progress = begin_writing(state, "Rédige tout l'état de l'art complet.")
    assert progress["mode"] == "full" and len(progress["target_ids"]) == len(state["outline"]["sections"]) == 11


# --- 3, 4, 6: the real refusals and the real false refusals -------------------------------

def tools(state):
    from unittest.mock import Mock
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.toolkit import ScholarTools
    return ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))


def test_each_real_proposal_of_the_first_section_is_saved_with_the_existing_proofs():
    state, proposals, _ = before_run()
    assert len(proposals) == 3
    for proposal in proposals:
        local = copy.deepcopy(state)
        result = tools(local).execute("write_section", copy.deepcopy(proposal), float("inf"))
        assert result["saved"], result
        draft = next(s for s in local["outline"]["sections"] if s["id"] == SECTION_1)["drafts"][-1]
        assert set(draft["article_ids"]) <= set(next(s for s in state["outline"]["sections"] if s["id"] == SECTION_1)["article_ids"])
        assert draft["evidence_ids"] and draft["numeric_trace"] is not None


def first_section_proposal(state, proposals, **changes):
    proposal = copy.deepcopy(proposals[-1])
    proposal.update(changes)
    return proposal


def test_a_claim_really_absent_from_the_text_is_still_refused():
    state, proposals, _ = before_run()
    proposal = first_section_proposal(state, proposals)
    proposal["claims"] = [*proposal["claims"], {"statement": "Cette phrase n'existe nulle part dans la section.", "kind": "scientific",
        "evidence_ids": proposal["evidence_ids"][:1], "project_evidence_ids": [], "origin": "source_statement"}]
    result = tools(copy.deepcopy(state)).execute("write_section", proposal, float("inf"))
    assert not result["saved"] and result["claims_not_in_text"]


def test_only_the_initial_letter_case_may_differ():
    from agents.EnnoScholarV4.toolkit import anchor_in_text
    text = "Le réseau est extrait ; la méthode multimodale détecte les bords routiers avec précision."
    assert anchor_in_text("La méthode multimodale détecte les bords routiers", text)
    assert not anchor_in_text("La Méthode multimodale détecte les bords routiers", text)
    assert not anchor_in_text("La méthode multimodale détecte les bords ROUTIERS", text)


def test_an_unsupported_number_or_name_is_still_refused_but_a_heading_number_is_not_a_value():
    from agents.EnnoScholarV4.provenance_check import numeric_trace
    from agents.EnnoScholarV4.attribution import check_attribution
    state, proposals, _ = before_run()
    toolkit = tools(state)
    _, missing = numeric_trace(toolkit, "### 3.1 Extraire et régulariser les limites\n\nTexte sans valeur.", [], [])
    assert not missing
    _, missing = numeric_trace(toolkit, "La méthode atteint 93,4 % de précision.", [], [])
    assert [row["value"] for row in missing] == ["93,4"]
    article_ids = proposals[-1]["article_ids"]
    # « LiDAR » appears in several titles of the library: a sensor, not one article's identity.
    other = next(aid for aid in article_ids if aid != "paper_90f19e1ed6a2027dcbeb")
    eid = next(e["evidence_id"] for e in state["articles"][other]["analysis"]["evidence"])
    assert not check_attribution(f"Une image LiDAR et une image aérienne sont fusionnées [{other}] [{eid}].", article_ids, toolkit)
    # A distinctive method name without any supporting passage is still refused.
    assert check_attribution(f"La méthode HoloRoadNet améliore la continuité [{other}] [{eid}].", article_ids, toolkit)


def test_a_comparison_between_publications_is_recorded_as_an_interpretation_and_project_facts_still_need_proof():
    from agents.EnnoScholarV4.provenance_check import type_claims
    state, proposals, _ = before_run()
    toolkit = tools(state)
    a, b = proposals[-1]["article_ids"][:2]
    ea = state["articles"][a]["analysis"]["evidence"][0]["evidence_id"]
    eb = state["articles"][b]["analysis"]["evidence"][0]["evidence_id"]
    text = f"La première détecte le réseau [{ea}], tandis que la seconde extrait les bords [{eb}]."
    args = NS(text=text, evidence_ids=[ea, eb], project_evidence_ids=[], article_ids=[a, b])
    claims = type_claims(toolkit, [{"statement": text, "kind": "comparison", "evidence_ids": [ea, eb],
        "project_evidence_ids": [], "origin": "source_statement"}], args)
    assert claims[0]["kind"] == "interpretation" and claims[0]["compared_publications"]
    # A comparison of a single publication with nothing else is still refused.
    with pytest.raises(ValueError, match="une comparaison cite"):
        type_claims(toolkit, [{"statement": text, "kind": "comparison", "evidence_ids": [ea], "project_evidence_ids": [],
            "origin": "source_statement"}], NS(text=text, evidence_ids=[ea], project_evidence_ids=[], article_ids=[a]))


# --- 5: a card update keeps the refused proposal and its diagnosis -------------------------

def test_a_card_update_keeps_the_refused_proposal_for_a_local_repair():
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.task_results import TaskResults
    state = {"task_results": {}}
    results = TaskResults(state, ScholarV4Config(api_key="mock"), dict(input_tokens=0, output_tokens=0,
        estimated_cost_usd=0., unreported_reserved_cost_usd=0.), [])
    old = results.task("section", "s1", "cards-v1")
    attempt = results.begin(old)
    call = {"type": "function_call", "name": "write_section", "call_id": "c1", "arguments": json.dumps({"action": "save", "text": "x"})}
    results.receive(old, attempt, NS(output=[call], usage={"input_tokens": 10, "output_tokens": 5}, status="completed"))
    attempt["arguments"] = {"action": "save", "text": "x"}
    results.reject(old, attempt, {"error": "Attribution non vérifiable", "paragraph_diagnosis": {"paragraphs": [{"index": 0}]}})
    new = results.task("section", "s1", "cards-v2")              # a reading updated a source's card
    assert results.last_refusal(new) is None
    new["inherited_from"] = old["id"]
    inherited = results.last_refusal(new)
    assert inherited is attempt and inherited["refusal"]["paragraph_diagnosis"]
    assert results.recovery_exchange(new)[-1]["call_id"] == "c1"


# --- 2 + replay: only the requested section, its five sources, saved -----------------------

class Model:
    """Simulated provider: the goal the real model declared, analyses of the passages it is
    given, an empty comparison, and for the first section the real proposal of the run."""
    def __init__(self, goal, proposal):
        self.goal, self.proposal, self.calls, self.lock, self.sections = goal, proposal, [], threading.Lock(), []

    def with_options(self, **kw):
        return self

    @property
    def responses(self):
        return self

    def create(self, **kw):
        from tests.ennoscholar_v4.test_finalization import Stream
        with self.lock:
            self.calls.append(kw)
            n = len(self.calls)
        return Stream(NS(output=self.respond(n, kw), output_text="", status="completed", usage={"input_tokens": 1000, "output_tokens": 200}))

    def respond(self, n, kw):
        from tests.ennoscholar_v4.test_finalization import call
        choice = kw.get("tool_choice")
        name = choice.get("name") if isinstance(choice, dict) else None
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        tools = {t.get("name") for t in kw.get("tools", [])}
        if name == "set_turn_goal" or ("set_turn_goal" in tools and not memory.get("turn_goal")):
            return call("set_turn_goal", self.goal, n)
        if name == "analyze_article":
            from tests.ennoscholar_v4.replay_2fd068ff import analysis
            packet = memory["automatic_scientific_reading"]
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "x",
                "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": analysis(packet)}, n)
        if name == "compare_articles":
            focus = memory["argumentation_focus"]
            return call("compare_articles", {"claim_offsets": None, "article_ids": focus["article_ids"], "research_context": "x",
                "conclusions": [], "relations": []}, n)
        section = ((memory.get("section_drafting") or {}).get("focus") or {}).get("section_id")
        if section:
            with self.lock:
                self.sections.append(section)
            return call("write_section", {**copy.deepcopy(self.proposal), "section_id": section, "action": "save"}, n)
        return call("finalize_response", {"answer": "Section rédigée.", "article_ids": [], "evidence_ids": [],
            "coverage_sufficient": False, "remaining_uncertainties": [], "displayed_article_ids": None}, n)


def test_replaying_the_run_writes_the_first_section_only_from_its_five_sources():
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.config import ScholarV4Config
    from tests.ennoscholar_v4.replay_b52f582a import sources
    state, proposals, goal = before_run()
    assert goal["deliverable"] == "draft" and len(goal["article_ids"]) == 10 and goal["article_scope"] == "exclusive"
    plan = json.dumps(state["outline"], sort_keys=True, ensure_ascii=False)
    cards = json.dumps({aid: a.get("analysis") for aid, a in state["articles"].items()}, sort_keys=True, ensure_ascii=False)
    section_sources = set(state["outline"]["sections"][0]["article_ids"])
    model, events = Model(goal, proposals[-1]), []
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False),
        client=model, sources=sources()).run(state, emit=events.append)
    progress = state["document"]["writing_progress"]
    # Exactly one section targeted and written; no other section attempted.
    assert progress["mode"] == "section" and progress["target_ids"] == [SECTION_1]
    assert set(model.sections) == {SECTION_1} and SECTION_1 in progress["saved_draft_ids"]
    # Readings, comparisons and bibliographic checks stay within the section's five sources.
    read = {e.get("article_id") for e in events if e.get("tool") == "scientific_reading" and e.get("article_id")}
    compared = {aid for e in events if e.get("tool") == "argumentation" for aid in e.get("article_ids") or []}
    assert read <= section_sources and compared <= section_sources
    checked = {aid for kw in model.calls for item in kw["input"] if item.get("type") == "function_call"
        and item.get("name") == "verify_bibliography" for aid in json.loads(item["arguments"]).get("article_ids", [])}
    assert checked <= section_sources
    assert not result["partial"], result["reason"]
    # The plan is unchanged except for the new draft of the first section; no other section has a draft.
    after = copy.deepcopy(state["outline"])
    before = json.loads(plan)
    for section in (*after["sections"], *before["sections"]):
        section.pop("drafts", None)
    assert [s["id"] for s in after["sections"]] == [s["id"] for s in before["sections"]]
    assert all({k: v for k, v in a.items()} == b for a, b in zip(after["sections"], before["sections"]))
    assert [bool(s.get("drafts")) for s in state["outline"]["sections"]] == [True] + [False]*10
    # Cards of the five sources are read-only here (they already exist): no card changed.
    assert json.dumps({aid: a.get("analysis") for aid, a in state["articles"].items()}, sort_keys=True, ensure_ascii=False) == cards
    assert len(model.calls) <= 8, len(model.calls)
