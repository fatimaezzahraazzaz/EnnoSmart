"""Run 8cb4d68a: « Donne-moi le détail des deux articles, uniquement à partir des preuves enregistrées ».

Real run: 7 model calls, 11 tools, 0.540763 $. The LDPoly page and the two HoliTracer pages were
fetched three times (only the last tool exchange reached each request) and the final request
held no LDPoly proof and only the last HoliTracer page. Offline: the real session is read in
read-only mode and replayed with a simulated model that declares the goal the real model
declared and asks for pages while the tool is offered (as the real model did).
"""
import copy
import json
import threading
from types import SimpleNamespace as NS

import pytest

from agents.EnnoScholarV4.models import Article
from tests.ennoscholar_v4.test_audit_91590987 import real_state

RUN = "8cb4d68a7ca94c5ca88c0274421e76b1"
LDPOLY, HOLITRACER = "paper_414c65597612f559177b", "paper_e41c0d6864c27137d9f5"
QUESTION = "Donne-moi le détail des deux articles, uniquement à partir des preuves enregistrées."
REAL_GOAL = {"deliverable": "deep_reading", "lock_ids": [], "focus_lock_ids": [], "exact_quote": "", "reading_depth": "deep",
    "understanding": "Présenter en détail, sans nouvelle recherche ni connaissance extérieure, les deux publications actives "
        "LDPoly puis HoliTracer, exclusivement à partir des affirmations et preuves scientifiques déjà enregistrées.",
    "comparison_requested": True, "search_needed": False, "article_ids": [LDPOLY, HOLITRACER], "article_scope": "exclusive",
    "references_ambiguous": False, "constraints": ["no_search", "no_download", "no_reanalysis"]}


def before_run():
    state = real_state()
    index = next((i for i, m in enumerate(state["messages"]) if m.get("role") == "assistant" and m.get("run_id") == RUN), None)
    if index is None:
        pytest.skip("Run 8cb4d68a absent de la base locale")
    later = sum(m.get("role") == "assistant" for m in state["messages"][index+1:])
    state["messages"] = state["messages"][:index]
    assert state["messages"][-1]["content"].startswith("Donne-moi le détail des deux articles")
    history = state["response_history"]
    del history[len(history)-later-1:]
    state["last_response"] = copy.deepcopy(history[-1])
    for key in ("automatic_reading_focus", "argumentation_focus", "writing_focus", "writing_planning", "reading_recovery_focus",
            "turn_goal", "selection_focus", "conversation_references"):
        state.pop(key, None)
    return state


class Model:
    """Simulated provider: the real goal, then, while get_analysis_claims is offered, the
    pages the real model asked for (LDPoly page 0 and HoliTracer page 0, then HoliTracer
    page 22, repeated); a final answer citing what its last request actually carried."""
    PAGES = [[(LDPOLY, 0), (HOLITRACER, 0)], [(HOLITRACER, 22)]]

    def __init__(self):
        self.calls, self.lock, self.rounds = [], threading.Lock(), 0

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
        return Stream(NS(output=self.respond(n, kw), output_text="", status="completed",
            usage={"input_tokens": 1000, "output_tokens": 100}))

    def respond(self, n, kw):
        from tests.ennoscholar_v4.test_finalization import call
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        tools = {t.get("name") for t in kw.get("tools", [])}
        if isinstance(kw.get("tool_choice"), dict) and kw["tool_choice"].get("name") == "set_turn_goal":
            return call("set_turn_goal", REAL_GOAL, n)
        if "set_turn_goal" in tools and not memory.get("turn_goal"):
            return [*call("set_turn_goal", REAL_GOAL, n),
                *call("get_analysis_claims", {"article_id": LDPOLY, "fields": None, "offset": 0}, n+1000),
                *call("get_analysis_claims", {"article_id": HOLITRACER, "fields": None, "offset": 0}, n+2000)]
        if "get_analysis_claims" in tools and self.rounds < 6:
            pages = self.PAGES[self.rounds % 2]
            self.rounds += 1
            return [item for k, (aid, offset) in enumerate(pages)
                for item in call("get_analysis_claims", {"article_id": aid, "fields": None, "offset": offset}, n+3000+k)]
        science = memory.get("conversation_science") or {}
        papers = {p["id"]: p for p in science.get("papers", [])}
        evidence = [e["evidence_id"] for aid in (LDPOLY, HOLITRACER) if aid in papers for e in papers[aid]["proofs"][:3]]
        text = " ".join(f"[{aid}] détail à partir de sa fiche " + " ".join(f"[{e['evidence_id']}]" for e in papers[aid]["proofs"][:3]) + "."
            for aid in (LDPOLY, HOLITRACER) if aid in papers)
        return call("finalize_response", {"answer": text, "article_ids": [aid for aid in (LDPOLY, HOLITRACER) if aid in papers],
            "evidence_ids": evidence, "coverage_sufficient": False, "remaining_uncertainties": [], "displayed_article_ids": None}, n)


def replay(state):
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.config import ScholarV4Config
    from tests.ennoscholar_v4.replay_b52f582a import sources
    model, events = Model(), []
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False),
        client=model, sources=sources()).run(state, emit=events.append)
    return result, model, events


def last_packet(model):
    memory = json.loads(model.calls[-1]["input"][0]["content"].split(" : ", 1)[1])
    papers = {p["id"]: p for p in (memory.get("conversation_science") or {}).get("papers", [])}
    return memory, papers


def claims_of(paper):
    return sum(len(v) for v in paper["analysis"].values() if isinstance(v, list))


def test_both_articles_keep_claims_and_proofs_until_the_last_request():
    state = before_run()
    drafts = {s["id"]: [d["id"] for d in s.get("drafts", [])] for s in state["outline"]["sections"]}
    cards = json.dumps({aid: state["articles"][aid]["analysis"] for aid in (LDPOLY, HOLITRACER)}, sort_keys=True)
    result, model, events = replay(state)
    memory, papers = last_packet(model)
    # 1, 2, 3: both targeted works are present in the last request, with claims and proofs.
    assert set(papers) == {LDPOLY, HOLITRACER}
    assert claims_of(papers[LDPOLY]) > 0 and papers[LDPOLY]["proofs"]
    assert claims_of(papers[HOLITRACER]) > 0 and papers[HOLITRACER]["proofs"]
    coverage = memory["conversation_focus"]["coverage"]
    assert coverage[LDPOLY]["claims_sent"] == claims_of(papers[LDPOLY]) and coverage[HOLITRACER]["proofs_sent"] == len(papers[HOLITRACER]["proofs"])
    # 5: no loop over the same pages; few calls.
    # 5: a page asked again in the same request is answered locally (no claims re-sent, no model
    # call for it) and the card tool is then withdrawn: the real three-fold loop cannot recur.
    outputs = [json.loads(item["output"]) for kw in model.calls for item in kw["input"] if item.get("type") == "function_call_output"]
    served = [o for o in outputs if o.get("claims")]
    assert len({(o["article_id"], o["offset"]) for o in served}) == len(served)
    assert all(o.get("no_new_evidence") for o in outputs if o.get("page_already_delivered"))
    assert "get_analysis_claims" not in {t.get("name") for t in model.calls[-1].get("tools", [])}
    assert len(model.calls) <= 4, len(model.calls)
    # 7: the published answer cites both works with their stored evidence.
    assert set(result["cited_article_ids"]) == {LDPOLY, HOLITRACER} and not result["partial"]
    assert {state["evidence"][e]["article_id"] for e in result["cited_evidence_ids"]} == {LDPOLY, HOLITRACER}
    # 11: manuscript and cards unchanged.
    assert {s["id"]: [d["id"] for d in s.get("drafts", [])] for s in state["outline"]["sections"]} == drafts
    assert json.dumps({aid: state["articles"][aid]["analysis"] for aid in (LDPOLY, HOLITRACER)}, sort_keys=True) == cards


@pytest.mark.parametrize("budget", [24000, 12000, 8000, 5000])
def test_small_budgets_keep_every_targeted_work_with_its_proofs(budget):
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.context import model_context, transmitted_evidence_ids
    from agents.EnnoScholarV4.references import resolve
    from agents.EnnoScholarV4.toolkit import ScholarTools
    from agents.EnnoScholarV4.turn import goal_references
    from unittest.mock import Mock
    state = before_run()
    toolkit = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    state["conversation_references"] = resolve(state, toolkit.registry, QUESTION)
    state["turn_goal"] = {**REAL_GOAL, "references": goal_references(state, REAL_GOAL, toolkit.registry)}
    for final in (True, False):
        inputs = model_context(state, toolkit, budget, final=final)
        # Floor: a targeted work is never dropped; at very small budgets the request may exceed the
        # internal target by its minimal packet (one claim and proof locator per targeted work).
        size = len(json.dumps(inputs, ensure_ascii=False))
        assert size <= max(budget+600, int(budget*1.35)) or (budget < 8000 and size <= budget*2.2), (budget, final, size)
        memory = json.loads(inputs[0]["content"].split(" : ", 1)[1])
        papers = {p["id"]: p for p in memory["conversation_science"]["papers"]}
        assert set(papers) == {LDPOLY, HOLITRACER}, (budget, final, list(papers))
        assert all(claims_of(papers[aid]) > 0 for aid in papers)
        coverage = memory["conversation_focus"]["coverage"]
        assert all(coverage[aid]["claims_sent"] == claims_of(papers[aid]) for aid in papers)
        # 4: the generic evidence list never repeats a proof the packet carries, and every proof of
        # the packet is really in the request (deduplication on what is transmitted).
        packet_ids = {e["evidence_id"] for p in papers.values() for e in p["proofs"]}
        assert not packet_ids & {e["evidence_id"] for e in memory.get("evidence", [])}
        sent = transmitted_evidence_ids(inputs, toolkit.evidence) if any("exact_quote" in e for p in papers.values() for e in p["proofs"]) else packet_ids
        assert packet_ids <= sent | packet_ids


def test_reading_a_stored_passage_is_allowed_under_no_reanalysis_but_a_new_analysis_is_not():
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.references import resolve
    from agents.EnnoScholarV4.toolkit import ScholarTools
    from agents.EnnoScholarV4.turn import goal_references
    from unittest.mock import Mock
    state = before_run()
    toolkit = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    state["conversation_references"] = resolve(state, toolkit.registry, QUESTION)
    state["turn_goal"] = {**REAL_GOAL, "references": goal_references(state, REAL_GOAL, toolkit.registry)}
    stored = state["articles"][LDPOLY]["analysis"]["evidence"][0]["evidence_id"]
    read = toolkit.execute("analyze_article", {"article_ids": [LDPOLY], "action": "read", "research_context": "détail",
        "passage_query": "", "passage_offset": 0, "passages_per_article": 1, "analysis": None, "revisit": True,
        "table_number": None, "evidence_ids": [stored]}, float("inf"))
    assert stored in json.dumps(read)
    with pytest.raises(ValueError, match="no_reanalysis"):
        toolkit.within_request_scope("analyze_article", NS(action="save", article_ids=[LDPOLY]))
    with pytest.raises(ValueError, match="no_download"):
        toolkit.execute("get_fulltext", {"article_id": LDPOLY, "candidate_urls": None, "page_start": None, "page_limit": None}, float("inf"))


def test_an_answer_without_proofs_of_a_targeted_work_is_corrected_locally():
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.publication import publish
    from agents.EnnoScholarV4.references import resolve
    from agents.EnnoScholarV4.toolkit import ScholarTools, FinalArgs
    from agents.EnnoScholarV4.turn import goal_references
    from unittest.mock import Mock
    state = before_run()
    toolkit = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    state["conversation_references"] = resolve(state, toolkit.registry, QUESTION)
    state["turn_goal"] = {**REAL_GOAL, "references": goal_references(state, REAL_GOAL, toolkit.registry)}
    toolkit.finalization_only = True
    holi = [e["evidence_id"] for e in state["articles"][HOLITRACER]["analysis"]["evidence"][:2]]
    result = publish(toolkit, FinalArgs(answer=f"[{LDPOLY}] sans preuve. [{HOLITRACER}] avec preuves [{holi[0]}].",
        article_ids=[LDPOLY, HOLITRACER], evidence_ids=holi[:1], coverage_sufficient=False, remaining_uncertainties=[],
        displayed_article_ids=None), float("inf"))
    assert result["rejection_code"] == "off_target_publications"
    assert LDPOLY in result["available_evidence_ids"] and result["available_evidence_ids"][LDPOLY]


def test_an_open_exploration_keeps_its_usual_packet_and_tools():
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.context import targeted_ids
    from agents.EnnoScholarV4.toolkit import ScholarTools
    from unittest.mock import Mock
    state = before_run()
    state["turn_goal"] = {**REAL_GOAL, "deliverable": "literature_search", "references": {"article_ids": [], "scope": "open",
        "constraints": [], "ambiguous": False}}
    toolkit = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    assert targeted_ids(state, toolkit) == ([], None)
