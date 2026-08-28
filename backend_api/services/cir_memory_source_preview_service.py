from __future__ import annotations

"""
Service d'ouverture des CIR de Memory V2.

Le frontend ne transmet jamais un chemin disque.
Il transmet uniquement l'identifiant Memory V2.
Le serveur relit son propre catalogue et résout le fichier source associé.
"""

import mimetypes
import zlib
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from services.experience_memory_v2_service import get_memory_v2_catalog

ALLOWED_SUFFIXES = {
    ".pdf",
    ".doc",
    ".docx",
    ".docm",
    ".txt",
    ".md",
}


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

    path = Path(raw_path).expanduser()

    if not path.is_absolute():
        root = Path(__file__).resolve().parents[2]
        path = root / path

    try:
        path = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Le CIR final n'existe plus sur le stockage : {Path(raw_path).name}",
        ) from exc

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
    source = _resolve_source_path(project)
    suffix = source.suffix.lower()

    if suffix == ".pdf":
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
        try:
            from routers.source_highlight import convert_office_to_pdf

            pdf = convert_office_to_pdf(
                source,
                _office_cache_bucket(memory_id),
            )

            return FileResponse(
                pdf,
                media_type="application/pdf",
                filename=f"{source.stem}.pdf",
                content_disposition_type="inline",
                headers={
                    "Cache-Control": "private, max-age=300",
                    "X-EnnoSmart-Memory-Source-Mode": "office-pdf",
                    "X-EnnoSmart-Memory-Original-Name": source.name,
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

    if suffix in {".txt", ".md"}:
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
    source = _resolve_source_path(project)

    return FileResponse(
        source,
        media_type=_media_type(source),
        filename=source.name,
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "no-store",
        },
    )
