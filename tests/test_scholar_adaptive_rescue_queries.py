from unittest.mock import patch

from agents.EnnoScholar.scholar_agent import EnnoScholarAgent, _adaptive_rescue_queries


def test_adaptive_rescue_queries_do_not_depend_on_missing_norm_helper():
    intent = {
        "primary_core_concepts": ["synthetic aperture radar"],
        "core_concepts": ["synthetic aperture radar", "domain adaptation"],
        "method_anchors": ["transfer learning"],
        "phenomenon_anchors": ["simulation to real gap"],
        "constraints": ["limited measured data"],
        "key_terms_en": ["target recognition"],
    }

    queries = _adaptive_rescue_queries(
        intent,
        [{"query": "synthetic aperture radar transfer learning"}],
        max_queries=6,
    )

    assert queries
    assert len(queries) == len({query.casefold() for query in queries})
    assert "synthetic aperture radar transfer learning" not in {
        query.casefold() for query in queries
    }


def test_one_broken_lock_does_not_crash_the_whole_run():
    agent = EnnoScholarAgent(offline_dry_run=True)
    agent.verrou_workers = 1

    with patch.object(
        agent,
        "search_for_verrou",
        side_effect=[RuntimeError("broken lock"), {
            "verrou_id": "v2",
            "verrou_title": "Second lock",
            "articles": [],
            "decision": "aucun_article_trouve",
        }],
    ):
        report = agent.run_search({
            "ignore_cache": True,
            "verrous": [
                {"verrou_id": "v1", "title": "First lock"},
                {"verrou_id": "v2", "title": "Second lock"},
            ],
        })

    assert report["subjects_analyzed"] == 2
    assert report["subjects_failed"] == 1
    assert report["results"][0]["subject_search_failed"] is True
    assert report["results"][1]["verrou_id"] == "v2"
