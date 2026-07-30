from mcp_servers.legal_fulltext_mcp.domain.identity import validate_identity
from mcp_servers.legal_fulltext_mcp.domain.models import ArticleIdentity, FulltextCandidate


def test_same_doi_is_accepted():
    selected = ArticleIdentity(doi="https://doi.org/10.1000/XYZ", title="Example paper")
    candidate = FulltextCandidate(provider="test", candidate_doi="10.1000/xyz", candidate_title="Example paper")
    result = validate_identity(selected, candidate)
    assert result.same_article is True
    assert result.method == "same_doi"
    assert result.score == 1.0


def test_different_doi_is_rejected():
    selected = ArticleIdentity(doi="10.1000/a", title="Example paper")
    candidate = FulltextCandidate(provider="test", candidate_doi="10.1000/b", candidate_title="Example paper")
    result = validate_identity(selected, candidate)
    assert result.same_article is False
    assert result.method == "doi_mismatch"


def test_metadata_match_without_doi():
    selected = ArticleIdentity(
        title="A robust method for scientific document classification",
        authors=["Alice Smith", "Bob Doe"],
        year=2022,
    )
    candidate = FulltextCandidate(
        provider="test",
        candidate_title="A Robust Method for Scientific Document Classification",
        candidate_authors=["A. Smith", "B. Doe"],
        candidate_year=2022,
    )
    result = validate_identity(selected, candidate, min_identity_score=0.85)
    assert result.same_article is True
