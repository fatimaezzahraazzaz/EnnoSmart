# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
for import_path in (ROOT_DIR, BACKEND_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env", override=False)
    load_dotenv(BACKEND_DIR / ".env", override=False)
except Exception:
    pass

from sqlalchemy.orm import undefer

from db.database import Base, SessionLocal, engine, ensure_runtime_schema
from db.models import Document, MemoryV2Document, Project, StorageMigrationEvent
from modules.common.runtime_paths import audit_root, data_root, experience_memory_root, storage_root
from modules.common.storage_v2 import (
    StorageScope,
    build_storage_key,
    get_storage_service,
    sha256_bytes,
    sha256_file,
    storage_v2_enabled,
)
from services.document_storage_service import apply_storage_metadata


PERMANENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".docm", ".odt", ".rtf",
    ".xls", ".xlsx", ".xlsm", ".ods",
    ".ppt", ".pptx", ".pptm", ".odp",
    ".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp", ".webp",
    ".msg", ".eml", ".txt", ".md",
}
LEGACY_SCAN_EXCLUDED_PARTS = {
    "chroma",
    "_legacy_chroma_collections",
    "_chroma_global_staging_20260823_172151",
    "_chroma_global_staging_20260824_162046",
    "chunks",
    "nlp",
    "extraction",
    "cards",
    "relations",
    "previews",
    "cache",
    "temp",
}


@dataclass
class Candidate:
    document_id: int
    project_id: int
    organisme_id: str
    subproject: str
    year: str
    filename: str
    source_type: str
    source_path: str | None
    size_bytes: int
    sha256: str
    storage_key: str
    status: str
    duplicate_of: int | None = None
    error: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _legacy_source(document: Document) -> tuple[str, bytes | Path]:
    payload = bytes(document.file_data or b"")
    if payload:
        return "postgresql_bytea", payload
    raw_path = str(document.file_path or "").strip()
    if raw_path and "://" not in raw_path:
        path = Path(raw_path).expanduser()
        if path.is_file():
            return "legacy_disk", path.resolve()
    raise FileNotFoundError("Aucune source BYTEA ou disque legacy accessible.")


def _source_identity(source: bytes | Path) -> tuple[int, str]:
    if isinstance(source, bytes):
        return len(source), sha256_bytes(source)
    return int(source.stat().st_size), sha256_file(source)


def _scope(project: Project, source_kind: str = "project_document") -> StorageScope:
    return StorageScope(
        organisme_id=str(project.organisme),
        project_id=int(project.id),
        subproject=str(project.subproject_name or ""),
        year=str(project.year or ""),
        source_kind=source_kind,
    )


def _event(
    db,
    *,
    document_id: int | None,
    event_type: str,
    status: str,
    storage_provider: str | None,
    storage_key: str | None,
    sha256: str | None,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        StorageMigrationEvent(
            document_id=document_id,
            event_type=event_type,
            status=status,
            storage_provider=storage_provider,
            storage_key=storage_key,
            sha256=sha256,
            details_json=details or {},
        )
    )


def _discover_legacy_files(known_hashes: dict[str, int]) -> dict[str, Any]:
    roots = [storage_root() / "organismes", storage_root() / "experience_memory_v2"]
    detected = 0
    total_bytes = 0
    errors: list[dict[str, str]] = []
    needs_review: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    duplicates = 0
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(root)
                lowered = {part.lower() for part in relative.parts}
                if lowered & LEGACY_SCAN_EXCLUDED_PARTS:
                    continue
                if path.suffix.lower() not in PERMANENT_EXTENSIONS:
                    continue
                detected += 1
                size = int(path.stat().st_size)
                total_bytes += size
                digest = sha256_file(path)
                duplicate_of = known_hashes.get(digest)
                duplicate_path = seen.get(digest)
                if duplicate_of is not None or duplicate_path is not None:
                    duplicates += 1
                if digest not in seen:
                    seen[digest] = str(path.resolve())
                if duplicate_of is None:
                    needs_review.append(
                        {
                            "path": str(path.resolve()),
                            "size_bytes": size,
                            "sha256": digest,
                            "classification": "NEEDS_REVIEW",
                            "reason": "permanent_file_without_reliable_postgresql_document_mapping",
                            "duplicate_path": duplicate_path,
                        }
                    )
            except Exception as exc:
                errors.append({"path": str(path), "error": str(exc)})
    return {
        "files_detected": detected,
        "unique_sha256": len(seen),
        "duplicates": duplicates,
        "size_bytes": total_bytes,
        "needs_review": needs_review,
        "errors": errors,
    }


def _identity_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", text) or "unknown"


def _memory_id(run: dict[str, Any]) -> str:
    return "::".join(
        (
            _identity_key(run.get("organisme")),
            _identity_key(run.get("project")),
            _identity_key(run.get("subproject")),
            str(run.get("year") or "unknown").strip(),
        )
    )


@lru_cache(maxsize=1)
def _readonly_source_name_index() -> dict[str, tuple[Path, ...]]:
    """Index filename candidates from the configured read-only source.

    The source tree is never written to.  Candidates are still accepted only
    when their content SHA-256 equals the hash recorded by Memory V2.
    """

    configured = str(
        os.getenv("POWER_AUTOMATE_IMPORT_ROOT")
        or os.getenv("POWER_AUTOMATE_SOURCE_ROOT")
        or ""
    ).strip()
    root = Path(configured).expanduser() if configured else None
    if root is None or not root.is_dir():
        return {}
    found: dict[str, list[Path]] = {}
    for current, _directories, filenames in os.walk(root):
        base = Path(current)
        for filename in filenames:
            found.setdefault(filename.casefold(), []).append(base / filename)
    return {name: tuple(paths) for name, paths in found.items()}


def _memory_source(run: dict[str, Any]) -> tuple[Path, int, str]:
    digest = str(run.get("source_hash") or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("source_hash Memory V2 invalide")
    filename = Path(str(run.get("file_name") or run.get("file") or "document")).name
    candidates: list[Path] = []
    raw = str(run.get("file") or "").strip()
    if raw:
        candidates.append(Path(raw).expanduser())
    candidates.append(audit_root() / "staging" / digest[:2] / digest / filename)
    # Réserves historiques créées avant l'unification de STORAGE_ROOT. Elles
    # sont consultées en lecture seule afin de récupérer tous les CIR déjà
    # indexés avant de nettoyer les copies locales.
    candidates.extend(
        (
            data_root() / "power_automate_import" / "staging" / digest[:2] / digest / filename,
            data_root() / "cir_index_preparation" / "objects" / digest[:2] / digest / filename,
        )
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        size = int(candidate.stat().st_size)
        actual = sha256_file(candidate)
        if actual != digest:
            raise ValueError(f"SHA-256 Memory V2 invalide : {candidate}")
        return candidate.resolve(), size, digest
    # Dernier recours : la source OneDrive/SharePoint configurée, en lecture
    # seule et avec validation stricte du hash historique.
    for candidate in _readonly_source_name_index().get(filename.casefold(), ()):
        try:
            if candidate.is_file() and sha256_file(candidate) == digest:
                return candidate.resolve(), int(candidate.stat().st_size), digest
        except OSError:
            continue
    raise FileNotFoundError(f"Source Memory V2 introuvable : {filename}")


def _matching_project(projects: list[Project], run: dict[str, Any]) -> Project | None:
    wanted = (
        _identity_key(run.get("organisme")),
        _identity_key(run.get("project")),
        _identity_key(run.get("subproject")),
        str(run.get("year") or "").strip(),
    )
    matches = [
        project
        for project in projects
        if (
            _identity_key(project.organisme),
            _identity_key(project.project_name),
            _identity_key(project.subproject_name),
            str(project.year or "").strip(),
        )
        == wanted
    ]
    return matches[0] if len(matches) == 1 else None


def _migrate_memory_v2_documents(
    db,
    *,
    projects: list[Project],
    apply: bool,
    provider: str | None,
) -> dict[str, Any]:
    runs_dir = experience_memory_root() / "runs"
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    total_bytes = 0
    unique_bytes = 0
    duplicates = 0
    migrated = 0
    already_migrated = 0
    detected = 0
    if not runs_dir.is_dir():
        return {
            "files_detected": 0,
            "files_unique": 0,
            "duplicates": 0,
            "size_to_migrate_bytes": 0,
            "migrated_this_run": 0,
            "already_migrated": 0,
            "candidates": [],
            "errors": [],
        }

    selected_runs: dict[str, tuple[Path, dict[str, Any]]] = {}
    for run_path in sorted(runs_dir.glob("*.run_v2.json")):
        try:
            run = json.loads(run_path.read_text(encoding="utf-8"))
            if not isinstance(run, dict) or not run.get("ok"):
                continue
            memory_id = _memory_id(run)
            current = selected_runs.get(memory_id)
            if current is None or run_path.stat().st_mtime_ns > current[0].stat().st_mtime_ns:
                selected_runs[memory_id] = (run_path, run)
        except Exception as exc:
            errors.append(
                {
                    "entity_type": "memory_v2_document",
                    "run_path": str(run_path),
                    "classification": "NEEDS_REVIEW",
                    "error": str(exc),
                }
            )

    for memory_id, (run_path, run) in sorted(selected_runs.items()):
        try:
            detected += 1
            filename = Path(str(run.get("file_name") or run.get("file") or "document")).name
            row = db.query(MemoryV2Document).filter(MemoryV2Document.memory_id == memory_id).one_or_none()
            if row is not None:
                source = Path(str(row.legacy_path or filename))
                size = int(row.size_bytes)
                digest = str(row.sha256).lower()
            else:
                source, size, digest = _memory_source(run)
            duplicate_of = seen.get(digest)
            total_bytes += size
            if duplicate_of is None:
                seen[digest] = memory_id
                unique_bytes += size
            else:
                duplicates += 1
            source_kind = "memory_v2_document"
            scope = StorageScope(
                organisme_id=str(run.get("organisme") or "unknown"),
                project_id=_identity_key(run.get("project")),
                subproject=str(run.get("subproject") or ""),
                year=str(run.get("year") or ""),
                source_kind=source_kind,
            )
            key = build_storage_key(scope, digest)
            status = "already_migrated" if row is not None else "planned"
            if row is not None:
                already_migrated += 1
                key = row.storage_key

            item = {
                "entity_type": "memory_v2_document",
                "memory_id": memory_id,
                "project_id": row.project_id if row is not None else None,
                "organisme_id": str(run.get("organisme") or "unknown"),
                "project_name": str(run.get("project") or "unknown"),
                "subproject": str(run.get("subproject") or ""),
                "year": str(run.get("year") or ""),
                "filename": filename,
                "source_path": str(source),
                "size_bytes": size,
                "sha256": digest,
                "storage_key": key,
                "status": status,
                "duplicate_of": duplicate_of,
            }

            if apply and row is None:
                service = get_storage_service(provider)
                content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                metadata = service.store_file(
                    source,
                    storage_key=key,
                    content_type=content_type,
                    expected_sha256=digest,
                    metadata={
                        "memory-id": memory_id,
                        "organisme-id": str(run.get("organisme") or "unknown"),
                        "source-kind": source_kind,
                    },
                )
                project = _matching_project(projects, run)
                row = MemoryV2Document(
                    memory_id=memory_id,
                    project_id=project.id if project is not None else None,
                    organisme_id=str(run.get("organisme") or "unknown"),
                    project_name=str(run.get("project") or "unknown"),
                    subproject=str(run.get("subproject") or "") or None,
                    year=str(run.get("year") or "") or None,
                    filename=filename,
                    original_filename=filename,
                    mime_type=content_type,
                    size_bytes=metadata.size_bytes,
                    sha256=digest,
                    storage_provider=metadata.provider,
                    storage_key=metadata.storage_key,
                    source_kind=source_kind,
                    legacy_path=str(source),
                    migration_status="copied_verified",
                )
                db.add(row)
                _event(
                    db,
                    document_id=None,
                    event_type="memory_v2_copy",
                    status="verified",
                    storage_provider=metadata.provider,
                    storage_key=metadata.storage_key,
                    sha256=digest,
                    details={"memory_id": memory_id, "legacy_path": str(source)},
                )
                db.commit()
                migrated += 1
                item["status"] = "copied_verified_metadata_recorded"
                item["project_id"] = project.id if project is not None else None
            candidates.append(item)
        except Exception as exc:
            db.rollback()
            errors.append(
                {
                    "entity_type": "memory_v2_document",
                    "run_path": str(run_path),
                    "classification": "NEEDS_REVIEW",
                    "error": str(exc),
                }
            )

    return {
        "files_detected": detected,
        "files_unique": len(seen),
        "duplicates": duplicates,
        "source_size_bytes": total_bytes,
        "size_to_migrate_bytes": unique_bytes,
        "migrated_this_run": migrated,
        "already_migrated": already_migrated,
        "candidates": candidates,
        "errors": errors,
    }


def run_migration(*, apply: bool = False, verify: bool = False, provider: str | None = None) -> dict[str, Any]:
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()
    db = SessionLocal()
    report: dict[str, Any] = {
        "version": "storage-v2-migration-v1",
        "mode": "verify" if verify else "apply" if apply else "dry-run",
        "started_at": _now(),
        "storage_root": str(storage_root().resolve()),
        "provider": provider or os.getenv("ENNOSMART_OBJECT_STORAGE_PROVIDER", "local"),
        "candidates": [],
        "errors": [],
        "safe_to_delete": [],
        "needs_review": [],
    }
    try:
        if apply and not storage_v2_enabled():
            raise RuntimeError(
                "--apply refusé : ENNOSMART_STORAGE_V2_ENABLED=true est obligatoire."
            )

        if verify:
            documents = (
                db.query(Document)
                .filter(Document.storage_key.isnot(None))
                .order_by(Document.id.asc())
                .all()
            )
            verified_count = 0
            migrated_count = len(documents)
            for document in documents:
                expected = str(document.sha256 or document.file_sha256 or "").strip().lower()
                item = {
                    "document_id": int(document.id),
                    "storage_provider": document.storage_provider,
                    "storage_key": document.storage_key,
                    "sha256": expected,
                    "verified": False,
                }
                try:
                    document_service = get_storage_service(str(document.storage_provider or provider or ""))
                    metadata = document_service.get_metadata(str(document.storage_key))
                    expected_size = int(document.size_bytes or document.file_size or 0)
                    size_ok = not expected_size or metadata.size_bytes == expected_size
                    sha_ok = bool(expected) and document_service.verify_sha256(str(document.storage_key), expected)
                    item.update(
                        {
                            "exists": True,
                            "size_bytes": metadata.size_bytes,
                            "size_ok": size_ok,
                            "sha256_ok": sha_ok,
                            "verified": bool(size_ok and sha_ok),
                        }
                    )
                    if item["verified"]:
                        verified_count += 1
                        _event(
                            db,
                            document_id=document.id,
                            event_type="verify",
                            status="verified",
                            storage_provider=document.storage_provider,
                            storage_key=document.storage_key,
                            sha256=expected,
                            details={"size_bytes": metadata.size_bytes},
                        )
                    else:
                        report["needs_review"].append(item)
                except Exception as exc:
                    item["error"] = str(exc)
                    report["errors"].append(item)
                    report["needs_review"].append(item)
                report["candidates"].append(item)
            memory_documents = db.query(MemoryV2Document).order_by(MemoryV2Document.id.asc()).all()
            memory_verified = 0
            for memory_document in memory_documents:
                item = {
                    "entity_type": "memory_v2_document",
                    "memory_id": memory_document.memory_id,
                    "storage_provider": memory_document.storage_provider,
                    "storage_key": memory_document.storage_key,
                    "sha256": memory_document.sha256,
                    "verified": False,
                }
                try:
                    memory_service = get_storage_service(memory_document.storage_provider)
                    metadata = memory_service.get_metadata(memory_document.storage_key)
                    size_ok = metadata.size_bytes == int(memory_document.size_bytes)
                    sha_ok = memory_service.verify_sha256(
                        memory_document.storage_key, memory_document.sha256
                    )
                    item.update(
                        {
                            "exists": True,
                            "size_bytes": metadata.size_bytes,
                            "size_ok": size_ok,
                            "sha256_ok": sha_ok,
                            "verified": bool(size_ok and sha_ok),
                        }
                    )
                    if item["verified"]:
                        memory_verified += 1
                        _event(
                            db,
                            document_id=None,
                            event_type="memory_v2_verify",
                            status="verified",
                            storage_provider=memory_document.storage_provider,
                            storage_key=memory_document.storage_key,
                            sha256=memory_document.sha256,
                            details={"memory_id": memory_document.memory_id},
                        )
                    else:
                        report["needs_review"].append(item)
                except Exception as exc:
                    item["error"] = str(exc)
                    report["errors"].append(item)
                    report["needs_review"].append(item)
                report["candidates"].append(item)
            db.commit()
            total_all = migrated_count + len(memory_documents)
            verified_all = verified_count + memory_verified
            report["summary"] = {
                "documents_migrated": migrated_count,
                "documents_verified": verified_count,
                "memory_v2_documents_migrated": len(memory_documents),
                "memory_v2_documents_verified": memory_verified,
                "total_migrated": total_all,
                "verified": verified_all,
                "verification_errors": total_all - verified_all,
                "verified_equals_total_migrated": verified_all == total_all,
            }
            return report

        documents = (
            db.query(Document)
            .options(undefer(Document.file_data))
            .join(Project, Project.id == Document.project_id)
            .order_by(Document.id.asc())
            .all()
        )
        projects = {project.id: project for project in db.query(Project).all()}
        seen_hashes: dict[str, int] = {}
        total_bytes = 0
        unique_bytes = 0
        duplicate_count = 0
        migrated = 0
        already_migrated = 0
        ignored = 0

        for document in documents:
            project = projects.get(document.project_id)
            if project is None:
                ignored += 1
                report["needs_review"].append(
                    {"document_id": document.id, "reason": "project_missing"}
                )
                continue
            try:
                recorded_digest = str(document.sha256 or document.file_sha256 or "").strip().lower()
                if document.storage_key and recorded_digest and document.size_bytes is not None:
                    source_type, source = "storage_v2", None
                    size, digest = int(document.size_bytes), recorded_digest
                else:
                    source_type, source = _legacy_source(document)
                    size, digest = _source_identity(source)
                    if recorded_digest and recorded_digest != digest:
                        raise ValueError("SHA-256 source différent de la métadonnée PostgreSQL.")
                source_kind = str(document.source_kind or "project_document")
                key = build_storage_key(_scope(project, source_kind), digest)
                duplicate_of = seen_hashes.get(digest)
                total_bytes += size
                if duplicate_of is None:
                    seen_hashes[digest] = int(document.id)
                    unique_bytes += size
                else:
                    duplicate_count += 1

                status = "already_migrated" if document.storage_key else "planned"
                if document.storage_key:
                    already_migrated += 1
                candidate = Candidate(
                    document_id=int(document.id),
                    project_id=int(project.id),
                    organisme_id=str(project.organisme),
                    subproject=str(project.subproject_name or ""),
                    year=str(project.year or ""),
                    filename=str(document.filename or document.stored_filename or f"document_{document.id}"),
                    source_type=source_type,
                    source_path=str(source) if isinstance(source, Path) else None,
                    size_bytes=size,
                    sha256=digest,
                    storage_key=str(document.storage_key or key),
                    status=status,
                    duplicate_of=duplicate_of,
                )

                if apply and not document.storage_key:
                    content_type = str(
                        document.mime_type
                        or document.content_type
                        or mimetypes.guess_type(candidate.filename)[0]
                        or "application/octet-stream"
                    )
                    service = get_storage_service(provider)
                    metadata = service.store_file(
                        source,
                        storage_key=key,
                        content_type=content_type,
                        expected_sha256=digest,
                        metadata={
                            "document-id": str(document.id),
                            "project-id": str(project.id),
                            "organisme-id": str(project.organisme),
                            "source-kind": source_kind,
                        },
                    )
                    apply_storage_metadata(
                        document,
                        project,
                        metadata,
                        filename=candidate.filename,
                        source_kind=source_kind,
                    )
                    # Les sources legacy restent volontairement intactes.
                    candidate.status = "copied_verified_metadata_recorded"
                    _event(
                        db,
                        document_id=document.id,
                        event_type="copy",
                        status="verified",
                        storage_provider=metadata.provider,
                        storage_key=metadata.storage_key,
                        sha256=digest,
                        details={"source_type": source_type, "source_path": candidate.source_path},
                    )
                    db.commit()
                    migrated += 1
                report["candidates"].append(asdict(candidate))
            except Exception as exc:
                db.rollback()
                ignored += 1
                item = {
                    "document_id": int(document.id),
                    "project_id": int(document.project_id),
                    "filename": str(document.filename or ""),
                    "classification": "NEEDS_REVIEW",
                    "error": str(exc),
                }
                report["errors"].append(item)
                report["needs_review"].append(item)

        memory_v2 = _migrate_memory_v2_documents(
            db,
            projects=list(projects.values()),
            apply=apply,
            provider=provider,
        )
        report["memory_v2_inventory"] = {
            key: value
            for key, value in memory_v2.items()
            if key not in {"candidates", "errors"}
        }
        report["candidates"].extend(memory_v2["candidates"])
        report["errors"].extend(memory_v2["errors"])
        report["needs_review"].extend(memory_v2["errors"])

        legacy = _discover_legacy_files(seen_hashes)
        report["legacy_disk_inventory"] = legacy
        report["needs_review"].extend(legacy["needs_review"])
        report["errors"].extend(legacy["errors"])
        report["summary"] = {
            "files_detected": len(documents) + memory_v2["files_detected"],
            "files_unique": len(seen_hashes) + memory_v2["files_unique"],
            "duplicates": duplicate_count + memory_v2["duplicates"],
            "size_to_migrate_bytes": unique_bytes + memory_v2["size_to_migrate_bytes"],
            "source_size_bytes": total_bytes + memory_v2["source_size_bytes"],
            "files_ignored": ignored + len(memory_v2["errors"]),
            "errors": len(report["errors"]),
            "already_migrated": already_migrated + memory_v2["already_migrated"],
            "migrated_this_run": migrated + memory_v2["migrated_this_run"],
            "project_documents_detected": len(documents),
            "memory_v2_documents_detected": memory_v2["files_detected"],
            "legacy_files_detected": legacy["files_detected"],
            "legacy_files_needs_review": len(legacy["needs_review"]),
        }
        return report
    except Exception as exc:
        db.rollback()
        report["errors"].append({"fatal": str(exc)})
        report["summary"] = {
            "files_detected": 0,
            "files_unique": 0,
            "duplicates": 0,
            "size_to_migrate_bytes": 0,
            "files_ignored": 0,
            "errors": len(report["errors"]),
        }
        return report
    finally:
        db.close()
        report["completed_at"] = _now()


def _write_report(report: dict[str, Any], report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = report_dir / f"storage_v2_migration_{stamp}_{report['mode']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Migration EnnoSmart Storage V2 (dry-run par défaut).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Copie, vérifie puis enregistre les métadonnées. Ne supprime aucune source.")
    mode.add_argument("--verify", action="store_true", help="Vérifie taille et SHA-256 de tous les documents migrés.")
    parser.add_argument("--provider", choices=["local", "s3"], default=None)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=data_root() / "logs" / "storage_v2_migration",
        help="Dossier des rapports JSON.",
    )
    parser.add_argument("--show-files", action="store_true")
    args = parser.parse_args()

    report = run_migration(apply=args.apply, verify=args.verify, provider=args.provider)
    try:
        report_path = _write_report(report, args.report_dir)
    except Exception as exc:
        report_path = None
        report.setdefault("errors", []).append({"report_write": str(exc)})

    summary = report.get("summary") or {}
    print("=" * 78)
    print(f"EnnoSmart Storage V2 migration — {report['mode']}")
    for key in (
        "files_detected", "files_unique", "duplicates", "size_to_migrate_bytes",
        "files_ignored", "errors", "already_migrated", "migrated_this_run",
        "total_migrated", "verified", "verified_equals_total_migrated",
    ):
        if key in summary:
            print(f"{key}: {summary[key]}")
    if report_path:
        print(f"report: {report_path.resolve()}")
    if args.show_files:
        for item in report.get("candidates") or []:
            print(json.dumps(item, ensure_ascii=False, default=str))
    print("SAFE_TO_DELETE: 0 (la première migration ne supprime jamais les sources)")
    print("=" * 78)
    fatal_or_errors = bool(report.get("errors"))
    if args.verify and not summary.get("verified_equals_total_migrated", False):
        return 2
    return 1 if fatal_or_errors and (args.apply or args.verify) else 0


if __name__ == "__main__":
    raise SystemExit(main())
