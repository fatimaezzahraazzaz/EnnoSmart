# -*- coding: utf-8 -*-
from agents.EnnoScholar.guided_research.application.web_research_service import (
    WebResearchService,
)


def test_chat_request_becomes_primary_target_and_lock_stays_secondary_context():
    targets = WebResearchService._full_scholar_targets(
        [{
            "query": "weakly supervised defect detection",
            "entity_name": "weakly supervised defect detection",
            "target_verrous": ["42"],
            "query_kind": "scientific_evidence",
            "required_terms": ["weak supervision", "rare defects"],
        }],
        {
            "project": {"name": "Inspection", "year": "2025"},
            "scientific_context": "Détection de défauts rares.",
            "current_verrous": [
                {"id": 41, "title": "Autre verrou"},
                {
                    "id": 42,
                    "title": "Détection avec peu d'annotations",
                    "justification": "Les défauts annotés sont rares.",
                },
            ],
        },
    )

    assert len(targets) == 1
    assert targets[0]["research_target_id"].startswith("CHAT-R-")
    assert targets[0]["research_target_type"] == "conversation_supplementary_request"
    assert targets[0]["research_target_title"] == "weakly supervised defect detection"
    assert targets[0]["related_verrou_ids"] == ["42"]
    assert "weakly supervised defect detection" in targets[0]["suggested_queries"]
    assert targets[0]["text"].index("weakly supervised") < targets[0]["text"].index(
        "Détection avec peu d'annotations"
    )
    assert "verrou_id" not in targets[0]


def test_full_scholar_mapping_keeps_ranked_pool_before_chat_shortlist():
    articles = [
        {
            "title": f"Scientific paper number {index} for defect detection",
            "year": 2020 + (index % 5),
            "doi": f"10.1000/paper-{index}",
            "source": "openalex",
            "tag": "Direct" if index % 2 == 0 else "Connexe",
            "relevance_score": 80,
            "abstract": "A scientific abstract.",
        }
        for index in range(75)
    ]
    candidates = WebResearchService._map_full_scholar_report(
        {
            "results": [{
                "research_target_id": "CHAT-V1",
                "research_target_title": "Defect detection",
                "articles": articles,
            }]
        },
        [{"query": "defect detection"}],
        set(),
    )

    assert len(candidates) == 75
    assert all(row["target_verrous"] == [] for row in candidates)
    assert candidates[0]["relevance_role"] == "direct_evidence"
    assert candidates[1]["relevance_role"] == "connected_evidence"


def test_feko_chat_shortlist_rejects_broad_sar_and_unrelated_synthetic_articles():
    request = {
        "query": "Altair FEKO bistatic radar cross section ray launching",
        "entity_name": "Altair FEKO",
        "entity_type": "scientific_software",
        "query_kind": "direct_scientific_evidence",
        "required_terms": ["Altair FEKO", "bistatic RCS", "ray launching"],
        "target_context_dimensions": ["radar simulation", "SAR"],
        "require_direct_evidence": True,
    }
    noise = [
        {
            "candidate_id": f"NOISE-{index}",
            "candidate_kind": "scientific_article",
            "title": f"Synthetic data for unrelated recognition task {index}",
            "abstract": "Synthetic images and deep learning classification.",
            "relevance_role": "direct_evidence",
            "relevance_score": 0.92,
            "source_authority": 0.9,
        }
        for index in range(25)
    ]
    relevant = [
        {
            "candidate_id": "FEKO-1",
            "candidate_kind": "scientific_article",
            "title": "Bistatic RCS simulation with Altair FEKO",
            "abstract": "FEKO ray launching is evaluated for radar cross section prediction.",
            "relevance_role": "direct_evidence",
            "relevance_score": 0.91,
            "source_authority": 0.88,
        },
        {
            "candidate_id": "FEKO-2",
            "candidate_kind": "scientific_article",
            "title": "SAR target modelling using FEKO",
            "abstract": "Bistatic RCS signatures are computed for radar simulation.",
            "relevance_role": "direct_evidence",
            "relevance_score": 0.82,
            "source_authority": 0.84,
        },
    ]

    selected, report = WebResearchService._select_full_chat_candidates(
        [*noise, *relevant],
        [request],
        max_candidates=60,
    )

    assert [row["candidate_id"] for row in selected] == ["FEKO-1", "FEKO-2"]
    assert report["input_count"] == 27
    assert report["output_count"] == 2
    assert report["no_padding"] is True
    assert any(
        row["reason"] == "named_entity_absent"
        for row in report["rejected_examples"]
    )


def test_chat_shortlist_never_returns_the_full_large_ranked_pool():
    request = {
        "query": "Altair FEKO bistatic radar cross section ray launching",
        "entity_name": "Altair FEKO",
        "entity_type": "scientific_software",
        "query_kind": "direct_scientific_evidence",
        "required_terms": ["FEKO", "bistatic RCS", "ray launching"],
        "target_context_dimensions": ["radar simulation"],
        "require_direct_evidence": True,
    }
    ranked_pool = [
        {
            "candidate_id": f"FEKO-{index}",
            "candidate_kind": "scientific_article",
            "title": f"Altair FEKO bistatic RCS study {index}",
            "abstract": "FEKO ray launching for radar simulation and RCS prediction.",
            "relevance_role": "direct_evidence",
            "relevance_score": 0.95 - index / 1000,
            "source_authority": 0.9,
        }
        for index in range(40)
    ]

    selected, report = WebResearchService._select_full_chat_candidates(
        ranked_pool,
        [request],
        max_candidates=60,
    )

    assert len(selected) == WebResearchService.CHAT_REVIEW_MAX_DIRECT
    assert len(selected) <= WebResearchService.CHAT_REVIEW_MAX_CANDIDATES
    assert report["input_count"] == 40
    assert report["max_candidates"] == WebResearchService.CHAT_REVIEW_MAX_CANDIDATES


def test_mixed_chat_portfolio_keeps_full_science_and_searches_official_docs():
    class HybridService(WebResearchService):
        @staticmethod
        def _candidate_matches_request(candidate):
            return True

        def _search_with_full_ennoscholar(self, *args, **kwargs):
            return {
                "ok": True,
                "payload_type": "guided_full_ennoscholar_research_v2",
                "executions": [{"provider": "full_ennoscholar_agent", "ok": True}],
                "candidates": [{
                    "candidate_id": "SCI-FEKO",
                    "candidate_kind": "scientific_article",
                    "title": "Bistatic RCS simulation with Altair FEKO",
                    "abstract": "FEKO ray launching for radar cross section prediction.",
                    "relevance_role": "direct_evidence",
                    "relevance_score": 0.9,
                    "source_authority": 0.9,
                }],
                "chat_selection": {"output_count": 1},
            }

        def _run_job(self, provider, request):
            if request.get("query_kind") != "official_documentation":
                raise AssertionError("scientific lightweight providers must not rerun")
            if provider != "public_web":
                return []
            return [{
                "candidate_id": "DOC-FEKO",
                "candidate_kind": "documentation",
                "title": "Altair FEKO official documentation",
                "url": "https://help.altair.com/feko/",
                "official_source": True,
                "open_access": True,
                "source_authority": 0.96,
                "relevance_score": 0.9,
            }]

    requests = [
        {
            "query": "Altair FEKO bistatic RCS ray launching",
            "entity_name": "Altair FEKO",
            "entity_type": "scientific_software",
            "query_kind": "direct_scientific_evidence",
            "required_terms": ["FEKO", "bistatic RCS"],
            "target_context_dimensions": ["radar simulation"],
            "require_direct_evidence": True,
        },
        {
            "query": "Altair FEKO",
            "entity_name": "Altair FEKO",
            "entity_type": "scientific_software",
            "query_kind": "official_documentation",
            "required_terms": ["FEKO"],
            "target_context_dimensions": ["ray launching configuration"],
        },
    ]
    result = HybridService(enable_llm_rerank=False).search(
        requests,
        full_ennoscholar=True,
        project_context={},
        max_candidates=12,
        auto_refine=False,
    )

    assert result["payload_type"] == "guided_hybrid_full_scholar_research_v2"
    assert {row["candidate_id"] for row in result["candidates"]} == {
        "SCI-FEKO",
        "DOC-FEKO",
    }
    assert result["completeness"]["found"]["official_documentation_pages"] == 1


def test_full_engine_failure_keeps_light_engine_as_safe_fallback():
    class FailingFullService(WebResearchService):
        @staticmethod
        def _candidate_matches_request(candidate):
            return True

        def _search_with_full_ennoscholar(self, *args, **kwargs):
            raise RuntimeError("full engine unavailable")

        def _run_job(self, provider, request):
            if provider != "openalex":
                return []
            return [{
                "candidate_id": "LIGHT-1",
                "candidate_kind": "scientific_article",
                "title": "Fallback scientific evidence for defect detection",
                "url": "https://example.test/paper",
                "open_access": True,
                "source_authority": 0.8,
                "relevance_score": 0.8,
                "relevance_role": "direct_evidence",
                "scientific_evidence_eligible": True,
                "target_verrous": ["CHAT-V1"],
            }]

    result = FailingFullService(enable_llm_rerank=False).search(
        [{
            "query": "defect detection",
            "query_kind": "scientific_evidence",
            "target_context_dimensions": ["problem", "method"],
        }],
        full_ennoscholar=True,
        project_context={},
        auto_refine=False,
    )

    assert result["payload_type"] == "guided_multisource_web_research_v1"
    assert result["candidates"]
    assert result["full_ennoscholar_attempt"]["status"] == "full_ennoscholar_fallback"
