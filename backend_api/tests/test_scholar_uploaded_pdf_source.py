from __future__ import annotations

import asyncio
from io import BytesIO

import fitz
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db.models import Article, Project, ScholarRun, User
from services.scholar_uploaded_pdf_extractor import (
    upload_and_extract_pdf_for_article,
    uploaded_pdf_path,
)


def test_uploaded_pdf_is_persisted_and_marked_as_consultable(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENNOSMART_STORAGE_ROOT", str(tmp_path))
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    try:
        user = User(
            full_name="Consultant test",
            email="consultant@example.com",
            hashed_password="not-used-in-this-test",
            role="consultant",
            is_active=True,
        )
        db.add(user)
        db.flush()
        project = Project(
            consultant_id=user.id,
            organisme="Scalian",
            project_name="AI-RADAR",
            year="2025",
            status="test",
        )
        db.add(project)
        db.flush()
        scholar_run = ScholarRun(project_id=project.id, status="test")
        db.add(scholar_run)
        db.flush()
        article = Article(
            scholar_run_id=scholar_run.id,
            title="Source PDF locale",
            source="consultant_upload",
            tag_article="Connexe",
            consultant_status="garde",
            source_json={"manual_upload_source": True},
        )
        db.add(article)
        db.commit()
        db.refresh(article)

        document = fitz.open()
        for _ in range(4):
            page = document.new_page()
            page.insert_textbox(
                fitz.Rect(50, 50, 545, 790),
                "Validation expérimentale SAR et comparaison aux mesures. " * 90,
                fontsize=9,
            )
        pdf_bytes = document.tobytes()
        document.close()
        upload = UploadFile(
            filename="preuve_sar.pdf",
            file=BytesIO(pdf_bytes),
        )

        result = asyncio.run(
            upload_and_extract_pdf_for_article(
                db=db,
                project=project,
                article_id=article.id,
                file=upload,
            )
        )
        db.refresh(article)

        assert result["ok"] is True
        assert result["saved_pdf"] is True
        assert result["text_chars"] > 1000
        assert uploaded_pdf_path(project, article).is_file()
        assert article.source_json["uploaded_pdf_available"] is True
    finally:
        db.close()
        engine.dispose()
