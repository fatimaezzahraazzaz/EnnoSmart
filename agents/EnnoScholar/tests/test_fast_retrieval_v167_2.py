from agents.EnnoScholar.fast_retrieval import (
    VERSION,
    build_fast_retrieval_plan,
    build_query_portfolio,
    should_expand_wave2,
)


def _plan():
    return {
        "scientific_object": [{"term_en": "compressed air compressor"}],
        "independent_variables": [{"term_en": "cooling water flow rate"}],
        "response_variables": [{"term_en": "discharge air temperature"}],
        "operating_conditions": [{"term_en": "suction conditions"}],
        "phenomena": [{"term_en": "discharge temperature variation"}],
        "methods": [],
        "validation_concepts": [],
        "local_identifiers": [{"value": "TGM100"}],
    }


def test_portfolio_has_five_distinct_grounded_angles():
    rows = build_query_portfolio(_plan(), [], target=5)
    assert len(rows) == 5
    assert len({r["family"] for r in rows}) == 5
    for row in rows:
        q = row["query"].lower()
        assert "compressor" in q
        assert "tgm100" not in q
        assert row["planner_version"] == VERSION


def test_portfolio_does_not_need_an_extra_llm_call():
    # Pure deterministic function: a sparse-but-valid role plan still expands
    # to multiple anchored retrieval angles.
    plan = _plan()
    plan["phenomena"] = []
    plan["operating_conditions"] = []
    rows = build_query_portfolio(plan, [], target=5)
    assert len(rows) >= 4
    assert any(r["family"] == "experimental" for r in rows)
    assert any(r["family"] == "review" for r in rows)


def test_wave1_is_bounded_not_cartesian():
    queries = build_query_portfolio(_plan(), [], target=5)
    sources = [
        "openalex", "semantic_scholar", "crossref", "doaj", "arxiv",
        "hal", "core", "europe_pmc", "zenodo",
    ]
    plan = build_fast_retrieval_plan(queries, sources, ["github", "huggingface"], 50)
    old_cartesian = len(queries) * len(sources)
    assert len(plan["wave1_jobs"]) == 9
    assert len(plan["wave1_jobs"]) < old_cartesian / 2
    assert sum(1 for r in plan["wave1_jobs"] if r["source"] == "semantic_scholar") == 1
    assert all(r["limit"] <= 18 for r in plan["wave1_jobs"])


def test_wave2_is_conditional_and_capped():
    queries = build_query_portfolio(_plan(), [], target=5)
    sources = [
        "openalex", "semantic_scholar", "crossref", "doaj", "arxiv",
        "hal", "europe_pmc", "zenodo",
    ]
    plan = build_fast_retrieval_plan(queries, sources, [], 50)
    assert len(plan["wave2_jobs"]) <= 8
    expand, reason, target = should_expand_wave2(
        unique_candidates=60,
        successful_wave1_sources=3,
        wave1_plan=plan,
    )
    assert expand is False
    assert reason == "wave1_sufficient"
    assert target >= 20

    expand, reason, _ = should_expand_wave2(
        unique_candidates=10,
        successful_wave1_sources=3,
        wave1_plan=plan,
    )
    assert expand is True
    assert "unique_candidates_below_target" in reason


def test_artifact_search_is_opt_in(monkeypatch):
    monkeypatch.delenv("ENNOSCHOLAR_FAST_ARTIFACTS_ENABLED", raising=False)
    queries = build_query_portfolio(_plan(), [], target=5)
    plan = build_fast_retrieval_plan(
        queries,
        ["openalex", "crossref"],
        ["github", "huggingface"],
        50,
    )
    assert plan["artifact_jobs"] == []
