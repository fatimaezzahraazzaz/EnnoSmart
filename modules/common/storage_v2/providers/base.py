from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from pathlib import Path
from typing import BinaryIO, Iterator, Mapping

from ..models import StorageObjectMetadata


class StorageProvider(ABC):
    name: str

    @abstractmethod
    def put_file(
        self,
        storage_key: str,
        source_path: Path,
        *,
        content_type: str,
        sha256: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StorageObjectMetadata:
        raise NotImplementedError

    @abstractmethod
    def get_file(self, storage_key: str, destination_path: Path) -> Path:
        raise NotImplementedError

    @abstractmethod
    def open_file(self, storage_key: str) -> AbstractContextManager[BinaryIO]:
        raise NotImplementedError

    @abstractmethod
    def iter_bytes(self, storage_key: str, *, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        raise NotImplementedError

    @abstractmethod
    def exists(self, storage_key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def delete_file(self, storage_key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_metadata(self, storage_key: str) -> StorageObjectMetadata:
        raise NotImplementedError

