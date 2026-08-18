import json

from agents.EnnoAmelioration.application.source_integrity_v318 import (
    hard_conservation_issues,
    mask_protected_text,
    restore_protected_candidate,
)
from agents.EnnoAmelioration.application.revision_integrity_v318 import (
    cited_claims,
    source_units,
    verify_additions_entailment,
)


def test_immutable_block_is_masked_and_restored_exactly():
    source = (
        "Avant.\n\n"
        '[BLOC DOCUMENT IMMUTABLE id="figure-p1-1" type="figure" page=1]\n'
        "[Figure originale conservée à l'identique dans le document source]\n"
        "Figure 1 : test\n"
        "[/BLOC DOCUMENT IMMUTABLE]\n\n"
        "Après."
    )
    masked, fragments = mask_protected_text(source)
    assert "ENNO_PROTECTED_V318" in masked
    assert "Figure 1 : test" not in masked
    restored, report = restore_protected_candidate(masked, fragments)
    assert restored == source
    assert report["complete"] is True


def test_reference_entry_is_masked_and_restored():
    source = (
        "Texte principal.\n\n"
        "54 Morgan, D. A. E., “Deep convolutional neural networks for ATR from SAR imagery,” "
        "vol. 9475, 2015.\n\n"
        "Suite du texte."
    )
    masked, fragments = mask_protected_text(source)
    assert any(fragment.identifier == "reference:54" for fragment in fragments)
    restored, report = restore_protected_candidate(masked, fragments)
    assert "54 Morgan" in restored
    assert report["complete"] is True


def test_missing_protected_token_is_detected():
    source = (
        '[BLOC DOCUMENT IMMUTABLE id="figure-p1-1" type="figure" page=1]\n'
        "Figure 1\n"
        "[/BLOC DOCUMENT IMMUTABLE]"
    )
    _, fragments = mask_protected_text(source)
    restored, report = restore_protected_candidate("texte sans jeton", fragments)
    assert restored == "texte sans jeton"
    assert report["complete"] is False
    assert report["missing_tokens"]


def test_hard_conservation_classifies_source_loss():
    issues = hard_conservation_issues(
        [
            "references_perdues:54,55",
            "citation_non_etayee:C1:A2:partial",
            "contraction_excessive:0.80",
        ]
    )
    assert "references_perdues:54,55" in issues
    assert "contraction_excessive:0.80" not in issues


def test_cited_claims_extracts_each_citation():
    claims = cited_claims(
        "La méthode améliore le résultat [A1]. "
        "Une autre limite est rapportée [A2][A3]."
    )
    assert claims[0]["citation_ids"] == ["A1"]
    assert claims[1]["citation_ids"] == ["A2", "A3"]


def test_source_units_ignore_short_headings():
    units = source_units(
        "1.3.1.6. État de l'art interne\n\n"
        "Cette phrase contient suffisamment d'information technique pour être contrôlée."
    )
    assert len(units) == 1
    assert units[0]["source_id"] == "S1"


def test_entailment_control_is_advisory_even_when_no_citation_is_found():
    report = verify_additions_entailment(
        [{"content": "Complément scientifique sans citation exploitable."}],
        [],
    )

    assert report["complete"] is False
    assert report["control_mode"] == "advisory_only"
    assert report["blocking"] is False


def test_scholar_keeps_insertable_addition_when_quality_control_warns(monkeypatch):
    from agents.EnnoScholar.state_of_art import (
        existing_review_enrichment_service as service,
    )

    class FakeLLM:
        def generate(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "additions": [
                        {
                            "section_id": "S1",
                            "anchor": "Cette phrase technique constitue une ancre unique dans le corps.",
                            "content": (
                                "Cette analyse scientifique relie prudemment la méthode décrite "
                                "aux limites observées dans la littérature disponible, sans "
                                "présenter cette proximité comme une validation directe du projet. "
                                "Elle apporte un argument comparatif utile au consultant tout en "
                                "signalant que la transférabilité vers le contexte opérationnel "
                                "devra encore être examinée lors de la validation finale [A1]."
                            ),
                            "citations": ["A1"],
                        }
                    ],
                    "uncovered_sections": [],
                },
                ensure_ascii=False,
            )

        def get_last_generation_meta(self):
            return {}

    monkeypatch.setattr(
        service,
        "verify_additions_entailment",
        lambda *_args, **_kwargs: {"complete": True, "issues": []},
    )
    section = {
        "section_id": "S1",
        "title": "État de l'art",
        "content": (
            "1.5 — État de l'art\n"
            "Cette phrase technique constitue une ancre unique dans le corps. "
            "Elle décrit le contexte scientifique du projet avec suffisamment de détails."
        ),
    }

    additions, metadata = service.generate_state_of_art_additions(
        target_text=section["content"],
        sections=[section],
        instruction="Renforcer scientifiquement la section.",
        project_name="Projet",
        project_domain="Domaine",
        evidence_rows=[
            {"citation_id": "A1", "title": "Article mobilisé"},
            {"citation_id": "A2", "title": "Article non mobilisé"},
        ],
        llm=FakeLLM(),
    )

    assert additions
    assert metadata["attempt_count"] == 1
    assert metadata["quality_control_mode"] == "advisory_only"
    assert metadata["controls_blocked_candidate"] is False
    assert "sources_acceptees_non_utilisees:A2" in metadata["integrity_warnings"]
