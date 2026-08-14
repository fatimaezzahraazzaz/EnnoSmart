from unittest.mock import patch

from agents.EnnoScholar.source_router import build_source_plan


def test_github_is_searched_for_generic_lock_when_technically_relevant():
    with patch.dict(
        "os.environ",
        {
            "ENNOSCHOLAR_FAST_MODE": "1",
            "ENNOSCHOLAR_FAST_SEARCH_ARTIFACTS": "1",
            "ENNOSCHOLAR_USE_GITHUB": "1",
        },
        clear=False,
    ):
        plan = build_source_plan(
            {
                "backend_enrichment_profile": "generic",
                "verrou_title": "Reproducibility of a simulation software pipeline",
            }
        )

    assert "github" in plan["artifact_sources"]
    assert plan["version"] == "v149_relevance_routed_technical_artifacts"


def test_github_can_still_be_explicitly_disabled():
    with patch.dict(
        "os.environ",
        {
            "ENNOSCHOLAR_FAST_MODE": "1",
            "ENNOSCHOLAR_FAST_SEARCH_ARTIFACTS": "0",
            "ENNOSCHOLAR_USE_GITHUB": "1",
        },
        clear=False,
    ):
        plan = build_source_plan(
            {
                "backend_enrichment_profile": "signal_image_vision",
                "verrou_title": "simulation model benchmark",
            }
        )

    assert plan["artifact_sources"] == []
