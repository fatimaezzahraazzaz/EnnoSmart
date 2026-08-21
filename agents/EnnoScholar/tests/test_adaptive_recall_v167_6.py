from __future__ import annotations

from agents.EnnoScholar import scientific_query_workflow as wf
from agents.EnnoScholar.adaptive_query_refinement import build_adaptive_refinement_queries
from agents.EnnoScholar.fast_retrieval import (
    VERSION,
    article_has_core_alignment,
    build_fast_retrieval_plan,
    build_query_portfolio,
    build_refinement_jobs,
    should_run_adaptive_refinement,
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


def test_six_levels_are_preserved():
    rows = build_query_portfolio(rich_plan(), target=6)
    assert len(rows) == 6
    assert [x["family"] for x in rows] == [
        "strict_core_a", "strict_core_b", "connexe_a", "connexe_b", "fundamental", "technical"
    ]
    assert [x["target_category"] for x in rows] == [
        "Direct", "Direct", "Connexe", "Connexe", "Fondamental", "Technique"
    ]
    assert all(x["planner_version"] == VERSION for x in rows)


def test_workflow_selector_keeps_six(monkeypatch):
    plan = rich_plan()
    monkeypatch.setattr(
        wf,
        "attach_query_plan",
        lambda intent, max_queries=14: {
            **dict(intent),
            "scientific_query_plan": plan,
            "search_queries": [],
            "query_workflow": {"version": wf.WORKFLOW_VERSION, "status": wf.STATUS_READY},
        },
    )
    rows = wf.select_queries([], {"verrou_title": "x"}, max_queries=6)
    assert len(rows) == 6
    assert rows[0]["family"] == "strict_core_a"
    assert rows[-1]["family"] == "technical"


def test_raw_target_is_150_and_no_cartesian_explosion():
    rows = build_query_portfolio(rich_plan(), target=6)
    scientific = [
        "openalex", "semantic_scholar", "crossref", "doaj", "arxiv",
        "hal", "core", "europe_pmc", "zenodo",
    ]
    artifacts = ["github", "huggingface"]
    plan = build_fast_retrieval_plan(rows, scientific, artifacts, requested_limit=50)
    assert plan["target_final_useful"] == 50
    assert plan["raw_candidate_target"] >= 150
    assert plan["providers_available_count"] == 11
    assert plan["wave1_calls"] == 11
    assert plan["wave1_calls"] + plan["wave2_calls_max"] <= 20


def test_refinement_is_conditional_not_automatic():
    rows = build_query_portfolio(rich_plan(), target=6)
    plan = build_fast_retrieval_plan(rows, ["openalex", "core", "crossref"], [], 50)
    run, _, trigger = should_run_adaptive_refinement(unique_candidates=45, retrieval_plan=plan)
    assert run is True
    run2, _, _ = should_run_adaptive_refinement(unique_candidates=trigger + 1, retrieval_plan=plan)
    assert run2 is False


def test_refinement_llm_one_call_and_novel_queries():
    intent = {"scientific_query_plan": rich_plan()}
    papers = [
        {"title": "Experimental compressor discharge temperature under cooling water flow variation", "abstract": "Compressor tests study cooling water flow and discharge temperature."},
        {"title": "Cooling circuit flow effects on compressor outlet temperature", "abstract": "Experimental thermal performance under varying water flow rates."},
        {"title": "Compressor thermal performance at severe operating conditions", "abstract": "Discharge temperature changes with cooling conditions."},
    ]
    calls = {"n": 0}
    def fake_llm(messages, **kwargs):
        calls["n"] += 1
        return {
            "ok": True,
            "content": '{"queries":[{"query":"compressor cooling water flow discharge temperature experimental","reason":"observed discharge temperature vocabulary"},{"query":"compressor cooling circuit flow outlet temperature severe conditions","reason":"observed cooling circuit vocabulary"}]}'
        }
    existing = [{"query": "compressor water flow rate outlet temperature"}]
    rows, report = build_adaptive_refinement_queries(intent, papers, existing, fake_llm)
    assert calls["n"] == 1
    assert report["llm_calls"] == 1
    assert 1 <= len(rows) <= 2
    assert all(x["family"] == "adaptive_refinement" for x in rows)


def test_refinement_jobs_are_bounded():
    qs = [
        {"query": "compressor cooling water flow discharge temperature", "family": "adaptive_refinement"},
        {"query": "compressor cooling circuit outlet temperature", "family": "adaptive_refinement"},
    ]
    jobs = build_refinement_jobs(qs, ["openalex", "core", "crossref", "semantic_scholar", "doaj"])
    assert 1 <= len(jobs) <= 4
    assert all(x["wave"] == 3 for x in jobs)


def test_citation_seed_guard_rejects_unrelated_and_accepts_core_aligned():
    plan = rich_plan()
    good = {
        "title": "Experimental compressor outlet temperature under water flow variation",
        "abstract": "Water flow rate and severe operating conditions affect compressor outlet temperature.",
    }
    bad = {
        "title": "Global energy balance of a fusion reactor",
        "abstract": "Cooling water calorimetry for plasma energy balance.",
    }
    assert article_has_core_alignment(good, plan) is True
    assert article_has_core_alignment(bad, plan) is False
