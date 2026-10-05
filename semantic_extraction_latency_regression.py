"""Régressions locales sans appel réseau pour l'extraction V2."""

import threading
import time
from unittest.mock import patch

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import (
    Chunk, EvidenceRef, ExtractionItem,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.semantic_extractor import SemanticExtractor


class FakeLLM:
    supports_fork = False


class ForkableFakeLLM:
    supports_fork = True

    def fork(self):
        return ForkableFakeLLM()


def make_chunk(index, document_id, document_name, text="Essai technique observé."):
    return Chunk(
        chunk_id=f"chunk-{document_id}-{index}",
        document_id=document_id,
        document_name=document_name,
        text=text,
        section_title="Travaux R&D",
        document_mode="raw",
        metadata={
            "chunk_index": index,
            "document_outline": "Travaux R&D",
            "content_origin": "raw_client_document",
            "document_type": "concept_projet",
            "source_policy": "core_or_useful",
        },
    )


def make_item(chunk, verified=True):
    return ExtractionItem(
        item_id=f"item-{chunk.chunk_id}",
        kind="method",
        statement="Essai technique",
        evidence=EvidenceRef(
            evidence_id=f"evidence-{chunk.chunk_id}",
            document_id=chunk.document_id,
            document_name=chunk.document_name,
            chunk_id=chunk.chunk_id,
            quote=chunk.text,
            section_title=chunk.section_title,
            quote_verified=verified,
        ),
        metadata={"source_role": "project_evidence", "source": "semantic_llm"},
    )


def test_exact_duplicate_reuses_calls_and_preserves_both_sources():
    first = make_chunk(0, "doc-1", "Presentation_AI_CODE (1)_aaaaaaaaaaaa.pptx")
    second = make_chunk(0, "doc-2", "Presentation_AI_CODE_bbbbbbbbbbbb.pptx")
    calls = []

    def fake_extract(_self, chunk):
        calls.append(chunk.chunk_id)
        return [make_item(chunk)]

    with patch.object(SemanticExtractor, "extract_chunk", fake_extract):
        items = SemanticExtractor(FakeLLM()).extract([first, second])

    assert calls == [first.chunk_id]
    assert len(items) == 2
    assert [item.evidence.document_id for item in items] == ["doc-1", "doc-2"]
    assert len({item.item_id for item in items}) == 2
    assert len({item.evidence.evidence_id for item in items}) == 2
    assert all(item.evidence.quote == first.text for item in items)
    assert all(item.evidence.quote_verified for item in items)
    assert all(item.metadata["source_role"] == "project_evidence" for item in items)
    assert items[1].metadata["reused_from_item_id"] == items[0].item_id


def test_distinct_document_name_does_not_reuse_even_with_same_text():
    first = make_chunk(0, "doc-1", "article_aaaaaaaaaaaa.docx")
    second = make_chunk(0, "doc-2", "projet_bbbbbbbbbbbb.docx")
    calls = []

    def fake_extract(_self, chunk):
        calls.append(chunk.chunk_id)
        return [make_item(chunk)]

    with patch.object(SemanticExtractor, "extract_chunk", fake_extract):
        SemanticExtractor(FakeLLM()).extract([first, second])
    assert calls == [first.chunk_id, second.chunk_id]


def test_unverified_citation_is_reextracted_for_duplicate():
    first = make_chunk(0, "doc-1", "Presentation_AI_CODE (1)_aaaaaaaaaaaa.pptx")
    second = make_chunk(0, "doc-2", "Presentation_AI_CODE_bbbbbbbbbbbb.pptx")
    calls = []

    def fake_extract(_self, chunk):
        calls.append(chunk.chunk_id)
        return [make_item(chunk, verified=False)]

    with patch.object(SemanticExtractor, "extract_chunk", fake_extract):
        SemanticExtractor(FakeLLM()).extract([first, second])
    assert calls == [first.chunk_id, second.chunk_id]


def test_parallel_workers_keep_order_and_use_separate_clients():
    chunks = [
        make_chunk(i, f"doc-{i}", f"unique-{i}.docx", text=f"Essai {i}.")
        for i in range(6)
    ]
    lock = threading.Lock()
    active = 0
    maximum = 0
    worker_ids = set()

    def fake_extract(self, chunk):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            worker_ids.add(id(self.llm))
        time.sleep(0.03 if chunk.document_id != "doc-0" else 0.08)
        with lock:
            active -= 1
        return [make_item(chunk)]

    with patch.object(SemanticExtractor, "extract_chunk", fake_extract):
        items = SemanticExtractor(ForkableFakeLLM()).extract(chunks)
    assert maximum > 1
    assert maximum <= 3
    assert len(worker_ids) > 1
    assert [item.evidence.document_id for item in items] == [c.document_id for c in chunks]
