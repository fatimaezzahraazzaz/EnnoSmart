from backend_api.services.scholar_fulltext_identity import (
    verify_extracted_document_identity,
)


def test_license_terms_marker_does_not_reject_strong_article_identity() -> None:
    title = "A Generic Study of Radar Domain Adaptation"
    result = verify_extracted_document_identity(
        expected_title=title,
        expected_doi="10.1234/example.42",
        expected_authors=["Alice Martin", "Bob Smith"],
        expected_year=2025,
        extraction_payload={
            "full_text": (
                f"{title}\nAlice Martin and Bob Smith\n2025\n"
                "https://doi.org/10.1234/example.42\n"
                "Abstract. This article presents the complete scientific study.\n"
                "License terms and conditions apply to redistribution."
            )
        },
        resolver_candidate={
            "same_article": True,
            "verified_pdf": True,
            "identity_score": 1.0,
        },
    )
    assert result["same_article"] is True
    assert result["doi_match_in_content"] is True


def test_generic_publisher_document_is_still_rejected_without_identity_evidence() -> None:
    result = verify_extracted_document_identity(
        expected_title="A Generic Study of Radar Domain Adaptation",
        expected_doi="10.1234/example.42",
        expected_authors=["Alice Martin"],
        expected_year=2025,
        extraction_payload={
            "full_text": (
                "Instructions for authors\nSubmission guidelines\n"
                "Terms and conditions and publication ethics."
            )
        },
    )
    assert result["same_article"] is False
    assert "generic_publisher_document_detected" in result["reasons"]
