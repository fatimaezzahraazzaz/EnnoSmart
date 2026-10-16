"""Actual payload receipts for existing reading windows; no scientific workflow changes."""
import copy
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4.agent import ScholarChatAgent, window_ids
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.context import (
    ActiveReadingContextError, compact_exchange, model_context, transmitted_evidence_ids,
)
from agents.EnnoScholarV4.scientific_reading import prepare_reading, reading_tasks
from agents.EnnoScholarV4.toolkit import ScholarTools
from tests.ennoscholar_v4.test_finalization import Client, call
from tests.ennoscholar_v4.test_scientific_reading import article
from tests.ennoscholar_v4.test_tools import analysis, tool_inputs


def prepared_reading():
    paper = article("Transmission study", "10.1234/transmission", "test axis", "Test method")
    paper.id, paper.selected = "paper_transmission", True
    state = {"messages": [{"role": "user", "content": "Explique la méthode de cet article."}],
        "articles": {paper.id: paper.durable()}}
    sources = Mock()
    sources.verify.side_effect = lambda value, deadline: value.model_copy(update={"verified": True})
    config = ScholarV4Config(api_key="mock", web_search_enabled=False)
    toolkit = ScholarTools(state, sources, config)
    task = reading_tasks(state, toolkit.registry)["tasks"][0]
    prepare_reading(state, toolkit, task,
        lambda name, args: toolkit.execute(name, args, float("inf")))
    toolkit.save()
    return state, toolkit, config, task


def save_args(state):
    focus = state["automatic_reading_focus"]
    return {"article_ids": [focus["article_id"]], "action": "save", "research_context": "method",
        "passage_query": "", "passage_offset": 0, "passages_per_article": 2,
        "analysis": analysis(focus["evidence_ids"][0])}


def responder(state):
    def respond(number, kwargs):
        if number == 1:
            packet = json.loads(kwargs["input"][0]["content"].split(" : ", 1)[1])["automatic_scientific_reading"]
            assert {row["evidence_id"] for row in packet["passages"]} == set(state["automatic_reading_focus"]["evidence_ids"])
            assert kwargs["tool_choice"] == {"type": "function", "name": "analyze_article"}
            return call("analyze_article", save_args(state), number)
        aid = "paper_transmission"
        return call("finalize_response", {"answer": f"Analyse ciblée [{aid}]", "article_ids": [aid],
            "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": [],
            "analyses": None, "displayed_article_ids": None}, number)
    return respond


def test_oversized_memory_keeps_every_active_exact_passage_and_latest_question():
    state, toolkit, _, task = prepared_reading()
    state["research_state"] = {"topic": "Long historical scientific context. "*2500}
    state["summary"] = "Historical decisions. "*2000
    state["messages"].insert(0, {"role": "assistant", "content": "Old response. "*2500})
    original = copy.deepcopy((toolkit.evidence, toolkit.snapshots, state["automatic_reading_focus"], task))
    inputs = model_context(state, toolkit, 6500)
    packet = json.loads(inputs[0]["content"].split(" : ", 1)[1])["automatic_scientific_reading"]
    assert len(json.dumps(inputs, ensure_ascii=False)) <= 6500
    assert inputs[-1] == state["messages"][-1]
    assert {row["evidence_id"] for row in packet["passages"]} == set(task["evidence_ids"])
    assert transmitted_evidence_ids(inputs, toolkit.evidence) == set(task["evidence_ids"])
    assert not toolkit.shown_evidence  # Preparing a context is not a receipt.
    assert (toolkit.evidence, toolkit.snapshots, state["automatic_reading_focus"], task) == original


def test_prepared_window_is_presented_only_after_receiving_its_actual_payload():
    state, toolkit, _, task = prepared_reading()
    inputs = model_context(state, toolkit, 18000)
    assert all(w["status"] == "queued" for w in toolkit.reading_windows)
    toolkit.delivered(window_ids(inputs), inputs=inputs)
    assert all(w["status"] == "presented" for w in toolkit.reading_windows)
    assert set(toolkit.shown_evidence) == set(task["evidence_ids"])
    toolkit.execute("analyze_article", save_args(state), float("inf"))
    assert toolkit.registry[task["article_id"]].analysis


@pytest.mark.parametrize("receipt", ["ids_only", "references_only", "untransmitted"])
def test_prepared_but_untransmitted_proofs_cannot_authorize_a_save(receipt):
    state, toolkit, _, task = prepared_reading()
    ids = set(state["automatic_reading_focus"]["reading_window_ids"])
    if receipt == "ids_only":
        toolkit.delivered(ids)
    else:
        payload = {"reading_windows": [{"reading_window_id": wid} for wid in ids],
            "evidence_ids": task["evidence_ids"]} if receipt == "references_only" else {"other_context": "no passages"}
        toolkit.delivered(ids, inputs=tool_inputs(payload))
    assert not toolkit.shown_evidence
    assert all(w["status"] == "queued" for w in toolkit.reading_windows)
    with pytest.raises(ValueError, match="réellement présentés"):
        toolkit.execute("analyze_article", save_args(state), float("inf"))
    assert task["status"] == "awaiting_analysis" and not toolkit.registry[task["article_id"]].analysis


@pytest.mark.parametrize("field", ["exact_quote", "source_version", "article_id"])
def test_modified_quote_or_provenance_is_not_a_delivery_receipt(field):
    state, toolkit, _, task = prepared_reading()
    inputs = model_context(state, toolkit, 18000)
    memory = json.loads(inputs[0]["content"].split(" : ", 1)[1])
    for passage in memory["automatic_scientific_reading"]["passages"]:
        passage[field] = "different content"
    toolkit.delivered(window_ids(inputs), inputs=tool_inputs(memory))
    assert not toolkit.shown_evidence
    assert all(w["status"] == "queued" or not w["evidence_ids"] for w in toolkit.reading_windows)
    with pytest.raises(ValueError):
        toolkit.execute("analyze_article", save_args(state), float("inf"))


def test_exchange_compression_keeps_original_windows_and_only_receipts_sent_proofs():
    state, toolkit, _, _ = prepared_reading()
    aid = "paper_transmission"
    result = toolkit.execute("analyze_article", {"article_ids": [aid], "action": "read",
        "research_context": "method", "passage_query": "", "passage_offset": 0,
        "passages_per_article": 8, "analysis": None, "revisit": True}, float("inf"))
    window = toolkit.reading_windows[-1]
    original = copy.deepcopy((window, toolkit.evidence, toolkit.snapshots))
    exchange = [{"type": "function_call", "name": "analyze_article", "call_id": "read",
        "arguments": json.dumps({"research_context": "old argument "*3000})},
        {"type": "function_call_output", "call_id": "read", "output": json.dumps(result)}]
    compacted = compact_exchange(exchange, toolkit, 2500)
    sent = transmitted_evidence_ids(compacted, toolkit.evidence)
    assert 0 < len(sent) < len(window["evidence_ids"])
    assert (window, toolkit.evidence, toolkit.snapshots) == original
    toolkit.delivered(window_ids(compacted), inputs=compacted)
    assert set(toolkit.shown_evidence) == sent
    assert window["status"] == "queued"
    assert window["evidence_ids"] == original[0]["evidence_ids"]
    omitted = next(eid for eid in window["evidence_ids"] if eid not in sent)
    args = save_args(state)
    args["analysis"] = analysis(omitted)
    with pytest.raises(ValueError, match="réellement présentés"):
        toolkit.execute("analyze_article", args, float("inf"))
    toolkit.delivered(window_ids(tool_inputs(result)), inputs=tool_inputs(result))
    assert window["status"] == "presented"


def test_interruption_before_receipt_preserves_focus_and_resends_same_proofs():
    state, toolkit, config, task = prepared_reading()
    original = copy.deepcopy((state["automatic_reading_focus"], toolkit.evidence, toolkit.snapshots))
    def interrupted(number, kwargs):
        raise RuntimeError("Simulated interruption before response")
    first = ScholarChatAgent(config, client=Client(interrupted), sources=toolkit.sources).run(state)
    assert first["partial"] and not state["shown_evidence_ids"]
    assert task["status"] == "awaiting_analysis" and task["attempts"] == 0
    assert (state["automatic_reading_focus"], state["evidence"], state["source_snapshots"]) == original
    assert all(w["status"] == "undelivered" for w in state["reading_windows"])
    client = Client(responder(state))
    again = ScholarChatAgent(config, client=client, sources=toolkit.sources).run(state)
    assert again["publication_accepted"] and not again["partial"], again
    assert set(state["shown_evidence_ids"]) == set(original[0]["evidence_ids"])
    assert all(w["status"] == "presented" for w in state["reading_windows"])
    assert state["source_snapshots"] == original[2]


def test_packet_too_large_blocks_before_llm_and_leaves_task_resumable():
    state, toolkit, config, task = prepared_reading()
    original = copy.deepcopy((state["automatic_reading_focus"], toolkit.evidence, toolkit.snapshots, task))
    with pytest.raises(ActiveReadingContextError):
        model_context(state, toolkit, 1000)
    client, events = Client(responder(state)), []
    # Whole windows can now be partitioned. This budget cannot hold even one
    # intact proof, so the original no-delivery/no-save protection still applies.
    result = ScholarChatAgent(replace(config, max_context_chars=1000), client=client,
        sources=toolkit.sources).run(state, emit=events.append)
    assert result["reason"] == "evidence_transmission_blocked" and result["partial"]
    assert not client.calls and result["tool_counts"].get("analyze_article", 0) == 0
    assert "tâche est conservée pour reprise" in result["answer"]
    assert any(e["tool"] == "scientific_reading" and e["status"] == "blocked" for e in events)
    assert not state["shown_evidence_ids"]
    assert (state["automatic_reading_focus"], state["evidence"], state["source_snapshots"], task) == original
    resumed = ScholarChatAgent(config, client=Client(responder(state)), sources=toolkit.sources).run(state)
    assert resumed["publication_accepted"] and not resumed["partial"]


def test_agent_never_forces_save_if_context_contains_only_window_references(monkeypatch):
    from agents.EnnoScholarV4 import context
    state, toolkit, config, task = prepared_reading()
    monkeypatch.setattr(context, "model_context", lambda *args, **kwargs:
        tool_inputs({"automatic_scientific_reading": {"reading_windows": [
            {"reading_window_id": wid} for wid in state["automatic_reading_focus"]["reading_window_ids"]]}}))
    client = Client(responder(state))
    result = ScholarChatAgent(config, client=client, sources=toolkit.sources).run(state)
    assert result["reason"] == "evidence_transmission_blocked"
    assert not client.calls and task["status"] == "awaiting_analysis" and not task["attempts"]
    assert not state["shown_evidence_ids"]
