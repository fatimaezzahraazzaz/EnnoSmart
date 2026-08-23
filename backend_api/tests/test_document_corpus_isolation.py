from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
for candidate in (str(BACKEND_ROOT), str(PROJECT_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from db.database import Base
from db.models import Document, Project, User
from core.deps import get_current_user, get_db
from routers import documents as documents_router
from services import diagnostic_service
from services.document_corpus_service import (
    CORPUS_DIAGNOSTIC,
    CORPUS_IMPROVEMENT,
    diagnostic_corpus_manifest,
    diagnostic_document_review,
    documents_for_corpus,
    ensure_document_corpus,
    set_diagnostic_decision,
)


def _database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'corpus.db').as_posix()}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _project(db) -> Project:
    user = User(
        full_name="Consultante Test",
        email="corpus@example.test",
        hashed_password="not-used",
    )
    db.add(user)
    db.flush()
    project = Project(
        consultant_id=user.id,
        organisme="Acme",
        project_name="Corpus",
        year="2026",
    )
    db.add(project)
    db.flush()
    return project


def _document(
    db,
    project: Project,
    *,
    filename: str,
    document_type: str,
    content: bytes,
) -> Document:
    document = Document(
        project_id=project.id,
        filename=filename,
        stored_filename=filename,
        file_path=f"db://documents/{filename}",
        content_type="text/plain",
        file_size=len(content),
        document_type=document_type,
        upload_status="importé_en_base",
        file_data=content,
        file_sha256=filename.replace(".", "")[:64],
        storage_mode="database",
    )
    db.add(document)
    db.flush()
    return document


def test_improvement_document_is_pending_and_excluded_by_default(tmp_path: Path):
    engine, db = _database(tmp_path)
    try:
        project = _project(db)
        diagnostic = _document(
            db,
            project,
            filename="preuve.txt",
            document_type="Document brut",
            content=b"preuve diagnostic",
        )
        improvement = _document(
            db,
            project,
            filename="cir_a_ameliorer.txt",
            document_type="Texte à améliorer",
            content=b"texte cir",
        )
        ensure_document_corpus(db, diagnostic, CORPUS_DIAGNOSTIC)
        ensure_document_corpus(db, improvement, CORPUS_IMPROVEMENT)
        db.commit()

        selected = documents_for_corpus(db, project.id, CORPUS_DIAGNOSTIC)
        assert [document.id for document in selected] == [diagnostic.id]

        review = diagnostic_document_review(db, project.id)
        assert [document.id for document in review["diagnostic_documents"]] == [diagnostic.id]
        assert [document.id for document in review["pending_improvement_documents"]] == [improvement.id]

        set_diagnostic_decision(db, improvement, keep=False)
        db.commit()
        review = diagnostic_document_review(db, project.id)
        assert review["pending_improvement_documents"] == []
        assert [document.id for document in review["excluded_improvement_documents"]] == [improvement.id]
        assert [document.id for document in documents_for_corpus(db, project.id, CORPUS_DIAGNOSTIC)] == [diagnostic.id]
    finally:
        db.close()
        engine.dispose()


def test_keep_adds_document_to_both_corpora_and_manifest(tmp_path: Path):
    engine, db = _database(tmp_path)
    try:
        project = _project(db)
        improvement = _document(
            db,
            project,
            filename="annexe_cir.txt",
            document_type="Texte à améliorer",
            content=b"annexe utile",
        )
        ensure_document_corpus(db, improvement, CORPUS_IMPROVEMENT)
        set_diagnostic_decision(db, improvement, keep=True)
        db.commit()

        assert [document.id for document in documents_for_corpus(db, project.id, CORPUS_IMPROVEMENT)] == [improvement.id]
        assert [document.id for document in documents_for_corpus(db, project.id, CORPUS_DIAGNOSTIC)] == [improvement.id]
        manifest = diagnostic_corpus_manifest(db, project.id)
        assert manifest["document_ids"] == [improvement.id]
        assert manifest["count"] == 1
        assert len(str(manifest["fingerprint"])) == 64
    finally:
        db.close()
        engine.dispose()


def test_diagnostic_working_copy_contains_only_selected_corpus(tmp_path: Path, monkeypatch):
    engine, db = _database(tmp_path)
    try:
        project = _project(db)
        diagnostic = _document(
            db,
            project,
            filename="preuve.txt",
            document_type="Document brut",
            content=b"preuve diagnostic",
        )
        improvement = _document(
            db,
            project,
            filename="cir.txt",
            document_type="Texte à améliorer",
            content=b"cir non selectionne",
        )
        ensure_document_corpus(db, diagnostic, CORPUS_DIAGNOSTIC)
        ensure_document_corpus(db, improvement, CORPUS_IMPROVEMENT)
        db.commit()

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        (raw_dir / improvement.stored_filename).write_bytes(b"ancienne copie")

        class Store:
            documents_raw_dir = raw_dir

        monkeypatch.setattr(diagnostic_service, "get_project_store", lambda _project: Store())
        selected = documents_for_corpus(
            db,
            project.id,
            CORPUS_DIAGNOSTIC,
            load_file_data=True,
        )
        copied = diagnostic_service.copy_uploaded_docs_to_project_store(
            db,
            project,
            selected,
        )

        assert copied == [str(raw_dir / diagnostic.stored_filename)]
        assert (raw_dir / diagnostic.stored_filename).read_bytes() == b"preuve diagnostic"
        assert not (raw_dir / improvement.stored_filename).exists()
        # La suppression porte uniquement sur la copie de travail : la donnée DB reste intacte.
        assert db.query(Document).filter(Document.id == improvement.id).one().file_data == b"cir non selectionne"
    finally:
        db.close()
        engine.dispose()


def test_agent_only_refuses_stale_or_legacy_preparation(tmp_path: Path, monkeypatch):
    engine, db = _database(tmp_path)
    try:
        project = _project(db)
        diagnostic = _document(
            db,
            project,
            filename="preuve.txt",
            document_type="Document brut",
            content=b"preuve",
        )
        ensure_document_corpus(db, diagnostic, CORPUS_DIAGNOSTIC)
        db.commit()

        class Store:
            rag_dir = tmp_path / "rag"
            nlp_dir = tmp_path / "nlp"

        Store.rag_dir.mkdir()
        Store.nlp_dir.mkdir()
        (Store.rag_dir / "chunks.json").write_text("{}", encoding="utf-8")
        (Store.nlp_dir / "nlp_result.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(diagnostic_service, "get_project_store", lambda _project: Store())
        monkeypatch.setattr(diagnostic_service, "_load_prepare_report", lambda _project: {})

        with pytest.raises(RuntimeError, match="corpus Diagnostic a changé"):
            diagnostic_service.run_ennodiagnostic_agent_only(db, project)
    finally:
        db.close()
        engine.dispose()


def test_upload_review_api_keeps_improvement_document_only_on_request(tmp_path: Path):
    engine, seed_db = _database(tmp_path)
    session_factory = sessionmaker(bind=engine)
    try:
        project = _project(seed_db)
        consultant_id = int(project.consultant_id)
        project_id = int(project.id)
        seed_db.commit()
    finally:
        seed_db.close()

    app = FastAPI()
    app.include_router(documents_router.router)

    def override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=consultant_id,
        role="consultant",
    )

    client = TestClient(app)
    uploaded = client.post(
        f"/projects/{project_id}/documents/upload",
        params={
            "document_type": "Texte à améliorer",
            "corpus_scope": "improvement",
        },
        files={"file": ("cir.docx", b"contenu cir", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]

    review = client.get(f"/projects/{project_id}/documents/diagnostic-review")
    assert review.status_code == 200, review.text
    assert review.json()["diagnostic_documents"] == []
    assert [item["id"] for item in review.json()["pending_improvement_documents"]] == [document_id]

    kept = client.post(
        f"/projects/{project_id}/documents/diagnostic-review",
        json={"decisions": [{"document_id": document_id, "keep": True}]},
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["pending_improvement_documents"] == []
    assert [item["id"] for item in kept.json()["diagnostic_documents"]] == [document_id]
    engine.dispose()
