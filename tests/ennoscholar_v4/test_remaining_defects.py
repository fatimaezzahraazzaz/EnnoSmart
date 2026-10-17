"""Deterministic reproductions of the defects seen in runs 80a58dae and 0c604eb8 (2026-10-16)."""
import copy
import json
import time
from dataclasses import replace
from unittest.mock import Mock

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.context import compact_exchange, model_context
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.sources import provider_query
from agents.EnnoScholarV4.toolkit import ScholarTools
from tests.ennoscholar_v4.test_finalization import Client, call
from tests.ennoscholar_v4.test_multi_article_reading import corpus, long_paper, read_windows
from tests.ennoscholar_v4.test_progressive_reading import analysis_for

RELEVANT = {27: "DL-Polycube", 35: "DDPM-Polycube", 44: "3DMeshNet", 65: "HexNet"}


def provider_page(start=0, count=70):
    """One provider's ranked results; positions are 1-based provider ranks."""
    rows = []
    for rank in range(start+1, start+count+1):
        name = RELEVANT.get(rank)
        rows.append(Article(title=f"{name}: learned hexahedral mesh generation" if name else f"Generic learning study {rank}",
            abstract="Hexahedral mesh generation with learning." if name else "A generic study.", authors=["A"], year=2024,
            doi=f"10.1234/rank{rank}", sources=["semantic_scholar"]))
    return rows


def tools():
    sources = Mock()
    sources.search.side_effect = lambda query, src, limit, deadline, check, offset=0: (provider_page(offset, 70-offset), [])
    return ScholarTools({"articles": {}, "messages": []}, sources, ScholarV4Config(api_key="mock"))


def search(t, offset=0, sources=None):
    return t.execute("search_scientific", {"query": "learned hexahedral mesh generation", "sources": sources,
        "limit_per_source": 70, "offset": offset, "family": "hexahedral"}, time.monotonic()+30)


def stored_titles(t):
    return {a.title.split(":")[0] for a in t.registry.values()}


def test_relevant_results_at_ranks_27_35_44_65_stay_reachable_without_false_storage():
    t = tools()
    first = search(t)
    assert {"DL-Polycube", "DDPM-Polycube"} <= stored_titles(t)
    missing = {name for name in RELEVANT.values() if name not in stored_titles(t)}
    unstored = first.get("not_stored", {})
    # Not stored results are announced as such (a bounded sample of titles), with
    # the provider pagination that retrieves every one of them.
    assert unstored["total"] >= len(missing) and len(unstored["titles"]) <= 15
    assert "3DMeshNet" in {row["title"].split(":")[0] for row in unstored["titles"]}
    assert unstored["stored"] is False
    assert not {row["title"] for row in unstored["titles"]} & {a.title for a in t.registry.values()}
    page = unstored["by_provider"]["semantic_scholar"]["next_offset"]
    while missing and page is not None:
        result = search(t, offset=page, sources=["semantic_scholar"])
        missing -= stored_titles(t)
        page = result.get("not_stored", {}).get("by_provider", {}).get("semantic_scholar", {}).get("next_offset")
    assert not missing
    assert t.new_candidates <= 80


def test_or_alternatives_stay_alternatives_for_arxiv():
    query = '("hexahedral mesh" OR "tetrahedral mesh") (deep learning OR neural network) generation'
    arxiv = provider_query("arxiv", query)
    assert ' OR ' in arxiv and ' AND ' in arxiv
    assert '"hexahedral mesh" OR "tetrahedral mesh"' in arxiv
    assert '"deep learning" OR "neural network"' in arxiv
    # Semantic Scholar has no boolean syntax: ranked keywords, hyphens become spaces.
    s2 = provider_query("semantic_scholar", query + " DL-Polycube")
    assert " OR " not in s2 and "-" not in s2 and "DL Polycube" in s2
    assert provider_query("openalex", query) == query


def test_final_context_keeps_numeric_results_for_every_work():
    state, toolkit = corpus(7)
    read_windows(state, toolkit, 40)
    for paper in toolkit.registry.values():
        results = paper.analysis["results"]
        verbal = [{**copy.deepcopy(results[0]), "statement": f"The {paper.id} results are discussed qualitatively ({i})."} for i in range(3)]
        paper.analysis["results"] = verbal + results
    toolkit.save()
    state["messages"] = [{"role": "user", "content": "Synthèse comparative des résultats."}]
    memory = json.loads(model_context(state, toolkit, 24000, final=True)[0]["content"].split(" : ", 1)[1])
    papers = memory["conversation_science"]["papers"]
    assert len(papers) == 7
    for paper in papers:
        assert any(any(ch.isdigit() for ch in c["statement"]) for c in paper["analysis"].get("results", [])), paper["id"]


def test_compacted_exchange_never_exceeds_its_budget():
    state, toolkit = corpus(7)
    read_windows(state, toolkit, 40)
    huge = {"matrix": [{"id": aid, **toolkit.registry[aid].compact()} for aid in toolkit.registry],
        "packet": {"papers": [{"id": aid, "claims": ["x"*2000]*10} for aid in toolkit.registry]},
        "scientific_relations": [], "guidance": "g"}
    exchange = [{"type": "function_call", "name": "compare_articles", "call_id": "c1", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": json.dumps(huge)}]
    budget = 30000
    assert len(json.dumps(compact_exchange(exchange, toolkit, budget), ensure_ascii=False)) <= budget


def test_stale_download_focus_is_not_a_named_priority_in_a_typo_follow_up():
    state, toolkit = corpus(3)
    ids = list(toolkit.registry)
    state["focus"] = {"article_id": ids[2]}
    state["evaluation"] = {"recommended_article_ids": ids}
    state["messages"] = [{"role": "user", "content": "ok je valide le plan et les artcile redige moi l'etatad el'art complete"}]
    read = []

    def respond(n, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            packet = memory["automatic_scientific_reading"]
            read.append(packet["article_id"])
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "x",
                "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": analysis_for(packet)}, n)
        return call("finalize_response", {"answer": "x", "article_ids": [], "evidence_ids": [], "coverage_sufficient": False,
            "remaining_uncertainties": ["x"], "analyses": None, "displayed_article_ids": None}, n)

    config = replace(toolkit.config, max_tool_rounds=2, max_deep_analysis=6)
    ScholarChatAgent(config, client=Client(respond), sources=toolkit.sources).run(state)
    assert len(set(read[:3])) == 3, read


def test_reading_leaves_tools_for_comparison_before_publication():
    state, toolkit = corpus(4)
    state["messages"] = [{"role": "user", "content": "Prépare un état de l'art et compare les méthodes, résultats et limites."}]
    stages = []

    def respond(n, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            packet = memory["automatic_scientific_reading"]
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "x",
                "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": analysis_for(packet)}, n)
        focus = memory.get("argumentation_focus", {})
        stages.append(focus.get("stage") or kw["tool_choice"])
        if focus.get("stage") == "comparison":
            proofs = [p["proofs"][0]["evidence_id"] for p in focus["packet"]["papers"]]
            return call("compare_articles", {"article_ids": focus["article_ids"], "research_context": "x", "conclusions": None,
                "relations": [{"article_ids": focus["article_ids"], "kind": "alternative", "statement": "Distinct mechanisms",
                    "origin": "comparative_inference", "conditions": "Different protocols", "experimental_results": [],
                    "evidence_ids": proofs, "comparable_protocols": False, "uncertainties": ["partial"]}]}, n)
        return call("finalize_response", {"answer": "Synthèse " + " ".join(f"[{a}]" for a in toolkit.registry), "article_ids": list(toolkit.registry),
            "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": ["x"], "analyses": None,
            "displayed_article_ids": None}, n)

    # A tool budget that cannot cover full reading of four long works.
    config = replace(toolkit.config, max_tool_calls=24, max_tool_rounds=6)
    result = ScholarChatAgent(config, client=Client(respond), sources=toolkit.sources).run(state)
    assert "comparison" in stages, (stages, result["exploration_stop_reason"])
    assert result["tool_calls"] <= config.max_tool_calls
