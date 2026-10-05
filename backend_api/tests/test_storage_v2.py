from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend_api"
for import_path in (ROOT_DIR, BACKEND_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from modules.common.storage_v2 import (
    StorageScope,
    build_storage_key,
    create_storage_service,
    reset_storage_service_cache,
    sha256_bytes,
)
from modules.common.storage_v2.providers.s3_provider import S3StorageProvider
from modules.common.storage_v2.storage_service import StorageService
from modules.common import runtime_paths
from services.document_storage_service import document_storage_source, read_document_bytes


class StorageV2LocalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ennosmart-storage-v2-test-")
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(
            os.environ,
            {
                "ENNOSMART_STORAGE_V2_ENABLED": "true",
                "ENNOSMART_OBJECT_STORAGE_PROVIDER": "local",
                "ENNOSMART_STORAGE_V2_LOCAL_ROOT": str(self.root / "objects"),
            },
            clear=False,
        )
        self.environment.start()
        reset_storage_service_cache()

    def tearDown(self) -> None:
        reset_storage_service_cache()
        self.environment.stop()
        self.temporary.cleanup()

    def test_round_trip_integrity_and_materialization(self) -> None:
        payload = b"EnnoSmart Storage V2\n" * 128
        digest = sha256_bytes(payload)
        scope = StorageScope("Acme R&D", 42, "Moteur", "2026", "project_document")
        service = create_storage_service("local")

        metadata = service.store_file(
            payload,
            scope=scope,
            content_type="application/pdf",
            expected_sha256=digest,
        )

        self.assertTrue(service.exists(metadata.storage_key))
        self.assertEqual(metadata.sha256, digest)
        self.assertEqual(metadata.size_bytes, len(payload))
        self.assertTrue(service.verify_sha256(metadata.storage_key, digest))
        self.assertEqual(service.get_bytes(metadata.storage_key), payload)
        with service.materialize_temp_file(metadata.storage_key, suffix=".pdf") as path:
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), payload)
        self.assertFalse(path.exists())

    def test_scope_is_part_of_object_key(self) -> None:
        digest = "a" * 64
        first = build_storage_key(StorageScope("org-a", 1, "sub", "2026"), digest)
        second = build_storage_key(StorageScope("org-b", 1, "sub", "2026"), digest)
        other_project = build_storage_key(StorageScope("org-a", 2, "sub", "2026"), digest)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, other_project)
        self.assertIn("organismes/org-a/projects/1/subprojects/sub/years/2026", first)

    def test_unconfigured_paths_remain_inside_storage_v2(self) -> None:
        data = self.root / "data"
        with (
            patch.object(runtime_paths, "_configured", return_value=""),
            patch.object(runtime_paths, "data_root", return_value=data),
        ):
            runtime = data / "object_storage_v2" / "runtime"
            self.assertEqual(runtime_paths.storage_root(), runtime)
            self.assertEqual(runtime_paths.outputs_root(), runtime / "outputs" / "safe_rag_upload")
            self.assertEqual(runtime_paths.logs_root(), runtime / "logs")
            self.assertEqual(runtime_paths.cache_root(), runtime / "cache")

        with (
            patch.dict(os.environ, {"ENNOSMART_STORAGE_V2_LOCAL_ROOT": ""}),
            patch("modules.common.storage_v2.storage_service.data_root", return_value=data),
        ):
            service = create_storage_service("local")
            self.assertEqual(service.provider.root, (data / "object_storage_v2").resolve())

    def test_long_scope_is_bounded_and_collision_resistant(self) -> None:
        digest = "b" * 64
        long_a = "Projet " + ("historique " * 30) + "A"
        long_b = "Projet " + ("historique " * 30) + "B"
        first = build_storage_key(StorageScope("org", long_a, "", "2024"), digest)
        second = build_storage_key(StorageScope("org", long_b, "", "2024"), digest)
        project_segment = first.split("/")[3]
        self.assertLessEqual(len(project_segment), 32)
        self.assertNotEqual(first, second)

    def test_document_read_order_v2_then_database_then_disk(self) -> None:
        payload = b"object-storage"
        digest = sha256_bytes(payload)
        service = create_storage_service("local")
        metadata = service.store_file(
            payload,
            scope=StorageScope("org-a", 7, "", "2025"),
            expected_sha256=digest,
        )
        document = SimpleNamespace(
            storage_provider="local",
            storage_key=metadata.storage_key,
            sha256=digest,
            file_sha256=digest,
            size_bytes=len(payload),
            file_size=len(payload),
            file_data=b"legacy-database",
            file_path=None,
        )
        self.assertEqual(read_document_bytes(document), payload)
        self.assertEqual(document_storage_source(document), "storage_v2:local")

        document.storage_key = "missing/object"
        document.sha256 = sha256_bytes(b"legacy-database")
        document.file_sha256 = document.sha256
        document.size_bytes = len(b"legacy-database")
        document.file_size = document.size_bytes
        self.assertEqual(read_document_bytes(document), b"legacy-database")
        self.assertEqual(document_storage_source(document), "legacy:database")

        legacy_path = self.root / "legacy.pdf"
        legacy_path.write_bytes(b"legacy-disk")
        document.file_data = None
        document.file_path = str(legacy_path)
        document.sha256 = sha256_bytes(b"legacy-disk")
        document.file_sha256 = document.sha256
        document.size_bytes = len(b"legacy-disk")
        document.file_size = document.size_bytes
        self.assertEqual(read_document_bytes(document), b"legacy-disk")
        self.assertEqual(document_storage_source(document), "legacy:disk")


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, object]] = {}

    def upload_file(self, filename, bucket, key, ExtraArgs):
        self.objects[(bucket, key)] = {
            "payload": Path(filename).read_bytes(),
            "content_type": ExtraArgs["ContentType"],
            "metadata": dict(ExtraArgs["Metadata"]),
        }

    def head_object(self, *, Bucket, Key):
        item = self.objects[(Bucket, Key)]
        return {
            "ContentLength": len(item["payload"]),
            "ContentType": item["content_type"],
            "Metadata": item["metadata"],
            "ETag": '"fake-etag"',
        }

    def get_object(self, *, Bucket, Key):
        from io import BytesIO

        return {"Body": BytesIO(self.objects[(Bucket, Key)]["payload"])}

    def download_file(self, bucket, key, filename):
        Path(filename).write_bytes(self.objects[(bucket, key)]["payload"])

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


class StorageV2S3ContractTests(unittest.TestCase):
    def test_s3_provider_persists_hash_metadata_and_round_trips(self) -> None:
        client = _FakeS3Client()
        service = StorageService(S3StorageProvider(bucket="test", client=client))
        payload = b"s3-compatible-payload"
        digest = sha256_bytes(payload)
        metadata = service.store_file(
            payload,
            scope=StorageScope("org", 1, "sub", "2026"),
            content_type="application/pdf",
            expected_sha256=digest,
        )
        self.assertEqual(metadata.provider, "s3")
        self.assertEqual(metadata.sha256, digest)
        self.assertEqual(service.get_bytes(metadata.storage_key), payload)
        self.assertTrue(service.verify_sha256(metadata.storage_key, digest))


if __name__ == "__main__":
    unittest.main()
