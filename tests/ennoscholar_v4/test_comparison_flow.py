"""Comparisons start once core dimensions are documented, even when bodies are not exhausted."""
import json
import time
from dataclasses import replace

import pytest

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.argumentation import comparison_batches
from agents.EnnoScholarV4.scientific_reading import reading_tasks
from tests.ennoscholar_v4.test_finalization import Client, call
from tests.ennoscholar_v4.test_multi_article_reading import corpus, read_windows
from tests.ennoscholar_v4.test_progressive_reading import analysis_for
from tests.ennoscholar_v4.test_reading_recovery import save_blueprint


def test_core_covered_works_are_batched_for_comparison_before_exhaustion():
    state, toolkit = corpus(3)
    read_windows(state, toolkit, 6)
    tasks = state["scientific_reading"]["tasks"]
    assert all(t["core_covered"] and not t["exhausted"] for t in tasks)
    batches = comparison_batches(state, toolkit.registry)
    assert batches and set(batches[0]) == set(toolkit.registry)


def test_compare_guard_still_rejects_an_unread_work():
    state, toolkit = corpus(2)
    read_windows(state, toolkit, 2)  # one window each: core not yet covered
    with pytest.raises(ValueError, match="Lectures scientifiques prioritaires"):
        toolkit.execute("compare_articles", {"article_ids": list(toolkit.registry), "research_context": "x",
            "conclusions": None, "relations": None}, time.monotonic()+30)


def test_state_of_the_art_turn_compares_when_reading_allowance_cannot_exhaust_bodies():
    state, toolkit = corpus(3)
    state["messages"] = [{"role": "user", "content": "Prépare un état de l'art et compare les méthodes, résultats et limites."}]
    config = replace(toolkit.config, max_tool_rounds=2, max_deep_analysis=6)
    stages, read = [], []

    def respond(n, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            packet = memory["automatic_scientific_reading"]
            read.append(packet["article_id"])
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save",
                "research_context": "meshes", "passage_query": "", "passage_offset": 0,
                "passages_per_article": 6, "analysis": analysis_for(packet)}, n)
        focus = memory.get("argumentation_focus", {})
        stages.append(focus.get("stage"))
        if focus.get("stage") == "comparison":
            proofs = [p["proofs"][0]["evidence_id"] for p in focus["packet"]["papers"]]
            return call("compare_articles", {"article_ids": focus["article_ids"], "research_context": "methods",
                "conclusions": None, "relations": [{"article_ids": focus["article_ids"], "kind": "alternative",
                    "statement": "Distinct documented mechanisms", "origin": "comparative_inference",
                    "conditions": "Different datasets and protocols", "experimental_results": [],
                    "evidence_ids": proofs, "comparable_protocols": False, "uncertainties": ["Partial reading"]}]}, n)
        if focus.get("stage") == "blueprint":
            return save_blueprint(state, focus, n)
        if focus.get("stage") == "project_facts":
            return call("record_project_facts", {"facts": []}, n)
        return call("finalize_response", {"answer": "Synthèse " + " ".join(f"[{aid}]" for aid in toolkit.registry),
            "article_ids": list(toolkit.registry), "evidence_ids": [], "coverage_sufficient": False,
            "remaining_uncertainties": ["Lecture ciblée"], "analyses": None, "displayed_article_ids": None}, n)

    result = ScholarChatAgent(config, client=Client(respond), sources=toolkit.sources).run(state)
    tasks = {t["article_id"]: t for t in state["scientific_reading"]["tasks"]}
    assert all(t["core_covered"] for t in tasks.values())
    assert not all(t["exhausted"] for t in tasks.values())
    assert "comparison" in stages
    relations = state.get("scientific_relations", [])
    assert relations and all(r["comparison_partial"] for r in relations)
    assert {read.count(aid) for aid in toolkit.registry} <= {2, 3}
    assert result["tool_calls"] <= config.max_tool_calls
