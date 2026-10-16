"""An exploring model is asked to select before its rounds run out (run 5768a223 regression)."""
import json
from dataclasses import replace
from unittest.mock import Mock

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from tests.ennoscholar_v4.test_finalization import Client, call
from tests.ennoscholar_v4.test_multi_article_reading import long_paper
from tests.ennoscholar_v4.test_progressive_reading import analysis_for

REQUEST = "Quels sont les travaux les plus pertinents sur la génération de maillages 3D ? Propose un état de l'art."


def scenario():
    papers = [long_paper(i).model_copy(update={"id": "", "selected": False}) for i in range(3)]
    sources = Mock()
    sources.search.return_value = (papers, [])
    sources.verify.side_effect = lambda p, deadline: p.model_copy(update={"verified": True})
    state = {"messages": [{"id": "msg_user", "role": "user", "content": REQUEST}], "articles": {}}
    config = replace(ScholarV4Config(api_key="mock", web_search_enabled=False), max_tool_rounds=6)
    return state, sources, config


def memory_of(kw):
    return json.loads(kw["input"][0]["content"].split(" : ", 1)[1])


def test_last_exploration_round_forces_a_selection_then_reading_starts():
    state, sources, config = scenario()
    choices, selections, read = [], [], []

    def respond(n, kw):
        choices.append(kw["tool_choice"])
        memory = memory_of(kw)
        if kw["tool_choice"] == {"type": "function", "name": "record_candidate_decisions"}:
            candidates = memory["selection_candidates"]
            selections.append(candidates)
            assert all(row.get("abstract") is not None or row.get("title") for row in candidates)
            ids = [row["id"] for row in candidates]
            return call("record_candidate_decisions", {"decisions": [{"article_id": aid, "decision": "retain",
                "justification": "Generates volumetric meshes."} for aid in ids[:2]],
                "finalist_ids": ids[:2], "covered_families": ["mesh"], "remaining_axes": []}, n)
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            packet = memory["automatic_scientific_reading"]
            read.append(packet["article_id"])
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save",
                "research_context": "meshes", "passage_query": "", "passage_offset": 0,
                "passages_per_article": 6, "analysis": analysis_for(packet)}, n)
        if kw["tool_choice"] == "auto" and n == 1:
            return call("search_scientific", {"query": "learned volumetric mesh generation", "sources": None,
                "limit_per_source": 10, "offset": 0, "family": "volumetric"}, n)
        if kw["tool_choice"] == "auto" and not selections:
            # Keeps exploring without deciding, as the observed model did.
            return call("search_collected_articles", {"query": "mesh generation", "limit": 8,
                "exclude_presented": False, "family": "unknown-label"}, n)
        return call("finalize_response", {"answer": "Synthèse provisoire.", "article_ids": [], "evidence_ids": [],
            "coverage_sufficient": False, "remaining_uncertainties": ["partial"], "analyses": None,
            "displayed_article_ids": None}, n)

    events = []
    result = ScholarChatAgent(config, client=Client(respond), sources=sources).run(state, emit=events.append)
    forced = [i for i, c in enumerate(choices) if c == {"type": "function", "name": "record_candidate_decisions"}]
    assert forced == [5], choices
    assert selections and len(selections[0]) == 3
    assert state["evaluation"]["recommended_article_ids"]
    assert read and set(read) <= set(state["evaluation"]["recommended_article_ids"])
    assert result["exploration_rounds"] > config.max_tool_rounds
    assert "selection_focus" not in state
    assert any(e["tool"] == "selection_checkpoint" and e["status"] == "completed" for e in events)


def test_no_checkpoint_when_the_model_already_selected_or_nothing_was_presented():
    state, sources, config = scenario()
    sources.search.return_value = ([], [])
    choices = []

    def respond(n, kw):
        choices.append(kw["tool_choice"])
        if n == 1:
            return call("search_scientific", {"query": "learned volumetric mesh generation", "sources": None,
                "limit_per_source": 10, "offset": 0, "family": "volumetric"}, n)
        return call("finalize_response", {"answer": "Aucune publication trouvée.", "article_ids": [], "evidence_ids": [],
            "coverage_sufficient": False, "remaining_uncertainties": ["none found"], "analyses": None,
            "displayed_article_ids": None}, n)

    ScholarChatAgent(config, client=Client(respond), sources=sources).run(state)
    assert {"type": "function", "name": "record_candidate_decisions"} not in choices
