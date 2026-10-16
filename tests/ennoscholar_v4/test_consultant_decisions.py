"""Consultant decisions expressed in the chat or the interface are saved and prevail."""
import time

import pytest
from pydantic import ValidationError

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.argumentation import active_articles
from agents.EnnoScholarV4.catalogue import library_groups
from agents.EnnoScholarV4.decisions import apply_decision
from agents.EnnoScholarV4.models import SelectionRequest
from agents.EnnoScholarV4.scientific_reading import reading_tasks, next_task
from agents.EnnoScholarV4.writing_progress import begin_writing
from tests.ennoscholar_v4.test_finalization import Client, call
from tests.ennoscholar_v4.test_multi_article_reading import corpus, read_windows


def decide(t, decisions, quote, order=None, message_id="latest"):
    return t.execute("record_consultant_decisions", {"decisions": decisions, "order": order,
        "source_message_id": message_id, "exact_quote": quote}, time.monotonic()+30)


def chat_state(text="Garde le premier et le troisième, mets le deuxième en attente, retire le quatrième."):
    state, toolkit = corpus(4)
    state["messages"] = [{"id": "msg_user_1", "role": "user", "content": text}]
    state["evaluation"] = {"recommended_article_ids": list(toolkit.registry)}
    return state, toolkit, list(toolkit.registry)


def test_chat_decisions_are_attributed_to_the_consultant_words_and_saved():
    state, t, ids = chat_state()
    result = decide(t, [{"article_id": ids[0], "decision": "accept"}, {"article_id": ids[2], "decision": "accept"},
        {"article_id": ids[1], "decision": "hold"}, {"article_id": ids[3], "decision": "reject"}],
        "Garde le premier et le troisième, mets le deuxième en attente", order=[ids[2], ids[0]], message_id="msg_user_1")
    assert result["saved"] and result["recommended_article_ids"] == [ids[2], ids[0]]
    assert state["held_article_ids"] == [ids[1]]
    assert state["articles"][ids[3]]["selection_override"] is False
    assert state["articles"][ids[0]]["selected"] and state["articles"][ids[0]]["selection_override"] is True
    assert all(row["source_message_id"] == "msg_user_1" for row in state["consultant_decisions"])
    with pytest.raises(ValueError, match="message exact"):
        decide(t, [{"article_id": ids[1], "decision": "accept"}], "Le consultant a tout validé")


def test_held_and_rejected_works_are_not_read_compared_or_listed_as_selected():
    state, t, ids = chat_state()
    decide(t, [{"article_id": ids[1], "decision": "hold"}, {"article_id": ids[3], "decision": "reject"}],
        "mets le deuxième en attente, retire le quatrième")
    order = read_windows(state, t, 20)
    assert {aid for aid, _ in order} == {ids[0], ids[2]}
    assert set(active_articles(state, t.registry)) == {ids[0], ids[2]}
    groups = library_groups(state)
    assert groups["on_hold"] == [ids[1]] and ids[3] in groups["rejected"]
    # Returning to the work later resumes it without losing anything.
    decide(t, [{"article_id": ids[1], "decision": "accept"}], "Garde le premier")
    reading_tasks(state, t.registry)
    assert next_task(state, t.registry)["article_id"] == ids[1]


def test_a_revised_model_ranking_cannot_override_the_consultant():
    state, t, ids = chat_state()
    decide(t, [{"article_id": ids[0], "decision": "accept"}, {"article_id": ids[1], "decision": "hold"}],
        "Garde le premier", order=[ids[0]])
    for aid in ids:
        t.store.exposed.append(aid)
    t.execute("record_candidate_decisions", {"decisions": [{"article_id": aid, "decision": "retain", "justification": "x"}
        for aid in ids[1:3]], "finalist_ids": [ids[2], ids[1]], "covered_families": [], "remaining_axes": []}, time.monotonic()+30)
    assert state["evaluation"]["recommended_article_ids"] == [ids[0], ids[2]]


def test_interface_payload_keeps_the_old_contract_and_adds_hold():
    assert SelectionRequest(selected=True).decision is None
    assert SelectionRequest(decision="hold").selected is None
    with pytest.raises(ValidationError):
        SelectionRequest()
    data = {"articles": {"paper_x": {"selected": True, "selection_override": True, "verified": True}},
        "evaluation": {"recommended_article_ids": ["paper_x"]}}
    apply_decision(data, "paper_x", "hold", lambda a, s, o: data["articles"][a].update(selected=s, selection_override=o),
        verified=True, origin="consultant_interface")
    assert data["held_article_ids"] == ["paper_x"] and not data["evaluation"]["recommended_article_ids"]
    assert data["evaluation_history"] and data["consultant_decisions"][-1]["origin"] == "consultant_interface"


def test_short_go_ahead_starts_drafting_only_when_a_plan_exists():
    state = {"outline": {"sections": [{"id": "section_a", "title": "Méthodes", "article_ids": [], "drafts": []}],
        "plan_revision": 1, "version": 1}}
    assert begin_writing({}, "Maintenant rédige") is None
    progress = begin_writing(state, "Maintenant rédige")
    assert progress["mode"] == "full" and progress["target_ids"] == ["section_a"] and progress["approval_required"]
    assert begin_writing(state, "Ne rédige pas encore, propose un plan avant de rédiger") is None


def test_cest_bon_reuses_saved_analyses_without_search_or_reading():
    state, t, ids = chat_state("C'est bon, garde ces articles.")
    read_windows(state, t, 8)
    state["messages"] = [{"id": "msg_ok", "role": "user", "content": "C'est bon, garde ces articles."}]
    state["displayed_article_ids"] = ids[:2]
    calls = []

    def respond(n, kw):
        calls.append(kw["tool_choice"])
        if n == 1:
            return call("record_consultant_decisions", {"decisions": [{"article_id": aid, "decision": "accept"} for aid in ids[:2]],
                "order": None, "source_message_id": "msg_ok", "exact_quote": "garde ces articles"}, n)
        return call("finalize_response", {"answer": "Sélection conservée [" + ids[0] + "] [" + ids[1] + "]",
            "article_ids": ids[:2], "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": [],
            "analyses": None, "displayed_article_ids": None}, n)

    events = []
    ScholarChatAgent(t.config, client=Client(respond), sources=t.sources).run(state, emit=events.append)
    assert state["consultant_decisions"][0]["exact_quote"] == "garde ces articles"
    assert not t.sources.search.called
    assert not any(e["tool"] in {"search_scientific", "get_fulltext", "analyze_article"} for e in events)
    assert len(calls) <= 3
