"""DL-Polycube-shaped 6+8 regression: synthetic sources, no network or LLM."""
import copy
import json
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4.agent import ScholarChatAgent, window_ids
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.context import ActiveReadingContextError, model_context, transmitted_evidence_ids
from agents.EnnoScholarV4.evidence import exact_source_valid
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.scientific_reading import (
    READING_QUERIES, completed, extend_reading_focus, fit_reading_focus,
    focused_reading_exchange, prepare_reading, reading_context, reading_tasks,
)
from agents.EnnoScholarV4.toolkit import Analysis, ScholarTools
from tests.ennoscholar_v4.test_finalization import Client, call
from tests.ennoscholar_v4.test_reading_orchestration import finish


def corpus(extra_section_chars=0, count=20):
    chunks = [{"section": "Methods" + ("-"*extra_section_chars if i >= 6 else ""), "page": i+1,
        "text": (f"Scientific method mechanism protocol result {i}. " + "x "*500)[:950]}
        for i in range(count)]
    a = Article(id="paper_focus", title="Synthetic DL-Polycube-shaped reading", doi="10.1234/focus",
        authors=["Synthetic author"], year=2025, verified=True, selected=True,
        fulltext={"text": "\n".join(c["text"] for c in chunks), "chunks": chunks, "url": "https://example.org/focus"})
    state = {"messages": [{"role": "user", "content": "Analyse cet article en profondeur."}], "articles": {a.id: a.durable()}}
    sources = Mock()
    sources.verify.side_effect = lambda a, deadline: a.model_copy(update={"verified": True})
    sources.search.side_effect = AssertionError("No scientific search")
    sources.metadata.side_effect = AssertionError("No metadata request")
    config = ScholarV4Config(api_key="mock", web_search_enabled=False)
    toolkit = ScholarTools(state, sources, config)
    task = reading_tasks(state, toolkit.registry)["tasks"][0]
    prepare_reading(state, toolkit, task, lambda n, a: toolkit.execute(n, a, float("inf")))
    return state, toolkit, task, sources, config


def read_eight(toolkit, aid):
    return toolkit.execute("analyze_article", {"article_ids": [aid], "action": "read",
        "research_context": "targeted continuation", "passage_query": " ".join(READING_QUERIES),
        "passage_offset": 6, "passages_per_article": 8, "analysis": None, "revisit": True}, float("inf"))


def save_args(toolkit, packet, previous=()):
    fields = {f: [] for f in Analysis.model_fields if f not in {"method_family", "analysis_limit"}}
    fields["method"] = [{"statement": toolkit.evidence[eid]["exact_quote"], "evidence_ids": [eid],
        "origin": "source_statement"} for eid in previous]
    fields["technical_mechanism"] = [{"statement": p["exact_quote"], "evidence_ids": [p["evidence_id"]],
        "origin": "source_statement"} for p in packet["passages"]]
    return {"article_ids": [packet["article_id"]], "action": "save", "research_context": "offline",
        "passage_query": "", "passage_offset": 0, "passages_per_article": 8,
        "analysis": {**fields, "method_family": "synthetic", "analysis_limit": "Only exact supplied passages"}}


@pytest.mark.parametrize("extra_section_chars", [0, 3200])
def test_six_delivered_then_eight_new_proofs_fit_progressively_and_continue(extra_section_chars):
    state, toolkit, task, _, _ = corpus(extra_section_chars)
    aid = task["article_id"]
    first = copy.deepcopy(state["automatic_reading_focus"])
    first_ids = list(first["evidence_ids"])
    assert len(first["evidence_ids"]) == 6
    inputs = model_context(state, toolkit, 18000)
    toolkit.delivered(window_ids(inputs), inputs=inputs)
    assert set(toolkit.shown_evidence) == set(first["evidence_ids"])
    result = read_eight(toolkit, aid)
    new_ids = [p["evidence_id"] for p in result["reading"][0]["passages"]]
    assert len(new_ids) == 8 and not set(new_ids) & set(first["evidence_ids"])
    # Reproduce the former failure with the original strict guard, before fitting.
    state["automatic_reading_focus"] = {**first, "evidence_ids": first["evidence_ids"] + new_ids,
        "reading_window_ids": first["reading_window_ids"] + [result["reading"][0]["reading_window_id"]]}
    with pytest.raises(ActiveReadingContextError):
        model_context(state, toolkit, 18000)
    state["automatic_reading_focus"] = first
    evidence, snapshots = copy.deepcopy((toolkit.evidence, toolkit.snapshots))
    assert extend_reading_focus(state, toolkit, result["reading"])
    assert state["automatic_reading_focus"]["evidence_ids"] == new_ids
    previous = first_ids
    steps = 0
    while state.get("automatic_reading_focus") and steps < 10:
        fit_reading_focus(state, toolkit, 18000)
        focus = state["automatic_reading_focus"]
        active = list(focus["evidence_ids"])
        assert not set(active) & set(first_ids)
        inputs = model_context(state, toolkit, 18000)
        assert len(json.dumps(inputs, ensure_ascii=False)) <= 18000
        sent = transmitted_evidence_ids(inputs, toolkit.evidence)
        shown_before = set(toolkit.shown_evidence)
        toolkit.delivered(window_ids(inputs), inputs=inputs)
        assert set(toolkit.shown_evidence) - shown_before <= sent
        assert set(active) <= set(toolkit.shown_evidence)
        packet = reading_context(state, toolkit)
        toolkit.execute("analyze_article", save_args(toolkit, packet, previous), float("inf"))
        completed(state, toolkit.registry[aid])
        previous = []
        assert not set(focus.get("queued_evidence_ids", [])) & set(task["examined_evidence_ids"])
        steps += 1
    assert not state.get("automatic_reading_focus")
    assert steps > 1 if extra_section_chars else steps == 1
    assert set(toolkit.shown_evidence) == set(first_ids + new_ids)
    assert set(task["examined_evidence_ids"]) == set(toolkit.shown_evidence)
    assert toolkit.evidence == evidence and toolkit.snapshots == snapshots
    a = toolkit.registry[aid]
    assert {e["evidence_id"] for e in a.analysis["evidence"]} == set(toolkit.shown_evidence)
    assert len(a.analysis["method"]) == 6 and len(a.analysis["technical_mechanism"]) == 8
    assert all(exact_source_valid(e, toolkit.snapshots) for e in toolkit.evidence.values())
    # The original sequential cursor still reaches the following unread source.
    prepare_reading(state, toolkit, task, lambda n, a: toolkit.execute(n, a, float("inf")))
    fit_reading_focus(state, toolkit, 18000)
    following = reading_context(state, toolkit)
    assert following["passages"] and not {p["evidence_id"] for p in following["passages"]} & set(evidence)
    inputs = model_context(state, toolkit, 18000)
    assert {p["evidence_id"] for p in following["passages"]} <= transmitted_evidence_ids(inputs, toolkit.evidence)


def test_agent_sends_only_current_quotes_and_saves_cumulatively_after_six_then_eight():
    state, original, _, sources, config = corpus(3200, count=14)
    old_ids = list(state["automatic_reading_focus"]["evidence_ids"])
    observed = []
    def respond(n, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        packet = memory.get("automatic_scientific_reading")
        if not packet:
            return finish(["paper_focus"], n)
        ids = {p["evidence_id"] for p in packet["passages"]}
        observed.append(ids)
        assert len(json.dumps(kw["input"], ensure_ascii=False)) <= config.max_context_chars
        if n == 1:
            return call("analyze_article", {"article_ids": ["paper_focus"], "action": "read", "research_context": "more details",
                "passage_query": " ".join(READING_QUERIES), "passage_offset": 6, "passages_per_article": 8, "analysis": None}, n)
        assert not ids & set(old_ids)
        assert transmitted_evidence_ids(kw["input"], state["evidence"]) == ids
        return call("analyze_article", save_args(original, packet, old_ids if n == 2 else ()), n)
    client = Client(respond)
    outcome = ScholarChatAgent(config, client=client, sources=sources).run(state)
    assert outcome["publication_accepted"] and outcome["reason"] is None, outcome
    assert len(observed) > 2 and len(set.union(*observed)) == 14
    assert set(state["shown_evidence_ids"]) == set.union(*observed)
    assert len(state["articles"]["paper_focus"]["analysis"]["evidence"]) == 14
    sources.search.assert_not_called()
    sources.metadata.assert_not_called()


def test_partition_checkpoint_resumes_the_same_unsent_slice_without_receipts():
    state, toolkit, task, sources, config = corpus(3200)
    inputs = model_context(state, toolkit, 18000)
    toolkit.delivered(window_ids(inputs), inputs=inputs)
    extend_reading_focus(state, toolkit, read_eight(toolkit, task["article_id"])["reading"])
    fit_reading_focus(state, toolkit, 18000)
    checkpoint = copy.deepcopy(state)
    focus = copy.deepcopy(state["automatic_reading_focus"])
    assert focus["queued_evidence_ids"]
    resumed = ScholarTools(checkpoint, sources, config)
    resumed_task = reading_tasks(checkpoint, resumed.registry)["tasks"][0]
    prepare_reading(checkpoint, resumed, resumed_task, lambda *a: pytest.fail("No repeated source read"))
    fit_reading_focus(checkpoint, resumed, 18000)
    assert checkpoint["automatic_reading_focus"] == focus
    assert checkpoint["shown_evidence_ids"] == state["shown_evidence_ids"]
    assert resumed_task["cursor"] == task["cursor"] == 0
    assert not set(focus["evidence_ids"]) & set(resumed.shown_evidence)


def test_old_accumulated_checkpoint_keeps_delivered_six_out_of_the_new_packet():
    state, toolkit, task, _, _ = corpus()
    inputs = model_context(state, toolkit, 18000)
    toolkit.delivered(window_ids(inputs), inputs=inputs)
    first = copy.deepcopy(state["automatic_reading_focus"])
    result = read_eight(toolkit, task["article_id"])["reading"][0]
    extra = [p["evidence_id"] for p in result["passages"]]
    state["automatic_reading_focus"]["evidence_ids"].extend(extra)
    state["automatic_reading_focus"]["reading_window_ids"].append(result["reading_window_id"])
    task["pending"] = copy.deepcopy(state["automatic_reading_focus"])
    fit_reading_focus(state, toolkit, 18000)
    focus = state["automatic_reading_focus"]
    assert focus["evidence_ids"] == extra
    assert focus["previous_evidence_ids"] == first["evidence_ids"]
    assert set(task["evidence_ids"]) == set(first["evidence_ids"] + extra)
    assert toolkit.shown_evidence == first["evidence_ids"]
    assert task["cursor"] == 0 and task["pending"] == focus
    inputs = model_context(state, toolkit, 18000)
    assert transmitted_evidence_ids(inputs, toolkit.evidence) == set(extra)


def test_indivisible_oversized_proof_stops_without_truncation_or_progress():
    state, toolkit, task, sources, config = corpus(40000)
    # The normal first six are delivered; the next evidence's metadata is indivisible.
    inputs = model_context(state, toolkit, 18000)
    toolkit.delivered(window_ids(inputs), inputs=inputs)
    extend_reading_focus(state, toolkit, read_eight(toolkit, task["article_id"])["reading"])
    original = copy.deepcopy((state["automatic_reading_focus"], toolkit.evidence, toolkit.snapshots, task["cursor"]))
    client = Client(lambda *a: pytest.fail("An unfit source must not reach the LLM"))
    result = ScholarChatAgent(config, client=client, sources=sources).run(state)
    assert result["reason"] == "evidence_transmission_blocked" and not client.calls
    assert state["scientific_reading"]["blocked_reason"] == "single_evidence_context"
    assert (state["automatic_reading_focus"], toolkit.evidence, toolkit.snapshots, task["cursor"]) == original
    assert len(state["shown_evidence_ids"]) == 6
