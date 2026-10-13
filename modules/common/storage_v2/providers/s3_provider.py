from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Mapping

from ..integrity import validate_sha256
from ..models import StorageObjectMetadata
from .base import StorageProvider


class S3StorageProvider(StorageProvider):
    """Provider S3 générique, compatible notamment avec OVH Object Storage."""

    name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        endpoint: str = "",
        region: str = "",
        access_key: str = "",
        secret_key: str = "",
        secure: bool = True,
        client: Any | None = None,
    ):
        self.bucket = str(bucket or "").strip()
        if not self.bucket:
            raise ValueError("ENNOSMART_S3_BUCKET est obligatoire.")
        if client is not None:
            self.client = client
            return
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("Le package boto3 est requis pour le provider S3.") from exc

        endpoint_url = str(endpoint or "").strip() or None
        if endpoint_url and "://" not in endpoint_url:
            endpoint_url = ("https://" if secure else "http://") + endpoint_url
        kwargs: dict[str, Any] = {}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if region:
            kwargs["region_name"] = region
        if access_key:
            kwargs["aws_access_key_id"] = access_key
        if secret_key:
            kwargs["aws_secret_access_key"] = secret_key
        self.client = boto3.client("s3", **kwargs)

    @staticmethod
    def _key(storage_key: str) -> str:
        key = str(storage_key or "").replace("\\", "/").strip("/")
        if not key or any(part in {"", ".", ".."} for part in key.split("/")):
            raise ValueError("Clé Storage V2 invalide.")
        return key

    def put_file(
        self,
        storage_key: str,
        source_path: Path,
        *,
        content_type: str,
        sha256: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StorageObjectMetadata:
        key = self._key(storage_key)
        digest = validate_sha256(sha256)
        custom = {str(k): str(v) for k, v in dict(metadata or {}).items()}
        object_metadata = {**custom, "sha256": digest, "storage-v2": "1"}
        extra_args = {"ContentType": content_type or "application/octet-stream", "Metadata": object_metadata}
        self.client.upload_file(str(Path(source_path)), self.bucket, key, ExtraArgs=extra_args)
        return self.get_metadata(key)

    def get_file(self, storage_key: str, destination_path: Path) -> Path:
        destination = Path(destination_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, self._key(storage_key), str(destination))
        return destination

    @contextmanager
    def open_file(self, storage_key: str) -> Iterator[BinaryIO]:
        response = self.client.get_object(Bucket=self.bucket, Key=self._key(storage_key))
        body = response["Body"]
        try:
            yield body
        finally:
            body.close()

    def iter_bytes(self, storage_key: str, *, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        with self.open_file(storage_key) as body:
            for chunk in iter(lambda: body.read(chunk_size), b""):
                yield chunk

    def exists(self, storage_key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(storage_key))
            return True
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
            code = str((response.get("Error") or {}).get("Code") or "")
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def delete_file(self, storage_key: str) -> bool:
        key = self._key(storage_key)
        if not self.exists(key):
            return False
        self.client.delete_object(Bucket=self.bucket, Key=key)
        return True

    def get_metadata(self, storage_key: str) -> StorageObjectMetadata:
        key = self._key(storage_key)
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        custom = {str(k): str(v) for k, v in dict(head.get("Metadata") or {}).items()}
        return StorageObjectMetadata(
            provider=self.name,
            storage_key=key,
            size_bytes=int(head.get("ContentLength") or 0),
            sha256=str(custom.get("sha256") or ""),
            content_type=str(head.get("ContentType") or "application/octet-stream"),
            created_at=(head.get("LastModified").isoformat() if head.get("LastModified") else ""),
            etag=str(head.get("ETag") or "").strip('"') or None,
            custom=custom,
        )

