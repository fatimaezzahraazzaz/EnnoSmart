"""Régressions de la provenance préalable et des preuves des verrous V2."""

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.qualification import LockQualifier
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.project_synthesis import ProjectSynthesizer
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import (
    CanonicalEntity,
    Chunk,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.semantic_extractor import (
    CallableLLMAdapter,
    SemanticExtractor,
)


LITERATURE = "L'article rapporte une limite de compilation de sa méthode."
PROJECT = "Le projet a testé deux formulations et observé un échec de compilation."
METRIC = "La compilabilité est mesurée comme critère d'évaluation."
PARAMETER = "La présence des imports est variée entre les essais."
RESULT = "Trois essais sur dix ont compilé."


def _extract_mixed():
    chunk = Chunk(
        chunk_id="c1", document_id="d1", document_name="dossier_DE.txt",
        text="\n".join([LITERATURE, PROJECT, METRIC, PARAMETER, RESULT]),
        section_title="Travaux et état de l'art",
    )
    calls = []

    def fake_llm(system, prompt):
        calls.append((system, prompt))
        if "Tu classes la provenance" in system:
            return {"passages": [
                {"id": "P1", "role": "literature"},
                {"id": "P2", "role": "project_evidence"},
                {"id": "P3", "role": "project_evidence"},
                {"id": "P4", "role": "project_evidence"},
                {"id": "P5", "role": "project_evidence"},
            ]}
        return {
            "locks": [
                {"statement": "Limite externe", "evidence_quote": LITERATURE,
                 "source_role": "project_evidence"},
                {"statement": "Échec propre au projet", "evidence_quote": PROJECT,
                 "source_role": "literature"},
            ],
            "parameters": [{"statement": "Imports présents ou absents",
                            "evidence_quote": PARAMETER}],
            "metrics": [{"statement": "Compilabilité",
                         "evidence_quote": METRIC}],
            "results": [{"statement": "Trois sur dix",
                         "evidence_quote": RESULT}],
        }

    items = SemanticExtractor(CallableLLMAdapter(fake_llm)).extract([chunk])
    return items, calls


def test_provenance_precedes_extraction_and_controls_each_evidence():
    items, calls = _extract_mixed()
    assert len(calls) == 3
    assert "Tu classes la provenance" in calls[0][0]
    assert "[P1: literature]" in calls[1][1]
    assert "Tu vérifies la provenance" in calls[2][0]
    by_statement = {item.statement: item for item in items}
    assert by_statement["Limite externe"].metadata["source_role"] == "literature"
    assert by_statement["Échec propre au projet"].metadata["source_role"] == "project_evidence"
    assert by_statement["Imports présents ou absents"].kind == "parameter"
    assert by_statement["Compilabilité"].kind == "metric"
    assert by_statement["Trois sur dix"].kind == "result"
    assert all(item.evidence.quote_verified for item in items)


def test_literature_only_lock_cannot_be_valid_without_project_proof():
    items, _ = _extract_mixed()
    literature = next(item for item in items if item.statement == "Limite externe")
    project = next(item for item in items if item.statement == "Échec propre au projet")
    entities = [
        CanonicalEntity(
            entity_id=f"lock_{item.item_id}", kind="lock",
            statement=item.statement, source_item_ids=[item.item_id],
            evidences=[item.evidence], document_ids=[item.evidence.document_id],
            metadata={
                "source_roles": [item.metadata["source_role"]],
                "evidence_source_roles": {
                    item.evidence.evidence_id: item.metadata["source_role"]
                },
            },
        )
        for item in (literature, project)
    ]
    captured = []

    def fake_qualification(_system, prompt):
        captured.append(prompt)
        return {"qualifications": [
            {"entity_id": entities[0].entity_id, "status": "VALID_LOCK",
             "reason": "", "evidence_ids": [literature.evidence.evidence_id]},
            {"entity_id": entities[1].entity_id, "status": "VALID_LOCK",
             "reason": "", "evidence_ids": [project.evidence.evidence_id]},
        ]}

    qualified = LockQualifier(CallableLLMAdapter(fake_qualification)).qualify(
        entities, extractions=items
    )
    assert "VUE GLOBALE DES CANDIDATS" in captured[0]
    assert "PREUVES PROPRES AU PROJET" in captured[0]
    assert qualified[0].metadata["qualification_status"] == "INSUFFICIENT_EVIDENCE"
    assert qualified[1].metadata["qualification_status"] == "VALID_LOCK"


def test_metric_remains_traceable_when_synthesis_omits_its_list():
    items, _ = _extract_mixed()
    synthesis = ProjectSynthesizer(
        CallableLLMAdapter(lambda _system, _prompt: {"metrics": []})
    ).synthesize(items, [])
    assert [point.text for point in synthesis.metrics] == ["Compilabilité"]
    assert synthesis.metrics[0].evidence_ids == [
        next(item.evidence.evidence_id for item in items if item.kind == "metric")
    ]
    assert [point.text for point in synthesis.parameters] == ["Imports présents ou absents"]
    assert [point.text for point in synthesis.results] == ["Trois sur dix"]
