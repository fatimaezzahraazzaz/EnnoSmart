"""Duration of consultant instructions (run 8cb4d68a).

« Donne-moi le détail des deux articles, uniquement à partir des preuves enregistrées » was kept
as a standing project instruction because of « uniquement »: every later plan or section
context carried it. An instruction now serves the request that states it unless the
consultant says it is a standing rule. Records captured before this rule keep their effect
until the consultant reviews them (legacy_review proposes, apply_review applies).
"""
import copy
import json

import pytest

from agents.EnnoScholarV4.instructions import (apply_review, capture_instructions, classify, legacy_review,
    relevant_instructions)


def state_with(*messages):
    state = {"messages": [], "outline": {"sections": [{"id": f"s{i}", "title": f"Section {i}"} for i in range(1, 8)]}}
    for index, text in enumerate(messages):
        message = {"id": f"m{index}", "role": "user", "content": text}
        state["messages"].append(message)
        capture_instructions(state, message)
        state["messages"].append({"id": f"a{index}", "role": "assistant", "content": "Réponse."})
    return state


def texts(state, task="conversation", **targets):
    return [row["text"] for row in relevant_instructions(state, task, **targets)]


def ask(state, text, message_id="next"):
    message = {"id": message_id, "role": "user", "content": text}
    state["messages"].append(message)
    capture_instructions(state, message)
    return state


@pytest.mark.parametrize("text", [
    "Donne-moi le détail des deux articles, uniquement à partir des preuves enregistrées.",
    "Compare uniquement ces deux articles.",
    "Réponds uniquement avec les preuves enregistrées.",
    "Approfondis LDPoly et HoliTracer, uniquement à partir de leurs fiches."])
def test_a_one_off_request_serves_its_own_message_only(text):
    state = ask(state_with(), text, "q")
    assert text in texts(state, "final")                                   # applied to its own request
    ask(state, "Propose un plan pour mon état de l'art.", "later")
    for task in ("conversation", "planning", "section", "final"):
        assert text not in texts(state, task, section_id="s1")             # never a standing rule afterwards
    row = next(r for r in state["consultant_instructions"] if r["text"] == text)
    assert row["duration"] == "request" and row["status"] == "active"     # kept for traceability


def test_a_no_search_limit_of_one_message_does_not_bind_later_requests():
    from agents.EnnoScholarV4.references import constraints_of
    first = "Compare-les sans nouvelle recherche, seulement avec les fiches."
    state = ask(state_with(), first, "q")
    assert constraints_of(first) and first in texts(state)
    later = "Trouve maintenant les travaux les plus récents sur la vectorisation routière."
    ask(state, later, "later")
    assert not constraints_of(later) and first not in texts(state)


@pytest.mark.parametrize("text", [
    "Pour ce dossier, utilise systématiquement le terme « vectorisation » plutôt que « polygonisation ».",
    "Dans toutes les prochaines sections, distingue toujours les résultats expérimentaux des hypothèses.",
    "Garde cette règle pour la suite de notre travail : privilégie les publications avec protocole décrit."])
def test_an_explicit_standing_instruction_is_kept(text):
    state = ask(state_with(), text, "rule")
    row = next(r for r in state["consultant_instructions"] if r["text"] == text)
    assert row["duration"] == "persistent"
    for later in ("Continue la rédaction.", "Révise la section 2.", "Compare ces deux articles."):
        ask(state, later, "m_" + later[:6])
    assert text in texts(state, "section", section_id="s2") or text in texts(state, "conversation")


def test_a_section_limited_instruction_does_not_become_a_dossier_rule():
    state = ask(state_with(), "Dans la section 3, distingue uniquement les résultats mesurés des interprétations.", "q")
    row = state["consultant_instructions"][-1]
    assert row["scope"] == "section" and row["target_ids"] == ["s3"] and row["duration"] == "request"
    assert texts(state, "section", section_id="s3") and not texts(state, "section", section_id="s5")
    ask(state, "Continue la rédaction des sections restantes.", "later")
    assert not texts(state, "section", section_id="s3")
    # A standing rule limited to one section stays limited to it.
    ask(state, "Pour la suite, dans la section 3, garde toujours ce découpage.", "rule")
    ask(state, "Continue.", "after")
    assert texts(state, "section", section_id="s3") and not texts(state, "section", section_id="s5")


def test_records_captured_before_durations_keep_their_effect_until_reviewed():
    state = state_with()
    legacy = {"id": "instruction_old", "text": "Donne-moi le détail des deux articles, uniquement à partir des preuves enregistrées.",
        "source_message_id": "m_old", "origin": "consultant_message", "order": 1, "status": "active", "created_at": "x",
        "scope": "project", "target_ids": [], "kind": "decision"}
    standing = {**legacy, "id": "instruction_rule", "text": "Ne modifie pas les sections terminées.", "scope": "writing"}
    state["consultant_instructions"] = [legacy, standing]
    ask(state, "Continue la rédaction.", "later")
    before = copy.deepcopy(state["consultant_instructions"])
    assert legacy["text"] in texts(state)                                  # unchanged behaviour before review
    review = {row["id"]: row["proposal"] for row in legacy_review(state)}
    assert review == {"instruction_old": "request_only", "instruction_rule": "confirm"}
    assert state["consultant_instructions"] == before                      # the review changes nothing
    apply_review(state, {"instruction_old": "request"})                    # only after the consultant's approval
    assert legacy["text"] not in texts(state) and standing["text"] in texts(state, "section", section_id="s1")
    assert len(state["consultant_instructions"]) == 2                      # nothing deleted


def test_the_real_session_proposes_the_detail_question_as_one_off_and_changes_nothing():
    from tests.ennoscholar_v4.test_audit_91590987 import real_state
    state = real_state()
    before = json.dumps(state.get("consultant_instructions"), sort_keys=True)
    review = {row["id"]: row for row in legacy_review(state)}
    detail = next(r for r in review.values() if r["text"].startswith("Donne-moi le détail des deux articles"))
    assert detail["proposal"] == "request_only"
    assert json.dumps(state.get("consultant_instructions"), sort_keys=True) == before


def test_run_8cb4d68a_with_the_new_rule_keeps_the_question_out_of_later_contexts():
    """The real session before run 8cb4d68a: its question is captured with the new rule, applies
    to its own request (targets, proofs and answer unchanged) and not to the next request."""
    from tests.ennoscholar_v4.test_run_8cb4d68a import before_run, replay, last_packet, LDPOLY, HOLITRACER
    state = before_run()
    question = state["messages"][-1]
    state["consultant_instructions"] = [r for r in state["consultant_instructions"] if r["source_message_id"] != question["id"]]
    drafts = {s["id"]: [d["id"] for d in s.get("drafts", [])] for s in state["outline"]["sections"]}
    result, model, events = replay(state)
    row = next(r for r in state["consultant_instructions"] if r["source_message_id"] == question["id"])
    assert row["duration"] == "request"
    memory, papers = last_packet(model)
    assert set(papers) == {LDPOLY, HOLITRACER} and set(result["cited_article_ids"]) == {LDPOLY, HOLITRACER}
    assert len(model.calls) <= 4
    assert {s["id"]: [d["id"] for d in s.get("drafts", [])] for s in state["outline"]["sections"]} == drafts
    state["messages"].append({"id": "next_draft", "role": "user", "content": "Continue la rédaction de mon état de l'art CIR."})
    assert row["text"] not in texts(state, "section", section_id=state["outline"]["sections"][0]["id"])
    assert row["text"] not in texts(state, "planning")
