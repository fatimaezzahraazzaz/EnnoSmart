"""Responses strict-schema contract and lossless cursor transport, offline."""
import copy

import pytest
from pydantic import ValidationError

from agents.EnnoScholarV4.argumentation import argument_packet, claim_entries
from agents.EnnoScholarV4.toolkit import BlueprintArgs, CompareArgs, definitions
from tests.ennoscholar_v4.test_progressive_reading import analyzed_corpus


@pytest.mark.parametrize("document_tools", [False, True])
def test_every_nested_tool_object_satisfies_the_openai_strict_contract(document_tools):
    def check(value, path):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False, path
                assert set(value.get("required", [])) == set(value.get("properties", {})), path
            for key, child in value.items():
                check(child, path + "/" + key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                check(child, path + "/" + str(index))

    for tool in definitions(document_tools):
        check(tool["parameters"], tool["name"])


@pytest.mark.parametrize("tool_name", ["build_argument_blueprint", "compare_articles"])
@pytest.mark.parametrize("wire_list", [False, True])
def test_claim_cursor_transport_recovers_every_claim_without_altering_sources(tool_name, wire_list):
    state, toolkit, _, _ = analyzed_corpus()
    state.setdefault("scientific_relations", [])
    original = copy.deepcopy(state)
    ids = list(toolkit.registry)[:2]
    full = argument_packet(state, toolkit, ids)
    expected = {(p["id"], field, c["statement"], tuple(c["evidence_ids"]))
                for p in full["papers"] for field, c in claim_entries(p["analysis"])}
    seen, offsets = set(), {}
    for _ in range(30):
        cursor = [{"article_id": aid, "offset": offset} for aid, offset in offsets.items()] if wire_list else offsets
        args = ({"action": "read"} if tool_name == "build_argument_blueprint" else
                {"article_ids": ids, "research_context": "Offline cursor contract", "conclusions": None})
        packet = toolkit.execute(tool_name, {**args, "claim_offsets": cursor}, float("inf"))["packet"]
        for paper in packet["papers"]:
            if paper["id"] not in ids:
                continue
            for field, claim in claim_entries(paper["analysis"]):
                key = (paper["id"], field, claim["statement"], tuple(claim["evidence_ids"]))
                assert key not in seen
                seen.add(key)
        if not packet["transmission_partial"]:
            break
        assert packet["next_claim_offsets"] != offsets
        offsets = packet["next_claim_offsets"]
    assert seen == expected
    assert offsets, "The fixture must exercise continuation beyond the first packet"
    assert state == original


@pytest.mark.parametrize("model,required", [(BlueprintArgs, {"action": "read"}),
    (CompareArgs, {"article_ids": ["paper_a", "paper_b"], "research_context": "", "conclusions": None})])
@pytest.mark.parametrize("offsets", [
    [{"article_id": "paper_a", "offset": -1}],
    [{"article_id": "paper_a", "offset": 0}, {"article_id": "paper_a", "offset": 3}],
])
def test_invalid_cursors_are_rejected_instead_of_silently_dropping_claims(model, required, offsets):
    with pytest.raises(ValidationError):
        model.model_validate({**required, "claim_offsets": offsets})
