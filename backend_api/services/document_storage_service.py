from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from modules.common.storage_v2 import (
    StorageObjectMetadata,
    StorageScope,
    get_storage_service,
    sha256_bytes,
    storage_v2_enabled,
)


logger = logging.getLogger(__name__)


def _value(instance: Any, name: str, fallback: Any = None) -> Any:
    value = getattr(instance, name, None)
    return fallback if value is None else value


def document_expected_sha256(document: Any) -> str:
    return str(_value(document, "sha256") or _value(document, "file_sha256") or "").strip().lower()


def store_document_bytes(
    project: Any,
    payload: bytes,
    *,
    filename: str,
    content_type: str,
    source_kind: str = "document",
) -> StorageObjectMetadata:
    if not storage_v2_enabled():
        raise RuntimeError("Storage V2 est désactivé (ENNOSMART_STORAGE_V2_ENABLED=false).")
    digest = sha256_bytes(payload)
    scope = StorageScope(
        organisme_id=str(_value(project, "organisme", "unknown")),
        project_id=int(_value(project, "id")),
        subproject=str(_value(project, "subproject_name", "") or ""),
        year=str(_value(project, "year", "") or ""),
        source_kind=source_kind,
    )
    return get_storage_service().store_file(
        payload,
        scope=scope,
        content_type=content_type,
        expected_sha256=digest,
        metadata={
            "filename": Path(filename or "document").name,
            "project-id": str(_value(project, "id")),
            "organisme-id": str(_value(project, "organisme", "unknown")),
            "source-kind": source_kind,
        },
    )


def apply_storage_metadata(
    document: Any,
    project: Any,
    metadata: StorageObjectMetadata,
    *,
    filename: str,
    source_kind: str,
) -> None:
    document.organisme_id = str(_value(project, "organisme", "unknown"))
    document.subproject = str(_value(project, "subproject_name", "") or "") or None
    document.year = str(_value(project, "year", "") or "") or None
    document.original_filename = filename
    document.mime_type = metadata.content_type
    document.size_bytes = int(metadata.size_bytes)
    document.sha256 = metadata.sha256
    document.storage_provider = metadata.provider
    document.storage_key = metadata.storage_key
    document.source_kind = source_kind
    document.storage_mode = "storage_v2"


def _storage_service_for_document(document: Any):
    provider = str(_value(document, "storage_provider", "") or "").strip().lower()
    if provider not in {"local", "s3"}:
        return None
    return get_storage_service(provider)


def _verified_v2_bytes(document: Any) -> bytes | None:
    storage_key = str(_value(document, "storage_key", "") or "").strip()
    service = _storage_service_for_document(document)
    if not storage_key or service is None:
        return None
    try:
        if not service.exists(storage_key):
            return None
        payload = service.get_bytes(storage_key)
        expected = document_expected_sha256(document)
        if expected and sha256_bytes(payload) != expected:
            raise IOError(f"SHA-256 Storage V2 invalide pour {storage_key}")
        expected_size = int(_value(document, "size_bytes") or _value(document, "file_size") or 0)
        if expected_size and len(payload) != expected_size:
            raise IOError(f"Taille Storage V2 invalide pour {storage_key}")
        return payload
    except Exception as exc:
        logger.error("Lecture Storage V2 impossible, fallback legacy: %s", exc)
        return None


def read_document_bytes(document: Any) -> bytes:
    """Lit Storage V2, puis BYTEA PostgreSQL, puis l'ancien chemin local."""

    payload = _verified_v2_bytes(document)
    if payload is not None:
        return payload

    legacy_db = bytes(_value(document, "file_data", b"") or b"")
    if legacy_db:
        expected = document_expected_sha256(document)
        if expected and sha256_bytes(legacy_db) != expected:
            raise IOError("Le SHA-256 du fallback PostgreSQL est invalide.")
        return legacy_db

    raw_path = str(_value(document, "file_path", "") or "").strip()
    if raw_path and "://" not in raw_path:
        path = Path(raw_path)
        if path.is_file():
            payload = path.read_bytes()
            expected = document_expected_sha256(document)
            if expected and sha256_bytes(payload) != expected:
                raise IOError("Le SHA-256 du fallback disque legacy est invalide.")
            return payload

    raise FileNotFoundError("Aucun contenu Storage V2, PostgreSQL legacy ou disque legacy disponible.")


def document_storage_source(document: Any) -> str:
    storage_key = str(_value(document, "storage_key", "") or "").strip()
    service = _storage_service_for_document(document)
    if storage_key and service is not None:
        try:
            if service.exists(storage_key):
                return f"storage_v2:{service.provider.name}"
        except Exception:
            pass
    if bytes(_value(document, "file_data", b"") or b""):
        return "legacy:database"
    raw_path = str(_value(document, "file_path", "") or "").strip()
    if raw_path and "://" not in raw_path and Path(raw_path).is_file():
        return "legacy:disk"
    return "missing"


def iter_document_bytes(document: Any, *, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    storage_key = str(_value(document, "storage_key", "") or "").strip()
    service = _storage_service_for_document(document)
    if storage_key and service is not None:
        try:
            if service.exists(storage_key):
                yield from service.iter_bytes(storage_key, chunk_size=chunk_size)
                return
        except Exception as exc:
            logger.error("Streaming Storage V2 impossible, fallback legacy: %s", exc)
    payload = read_document_bytes(document)
    for offset in range(0, len(payload), chunk_size):
        yield payload[offset : offset + chunk_size]


@contextmanager
def materialize_document(document: Any, *, suffix: str = "") -> Iterator[Path]:
    storage_key = str(_value(document, "storage_key", "") or "").strip()
    service = _storage_service_for_document(document)
    if storage_key and service is not None:
        try:
            if service.exists(storage_key):
                with service.materialize_temp_file(storage_key, suffix=suffix) as path:
                    yield path
                    return
        except Exception as exc:
            logger.error("Matérialisation Storage V2 impossible, fallback legacy: %s", exc)

    fd, name = tempfile.mkstemp(prefix="ennosmart-document-", suffix=suffix)
    os.close(fd)
    path = Path(name)
    try:
        path.write_bytes(read_document_bytes(document))
        yield path
    finally:
        path.unlink(missing_ok=True)

