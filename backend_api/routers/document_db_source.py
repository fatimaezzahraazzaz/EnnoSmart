# -*- coding: utf-8 -*-
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote
import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session, undefer

from core.deps import get_current_user, get_db
from db.models import Document, User
from services.project_service import get_project_for_user
from services.document_storage_service import document_storage_source, read_document_bytes

router = APIRouter(prefix="/projects", tags=["documents-db-sources"])


class ResolveSourcesPayload(BaseModel):
    text: str | None = None
    names: list[str] | None = None


def _inline_filename(filename: str) -> str:
    filename = filename or "document"
    return f"inline; filename*=UTF-8''{quote(filename)}"


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = value.replace("œ", "oe")
    value = re.sub(r"\.[a-z0-9]{2,5}$", "", value)  # extension
    value = re.sub(r"_[a-f0-9]{10,16}$", "", value)  # suffix hash
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _doc_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "filename": row["filename"],
        "stored_filename": row["stored_filename"],
        "content_type": row["content_type"],
        "file_size": row["file_size"],
        "document_type": row["document_type"],
        "upload_status": row["upload_status"],
        "storage_mode": row["storage_mode"],
        "has_file_data": bool(row["has_file_data"]),
        "storage_provider": row.get("storage_provider"),
        "storage_key": row.get("storage_key"),
        "open_url": f"/projects/{row['project_id']}/source-documents/{row['id']}/open",
    }


def _load_project_documents(db, project_id: int) -> list[Any]:
    return list(
        db.execute(
            text(
                """
                SELECT
                    id,
                    project_id,
                    filename,
                    stored_filename,
                    content_type,
                    file_size,
                    document_type,
                    upload_status,
                    COALESCE(storage_mode, 'disk') AS storage_mode,
                    storage_provider,
                    storage_key,
                    CASE WHEN file_data IS NULL AND storage_key IS NULL THEN false ELSE true END AS has_file_data
                FROM documents
                WHERE project_id = :project_id
                ORDER BY id ASC
                """
            ),
            {"project_id": project_id},
        ).mappings().all()
    )


def _score_match(query_norm: str, doc_norms: list[str]) -> int:
    if not query_norm:
        return 0
    best = 0
    q_words = set(query_norm.split())
    for dn in doc_norms:
        if not dn:
            continue
        if query_norm == dn:
            best = max(best, 100)
        elif query_norm in dn or dn in query_norm:
            best = max(best, 92)
        else:
            d_words = set(dn.split())
            common = len(q_words & d_words)
            total = max(1, min(len(q_words), len(d_words)))
            ratio = common / total
            if ratio >= 0.70 and common >= 3:
                best = max(best, int(70 + ratio * 20))
    return best


def _extract_candidate_names(text_value: str) -> list[str]:
    """
    Extract document-like names from free text.
    Handles strings like:
    "Documents concernés : A.pdf, B.docx. Indices sources : ..."
    "Source 1 – Analyse des sources..."
    """
    text_value = text_value or ""
    candidates: list[str] = []

    # Exact names with extension
    for m in re.finditer(r"([^\n\r;,:]+?\.(?:pdf|docx|doc|xlsx|xls|pptx|ppt|png|jpg|jpeg|msg|txt))", text_value, flags=re.I):
        name = m.group(1).strip(" \t-–—•,.;:")
        if len(name) >= 4:
            candidates.append(name)

    # Names after Source X – ...
    for m in re.finditer(r"Source\s*\d+\s*[–\-:]\s*([^\n\r|]+)", text_value, flags=re.I):
        name = m.group(1).strip(" \t-–—•,.;:")
        # cut long prose after separators
        name = re.split(r"\s{2,}|\s+-\s+|\s+\|\s+", name)[0].strip()
        if len(name) >= 4:
            candidates.append(name)

    # Deduplicate preserving order
    out: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        key = _normalize(c)
        if key and key not in seen:
            seen.add(key)
            out.append(c)
    return out


@router.get("/{project_id}/source-documents")
def list_source_documents(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all real source documents for a project from PostgreSQL metadata."""
    get_project_for_user(db, project_id, current_user)
    rows = _load_project_documents(db, project_id)
    return {"project_id": project_id, "documents": [_doc_to_dict(r) for r in rows]}


@router.post("/{project_id}/source-documents/resolve")
def resolve_source_documents(
    project_id: int,
    payload: ResolveSourcesPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Resolve document names mentioned in a signal/verrou text to documents.id.
    This uses PostgreSQL documents table, not Chroma.
    """
    get_project_for_user(db, project_id, current_user)
    try:
        rows = _load_project_documents(db, project_id)
        doc_items: list[tuple[Any, list[str]]] = []
        for r in rows:
            doc_items.append(
                (
                    r,
                    [
                        _normalize(r["filename"]),
                        _normalize(r["stored_filename"]),
                    ],
                )
            )

        names = list(payload.names or [])
        if payload.text:
            names.extend(_extract_candidate_names(payload.text))

        # If text contains no candidate names, try matching the whole text against all docs
        if not names and payload.text:
            names = [payload.text]

        matches: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        for raw_name in names:
            qn = _normalize(raw_name)
            best_row = None
            best_score = 0
            for row, norms in doc_items:
                score = _score_match(qn, norms)
                if score > best_score:
                    best_score = score
                    best_row = row

            if best_row is not None and best_score >= 70 and best_row["id"] not in seen_ids:
                item = _doc_to_dict(best_row)
                item["matched_from"] = raw_name
                item["match_score"] = best_score
                matches.append(item)
                seen_ids.add(best_row["id"])

        return {
            "project_id": project_id,
            "input_names": names,
            "matches": matches,
        }
    except HTTPException:
        raise


@router.get("/{project_id}/source-documents/{document_id}/open")
def open_source_document_from_database(
    project_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Ouvre l'original via Storage V2, puis PostgreSQL/disque legacy.
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
            raise HTTPException(status_code=404, detail="Document introuvable en base.")

        try:
            file_data = read_document_bytes(row)
        except (FileNotFoundError, IOError) as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Le document existe, mais son contenu permanent est indisponible : {exc}",
            ) from exc

        filename = row.filename or row.stored_filename or f"document_{document_id}"
        content_type = row.mime_type or row.content_type or "application/octet-stream"

        return StreamingResponse(
            BytesIO(bytes(file_data)),
            media_type=content_type,
            headers={
                "Content-Disposition": _inline_filename(filename),
                "X-Document-Storage": document_storage_source(row),
                "X-Document-Id": str(document_id),
            },
        )
    except HTTPException:
        raise
