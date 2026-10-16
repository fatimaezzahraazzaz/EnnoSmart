"""Readiness, source levels and durable scientific relation validity."""
import copy
import time

import pytest

from agents.EnnoScholarV4.argumentation import argument_packet, comparison_batches, refresh_relations
from agents.EnnoScholarV4.scientific_reading import reading_tasks
from tests.ennoscholar_v4.test_argumentation import prepared_project, relation_arguments
from tests.ennoscholar_v4.test_tools import tool_inputs
from agents.EnnoScholarV4.toolkit import Analysis


def compare(state, toolkit, args=None):
    return toolkit.execute("compare_articles", args or relation_arguments(state, list(toolkit.registry)), time.monotonic()+30)


def test_comparison_waits_for_pending_accessible_scientific_passages():
    state, toolkit, _, papers = prepared_project()
    papers = list(toolkit.registry.values())
    papers[0].fulltext["chunks"].append({"page": 8, "section": "Results", "text": "Additional result metric under a second protocol."})
    reading_tasks(state, toolkit.registry)
    assert not comparison_batches(state, toolkit.registry)
    with pytest.raises(ValueError, match="Lectures scientifiques prioritaires"):
        compare(state, toolkit)
    assert not state.get("scientific_relations")


def test_duplicate_comparison_reuses_the_existing_relation():
    state, toolkit, _, _ = prepared_project()
    first = compare(state, toolkit)
    second = compare(state, toolkit)
    assert len(state["scientific_relations"]) == 1
    assert second["scientific_relations"][0]["id"] == first["scientific_relations"][0]["id"]


def test_source_change_hides_stale_relations_and_preserves_their_records():
    state, toolkit, _, _ = prepared_project()
    compare(state, toolkit)
    old = copy.deepcopy(state["scientific_relations"][0])
    next(iter(toolkit.registry.values())).fulltext["chunks"][0]["text"] += " Updated scientific condition."
    assert argument_packet(state, toolkit)["relations"] == []
    refresh_relations(toolkit)
    assert len(state["scientific_relations"]) == 1
    assert state["scientific_relations"][0]["stale"]
    assert state["scientific_relations"][0]["evidence_ids"] == old["evidence_ids"]
    with pytest.raises(ValueError, match="anciennes"):
        compare(state, toolkit)


def test_abstract_comparison_is_explicitly_partial_and_cannot_claim_superiority():
    state, toolkit, _, _ = prepared_project()
    for a in toolkit.registry.values():
        a.fulltext = {}
        a.analysis = {}
        a.analysis_basis = None
        result = toolkit.execute("analyze_article", {"article_ids": [a.id], "action": "read", "research_context": "abstract", "passage_query": "", "passage_offset": 0, "passages_per_article": 8, "analysis": None}, float("inf"))
        toolkit.delivered([w["reading_window_id"] for w in result["reading"]], inputs=tool_inputs(result))
        row = result["reading"][0]["passages"][0]
        fields = {f: [] for f in Analysis.model_fields if f not in {"method_family", "analysis_limit"}}
        fields["method"] = [{"statement": row["exact_quote"], "evidence_ids": [row["evidence_id"]], "origin": "source_statement"}]
        toolkit.execute("analyze_article", {"article_ids": [a.id], "action": "save", "research_context": "abstract", "passage_query": "", "passage_offset": 0, "passages_per_article": 8, "analysis": {**fields, "method_family": "synthetic", "analysis_limit": "Abstract only"}}, float("inf"))
    reading_tasks(state, toolkit.registry)
    result = compare(state, toolkit)
    assert result["comparison_partial"]
    assert all(row["reading_status"]["basis"] == "abstract" for row in result["matrix"])
    args = relation_arguments(state, list(toolkit.registry))
    args["relations"][0].update(kind="conditional_superiority", comparable_protocols=True)
    with pytest.raises(ValueError, match="résultats et protocoles"):
        compare(state, toolkit, args)


@pytest.mark.parametrize("placement", ["experimental_result", "relation_statement"])
def test_invented_experimental_number_is_rejected_without_altering_saved_relations(placement):
    state, toolkit, _, _ = prepared_project()
    args = relation_arguments(state, list(toolkit.registry))
    proof = toolkit.registry[args["article_ids"][0]].analysis["results"][0]["evidence_ids"]
    if placement == "experimental_result":
        args["relations"][0]["experimental_results"] = [{"statement": "The measured score is 999.9", "evidence_ids": proof, "origin": "source_statement"}]
    else:
        args["relations"][0]["statement"] = "The measured score is 999.9"
    with pytest.raises(ValueError, match="numérique comparatif absent"):
        compare(state, toolkit, args)
    assert not state.get("scientific_relations")
