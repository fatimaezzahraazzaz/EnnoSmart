# -*- coding: utf-8 -*-
from __future__ import annotations

from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import undefer

from sqlalchemy.orm import Session

from core.deps import get_current_user, get_db
from db.models import Document, User
from services.project_service import get_project_for_user
from services.document_storage_service import document_storage_source, read_document_bytes

router = APIRouter(prefix="/projects", tags=["documents-db"])


def _safe_inline_filename(filename: str) -> str:
    filename = filename or "document"
    quoted = quote(filename)
    return f"inline; filename*=UTF-8''{quoted}"


@router.get("/{project_id}/documents/{document_id}/open-db")
def open_document_from_database(
    project_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Ouvre le document via Storage V2, avec fallback PostgreSQL/disque legacy.
    """
    get_project_for_user(db, project_id, current_user)
    try:
        row = (
            db.query(Document)
            .options(undefer(Document.file_data))
            .filter(Document.id == document_id, Document.project_id == project_id)
            .first()
        )

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Document introuvable en base.",
            )

        try:
            file_data = read_document_bytes(row)
        except (FileNotFoundError, IOError) as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Le document existe, mais son contenu permanent est indisponible : {exc}",
            ) from exc

        filename = (
            row.filename
            or row.stored_filename
            or f"document_{document_id}"
        )

        content_type = row.mime_type or row.content_type or "application/octet-stream"

        return StreamingResponse(
            BytesIO(bytes(file_data)),
            media_type=content_type,
            headers={
                "Content-Disposition": _safe_inline_filename(filename),
                "X-Document-Storage": document_storage_source(row),
            },
        )

    except HTTPException:
        raise
