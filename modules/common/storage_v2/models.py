from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class StorageObjectMetadata:
    provider: str
    storage_key: str
    size_bytes: int
    sha256: str
    content_type: str = "application/octet-stream"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    etag: str | None = None
    custom: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StorageScope:
    organisme_id: str
    project_id: int | str
    subproject: str = ""
    year: int | str = ""
    source_kind: str = "document"

