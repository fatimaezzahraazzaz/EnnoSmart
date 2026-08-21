from __future__ import annotations

from agents.EnnoScholar import scientific_query_workflow as wf
from agents.EnnoScholar.fast_retrieval import (
    VERSION,
    build_fast_retrieval_plan,
    build_query_portfolio,
    should_expand_wave2,
)


def rich_plan():
    return {
        "scientific_object": [{"term_en": "compressor TGM100"}],
        "independent_variables": [
            {"term_en": "water flow rate"},
            {"term_en": "type of refrigerant"},
        ],
        "response_variables": [
            {"term_en": "compressor outlet temperature"},
            {"term_en": "air intake temperature"},
        ],
        "operating_conditions": [{"term_en": "severe operating conditions for compressor"}],
        "phenomena": [{"term_en": "temperature variation of compressor outlet"}],
        "methods": [{"term_en": "experimental testing under varied water flow rates"}],
        "validation_concepts": [{"term_en": "complementary tests to validate performance"}],
        "local_identifiers": [{"value": "TGM100"}],
        "ambiguities": [],
    }


def test_portfolio_is_five_distinct_families():
    rows = build_query_portfolio(rich_plan(), target=5)
    assert len(rows) == 5
    assert len({r["family"] for r in rows}) == 5
    assert all(r["planner_version"] == VERSION for r in rows)
    assert any("tgm100" in r["query"].lower() for r in rows)
    assert any("tgm100" not in r["query"].lower() for r in rows)


def test_final_selector_does_not_collapse_five_to_three(monkeypatch):
    plan = rich_plan()
    rows = build_query_portfolio(plan, target=5)
    monkeypatch.setattr(
        wf,
        "attach_query_plan",
        lambda intent, max_queries=14: {
            **dict(intent),
            "scientific_query_plan": plan,
            "search_queries": rows,
            "query_workflow": {"version": wf.WORKFLOW_VERSION, "status": wf.STATUS_READY},
        },
    )
    selected = wf.select_queries(rows, {"verrou_title": "x"}, max_queries=5)
    assert len(selected) == 5
    assert len({r["family"] for r in selected}) == 5


def test_wave1_uses_all_11_enabled_providers_once():
    scientific = [
        "openalex", "semantic_scholar", "crossref", "doaj", "arxiv",
        "hal", "core", "europe_pmc", "zenodo",
    ]
    artifacts = ["github", "huggingface"]
    rows = build_query_portfolio(rich_plan(), target=5)
    plan = build_fast_retrieval_plan(rows, scientific, artifacts, requested_limit=50)
    assert plan["providers_available_count"] == 11
    assert plan["wave1_calls"] == 11
    assert len(plan["wave1_jobs"]) == 11
    assert len({j["source"] for j in plan["wave1_jobs"]}) == 11
    assert sum(1 for j in plan["wave1_jobs"] if j.get("artifact")) == 2
    assert plan["wave2_calls_max"] <= 9
    assert plan["old_cartesian_calls_avoided_estimate"] >= 44


def test_adaptive_depth_targets_pool_for_about_50_useful_sources(monkeypatch):
    scientific = [
        "openalex", "semantic_scholar", "crossref", "doaj", "arxiv",
        "hal", "core", "europe_pmc", "zenodo",
    ]
    rows = build_query_portfolio(rich_plan(), target=5)
    plan = build_fast_retrieval_plan(rows, scientific, ["github", "huggingface"], 50)
    assert plan["target_final_useful"] == 50
    assert plan["raw_candidate_target"] >= 80
    expand, reason, target = should_expand_wave2(
        unique_candidates=45,
        successful_wave1_sources=6,
        wave1_plan=plan,
    )
    assert expand is True
    assert "unique_candidates_below_target" in reason
    expand2, reason2, _ = should_expand_wave2(
        unique_candidates=target + 5,
        successful_wave1_sources=max(4, int(plan["min_success_sources"])),
        wave1_plan=plan,
    )
    assert expand2 is False
    assert reason2 == "coverage_sufficient"


def test_no_cartesian_explosion_for_five_queries():
    scientific = [
        "openalex", "semantic_scholar", "crossref", "doaj", "arxiv",
        "hal", "core", "europe_pmc", "zenodo",
    ]
    artifacts = ["github", "huggingface"]
    rows = build_query_portfolio(rich_plan(), target=5)
    plan = build_fast_retrieval_plan(rows, scientific, artifacts, 50)
    # Old strategy would be 55 calls; V167.4 does 11 concurrent coverage calls
    # plus at most 9 conditional depth calls.
    assert plan["wave1_calls"] + plan["wave2_calls_max"] <= 20
