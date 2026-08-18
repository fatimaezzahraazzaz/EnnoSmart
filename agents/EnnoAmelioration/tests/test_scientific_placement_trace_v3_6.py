from agents.EnnoAmelioration.application.traceability_service import (
    _supporting_items,
)
from agents.EnnoScholar.state_of_art.existing_review_enrichment_service import (
    _apply_anchored_additions,
    _is_writable_scientific_section,
    _validate_additions,
)


def _long_cited_paragraph(citation: str = "A3") -> str:
    return (
        "Les travaux analysés montrent que les performances des systèmes "
        "SAR ATR peuvent varier lorsque les conditions opérationnelles "
        "diffèrent de celles couvertes pendant l'apprentissage, et que "
        "l'évaluation sur des conditions étendues permet de mieux caractériser "
        "la robustesse ainsi que la capacité de généralisation des modèles "
        f"considérés dans ce contexte [{citation}]."
    )


def test_container_heading_is_not_writable_scientific_section():
    row = {
        "section_id": "parent",
        "title": "Etat de l'art et verrous",
        "content": "1.3. Etat de l'art et verrous\n",
    }
    assert _is_writable_scientific_section(row) is False


def test_real_subsection_body_is_writable():
    row = {
        "section_id": "child",
        "title": "Analyse de l'état de l'art",
        "content": (
            "1.3.1. Analyse de l'état de l'art\n"
            "Depuis plusieurs années, de nombreux travaux exploitent "
            "l'apprentissage profond pour le traitement des images SAR."
        ),
    }
    assert _is_writable_scientific_section(row) is True


def test_anchor_in_heading_is_rejected():
    section_content = (
        "1.3.1. Analyse de l'état de l'art\n"
        "Depuis plusieurs années, les CNN sont étudiés pour le SAR ATR."
    )
    payload = {
        "additions": [
            {
                "section_id": "s1",
                "anchor": "1.3.1. Analyse de l'état de l'art",
                "content": _long_cited_paragraph("A3"),
                "citations": ["A3"],
            }
        ]
    }

    accepted, errors = _validate_additions(
        payload,
        target_text=section_content,
        sections=[
            {
                "section_id": "s1",
                "title": "Analyse de l'état de l'art",
                "content": section_content,
            }
        ],
        evidence_by_citation={
            "A3": "Article scientifique sur robustesse SAR ATR."
        },
    )

    assert accepted == []
    assert any("ancre_dans_le_titre_interdite" in error for error in errors)


def test_anchor_in_body_keeps_heading_before_addition():
    section_content = (
        "1.3.1. Analyse de l'état de l'art\n"
        "Depuis plusieurs années, les CNN sont étudiés pour le SAR ATR."
    )
    anchor = "Depuis plusieurs années, les CNN sont étudiés pour le SAR ATR."
    addition = _long_cited_paragraph("A3")

    improved = _apply_anchored_additions(
        section_content,
        [
            {
                "section_id": "s1",
                "title": "Analyse de l'état de l'art",
                "content": section_content,
            }
        ],
        [
            {
                "section_id": "s1",
                "anchor": anchor,
                "content": addition,
                "citations": ["A3"],
            }
        ],
    )

    assert improved.startswith(
        "1.3.1. Analyse de l'état de l'art\n"
        "Depuis plusieurs années"
    )
    assert improved.index(anchor) < improved.index(addition)
    assert improved.index("1.3.1.") < improved.index(addition)


def test_trace_uses_only_explicit_citation_ids():
    rows = [
        {
            "citation_id": "A2",
            "evidence_id": "A2",
            "text": "SAR ATR robustesse CNN MSTAR conditions étendues",
            "_tokens": {"sar", "atr", "robustesse", "cnn", "mstar"},
        },
        {
            "citation_id": "A3",
            "evidence_id": "A3",
            "text": "SAR ATR robustesse CNN MSTAR conditions étendues",
            "_tokens": {"sar", "atr", "robustesse", "cnn", "mstar"},
        },
        {
            "citation_id": "A4",
            "evidence_id": "A4",
            "text": "SAR ATR robustesse CNN MSTAR conditions étendues",
            "_tokens": {"sar", "atr", "robustesse", "cnn", "mstar"},
        },
    ]

    supporting = _supporting_items(
        "",
        "La robustesse varie selon les conditions expérimentales [A3].",
        rows,
    )

    assert [row["citation_id"] for row in supporting] == ["A3"]


def test_trace_keeps_multiple_explicit_citations_and_no_extra():
    rows = [
        {"citation_id": "A1", "evidence_id": "A1", "text": "OOD", "_tokens": {"ood"}},
        {"citation_id": "A4", "evidence_id": "A4", "text": "simulation", "_tokens": {"simulation"}},
        {"citation_id": "A5", "evidence_id": "A5", "text": "CycleGAN", "_tokens": {"cyclegan"}},
    ]

    supporting = _supporting_items(
        "",
        "Les travaux combinent données synthétiques et réel [A1][A4].",
        rows,
    )

    assert {row["citation_id"] for row in supporting} == {"A1", "A4"}
