# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
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

from modules.common.runtime_paths import data_root, storage_root
from services.storage_cleanup_service import run_storage_cleanup


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _inventory(root: Path) -> dict[str, Any]:
    categories: dict[str, Any] = {}
    total_files = 0
    total_bytes = 0
    if root.is_dir():
        for child in sorted(root.iterdir(), key=lambda path: path.name.lower()):
            if child.is_symlink():
                continue
            files = [child] if child.is_file() else [p for p in child.rglob("*") if p.is_file() and not p.is_symlink()]
            size = sum(int(path.stat().st_size) for path in files)
            categories[child.name] = {"files": len(files), "bytes": size}
            total_files += len(files)
            total_bytes += size
    return {
        "storage_root": str(root),
        "files": total_files,
        "bytes": total_bytes,
        "categories": categories,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Nettoyage TTL Storage V2 sécurisé (dry-run par défaut)."
    )
    parser.add_argument("--apply", action="store_true", help="Applique les suppressions SAFE_TO_DELETE uniquement.")
    parser.add_argument(
        "--category",
        action="append",
        choices=["all", "staging", "previews", "cache", "ennoscholar_cache", "mcp_results", "terminal_tests"],
        help="Catégorie répétable ; all par défaut.",
    )
    parser.add_argument("--max-delete-gb", type=float, default=50.0)
    parser.add_argument("--show-files", action="store_true")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=data_root() / "logs" / "storage_v2_cleanup",
    )
    args = parser.parse_args()

    root = storage_root().resolve()
    before = _inventory(root)
    cleanup = run_storage_cleanup(
        apply=bool(args.apply),
        categories=args.category or ["all"],
        max_delete_gb=float(args.max_delete_gb),
    )
    after = _inventory(root)
    candidates = cleanup.get("candidates") or []
    safe = [item for item in candidates if item.get("eligible")]
    needs_review = [item for item in candidates if not item.get("eligible")]
    report = {
        "version": "storage-v2-cleanup-v1",
        "mode": "apply" if args.apply else "dry-run",
        "started_at": cleanup.get("started_at") or _now(),
        "completed_at": _now(),
        "before": before,
        "after": after,
        "cleanup": cleanup,
        "classification": {
            "SAFE_TO_DELETE": safe,
            "KEEP": [
                "organismes legacy tant que migration incomplète",
                "experience_memory_v2 permanente et Chroma locaux non vérifiés",
                "PostgreSQL",
                "Object Storage",
                "Chroma central",
            ],
            "NEEDS_REVIEW": needs_review,
        },
    }

    args.report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = args.report_dir / f"storage_v2_cleanup_{stamp}_{report['mode']}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("=" * 78)
    print(f"EnnoSmart Storage V2 cleanup — {report['mode']}")
    print(f"before: {before['files']} fichier(s), {before['bytes']} octets")
    print(f"SAFE_TO_DELETE: {len(safe)} fichier(s), {sum(int(x.get('bytes') or 0) for x in safe)} octets")
    print(f"NEEDS_REVIEW: {len(needs_review)} fichier(s)")
    print(f"deleted: {cleanup.get('deleted_files', 0)} fichier(s), {cleanup.get('deleted_bytes', 0)} octets")
    print(f"after: {after['files']} fichier(s), {after['bytes']} octets")
    print(f"report: {report_path.resolve()}")
    if args.show_files:
        for item in candidates:
            print(json.dumps(item, ensure_ascii=False, default=str))
    print("=" * 78)
    return 1 if args.apply and cleanup.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
