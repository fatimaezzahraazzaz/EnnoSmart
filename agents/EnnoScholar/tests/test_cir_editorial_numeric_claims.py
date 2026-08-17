from __future__ import annotations

from agents.EnnoScholar.state_of_art.cir_editorial_validator_service import (
    verify_numeric_claims,
)


def test_french_decimal_commas_match_article_decimal_points() -> None:
    draft = {
        "sections": [
            {
                "section_id": "analyse",
                "content": (
                    "La méthode atteint un Dice moyen de 0,787 et un IoU "
                    "moyen de 0,655 [A3]."
                ),
                "subsections": [],
            }
        ]
    }
    evidence = [
        {
            "citation_label": "A3",
            "text": "The method obtains mean Dice/IoU of 0.787/0.655.",
        }
    ]

    assert verify_numeric_claims(draft, evidence) == []


def test_different_numeric_value_is_still_reported() -> None:
    draft = {
        "sections": [
            {
                "section_id": "analyse",
                "content": "La méthode atteint un Dice moyen de 0,900 [A3].",
                "subsections": [],
            }
        ]
    }
    evidence = [
        {
            "citation_label": "A3",
            "text": "The method obtains a mean Dice of 0.787.",
        }
    ]

    issues = verify_numeric_claims(draft, evidence)

    assert len(issues) == 1
    assert issues[0]["value"] == "0,900"
    assert issues[0]["reason"] == "numeric_value_not_found_in_cited_evidence"
