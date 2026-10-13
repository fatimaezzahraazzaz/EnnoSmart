from __future__ import annotations

"""Séparation persistante des corpus Diagnostic et Amélioration."""

from collections import defaultdict
from hashlib import sha256
import json
from typing import Iterable

from sqlalchemy.orm import Session, undefer

from db.models import Document, DocumentCorpusAssignment


CORPUS_DIAGNOSTIC = "diagnostic"
CORPUS_IMPROVEMENT = "improvement"
SUPPORTED_CORPORA = {CORPUS_DIAGNOSTIC, CORPUS_IMPROVEMENT}
WORK_ITEM_DOCUMENT_TYPE = "Élément de travail"


def _legacy_corpora(document: Document) -> set[str]:
    """Classe les lignes historiques qui précèdent la table d'affectation."""
    document_type = str(getattr(document, "document_type", "") or "").casefold()
    if "amélior" in document_type or "amelior" in document_type:
        return {CORPUS_IMPROVEMENT}
    return {CORPUS_DIAGNOSTIC}


def _assignment_rows(
    db: Session,
    document_ids: Iterable[int],
) -> dict[int, dict[str, bool]]:
    ids = sorted({int(value) for value in document_ids if int(value) > 0})
    if not ids:
        return {}
    grouped: dict[int, dict[str, bool]] = defaultdict(dict)
    for row in (
        db.query(DocumentCorpusAssignment)
        .filter(DocumentCorpusAssignment.document_id.in_(ids))
        .all()
    ):
        grouped[int(row.document_id)][str(row.corpus)] = bool(row.included)
    return dict(grouped)


def corpus_states_for_documents(
    db: Session,
    documents: Iterable[Document],
) -> dict[int, dict[str, bool]]:
    docs = list(documents)
    explicit = _assignment_rows(db, [document.id for document in docs])
    states: dict[int, dict[str, bool]] = {}
    for document in docs:
        document_id = int(document.id)
        rows = dict(explicit.get(document_id) or {})
        if not rows:
            rows = {corpus: True for corpus in _legacy_corpora(document)}
        states[document_id] = rows
    return states


def documents_for_corpus(
    db: Session,
    project_id: int,
    corpus: str,
    *,
    load_file_data: bool = False,
) -> list[Document]:
    if corpus not in SUPPORTED_CORPORA:
        raise ValueError(f"Corpus documentaire inconnu : {corpus}")
    query = db.query(Document)
    if load_file_data:
        query = query.options(undefer(Document.file_data))
    documents = (
        query.filter(Document.project_id == int(project_id))
        .order_by(Document.created_at.asc(), Document.id.asc())
        .all()
    )
    states = corpus_states_for_documents(db, documents)
    return [
        document
        for document in documents
        if states.get(int(document.id), {}).get(corpus) is True
    ]


def ensure_document_corpus(
    db: Session,
    document: Document,
    corpus: str,
    *,
    included: bool = True,
) -> DocumentCorpusAssignment:
    if corpus not in SUPPORTED_CORPORA:
        raise ValueError(f"Corpus documentaire inconnu : {corpus}")
    if document.id is None:
        db.flush()

    rows = (
        db.query(DocumentCorpusAssignment)
        .filter(DocumentCorpusAssignment.document_id == int(document.id))
        .all()
    )
    # Matérialise le classement historique avant d'ajouter une seconde portée.
    if not rows:
        for inferred in _legacy_corpora(document):
            inferred_row = DocumentCorpusAssignment(
                document_id=int(document.id),
                corpus=inferred,
                included=True,
            )
            db.add(inferred_row)
            rows.append(inferred_row)

    existing = next((row for row in rows if row.corpus == corpus), None)
    if existing is None:
        existing = DocumentCorpusAssignment(
            document_id=int(document.id),
            corpus=corpus,
            included=bool(included),
        )
        db.add(existing)
    else:
        existing.included = bool(included)
    return existing


def diagnostic_document_review(db: Session, project_id: int) -> dict[str, list[Document]]:
    documents = (
        db.query(Document)
        .filter(Document.project_id == int(project_id))
        .order_by(Document.created_at.desc(), Document.id.desc())
        .all()
    )
    explicit = _assignment_rows(db, [document.id for document in documents])
    states = corpus_states_for_documents(db, documents)

    diagnostic_documents: list[Document] = []
    pending_improvement_documents: list[Document] = []
    excluded_improvement_documents: list[Document] = []
    for document in documents:
        document_id = int(document.id)
        state = states.get(document_id, {})
        explicit_diagnostic = (explicit.get(document_id) or {}).get(CORPUS_DIAGNOSTIC)
        if state.get(CORPUS_DIAGNOSTIC) is True:
            diagnostic_documents.append(document)
        if state.get(CORPUS_IMPROVEMENT) is True:
            if explicit_diagnostic is None:
                pending_improvement_documents.append(document)
            elif explicit_diagnostic is False:
                excluded_improvement_documents.append(document)

    return {
        "diagnostic_documents": diagnostic_documents,
        "pending_improvement_documents": pending_improvement_documents,
        "excluded_improvement_documents": excluded_improvement_documents,
    }


def set_diagnostic_decision(
    db: Session,
    document: Document,
    *,
    keep: bool,
) -> DocumentCorpusAssignment:
    states = corpus_states_for_documents(db, [document]).get(int(document.id), {})
    if states.get(CORPUS_IMPROVEMENT) is not True:
        raise ValueError("Seuls les documents du corpus Amélioration sont à examiner.")
    return ensure_document_corpus(
        db,
        document,
        CORPUS_DIAGNOSTIC,
        included=bool(keep),
    )


def diagnostic_corpus_manifest(db: Session, project_id: int) -> dict[str, object]:
    documents = documents_for_corpus(db, project_id, CORPUS_DIAGNOSTIC)
    items = [
        {
            "id": int(document.id),
            "filename": str(document.filename),
            "sha256": str(document.file_sha256 or ""),
            "file_size": int(document.file_size or 0),
        }
        for document in documents
    ]
    canonical = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "version": 1,
        "corpus": CORPUS_DIAGNOSTIC,
        "documents": items,
        "document_ids": [item["id"] for item in items],
        "count": len(items),
        "fingerprint": sha256(canonical.encode("utf-8")).hexdigest(),
    }
