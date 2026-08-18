from types import SimpleNamespace

from agents.EnnoAmelioration.application.auto_evidence_selector_v320 import build_traceable_evidence


def test_candidate_id_is_finalized_to_article_id_after_preparation():
    selection = {
        "selected": [{
            "candidate_id": "guided-C42",
            "article_id": None,
            "title": "Domain generalization for SAR ATR",
            "year": 2025,
            "reason": "direct",
            "supported_need": "generalisation",
        }]
    }
    result = SimpleNamespace(
        evidence={"scholar": {"evidence": [{
            "candidate_id": "guided-C42",
            "article_id": 321,
            "citation_id": "A1",
            "title": "Domain generalization for SAR ATR",
            "evidence_text": "Traceable evidence extracted from prepared full text.",
        }]}},
        sources_used=[{
            "candidate_id": "guided-C42",
            "article_id": 321,
            "title": "Domain generalization for SAR ATR",
        }],
    )
    trace = build_traceable_evidence(result=result, selection=selection)
    assert trace["writing_ready_count"] == 1
    assert trace["auto_accepted_article_ids"] == [321]
    assert trace["auto_accepted_candidate_ids"] == ["guided-C42"]
