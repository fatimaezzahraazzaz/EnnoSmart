from services.diagnostic_display_service import build_compact_diagnostic_display


def test_compact_display_keeps_consultant_data_and_drops_heavy_audit_blocks():
    display = {
        "summary": "Synthèse consultant",
        "chroma_sections": {"raw_chunks": "x" * 100_000},
        "diagnostic_cards": [
            {
                "key": "lecture_frascati",
                "title": "Éligibilité",
                "text": "Lecture utile",
                "evidence": [{"document": "preuve.pdf", "page": 4, "text": "Passage utile"}],
                "internal_audit": "x" * 100_000,
            },
            {"key": "raw_internal_card", "text": "x" * 100_000},
        ],
        "validation_verrous": [
            {
                "id": 12,
                "title": "Incertitude de stabilité",
                "consultant_status": "garde",
                "source_json": {
                    "manual_verrou": True,
                    "manual_description": "Description consultant",
                    "keywords": ["stabilité", "variabilité"],
                    "private_chain_of_thought": "x" * 100_000,
                },
            }
        ],
    }

    compact = build_compact_diagnostic_display(display)

    assert compact["summary"] == "Synthèse consultant"
    assert "chroma_sections" not in compact
    assert len(compact["diagnostic_cards"]) == 1
    assert compact["diagnostic_cards"][0]["key"] == "lecture_frascati"
    assert "internal_audit" not in compact["diagnostic_cards"][0]

    verrou = compact["validation_verrous"][0]
    assert verrou["consultant_status"] == "garde"
    assert verrou["source_json"]["manual_description"] == "Description consultant"
    assert verrou["source_json"]["keywords"] == ["stabilité", "variabilité"]
    assert "private_chain_of_thought" not in verrou["source_json"]
