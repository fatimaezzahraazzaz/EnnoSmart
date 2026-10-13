from __future__ import annotations

"""
Service d'ouverture des CIR de Memory V2.

Le frontend ne transmet jamais un chemin disque.
Il transmet uniquement l'identifiant Memory V2.
Le serveur relit son propre catalogue et résout le fichier source associé.
"""

import mimetypes
import tempfile
import zlib
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from sqlalchemy.orm import undefer

from db.database import SessionLocal
from db.models import Document, MemoryV2Document, Project
from modules.common.runtime_paths import audit_root, resolve_persisted_path
from modules.common.storage_v2 import get_storage_service, sha256_file
from services.document_storage_service import read_document_bytes
from services.experience_memory_v2_service import get_memory_v2_catalog

ALLOWED_SUFFIXES = {
    ".pdf",
    ".doc",
    ".docx",
    ".docm",
    ".txt",
    ".md",
}


def _content_disposition(disposition: str, filename: str) -> str:
    """Build an RFC 5987 header that remains valid for non-ASCII CIR names."""
    safe_fallback = "".join(
        character if 32 <= ord(character) < 127 and character not in {'"', "\\"} else "_"
        for character in filename
    ) or "document"
    return (
        f'{disposition}; filename="{safe_fallback}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def _projects(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows = catalog.get("projects")
    return [
        row
        for row in rows
        if isinstance(row, dict)
    ] if isinstance(rows, list) else []


def _memory_project(memory_id: str) -> dict[str, Any]:
    catalog = get_memory_v2_catalog()

    for row in _projects(catalog):
        if str(row.get("id") or "") == str(memory_id):
            return row

    raise HTTPException(
        status_code=404,
        detail="Entrée Memory V2 introuvable.",
    )


def _source_rows(project: dict[str, Any]) -> list[dict[str, Any]]:
    rows = project.get("source_files")
    return [
        row
        for row in rows
        if isinstance(row, dict)
    ] if isinstance(rows, list) else []


def _primary_source(project: dict[str, Any]) -> dict[str, Any]:
    rows = _source_rows(project)

    if not rows:
        indexed_name = str(project.get("indexed_file_name") or "").strip()
        indexed_path = str(project.get("indexed_file_path") or indexed_name).strip()
        if indexed_name or indexed_path:
            return {
                "file_name": indexed_name or Path(indexed_path).name,
                "file_path": indexed_path,
                "sha256": str(project.get("source_hash") or "").strip().lower(),
            }
        raise HTTPException(
            status_code=404,
            detail="Aucun CIR final n'est rattaché à cette entrée Memory V2.",
        )

    indexed_name = str(
        project.get("indexed_file_name")
        or ""
    ).strip()

    if indexed_name:
        for row in rows:
            if str(row.get("file_name") or "") == indexed_name:
                return row

    return rows[0]


def _resolve_source_path(project: dict[str, Any]) -> Path:
    source = _primary_source(project)
    raw_path = str(source.get("file_path") or "").strip()

    if not raw_path:
        raise HTTPException(
            status_code=404,
            detail="Le catalogue ne contient pas le chemin du CIR final.",
        )

    rebased = resolve_persisted_path(raw_path)
    path = rebased or Path(raw_path).expanduser()

    if not path.is_absolute():
        root = Path(__file__).resolve().parents[2]
        path = root / path

    try:
        path = path.resolve(strict=True)
    except FileNotFoundError:
        digest = str(source.get("sha256") or project.get("source_hash") or "").strip().lower()
        filename = Path(str(source.get("file_name") or raw_path)).name
        if len(digest) == 64 and all(char in "0123456789abcdef" for char in digest):
            staged = audit_root() / "staging" / digest[:2] / digest / filename
            if staged.is_file() and sha256_file(staged) == digest:
                path = staged.resolve()
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"Le CIR final n'existe plus sur le stockage : {filename}",
                )
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Le CIR final n'existe plus sur le stockage : {filename}",
            )

    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="La source Memory V2 n'est pas un fichier.",
        )

    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"Format non pris en charge : {path.suffix or 'inconnu'}.",
        )

    return path


def _storage_v2_source(project: dict[str, Any]) -> tuple[bytes, str, str]:
    """Retrouve le CIR par identité PostgreSQL lorsque le chemin legacy a disparu."""
    source = _primary_source(project)
    wanted_name = str(
        source.get("file_name")
        or project.get("indexed_file_name")
        or ""
    ).strip()
    organisme = str(project.get("organisme") or "").strip()
    project_name = str(project.get("project") or project.get("project_name") or "").strip()
    subproject = str(project.get("subproject") or "").strip()
    year = str(project.get("year") or "").strip()
    db = SessionLocal()
    try:
        memory_id = str(project.get("id") or "").strip()
        if memory_id:
            memory_document = (
                db.query(MemoryV2Document)
                .filter(MemoryV2Document.memory_id == memory_id)
                .one_or_none()
            )
            if memory_document is not None:
                try:
                    service = get_storage_service(memory_document.storage_provider)
                    if service.verify_sha256(memory_document.storage_key, memory_document.sha256):
                        payload = service.get_bytes(memory_document.storage_key)
                        return (
                            payload,
                            memory_document.original_filename or memory_document.filename,
                            memory_document.mime_type or "application/octet-stream",
                        )
                except Exception:
                    # Le staging legacy reste le dernier fallback de transition.
                    pass

        query = (
            db.query(Document)
            .options(undefer(Document.file_data))
            .join(Project, Project.id == Document.project_id)
            .filter(Project.organisme == organisme)
        )
        if project_name:
            query = query.filter(Project.project_name == project_name)
        if year:
            query = query.filter(Project.year == year)
        if subproject:
            query = query.filter(Project.subproject_name == subproject)
        if wanted_name:
            query = query.filter(
                (Document.original_filename == wanted_name)
                | (Document.filename == wanted_name)
                | (Document.stored_filename == wanted_name)
            )
        rows = query.order_by(Document.created_at.desc()).all()
        for document in rows:
            try:
                payload = read_document_bytes(document)
                filename = str(document.original_filename or document.filename or wanted_name or f"document_{document.id}")
                media_type = str(document.mime_type or document.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream")
                return payload, filename, media_type
            except (FileNotFoundError, IOError):
                continue
    finally:
        db.close()
    raise HTTPException(
        status_code=404,
        detail="Le CIR final n'existe ni dans Storage V2, ni dans les fallbacks legacy.",
    )


def _resolve_source(project: dict[str, Any]) -> tuple[Path | None, bytes | None, str, str]:
    try:
        path = _resolve_source_path(project)
        return path, None, path.name, _media_type(path)
    except HTTPException as legacy_error:
        if legacy_error.status_code not in {404}:
            raise
    payload, filename, media_type = _storage_v2_source(project)
    return None, payload, filename, media_type


def _media_type(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return "application/pdf"

    if path.suffix.lower() in {".docx", ".docm"}:
        return (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )

    if path.suffix.lower() == ".doc":
        return "application/msword"

    if path.suffix.lower() == ".md":
        return "text/markdown; charset=utf-8"

    if path.suffix.lower() == ".txt":
        return "text/plain; charset=utf-8"

    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _office_cache_bucket(memory_id: str) -> int:
    # project_id uniquement utilisé comme namespace de cache par le renderer.
    return 700_000 + (zlib.crc32(memory_id.encode("utf-8")) % 200_000)


def build_memory_source_preview(memory_id: str):
    project = _memory_project(memory_id)
    source, payload, filename, media_type = _resolve_source(project)
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        if payload is not None:
            return StreamingResponse(
                BytesIO(payload),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": _content_disposition("inline", filename),
                    "Cache-Control": "private, max-age=300",
                    "X-EnnoSmart-Memory-Source-Mode": "storage-v2-pdf",
                },
            )
        return FileResponse(
            source,
            media_type="application/pdf",
            filename=source.name,
            content_disposition_type="inline",
            headers={
                "Cache-Control": "private, max-age=300",
                "X-EnnoSmart-Memory-Source-Mode": "source-pdf",
            },
        )

    if suffix in {".doc", ".docx", ".docm"}:
        temporary_path: Path | None = None
        try:
            from routers.source_highlight import convert_office_to_pdf

            office_source = source
            if office_source is None:
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                    handle.write(payload or b"")
                    temporary_path = Path(handle.name)
                office_source = temporary_path

            pdf = convert_office_to_pdf(
                office_source,
                _office_cache_bucket(memory_id),
            )

            return FileResponse(
                pdf,
                media_type="application/pdf",
                filename=f"{Path(filename).stem}.pdf",
                content_disposition_type="inline",
                headers={
                    "Cache-Control": "private, max-age=300",
                    "X-EnnoSmart-Memory-Source-Mode": "office-pdf",
                    "X-EnnoSmart-Memory-Original-Name": filename,
                },
            )

        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Le CIR Word existe, mais sa prévisualisation PDF "
                    f"n'a pas pu être générée : {exc}"
                ),
            ) from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    if suffix in {".txt", ".md"}:
        if payload is not None:
            return PlainTextResponse(
                payload.decode("utf-8", errors="ignore"),
                media_type=media_type,
                headers={
                    "Cache-Control": "private, max-age=300",
                    "X-EnnoSmart-Memory-Source-Mode": "storage-v2-text",
                },
            )
        return PlainTextResponse(
            source.read_text(
                encoding="utf-8",
                errors="ignore",
            ),
            media_type=_media_type(source),
            headers={
                "Cache-Control": "private, max-age=300",
                "X-EnnoSmart-Memory-Source-Mode": "text",
            },
        )

    raise HTTPException(
        status_code=415,
        detail="Format non pris en charge.",
    )


def build_memory_source_download(memory_id: str):
    project = _memory_project(memory_id)
    source, payload, filename, media_type = _resolve_source(project)

    if payload is not None:
        return StreamingResponse(
            BytesIO(payload),
            media_type=media_type,
            headers={
                "Content-Disposition": _content_disposition("attachment", filename),
                "Cache-Control": "no-store",
            },
        )

    return FileResponse(
        source,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "no-store",
        },
    )
