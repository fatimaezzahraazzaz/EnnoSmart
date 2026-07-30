# -*- coding: utf-8 -*-
from __future__ import annotations

from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from db.database import SessionLocal

router = APIRouter(prefix="/projects", tags=["documents-db"])


def _safe_inline_filename(filename: str) -> str:
    filename = filename or "document"
    quoted = quote(filename)
    return f"inline; filename*=UTF-8''{quoted}"


@router.get("/{project_id}/documents/{document_id}/open-db")
def open_document_from_database(project_id: int, document_id: int):
    """
    Ouvre le vrai document depuis PostgreSQL.

    Le fichier complet est lu depuis :
    documents.file_data

    Pas depuis :
    storage/uploads
    """
    db = SessionLocal()

    try:
        row = db.execute(
            text("""
                SELECT
                    id,
                    project_id,
                    filename,
                    stored_filename,
                    content_type,
                    file_data,
                    file_size,
                    storage_mode
                FROM documents
                WHERE id = :document_id
                  AND project_id = :project_id
                LIMIT 1
            """),
            {
                "document_id": document_id,
                "project_id": project_id,
            },
        ).mappings().first()

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Document introuvable en base.",
            )

        file_data = row.get("file_data")

        if not file_data:
            raise HTTPException(
                status_code=404,
                detail="Le document existe, mais son contenu binaire file_data est vide.",
            )

        filename = (
            row.get("filename")
            or row.get("stored_filename")
            or f"document_{document_id}"
        )

        content_type = row.get("content_type") or "application/octet-stream"

        return StreamingResponse(
            BytesIO(bytes(file_data)),
            media_type=content_type,
            headers={
                "Content-Disposition": _safe_inline_filename(filename),
                "X-Document-Storage": row.get("storage_mode") or "database",
            },
        )

    finally:
        db.close()