"""Run ce08ad31: « donne moi le detaille des 2 articles » after a LDPoly/HoliTracer comparison.

Real run: 17 model calls, 37 tools, 1.303572 $, fifteen readings of four unrelated works,
then an answer on two other publications taken from an older displayed list.
Offline: the real session is read from the local base in read-only mode and replayed with a
simulated model that declares the goal the real model declared (no provider call).
"""
import copy
import json
import sqlite3
import threading
from types import SimpleNamespace as NS

import pytest

from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.references import current_order, resolve
from tests.ennoscholar_v4.test_audit_91590987 import DB, real_state

RUN = "ce08ad31090c4ad3abba2d056e2f6441"
PREVIOUS = "6c6af07b2b0a40ee9373162a76148248"          # the LDPoly/HoliTracer comparison
LDPOLY, HOLITRACER = "paper_9812a1f783142ceb8bf4", "paper_e41c0d6864c27137d9f5"
OLD_PAIR = ["paper_ffe22c2aea378dd3db0b", "paper_764b8e627c9c5be06e33"]   # displayed since run 85a926db
OFF_TARGET = {"paper_1e25107123a487db6d02", "paper_3b613caf83bde612b2b0", "paper_4847faeb24cd71d772fd", "paper_f519d52467352d537ef1"}
QUESTION = "donne moi le detaille des 2 articles"
# The goal the real model declared in this run (stored receipt), without the new fields.
REAL_GOAL = {"deliverable": "deep_reading", "lock_ids": ["2"], "focus_lock_ids": [], "exact_quote": "", "reading_depth": "deep",
    "understanding": "Présenter en détail les deux articles actuellement centraux et analysés en texte intégral dans la section "
        "active sur la vectorisation directe : LDPoly et HoliTracer, en distinguant objectifs, méthodes, sorties, protocoles, "
        "résultats, limites et pertinence pour le projet."}


def before_run():
    """The session as run ce08ad31 found it (its answer and receipt removed)."""
    state = real_state()
    index = next((i for i, m in enumerate(state["messages"]) if m.get("role") == "assistant" and m.get("run_id") == RUN), None)
    if index is None:
        pytest.skip("Run ce08ad31 absent de la base locale")
    # Later runs of the conversation (2ac3f3b7, 8cb4d68a…) are removed with their answers.
    later = sum(m.get("role") == "assistant" for m in state["messages"][index+1:])
    state["messages"] = state["messages"][:index]
    assert state["messages"][-1]["content"] == QUESTION
    history = state["response_history"]
    del history[len(history)-later-1:]
    state["last_response"] = copy.deepcopy(history[-1])
    for key in ("automatic_reading_focus", "argumentation_focus", "writing_focus", "writing_planning",
            "reading_recovery_focus", "turn_goal", "selection_focus", "conversation_references"):
        state.pop(key, None)
    assert state["displayed_article_ids"] == OLD_PAIR
    assert state["last_response"]["cited_article_ids"] == [LDPOLY, HOLITRACER]
    return state


def registry(state):
    return {aid: Article.model_validate(row) for aid, row in state["articles"].items()}


def ask(state, text, message_id="next"):
    state = copy.deepcopy(state)
    state["messages"].append({"id": message_id, "role": "user", "content": text})
    return state


# --- 1, 5, 6: resolution ----------------------------------------------------------------

def test_the_two_articles_are_ldpoly_and_holitracer_not_the_older_displayed_list():
    state = before_run()
    refs = resolve(state, registry(state), QUESTION)
    assert refs["article_ids"] == [LDPOLY, HOLITRACER] and refs["source"] == "last_exchange" and not refs["ambiguous"]
    assert refs["resolved_from"] == PREVIOUS
    assert current_order(state) == ([LDPOLY, HOLITRACER], "last_exchange")


@pytest.mark.parametrize("text,expected", [
    ("Détaille le deuxième article.", [HOLITRACER]),
    ("Quelles sont leurs limites ?", [LDPOLY, HOLITRACER]),
    ("Approfondis le premier.", [LDPOLY]),
    ("Compare leurs résultats.", [LDPOLY, HOLITRACER])])
def test_ordinals_and_plural_references_follow_the_discussion_order(text, expected):
    state = before_run()
    assert resolve(state, registry(state), text)["article_ids"] == expected


def test_get_article_two_is_the_second_discussed_publication():
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.toolkit import ScholarTools
    from unittest.mock import Mock
    state = before_run()
    toolkit = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    result = toolkit.execute("get_article", {"identifiers": ["2"]}, float("inf"))
    assert state["focus"]["article_id"] == HOLITRACER and HOLITRACER in json.dumps(result)


# --- 7, 8, 9: new publication, change of subject, ambiguity --------------------------------

def test_an_explicitly_named_publication_replaces_the_targets():
    state = before_run()
    reg = registry(state)
    loveda = next(aid for aid, a in reg.items() if a.title.startswith("LoveDA"))
    refs = resolve(state, reg, "Et que dit LoveDA sur l'adaptation de domaine ?")
    assert refs["article_ids"] == [loveda] and refs["source"] == "current_message"


def test_a_change_of_subject_is_an_open_request():
    state = before_run()
    refs = resolve(state, registry(state), "Trouve les travaux les plus pertinents sur la segmentation de nuages de points LiDAR.")
    assert refs["article_ids"] == [] and refs["source"] == "none" and not refs["ambiguous"]


def test_ambiguous_references_ask_for_a_clarification():
    state = before_run()
    reg = registry(state)
    refs = resolve(state, reg, "Détaille les trois articles.")                  # the last exchange discussed two
    assert refs["ambiguous"] and refs["candidates"] == [LDPOLY, HOLITRACER] and refs["clarification"]
    empty = copy.deepcopy(state)
    empty["last_response"], empty["response_history"] = {}, []
    # No discussed exchange: the list presented is the referent; without any list or finalist, ask.
    assert resolve(empty, reg, QUESTION)["article_ids"] == OLD_PAIR
    empty["displayed_article_ids"], empty["evaluation"]["recommended_article_ids"] = [], []
    assert resolve(empty, reg, QUESTION)["ambiguous"]


def test_back_to_an_earlier_subject_uses_the_earlier_exchange():
    state = before_run()
    reg = registry(state)
    later = ask(state, "x")
    later["response_history"].append({"cited_article_ids": OLD_PAIR[:1], "primary_article_ids": OLD_PAIR[:1]})
    later["last_response"] = copy.deepcopy(later["response_history"][-1])
    refs = resolve(later, reg, "Revenons aux deux articles d'avant.")
    assert refs["article_ids"] == [LDPOLY, HOLITRACER] and refs["source"] == "earlier_exchange"


# --- 2, 10: survival through compression, finalisation and resumption ------------------------

def goal_state():
    from agents.EnnoScholarV4.turn import goal_references
    state = before_run()
    reg = registry(state)
    state["conversation_references"] = resolve(state, reg, QUESTION)
    state["turn_goal"] = {**REAL_GOAL, "references": goal_references(state, REAL_GOAL, reg)}
    return state


@pytest.mark.parametrize("budget,final", [(24000, True), (12000, True), (18000, False), (9000, False)])
def test_targets_and_goal_survive_every_compression(budget, final):
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.context import model_context
    from agents.EnnoScholarV4.toolkit import ScholarTools
    from unittest.mock import Mock
    state = goal_state()
    assert state["turn_goal"]["references"]["scope"] == "exclusive"
    inputs = model_context(state, ScholarTools(state, Mock(), ScholarV4Config(api_key="mock")), budget, final=final)
    memory = json.loads(inputs[0]["content"].split(" : ", 1)[1])
    focus = memory["conversation_focus"]
    assert [p["id"] for p in focus["active_publications"]] == [LDPOLY, HOLITRACER]
    assert focus["scope"] == "exclusive" and focus["references_source"] == "last_exchange"
    assert memory.get("turn_goal", {}).get("deliverable") == "deep_reading"
    # The older pair is never presented as the current order.
    assert [r["id"] for r in memory.get("displayed_article_order", [])][:2] != OLD_PAIR
    if final and memory.get("conversation_science", {}).get("papers"):
        assert {p["id"] for p in memory["conversation_science"]["papers"]} <= {LDPOLY, HOLITRACER}


def test_resumption_after_a_reload_keeps_the_same_targets():
    state = goal_state()
    reloaded = json.loads(json.dumps(state))
    assert resolve(reloaded, registry(reloaded), QUESTION)["article_ids"] == [LDPOLY, HOLITRACER]
    assert reloaded["turn_goal"]["references"]["article_ids"] == [LDPOLY, HOLITRACER]


# --- 3, 4, 12, 13: the replay -----------------------------------------------------------------

class Model:
    """Simulated provider following the real run: the goal it declared and its first card
    reads, analyses of the passages it is given, then an answer on the publications its
    context presents as current (conversation_focus, else the displayed order)."""
    def __init__(self):
        self.calls, self.lock = [], threading.Lock()

    def with_options(self, **kw):
        return self

    @property
    def responses(self):
        return self

    def create(self, **kw):
        from tests.ennoscholar_v4.test_finalization import Stream, call
        with self.lock:
            self.calls.append(kw)
            n = len(self.calls)
        output = self.respond(n, kw, call)
        return Stream(NS(output=output, output_text="", status="completed", usage={"input_tokens": 1000, "output_tokens": 100}))

    def respond(self, n, kw, call):
        choice = kw.get("tool_choice")
        name = choice.get("name") if isinstance(choice, dict) else None
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if name == "set_turn_goal":
            return call("set_turn_goal", REAL_GOAL, n)
        if name == "analyze_article":
            from tests.ennoscholar_v4.replay_2fd068ff import analysis
            packet = memory["automatic_scientific_reading"]
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "x",
                "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": analysis(packet)}, n)
        if name == "record_project_facts":
            return call("record_project_facts", {"facts": []}, n)
        tools = {t.get("name") for t in kw.get("tools", [])}
        if "set_turn_goal" in tools:
            # The real first round: the goal (deep reading) and the two card reads, together.
            return [*call("set_turn_goal", REAL_GOAL, n),
                *call("get_analysis_claims", {"article_id": LDPOLY, "fields": None, "offset": 0}, n+1000),
                *call("get_analysis_claims", {"article_id": HOLITRACER, "fields": None, "offset": 0}, n+2000)]
        focus = memory.get("conversation_focus") or {}
        ids = [p["id"] for p in focus.get("active_publications", [])] or [r["id"] for r in memory.get("displayed_article_order", [])][:2]
        # Each publication is presented from the proofs its request carries (checked at publication).
        papers = {p["id"]: p for p in (memory.get("conversation_science") or {}).get("papers", [])}
        proofs = {aid: [e["evidence_id"] for e in papers[aid]["proofs"][:2]] if aid in papers else [] for aid in ids}
        text = " ".join(f"[{aid}] est présenté en détail à partir de sa fiche " + " ".join(f"[{e}]" for e in proofs[aid]) + "."
            for aid in ids)
        return call("finalize_response", {"answer": text, "article_ids": ids, "evidence_ids": [e for aid in ids for e in proofs[aid]],
            "coverage_sufficient": False, "remaining_uncertainties": [], "displayed_article_ids": None}, n)


def replay(state):
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.config import ScholarV4Config
    from tests.ennoscholar_v4.replay_b52f582a import sources
    model, events = Model(), []
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False),
        client=model, sources=sources()).run(state, emit=events.append)
    return result, model, events


def test_replaying_run_ce08ad31_stays_on_ldpoly_and_holitracer():
    state = before_run()
    drafts = {s["id"]: [d["id"] for d in s.get("drafts", [])] for s in state["outline"]["sections"]}
    facts, evidence = copy.deepcopy(state.get("project_facts", [])), copy.deepcopy(state.get("evidence", {}))
    result, model, events = replay(state)
    readings = [t["target"] for t in state["task_results"].values() if t["kind"] == "reading"
        and any(a["execution"] == next(iter(a for a in t["attempts"]))["execution"] for a in t["attempts"])]
    new_readings = [e for e in events if e.get("tool") == "scientific_reading" and e.get("status") == "completed"]
    # 3. No analysis outside the targets.
    assert not {e.get("article_id") for e in new_readings} & OFF_TARGET
    assert {e.get("article_id") for e in new_readings} <= {LDPOLY, HOLITRACER, "paper_414c65597612f559177b"}
    # 4. The answer is about LDPoly and HoliTracer.
    assert set(result["cited_article_ids"]) == {LDPOLY, HOLITRACER} and not set(result["cited_article_ids"]) & set(OLD_PAIR)
    assert state["last_response"]["discussed_article_ids"][:2] == [LDPOLY, HOLITRACER]
    # 12. One goal declaration, no general round re-declaring it, no project facts.
    assert result["tool_counts"].get("set_turn_goal", 0) <= 1 and not result["tool_counts"].get("record_project_facts")
    assert sum(any(t.get("name") == "set_turn_goal" for t in kw.get("tools", [])) for kw in model.calls) == 1
    # 13. The 14 sections, facts and proofs are unchanged.
    assert {s["id"]: [d["id"] for d in s.get("drafts", [])] for s in state["outline"]["sections"]} == drafts
    assert state.get("project_facts", []) == facts
    assert all(state["evidence"][k] == v for k, v in evidence.items())
    # Measured on this replay (simulated model): calls and tools, against 17 and 37 in the real run.
    assert len(model.calls) <= 6 and result["tool_calls"] <= 10, (len(model.calls), result["tool_calls"])


def test_an_answer_on_the_older_pair_is_not_published():
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.toolkit import ScholarTools
    from unittest.mock import Mock
    state = goal_state()
    toolkit = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    toolkit.finalization_only = True
    args = lambda ids: {"answer": " ".join(f"[{aid}] détail." for aid in ids), "article_ids": ids, "evidence_ids": [],
        "coverage_sufficient": False, "remaining_uncertainties": [], "displayed_article_ids": None}
    refused = toolkit.execute("finalize_response", args(OLD_PAIR), float("inf"), settle=False) \
        if False else __import__("agents.EnnoScholarV4.publication", fromlist=["publish"]).publish(
            toolkit, __import__("agents.EnnoScholarV4.toolkit", fromlist=["FinalArgs"]).FinalArgs(**args(OLD_PAIR)), float("inf"))
    assert refused["rejection_code"] == "off_target_publications"
    assert set(refused["missing_article_ids"]) == {LDPOLY, HOLITRACER}


def test_tools_refuse_work_outside_an_exclusive_request_and_respect_explicit_constraints():
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.toolkit import ScholarTools
    from unittest.mock import Mock
    state = goal_state()
    toolkit = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    off = sorted(OFF_TARGET)[0]
    with pytest.raises(ValueError, match="Hors du périmètre"):
        toolkit.execute("get_analysis_claims", {"article_id": off, "fields": None, "offset": 0}, float("inf"))
    assert toolkit.execute("get_analysis_claims", {"article_id": HOLITRACER, "fields": None, "offset": 0}, float("inf"))["claims"]
    state["turn_goal"]["references"]["constraints"] = ["no_search"]
    with pytest.raises(ValueError, match="no_search"):
        toolkit.execute("search_scientific", {"query": "road vectorization", "sources": None, "limit_per_source": 5,
            "offset": 0, "family": "methods"}, float("inf"))


# --- 11: open exploration is unchanged --------------------------------------------------------

def test_an_open_search_is_not_bounded_by_earlier_publications():
    from agents.EnnoScholarV4.turn import goal_references
    state = before_run()
    reg = registry(state)
    text = "Trouve les travaux les plus pertinents sur la segmentation de nuages de points LiDAR."
    state = ask(state, text)
    state["conversation_references"] = resolve(state, reg, text)
    goal = {**REAL_GOAL, "deliverable": "literature_search", "search_needed": True}
    refs = goal_references(state, goal, reg)
    assert refs["scope"] == "open" and refs["article_ids"] == []
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.toolkit import ScholarTools
    from unittest.mock import Mock
    state["turn_goal"] = {**goal, "references": refs}
    toolkit = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    toolkit.within_request_scope("get_analysis_claims", NS(article_id=sorted(OFF_TARGET)[0]))   # no refusal


def test_when_a_target_needs_reading_only_the_targets_are_read():
    """Variant: HoliTracer without card. The deep reading the real model asked for reads
    HoliTracer (the target), never the four works read in the real run."""
    state = before_run()
    state["articles"][HOLITRACER].update(analysis={}, analysis_basis=None)
    state["scientific_reading"]["tasks"] = [t for t in state["scientific_reading"]["tasks"] if t["article_id"] != HOLITRACER]
    result, model, events = replay(state)
    read = [e.get("article_id") for e in events if e.get("tool") == "scientific_reading" and e.get("status") == "completed"]
    assert read and set(read) <= {LDPOLY, HOLITRACER}
    assert HOLITRACER in read and not set(read) & OFF_TARGET
    analysed = [kw for kw in model.calls if kw.get("tool_choice") == {"type": "function", "name": "analyze_article"}]
    assert analysed and all(json.loads(kw["input"][0]["content"].split(" : ", 1)[1])["automatic_scientific_reading"]["article_id"]
        in {LDPOLY, HOLITRACER} for kw in analysed)
    assert set(result["cited_article_ids"]) == {LDPOLY, HOLITRACER}
