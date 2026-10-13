from __future__ import annotations

import os
import re
import tempfile
import hashlib
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Iterator, Mapping

from modules.common.runtime_paths import data_root

from .integrity import sha256_bytes, sha256_chunks, sha256_file, validate_sha256
from .models import StorageObjectMetadata, StorageScope
from .providers import LocalStorageProvider, S3StorageProvider, StorageProvider


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "true" if default else "false")).strip().lower()
    return raw in {"1", "true", "yes", "on", "oui"}


def storage_v2_enabled() -> bool:
    return _env_bool("ENNOSMART_STORAGE_V2_ENABLED", False)


def _segment(value: object, fallback: str = "_none", *, max_length: int = 32) -> str:
    """Return a stable, Windows-safe object-key segment.

    Long historical CIR titles previously produced paths beyond the Windows
    path limit for the local provider.  A digest suffix keeps truncated scope
    names collision-resistant while S3 keys remain human-readable.
    """

    raw = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-.")
    if not text:
        return fallback
    if len(text) <= max_length:
        return text
    suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{text[: max_length - len(suffix) - 1].rstrip('-.')}-{suffix}"


def build_storage_key(scope: StorageScope, sha256: str) -> str:
    digest = validate_sha256(sha256)
    return "/".join(
        (
            "organismes",
            _segment(scope.organisme_id, "unknown"),
            "projects",
            _segment(scope.project_id, "unknown"),
            "subprojects",
            _segment(scope.subproject),
            "years",
            _segment(scope.year),
            _segment(scope.source_kind, "document") + "s",
            "sha256",
            digest[:2],
            digest,
        )
    )


class StorageService:
    def __init__(self, provider: StorageProvider):
        self.provider = provider

    def store_file(
        self,
        source: str | Path | bytes | bytearray | BinaryIO,
        *,
        storage_key: str | None = None,
        scope: StorageScope | None = None,
        content_type: str = "application/octet-stream",
        expected_sha256: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> StorageObjectMetadata:
        temporary: Path | None = None
        if isinstance(source, (bytes, bytearray)):
            fd, name = tempfile.mkstemp(prefix="ennosmart-storage-v2-")
            os.close(fd)
            temporary = Path(name)
            temporary.write_bytes(bytes(source))
            path = temporary
        elif isinstance(source, (str, Path)):
            path = Path(source)
        else:
            fd, name = tempfile.mkstemp(prefix="ennosmart-storage-v2-")
            os.close(fd)
            temporary = Path(name)
            with temporary.open("wb") as output:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(block)
            path = temporary

        try:
            digest = sha256_file(path)
            if expected_sha256 and digest != validate_sha256(expected_sha256):
                raise ValueError("Le SHA-256 calculé ne correspond pas au SHA-256 attendu.")
            if not storage_key:
                if scope is None:
                    raise ValueError("scope ou storage_key est obligatoire.")
                storage_key = build_storage_key(scope, digest)
            result = self.provider.put_file(
                storage_key,
                path,
                content_type=content_type,
                sha256=digest,
                metadata=metadata,
            )
            if result.size_bytes != path.stat().st_size:
                raise IOError("La taille de l'objet permanent diffère de la source.")
            if not self.verify_sha256(storage_key, digest):
                raise IOError("Le SHA-256 de l'objet permanent diffère de la source.")
            return StorageObjectMetadata(
                provider=self.provider.name,
                storage_key=storage_key,
                size_bytes=result.size_bytes,
                sha256=digest,
                content_type=result.content_type or content_type,
                created_at=result.created_at,
                etag=result.etag,
                custom=result.custom,
            )
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def get_file(self, storage_key: str, destination_path: str | Path) -> Path:
        return self.provider.get_file(storage_key, Path(destination_path))

    def get_bytes(self, storage_key: str) -> bytes:
        return b"".join(self.provider.iter_bytes(storage_key))

    def open_file(self, storage_key: str):
        return self.provider.open_file(storage_key)

    def iter_bytes(self, storage_key: str, *, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        return self.provider.iter_bytes(storage_key, chunk_size=chunk_size)

    def exists(self, storage_key: str) -> bool:
        return self.provider.exists(storage_key)

    def delete_file(self, storage_key: str) -> bool:
        return self.provider.delete_file(storage_key)

    def get_metadata(self, storage_key: str) -> StorageObjectMetadata:
        return self.provider.get_metadata(storage_key)

    def verify_sha256(self, storage_key: str, expected_sha256: str) -> bool:
        expected = validate_sha256(expected_sha256)
        metadata = self.provider.get_metadata(storage_key)
        if metadata.sha256 and metadata.sha256.lower() != expected:
            return False
        return sha256_chunks(self.provider.iter_bytes(storage_key)) == expected

    @contextmanager
    def materialize_temp_file(self, storage_key: str, *, suffix: str = "") -> Iterator[Path]:
        fd, name = tempfile.mkstemp(prefix="ennosmart-storage-v2-", suffix=suffix)
        os.close(fd)
        path = Path(name)
        try:
            self.get_file(storage_key, path)
            yield path
        finally:
            path.unlink(missing_ok=True)


def create_storage_service(provider_name: str | None = None) -> StorageService:
    provider = str(provider_name or os.getenv("ENNOSMART_OBJECT_STORAGE_PROVIDER", "local")).strip().lower()
    if provider == "local":
        root = str(os.getenv("ENNOSMART_STORAGE_V2_LOCAL_ROOT") or "").strip()
        local_root = Path(root).expanduser() if root else data_root() / "object_storage_v2"
        return StorageService(LocalStorageProvider(local_root))
    if provider == "s3":
        return StorageService(
            S3StorageProvider(
                bucket=os.getenv("ENNOSMART_S3_BUCKET", ""),
                endpoint=os.getenv("ENNOSMART_S3_ENDPOINT", ""),
                region=os.getenv("ENNOSMART_S3_REGION", ""),
                access_key=os.getenv("ENNOSMART_S3_ACCESS_KEY", ""),
                secret_key=os.getenv("ENNOSMART_S3_SECRET_KEY", ""),
                secure=_env_bool("ENNOSMART_S3_SECURE", True),
            )
        )
    raise ValueError(f"Provider Storage V2 non supporté : {provider}")


@lru_cache(maxsize=4)
def get_storage_service(provider_name: str | None = None) -> StorageService:
    return create_storage_service(provider_name)


def reset_storage_service_cache() -> None:
    get_storage_service.cache_clear()
