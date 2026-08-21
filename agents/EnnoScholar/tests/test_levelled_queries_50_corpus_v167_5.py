from __future__ import annotations

from agents.EnnoScholar import scientific_query_workflow as wf
from agents.EnnoScholar.fast_retrieval import (
    VERSION,
    build_fast_retrieval_plan,
    build_query_portfolio,
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


def test_five_semantic_levels_no_hors_sujet():
    rows = build_query_portfolio(rich_plan(), target=5)
    assert len(rows) == 5
    assert [r["family"] for r in rows] == [
        "strict_core", "strict_conditions", "connexe", "fundamental", "technical"
    ]
    assert [r["target_category"] for r in rows] == [
        "Direct", "Direct", "Connexe", "Fondamental", "Technique"
    ]
    assert all("hors" not in (r.get("family") or "").lower() for r in rows)
    assert all(r["planner_version"] == VERSION for r in rows)


def test_strict_queries_are_close_and_technical_keeps_project_value():
    rows = build_query_portfolio(rich_plan(), target=5)
    q = {r["family"]: r["query"].lower() for r in rows}
    assert "compressor" in q["strict_core"]
    assert "water" in q["strict_core"] and "temperature" in q["strict_core"]
    assert "severe" in q["strict_conditions"]
    assert "refrigerant" in q["connexe"]
    assert "fundamentals" in q["fundamental"] or "review" in q["fundamental"]
    assert "tgm100" in q["technical"]
    assert "experimental" in q["technical"] or "validation" in q["technical"]


def test_final_selector_preserves_all_five_levels(monkeypatch):
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
    selected = wf.select_queries([], {"verrou_title": "x"}, max_queries=5)
    assert len(selected) == 5
    assert {r["family"] for r in selected} == {
        "strict_core", "strict_conditions", "connexe", "fundamental", "technical"
    }


def test_11_provider_routing_covers_levels_without_cartesian_explosion():
    scientific = [
        "openalex", "semantic_scholar", "crossref", "doaj", "arxiv",
        "hal", "core", "europe_pmc", "zenodo",
    ]
    artifacts = ["github", "huggingface"]
    rows = build_query_portfolio(rich_plan(), target=5)
    plan = build_fast_retrieval_plan(rows, scientific, artifacts, requested_limit=50)
    assert plan["providers_available_count"] == 11
    assert plan["wave1_calls"] == 11
    assert plan["wave1_calls"] + plan["wave2_calls_max"] <= 20
    families = {j["family"] for j in plan["wave1_jobs"]}
    assert "strict_core" in families
    assert "strict_conditions" in families
    assert "connexe" in families
    assert "fundamental" in families
    assert "technical" in families
    technical_jobs = [j for j in plan["wave1_jobs"] if j.get("artifact")]
    assert len(technical_jobs) == 2
    assert all(j["target_category"] == "Technique" for j in technical_jobs)


def test_target_is_50_useful_with_wider_raw_pool():
    rows = build_query_portfolio(rich_plan(), target=5)
    plan = build_fast_retrieval_plan(
        rows,
        ["openalex", "semantic_scholar", "crossref", "doaj", "arxiv", "hal", "core", "europe_pmc", "zenodo"],
        ["github", "huggingface"],
        50,
    )
    assert plan["target_final_useful"] == 50
    assert plan["raw_candidate_target"] >= 100
