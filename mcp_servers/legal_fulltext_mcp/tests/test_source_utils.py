from mcp_servers.legal_fulltext_mcp.providers.source_utils import (
    classify_public_source,
    is_blocked_fulltext_domain,
)


def test_academic_pdf_is_public_author_copy():
    access_type, rights_status, domain = classify_public_source(
        "https://www.ece.northwestern.edu/ecefaculty/taflove/Paper10.pdf"
    )
    assert access_type == "public_author_copy"
    assert rights_status == "publicly_accessible_license_unknown"
    assert domain == "www.ece.northwestern.edu"


def test_repository_copy_is_classified():
    access_type, rights_status, _ = classify_public_source(
        "https://hal.science/hal-123/document"
    )
    assert access_type == "repository_copy"
    assert rights_status == "repository_terms"


def test_public_preprint_repository_is_classified():
    access_type, rights_status, domain = classify_public_source(
        "https://deliverypdf.ssrn.com/delivery.php?ID=example"
    )
    assert access_type == "repository_copy"
    assert rights_status == "repository_terms"
    assert domain == "deliverypdf.ssrn.com"


def test_unauthorized_distribution_domains_are_blocked():
    assert is_blocked_fulltext_domain("https://sci-hub.se/example.pdf") is True
    assert is_blocked_fulltext_domain("https://library.lol/main/example.pdf") is True
    assert is_blocked_fulltext_domain("https://example.edu/paper.pdf") is False
