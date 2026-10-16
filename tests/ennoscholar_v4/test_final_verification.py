"""Publication verifies presented, never-checked references once, instead of rejecting them all."""
import time
from unittest.mock import Mock

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.toolkit import ScholarTools


def toolkit(verified_by_registry=True):
    sources = Mock()
    sources.verify.side_effect = lambda article, deadline: article.model_copy(update={"verified": verified_by_registry,
        "authors": ["Registry Author"], "year": 2024})
    t = ScholarTools({"articles": {}, "messages": [{"role": "user", "content": "Travaux pertinents ?"}]}, sources,
        ScholarV4Config(api_key="mock"))
    t.store.insert([Article(title="Graph Neural Networks for Mesh Generation and Adaptation", doi="10.1234/gnn-mesh",
        authors=["A"], year=2024, abstract="GNN mesh generation.", sources=["openalex"])])
    aid = next(iter(t.registry))
    t.store.exposed.append(aid)
    return t, aid


def publish(t, aid):
    return t.execute("finalize_response", {"answer": f"Travail repéré sur abstract seulement [{aid}].", "article_ids": [aid],
        "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": ["Abstract seulement"],
        "analyses": None, "displayed_article_ids": [aid]}, time.monotonic()+30)


def test_publication_phase_verifies_a_presented_reference_once():
    t, aid = toolkit()
    t.finalization_only = True
    t.final_verification_allowed = True
    result = publish(t, aid)
    assert result.get("published"), result
    assert t.sources.verify.call_count == 1 and t.registry[aid].verified


def test_failed_registry_check_is_still_refused_and_not_repeated():
    t, aid = toolkit(verified_by_registry=False)
    t.finalization_only = True
    t.final_verification_allowed = True
    first = publish(t, aid)
    second = publish(t, aid)
    assert first["rejection_code"] == second["rejection_code"] == "invalid_bibliography"
    assert t.sources.verify.call_count == 1


def test_no_network_verification_outside_the_publication_phase():
    t, aid = toolkit()
    t.finalization_only = True  # e.g. write_section's internal validation
    result = publish(t, aid)
    assert result["rejection_code"] == "invalid_bibliography"
    assert not t.sources.verify.called
