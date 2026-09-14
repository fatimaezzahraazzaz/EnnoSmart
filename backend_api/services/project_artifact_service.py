"""Persistance compacte des gros artefacts JSON de projet.

PostgreSQL conserve ici une seule copie gzip vérifiée. Les services historiques
qui exigent encore un ``Path`` peuvent matérialiser le JSON pour la durée de
leur traitement, puis la copie de travail est automatiquement supprimée.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, Iterator, Mapping

from sqlalchemy.orm import Session

from db.models import ProjectArtifact


ENCODING = "json-gzip-v1"
URI_PREFIX = "db+gzip://project-artifacts"
_materialization_locks: dict[str, Lock] = {}
_materialization_locks_guard = Lock()


def _clean_key(value: str) -> str:
    key = str(value or "").strip().replace("\\", "/").strip("/")
    if not key or len(key) > 700:
        raise ValueError("Clé d'artefact vide ou trop longue.")
    if any(part in {"", ".", ".."} for part in key.split("/")):
        raise ValueError("Clé d'artefact invalide.")
    if not re.fullmatch(r"[A-Za-z0-9._/@:-]+", key):
        raise ValueError("Clé d'artefact invalide.")
    return key


def artifact_uri(project_id: int, artifact_key: str) -> str:
    return f"{URI_PREFIX}/{int(project_id)}/{_clean_key(artifact_key)}"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(
        _json_safe(payload),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")


def _artifact_query(db: Session, project_id: int, artifact_key: str):
    return db.query(ProjectArtifact).filter(
        ProjectArtifact.project_id == int(project_id),
        ProjectArtifact.artifact_key == _clean_key(artifact_key),
    )


def save_json_artifact(
    db: Session,
    project_id: int,
    artifact_key: str,
    payload: Any,
    *,
    artifact_kind: str = "generated_json",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Crée ou remplace atomiquement un artefact dans la transaction courante."""

    key = _clean_key(artifact_key)
    raw = _json_bytes(payload)
    digest = hashlib.sha256(raw).hexdigest()
    compressed = gzip.compress(raw, compresslevel=6, mtime=0)
    row = _artifact_query(db, project_id, key).one_or_none()
    created = row is None
    if row is None:
        row = ProjectArtifact(
            project_id=int(project_id),
            artifact_key=key,
            artifact_kind=str(artifact_kind or "generated_json")[:100],
            encoding=ENCODING,
            content_sha256=digest,
            original_size=len(raw),
            stored_size=len(compressed),
            metadata_json=dict(metadata or {}),
            payload_data=compressed,
        )
        db.add(row)
    else:
        row.artifact_kind = str(artifact_kind or row.artifact_kind)[:100]
        row.encoding = ENCODING
        row.content_sha256 = digest
        row.original_size = len(raw)
        row.stored_size = len(compressed)
        row.metadata_json = dict(metadata or {})
        row.payload_data = compressed
    db.flush()
    return {
        "id": int(row.id),
        "project_id": int(project_id),
        "artifact_key": key,
        "artifact_kind": row.artifact_kind,
        "uri": artifact_uri(project_id, key),
        "content_sha256": digest,
        "original_size": len(raw),
        "stored_size": len(compressed),
        "compression_ratio": round(len(compressed) / max(1, len(raw)), 4),
        "created": created,
    }


def get_json_artifact(
    db: Session,
    project_id: int,
    artifact_key: str,
    *,
    default: Any = None,
) -> Any:
    row = _artifact_query(db, project_id, artifact_key).one_or_none()
    if row is None:
        return default
    if row.encoding != ENCODING:
        raise RuntimeError(f"Encodage d'artefact non supporté : {row.encoding}")
    raw = gzip.decompress(bytes(row.payload_data))
    digest = hashlib.sha256(raw).hexdigest()
    if digest != row.content_sha256:
        raise RuntimeError(
            f"Artefact PostgreSQL corrompu : project={project_id} key={artifact_key}"
        )
    return json.loads(raw.decode("utf-8"))


def json_artifact_exists(db: Session, project_id: int, artifact_key: str) -> bool:
    return _artifact_query(db, project_id, artifact_key).with_entities(
        ProjectArtifact.id
    ).first() is not None


def list_json_artifacts(
    db: Session,
    project_id: int,
    *,
    key_prefix: str = "",
    artifact_kind: str | None = None,
) -> list[dict[str, Any]]:
    query = db.query(
        ProjectArtifact.id,
        ProjectArtifact.artifact_key,
        ProjectArtifact.artifact_kind,
        ProjectArtifact.content_sha256,
        ProjectArtifact.original_size,
        ProjectArtifact.stored_size,
        ProjectArtifact.metadata_json,
        ProjectArtifact.created_at,
        ProjectArtifact.updated_at,
    ).filter(ProjectArtifact.project_id == int(project_id))
    if key_prefix:
        query = query.filter(
            ProjectArtifact.artifact_key.like(_clean_key(key_prefix) + "%")
        )
    if artifact_kind:
        query = query.filter(ProjectArtifact.artifact_kind == artifact_kind)
    rows = query.order_by(ProjectArtifact.created_at.asc()).all()
    return [
        {
            "id": int(row.id),
            "project_id": int(project_id),
            "artifact_key": row.artifact_key,
            "artifact_kind": row.artifact_kind,
            "uri": artifact_uri(project_id, row.artifact_key),
            "content_sha256": row.content_sha256,
            "original_size": int(row.original_size or 0),
            "stored_size": int(row.stored_size or 0),
            "metadata": dict(row.metadata_json or {}),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]


def import_json_file_if_needed(
    db: Session,
    project_id: int,
    artifact_key: str,
    path: Path,
    *,
    artifact_kind: str,
) -> bool:
    if json_artifact_exists(db, project_id, artifact_key) or not path.is_file():
        return False
    payload = json.loads(path.read_text(encoding="utf-8"))
    save_json_artifact(
        db,
        project_id,
        artifact_key,
        payload,
        artifact_kind=artifact_kind,
        metadata={"migrated_from": str(path)},
    )
    return True


@contextmanager
def materialized_json_artifact(
    db: Session,
    project_id: int,
    artifact_key: str,
    path: Path,
    *,
    artifact_kind: str,
    write_back: bool = False,
    required: bool = True,
) -> Iterator[Path]:
    """Expose temporairement un artefact DB à un module historique basé fichier."""

    path = Path(path)
    lock_key = str(path.resolve()).casefold()
    with _materialization_locks_guard:
        lock = _materialization_locks.setdefault(lock_key, Lock())
    with lock:
        import_json_file_if_needed(
            db,
            project_id,
            artifact_key,
            path,
            artifact_kind=artifact_kind,
        )
        payload = get_json_artifact(db, project_id, artifact_key, default=None)
        if payload is None:
            if required:
                raise FileNotFoundError(artifact_uri(project_id, artifact_key))
            yield path
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_json_bytes(payload))
        try:
            yield path
            if write_back and path.is_file():
                changed = json.loads(path.read_text(encoding="utf-8"))
                save_json_artifact(
                    db,
                    project_id,
                    artifact_key,
                    changed,
                    artifact_kind=artifact_kind,
                )
        finally:
            path.unlink(missing_ok=True)
