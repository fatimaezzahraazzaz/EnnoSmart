# -*- coding: utf-8 -*-
from agents.EnnoScholar.guided_research.application.web_research_service import (
    WebResearchService,
)


def test_chat_locks_become_private_research_targets_not_database_verrous():
    targets = WebResearchService._full_scholar_targets(
        [{
            "query": "weakly supervised defect detection",
            "target_verrous": ["42"],
            "query_kind": "scientific_evidence",
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
    assert targets[0]["research_target_id"] == "42"
    assert targets[0]["research_target_type"] == "conversation_scientific_lock"
    assert "weakly supervised defect detection" in targets[0]["suggested_queries"]
    assert "verrou_id" not in targets[0]


def test_full_scholar_mapping_returns_every_ranked_article_without_chat_cap():
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
    assert all(row["target_verrous"] == ["CHAT-V1"] for row in candidates)
    assert candidates[0]["relevance_role"] == "direct_evidence"
    assert candidates[1]["relevance_role"] == "connected_evidence"


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
