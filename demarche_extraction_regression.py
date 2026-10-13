from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.chunking import build_chunks
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import ChunkConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import Chunk, DocumentInput
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.semantic_extractor import (
    CallableLLMAdapter,
    SemanticExtractor,
)


STEPS = [
    "Etat de l'art des capteurs optiques",
    "Etude des approches de détection",
    "Proposition d'une architecture de mesure",
    "Implémentation du prototype",
    "Test et validation du prototype",
]


def test_unmarked_research_steps_remain_in_one_chunk():
    document = DocumentInput(
        document_id="d1",
        name="plan.txt",
        text=(
            "Bibliographie\n\nUne publication externe.\n\n"
            "Organisation des travaux de recherche\n\n"
            + "\n\n".join(STEPS)
            + "\n\nRessources\n\nDeux ingénieurs."
        ),
    )
    chunks = build_chunks(document, ChunkConfig())
    work_chunks = [
        chunk for chunk in chunks
        if chunk.section_title == "Organisation des travaux de recherche"
    ]
    assert len(work_chunks) == 1
    assert work_chunks[0].text.splitlines() == STEPS
    assert "Deux ingénieurs" not in work_chunks[0].text


def test_explicit_sequence_recovers_steps_omitted_by_llm():
    chunk = Chunk(
        chunk_id="c1",
        document_id="d1",
        document_name="plan.txt",
        section_title="Organisation des travaux de recherche",
        text="\n".join(STEPS),
    )

    def incomplete_llm(_system_prompt, _user_prompt):
        return {"demarches": [{
            "statement": STEPS[2],
            "evidence_quote": STEPS[2],
            "source_role": "project_evidence",
        }]}

    demarches = SemanticExtractor(
        CallableLLMAdapter(incomplete_llm)
    ).extract_chunk(chunk)
    assert [item.statement for item in demarches] == STEPS
    assert [item.sequence_position for item in demarches] == [1, 2, 3, 4, 5]
    assert all(item.evidence.quote_verified for item in demarches)
    assert all(item.metadata["source_role"] == "project_evidence" for item in demarches)


def test_project_title_cannot_become_demarche():
    title = "Outil de mesure assisté par IA"
    step = "Implémentation du prototype"
    chunk = Chunk(
        chunk_id="c2",
        document_id="d2",
        document_name="projet.txt",
        text=f"Titre des travaux envisagés : {title}\n{step}",
    )

    def fake_llm(_system_prompt, _user_prompt):
        return {"demarches": [
            {"statement": title, "evidence_quote": title},
            {"statement": step, "evidence_quote": step},
        ]}

    items = SemanticExtractor(CallableLLMAdapter(fake_llm)).extract_chunk(chunk)
    assert [item.statement for item in items] == [step]
