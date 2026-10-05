from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend_api"
for candidate in (ROOT, BACKEND):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from modules.RAG import indexer
from modules.RAG.config import FULLTEXT_CHAT_COLLECTION_SUFFIX


def _write_corpus(root: Path) -> Path:
    corpus_dir = root / "documents" / "processed" / "fulltext_rag_v1"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    introduction = (
        "Introduction hors section : le dossier décrit le contexte expérimental complet. "
        * 6
    )
    text = introduction + (
        "[SECTION : Objectif]\n\n"
        "Évaluer la robustesse du modèle dans plusieurs configurations.\n\n"
        "[SECTION : Résultats]\n\n"
        "Les essais montrent une variation mesurable selon le corpus et la température. "
    ) * 8
    first_objective = text.index("Évaluer")
    first_results_marker = text.index("[SECTION : Résultats]")
    first_results = text.index("Les essais")
    payload = {
        "schema_version": "full_document_corpus_v1",
        "document": "rapport.docx",
        "document_id": "doc_1",
        "source_path": "documents/raw/rapport.docx",
        "document_type": "technical_report",
        "text": text,
        "text_sha256": "",
        "sections": [
            {
                "section_title": "Objectif",
                "text": text[first_objective:first_results_marker].strip(),
                "char_start": first_objective,
                "char_end": first_results_marker,
            },
            {
                "section_title": "Résultats",
                "text": text[first_results:],
                "char_start": first_results,
                "char_end": len(text),
            },
        ],
    }
    (corpus_dir / "001_rapport.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (corpus_dir / "manifest.json").write_text(
        json.dumps({"documents_count": 1}),
        encoding="utf-8",
    )
    (corpus_dir / "nlp_result.json").write_text(
        json.dumps({"text": "POISON NLP QUI NE DOIT PAS ÊTRE INDEXÉ"}),
        encoding="utf-8",
    )
    return corpus_dir


def _fake_project_store(root: Path):
    processed = root / "documents" / "processed"
    rag_dir = root / "rag"
    chroma_dir = rag_dir / "chroma"
    rag_dir.mkdir(parents=True, exist_ok=True)
    chroma_dir.mkdir(parents=True, exist_ok=True)
    metadata: dict = {}

    store = SimpleNamespace(
        project_id="projet_test",
        year="2026",
        documents_processed_dir=processed,
        rag_dir=rag_dir,
        chroma_dir=chroma_dir,
        collection_name="ennosmart_org_projet_2026",
        chroma_scope_metadata={"ennosmart_scope_type": "project"},
    )
    store.ensure = lambda: store
    store.write_metadata = lambda values: metadata.update(values)
    store._written_metadata = metadata
    return store


class _FakeCollection:
    def __init__(self, registry: dict[str, int], name: str):
        self.registry = registry
        self.name = name

    def count(self) -> int:
        return int(self.registry.get(self.name, 0))


class _FakeVectorStore:
    counts: dict[str, int] = {}
    chunks_by_collection: dict[str, list[dict]] = {}

    def __init__(self, *args, **kwargs):
        pass

    def collection(self, name: str):
        return _FakeCollection(self.counts, name)

    def add_chunks(self, *, collection_name: str, chunks: list[dict], reset: bool = False):
        if reset:
            self.chunks_by_collection[collection_name] = []
        self.chunks_by_collection.setdefault(collection_name, []).extend(chunks)
        self.counts[collection_name] = len(self.chunks_by_collection[collection_name])
        return {
            "added": len(chunks),
            "deduplicated": 0,
            "embedding_model": indexer.EMBEDDING_MODEL_NAME,
        }


def test_fulltext_fingerprint_ignores_legacy_nlp_result(tmp_path: Path):
    corpus_dir = _write_corpus(tmp_path)
    before = indexer.fulltext_corpus_fingerprint(corpus_dir)

    (corpus_dir / "nlp_result.json").write_text(
        json.dumps({"text": "AUTRE CONTENU LEGACY" * 100}),
        encoding="utf-8",
    )
    after = indexer.fulltext_corpus_fingerprint(corpus_dir)

    assert before == after


def test_fulltext_chunks_keep_document_provenance_and_never_use_nlp(tmp_path: Path):
    corpus_dir = _write_corpus(tmp_path)
    store = _fake_project_store(tmp_path)

    chunks = indexer.fulltext_corpus_to_chunks(store, max_chars=450, overlap=60)

    assert chunks
    assert all(chunk["metadata"]["document"] == "rapport.docx" for chunk in chunks)
    assert all(chunk["metadata"]["document_id"] == "doc_1" for chunk in chunks)
    assert all(chunk["metadata"]["chunk_level"] == "fulltext_document_chunk" for chunk in chunks)
    assert all(chunk["metadata"]["char_end"] > chunk["metadata"]["char_start"] >= 0 for chunk in chunks)
    assert not any("POISON NLP" in chunk["text"] for chunk in chunks)
    assert any("Introduction hors section" in chunk["text"] for chunk in chunks)
    assert corpus_dir in Path(chunks[0]["metadata"]["fulltext_corpus_path"]).parents


def test_index_fulltext_uses_dedicated_collection_and_reuses_fingerprint(tmp_path: Path):
    _write_corpus(tmp_path)
    store = _fake_project_store(tmp_path)
    _FakeVectorStore.counts = {}
    _FakeVectorStore.chunks_by_collection = {}

    with (
        patch.object(indexer, "ProjectStore", return_value=store),
        patch.object(indexer, "RAGVectorStore", _FakeVectorStore),
    ):
        first = indexer.index_fulltext_corpus(
            organisme="Organisation test",
            project="Projet test",
            year="2026",
            reset=True,
        )
        second = indexer.index_fulltext_corpus(
            organisme="Organisation test",
            project="Projet test",
            year="2026",
            reset=True,
        )

        corpus_file = store.documents_processed_dir / "fulltext_rag_v1" / "001_rapport.json"
        changed_payload = json.loads(corpus_file.read_text(encoding="utf-8"))
        changed_payload["text"] += "\nNouvelle preuve ajoutée au document courant."
        changed_payload["text_sha256"] = ""
        corpus_file.write_text(json.dumps(changed_payload, ensure_ascii=False), encoding="utf-8")

        third = indexer.index_fulltext_corpus(
            organisme="Organisation test",
            project="Projet test",
            year="2026",
            reset=True,
        )

    expected_collection = store.collection_name + FULLTEXT_CHAT_COLLECTION_SUFFIX
    assert first["ok"] is True
    assert first["reused"] is False
    assert first["source"] == "fulltext_rag_v1"
    assert first["nlp_result_used"] is False
    assert first["collection_name"] == expected_collection
    assert first["chunks_indexed"] > 0
    assert second["reused"] is True
    assert second["collection_name"] == expected_collection
    assert third["reused"] is False
    assert third["fingerprint"] != second["fingerprint"]
    assert store._written_metadata["rag_index_source"] == "fulltext_rag_v1"
    assert store._written_metadata["rag_nlp_result_used"] is False
    assert (store.rag_dir / indexer.FULLTEXT_INDEX_MANIFEST).is_file()
    assert (store.rag_dir / "diagnostic_chat_raw_manifest.json").is_file()
