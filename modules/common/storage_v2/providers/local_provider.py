from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator, Mapping

from ..integrity import sha256_file, validate_sha256
from ..models import StorageObjectMetadata
from .base import StorageProvider


class LocalStorageProvider(StorageProvider):
    """Provider compatible destiné aux tests et développements locaux."""

    name = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, storage_key: str) -> Path:
        key = str(storage_key or "").replace("\\", "/").strip("/")
        if not key or any(part in {"", ".", ".."} for part in key.split("/")):
            raise ValueError("Clé Storage V2 invalide.")
        candidate = (self.root / Path(key)).resolve()
        candidate.relative_to(self.root)
        return candidate

    def _metadata_path(self, object_path: Path) -> Path:
        return object_path.with_name(object_path.name + ".storage-v2.json")

    def put_file(
        self,
        storage_key: str,
        source_path: Path,
        *,
        content_type: str,
        sha256: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StorageObjectMetadata:
        source = Path(source_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        expected = validate_sha256(sha256)
        if sha256_file(source) != expected:
            raise ValueError("Le SHA-256 source ne correspond pas au SHA-256 annoncé.")

        target = self._path(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and sha256_file(target) == expected:
            return self.get_metadata(storage_key)

        fd, temporary_name = tempfile.mkstemp(prefix=".storage-v2-", dir=str(target.parent))
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary)
            if sha256_file(temporary) != expected:
                raise IOError("La copie locale Storage V2 est corrompue.")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

        result = StorageObjectMetadata(
            provider=self.name,
            storage_key=str(storage_key),
            size_bytes=target.stat().st_size,
            sha256=expected,
            content_type=content_type or "application/octet-stream",
            custom={str(k): str(v) for k, v in dict(metadata or {}).items()},
        )
        metadata_path = self._metadata_path(target)
        metadata_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def get_file(self, storage_key: str, destination_path: Path) -> Path:
        source = self._path(storage_key)
        if not source.is_file():
            raise FileNotFoundError(storage_key)
        destination = Path(destination_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return destination

    @contextmanager
    def open_file(self, storage_key: str) -> Iterator[BinaryIO]:
        path = self._path(storage_key)
        with path.open("rb") as handle:
            yield handle

    def iter_bytes(self, storage_key: str, *, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        with self._path(storage_key).open("rb") as handle:
            for chunk in iter(lambda: handle.read(chunk_size), b""):
                yield chunk

    def exists(self, storage_key: str) -> bool:
        return self._path(storage_key).is_file()

    def delete_file(self, storage_key: str) -> bool:
        target = self._path(storage_key)
        if not target.is_file():
            return False
        target.unlink()
        self._metadata_path(target).unlink(missing_ok=True)
        return True

    def get_metadata(self, storage_key: str) -> StorageObjectMetadata:
        target = self._path(storage_key)
        if not target.is_file():
            raise FileNotFoundError(storage_key)
        sidecar = self._metadata_path(target)
        payload: dict[str, object] = {}
        if sidecar.is_file():
            try:
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
        return StorageObjectMetadata(
            provider=self.name,
            storage_key=str(storage_key),
            size_bytes=target.stat().st_size,
            sha256=str(payload.get("sha256") or sha256_file(target)),
            content_type=str(payload.get("content_type") or "application/octet-stream"),
            created_at=str(payload.get("created_at") or ""),
            etag=str(payload.get("etag")) if payload.get("etag") else None,
            custom={str(k): str(v) for k, v in dict(payload.get("custom") or {}).items()},
        )

