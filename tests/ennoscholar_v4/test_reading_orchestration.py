"""Offline scheduling: scientific decisions stay with the existing model/tools."""
import copy
import json
from dataclasses import replace
from unittest.mock import Mock

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.toolkit import Analysis
from tests.ennoscholar_v4.test_finalization import Client, call
from tests.ennoscholar_v4.test_reading_depth import fixture
from tests.ennoscholar_v4.test_scientific_reading import article, analysis_from_packet


def save_packet(packet, n):
    fields = {f: [] for f in Analysis.model_fields if f not in {"method_family", "analysis_limit"}}
    for row in packet["passages"]:
        f = {"Methods": "method", "Experiments": "experimental_setup", "Results": "results", "Limitations": "limitations", "Abstract": "method"}[row["section"]]
        fields[f].append({"statement": row["exact_quote"], "evidence_ids": [row["evidence_id"]], "origin": "source_statement"})
    return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "analysis": {**fields,
        "method_family": "synthetic", "analysis_limit": "Targeted source windows"}, "research_context": "test",
        "passage_query": "", "passage_offset": 0, "passages_per_article": 6}, n)


def finish(ids, n):
    return call("finalize_response", {"answer": "Analyse conditionnelle " + " ".join(f"[{a}]" for a in ids),
        "article_ids": ids, "evidence_ids": [], "coverage_sufficient": False,
        "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": ids}, n)


def test_first_coverage_is_fair_before_deepening_and_all_windows_fit_existing_limits():
    state, toolkit, _ = fixture()
    first = toolkit.registry["paper_depth"]
    second = first.model_copy(deep=True, update={"id": "paper_second", "title": "Second synthetic study", "doi": "10.1234/second"})
    state["articles"][second.id] = second.durable()
    seen = []
    def respond(n, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            packet = memory["automatic_scientific_reading"]
            seen.append(packet["article_id"])
            return save_packet(packet, n)
        return finish(list(state["articles"]), n)
    config = ScholarV4Config(api_key="mock", web_search_enabled=False)
    result = ScholarChatAgent(config, client=Client(respond), sources=toolkit.sources).run(state)
    assert result["publication_accepted"], result
    assert set(seen[:2]) == set(state["articles"])
    assert len(seen) == 6
    assert result["tool_calls"] <= config.max_tool_calls
    assert all(t["coverage_sufficient"] for t in state["scientific_reading"]["tasks"])


def test_single_explicit_article_does_not_deepen_unrelated_selected_papers():
    state, toolkit, _ = fixture()
    other = toolkit.registry["paper_depth"].model_copy(deep=True, update={"id": "paper_other", "title": "Other retained study", "doi": "10.1234/other"})
    state["articles"][other.id] = other.durable()
    state["messages"] = [{"role": "user", "content": "Approfondis Synthetic depth study et explique ses résultats."}]
    seen = []
    def respond(n, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            packet = memory["automatic_scientific_reading"]
            seen.append(packet["article_id"])
            return save_packet(packet, n)
        return finish(["paper_depth"], n)
    result = ScholarChatAgent(toolkit.config, client=Client(respond), sources=toolkit.sources).run(state)
    assert result["publication_accepted"] and set(seen) == {"paper_depth"}
    assert not state["articles"][other.id]["analysis"]


def test_six_priority_articles_are_read_without_a_hidden_five_article_cap():
    papers = [article(f"Brief synthetic {i}", f"10.1234/brief-{i}", "methods", f"Mechanism {i}") for i in range(6)]
    for i, p in enumerate(papers):
        p.id, p.selected = f"paper_brief_{i}", True
    state = {"articles": {p.id: p.durable() for p in papers}, "messages": [{"role": "user", "content": "Explique les méthodes et les résultats des articles."}]}
    seen = []
    def respond(n, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            packet = memory["automatic_scientific_reading"]
            seen.append(packet["article_id"])
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "methods", "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": analysis_from_packet(packet)}, n)
        return finish(list(state["articles"]), n)
    sources = Mock()
    sources.verify.side_effect = lambda a, deadline: a.model_copy(update={"verified": True})
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=Client(respond), sources=sources).run(state)
    assert result["publication_accepted"] and len(seen) == 6


def test_tool_budget_records_exact_cause_and_preserves_partial_progress():
    state, toolkit, _ = fixture()
    def respond(n, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            return save_packet(memory["automatic_scientific_reading"], n)
        assert kw["tool_choice"] == {"type": "function", "name": "finalize_response"}
        return finish(["paper_depth"], n)
    result = ScholarChatAgent(replace(toolkit.config, max_tool_calls=6), client=Client(respond), sources=toolkit.sources).run(state)
    assert result["publication_accepted"] and result["partial"]
    assert result["reason"] == "exploration_tool_budget"
    task = state["scientific_reading"]["tasks"][0]
    assert task["status"] == "partial" and task["cursor"] > 0 and task["remaining_passages"] > 0
    assert result["tool_calls"] <= 6


def test_new_discovery_and_model_selection_precede_long_automatic_reading():
    old = article("Existing selected method", "10.1234/existing", "methods", "Existing method")
    old.id, old.selected = "paper_existing", True
    new = article("New candidate method", "10.1234/new", "methods", "New mechanism")
    new.id = "paper_new"
    sources = Mock()
    sources.search.return_value = ([old, new], [])
    sources.verify.side_effect = lambda a, deadline: a.model_copy(update={"verified": True})
    state = {"articles": {old.id: old.durable()}, "messages": [{"role": "user", "content": "Cherche les publications pertinentes et analyse leurs méthodes."}]}
    sequence = []
    def respond(n, kw):
        if n == 1:
            assert kw["tool_choice"] == "auto"
            sequence.append("search")
            return call("search_scientific", {"query": "conceptual scientific mechanisms and validation", "sources": None, "limit_per_source": 10, "offset": 0, "family": "methods"}, n)
        if n == 2:
            assert kw["tool_choice"] == "auto"
            sequence.append("selection")
            return call("record_candidate_decisions", {"decisions": [{"article_id": aid, "decision": "retain", "justification": "Relevant scientific contribution in the abstract"} for aid in state["articles"]], "finalist_ids": list(state["articles"]), "covered_families": ["methods"], "remaining_axes": []}, n)
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            sequence.append("reading")
            packet = memory["automatic_scientific_reading"]
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "methods", "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": analysis_from_packet(packet)}, n)
        return finish(list(state["articles"]), n)
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=Client(respond), sources=sources).run(state)
    assert result["publication_accepted"], result
    assert sequence[:2] == ["search", "selection"] and sequence.count("reading") == 2
    assert sources.search.call_args.args[0] == "conceptual scientific mechanisms and validation"


def test_token_and_cost_limits_keep_their_exact_resource_reason_without_llm_calls():
    for overrides, expected in [({"max_tokens": 1000}, "token_budget"), ({"max_cost_usd": .001}, "cost_budget")]:
        state, toolkit, _ = fixture()
        client = Client(lambda *a: (_ for _ in ()).throw(AssertionError("LLM budget cannot fit")))
        result = ScholarChatAgent(replace(toolkit.config, **overrides), client=client, sources=toolkit.sources).run(state)
        assert result["partial"] and result["reason"] == expected
        assert not client.calls
        assert state["scientific_reading"]["remaining_article_ids"] == ["paper_depth"]


def test_elapsed_time_during_a_request_preserves_the_undelivered_reading(monkeypatch):
    from types import SimpleNamespace
    from agents.EnnoScholarV4 import agent as module
    clock = [0.]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    state, toolkit, _ = fixture()
    def respond(n, kw):
        clock[0] = 2.
        packet = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])["automatic_scientific_reading"]
        return save_packet(packet, n)
    result = ScholarChatAgent(replace(toolkit.config, timeout_seconds=1), client=Client(respond), sources=toolkit.sources).run(state)
    assert result["partial"] and result["reason"] == "timeout"
    assert state["automatic_reading_focus"]["article_id"] == "paper_depth"
    assert not state["articles"]["paper_depth"]["analysis"]
