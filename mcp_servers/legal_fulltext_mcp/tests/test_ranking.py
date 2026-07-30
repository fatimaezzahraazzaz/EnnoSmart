from mcp_servers.legal_fulltext_mcp.domain.models import FulltextCandidate
from mcp_servers.legal_fulltext_mcp.domain.ranking import sort_candidates


def test_verified_candidate_ranks_first():
    low = FulltextCandidate(
        provider="hal",
        provider_priority=4,
        pdf_url="https://example.org/a.pdf",
        verified_pdf=False,
        same_article=True,
        identity_score=1.0,
    )
    high = FulltextCandidate(
        provider="unpaywall",
        provider_priority=1,
        pdf_url="https://example.org/b.pdf",
        verified_pdf=True,
        same_article=True,
        identity_score=1.0,
        license="cc-by",
        version="publishedVersion",
    )
    assert sort_candidates([low, high])[0].provider == "unpaywall"
