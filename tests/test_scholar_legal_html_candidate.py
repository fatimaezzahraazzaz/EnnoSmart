from backend_api.services.scholar_legal_recovery_service import _mcp_candidates


def test_verified_legal_html_fulltext_is_accepted_as_mcp_candidate() -> None:
    text = (
        "A Generic Scientific Article\n"
        "Abstract and complete scientific content. "
        + ("Detailed method and results. " * 80)
    )
    candidates = _mcp_candidates(
        {
            "best_candidate": {
                "provider": "known_url_publisher_discovery",
                "final_url": "https://example.org/article",
                "legal_access": True,
                "same_article": True,
                "verified_pdf": False,
                "raw_metadata": {
                    "verified_html_fulltext": True,
                    "full_text": text,
                },
            },
            "locations": [],
        }
    )
    assert len(candidates) == 1
    assert candidates[0]["content_source_kind"] == "html"
    assert candidates[0]["full_text"].startswith("A Generic Scientific Article")


def test_short_or_unverified_html_is_rejected() -> None:
    candidates = _mcp_candidates(
        {
            "best_candidate": {
                "final_url": "https://example.org/article",
                "legal_access": True,
                "same_article": True,
                "verified_pdf": False,
                "raw_metadata": {
                    "verified_html_fulltext": True,
                    "full_text": "Too short",
                },
            },
            "locations": [],
        }
    )
    assert candidates == []
