# -*- coding: utf-8 -*-
from __future__ import annotations

"""Nettoyage disque EnnoSmart, strictement limité aux zones temporaires.

Principes de sécurité:
- dry-run par défaut ; aucune suppression sans apply=True ;
- aucune suppression hors des racines temporaires explicitement autorisées ;
- les données permanentes (organismes, Memory V2, DB, corpus, etc.) sont protégées ;
- staging Power Automate supprimé uniquement après indexation réussie ET vérification
  que la source canonique Power Automate/OneDrive existe encore avec le même SHA-256 ;
- caches supprimés uniquement après expiration TTL ;
- les liens symboliques ne sont jamais suivis.
"""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
import shutil
import time
from typing import Any, Iterable

from core.config import settings
from modules.common.runtime_paths import data_root, storage_root, audit_root


PATCH_VERSION = "2026.09-storage-cleanup-v2.0"

# Racines qui ne doivent JAMAIS être supprimées par ce service.
PROTECTED_TOP_LEVEL = {
    "organismes",
    "experience_memory_v2",
    "cir_corpus_collection",
    "budget_control",
    "mcp",
    "uploads",
}

# TTL conservateurs. Surchargables par variables d'environnement.
DEFAULT_TTLS_DAYS = {
    "previews": 14,
    "cache": 30,
    "ennoscholar_cache": 30,
    "mcp_results": 7,
    "terminal_tests": 3,
}


@dataclass
class CleanupCandidate:
    category: str
    path: str
    bytes: int
    reason: str
    eligible: bool
    action: str = "keep"
    detail: str = ""


@dataclass
class CleanupReport:
    version: str
    mode: str
    started_at: str
    completed_at: str = ""
    storage_root: str = ""
    candidates: list[dict[str, Any]] | None = None
    deleted_files: int = 0
    deleted_bytes: int = 0
    eligible_bytes: int = 0
    blocked_files: int = 0
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on", "oui"}


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int = 3650) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _assert_safe_delete_path(path: Path, allowed_root: Path) -> Path:
    resolved = path.resolve()
    allowed = allowed_root.resolve()
    if resolved == allowed:
        raise PermissionError(f"Suppression de la racine interdite: {allowed}")
    if not _is_within(resolved, allowed):
        raise PermissionError(f"Chemin hors zone temporaire autorisée: {resolved}")
    if resolved.is_symlink() or any(parent.is_symlink() for parent in resolved.parents if _is_within(parent, allowed)):
        raise PermissionError(f"Lien symbolique refusé: {resolved}")
    return resolved


def _is_protected_top_level(path: Path, root: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except Exception:
        return True
    return bool(relative.parts and relative.parts[0].lower() in PROTECTED_TOP_LEVEL)


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except Exception:
        return 0


def _safe_unlink(path: Path, allowed_root: Path, *, apply: bool) -> tuple[bool, int, str]:
    try:
        safe = _assert_safe_delete_path(path, allowed_root)
        if not safe.is_file():
            return False, 0, "not_file"
        size = _file_size(safe)
        if apply:
            safe.unlink()
        return True, size, "deleted" if apply else "would_delete"
    except Exception as exc:
        return False, 0, f"blocked: {exc}"


def _prune_empty_dirs(root: Path, *, apply: bool) -> int:
    if not root.is_dir():
        return 0
    removed = 0
    dirs = sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for directory in dirs:
        try:
            _assert_safe_delete_path(directory, root)
            if any(directory.iterdir()):
                continue
            if apply:
                directory.rmdir()
            removed += 1
        except Exception:
            continue
    return removed


def _source_root_for_run(run: dict[str, Any]) -> Path | None:
    provider = str(run.get("provider") or "").strip().lower().replace(" ", "_")
    if provider in {"fake", "factice", "local"}:
        raw = str(getattr(settings, "POWER_AUTOMATE_FAKE_ROOT", "") or "").strip()
    elif provider in {"inbox", "power_automate", "power_automate_inbox", "onedrive", "real"}:
        raw = str(getattr(settings, "POWER_AUTOMATE_IMPORT_ROOT", "") or "").strip()
    else:
        return None
    if not raw:
        return None
    root = Path(raw).expanduser()
    return root.resolve() if root.is_dir() else None


def _source_path_for_item(run: dict[str, Any], item: dict[str, Any]) -> Path | None:
    source_root = _source_root_for_run(run)
    if source_root is None:
        return None
    scope = str(run.get("source_scope") or "").replace("\\", "/").strip("/")
    relative = str(item.get("source_path") or "").replace("\\", "/").strip("/")
    candidate = source_root
    if scope:
        candidate = candidate / Path(scope)
    if relative:
        candidate = candidate / Path(relative)
    try:
        resolved = candidate.resolve()
        resolved.relative_to(source_root)
    except Exception:
        return None
    return resolved


def _staging_paths(item: dict[str, Any], staging_root: Path) -> list[Path]:
    paths: list[Path] = []
    for key in ("staged_path", "index_staged_path"):
        raw = str(item.get(key) or "").strip()
        if not raw:
            continue
        try:
            path = Path(raw).expanduser().resolve()
            path.relative_to(staging_root.resolve())
        except Exception:
            continue
        if path not in paths:
            paths.append(path)
    return paths


@lru_cache(maxsize=1)
def _storage_v2_destination_records() -> dict[str, list[dict[str, Any]]]:
    db = None
    try:
        from db.database import SessionLocal
        from db.models import Document

        db = SessionLocal()
        rows = (
            db.query(Document)
            .filter(Document.storage_key.isnot(None))
            .all()
        )
        result: dict[str, list[dict[str, Any]]] = {}
        for document in rows:
            digest = str(document.sha256 or document.file_sha256 or "").strip().lower()
            if not digest:
                continue
            result.setdefault(digest, []).append(
                {
                    "document_id": int(document.id),
                    "storage_provider": str(document.storage_provider or ""),
                    "storage_key": str(document.storage_key or ""),
                    "size_bytes": int(document.size_bytes or document.file_size or 0),
                }
            )
        return result
    except Exception:
        return {}
    finally:
        if db is not None:
            db.close()


@lru_cache(maxsize=20_000)
def _verified_storage_v2_destination(expected_hash: str) -> tuple[bool, str]:
    """Confirme Object Storage + taille/SHA + métadonnée PostgreSQL."""
    if not expected_hash:
        return False, "destination_expected_sha256_missing"
    try:
        from modules.common.storage_v2 import get_storage_service

        rows = _storage_v2_destination_records().get(expected_hash, [])
        if not rows:
            return False, "postgresql_storage_metadata_missing"
        failures: list[str] = []
        for document in rows:
            try:
                service = get_storage_service(document["storage_provider"])
                key = document["storage_key"]
                metadata = service.get_metadata(key)
                expected_size = document["size_bytes"]
                if expected_size and metadata.size_bytes != expected_size:
                    failures.append(f"size_mismatch:{document['document_id']}")
                    continue
                if not service.verify_sha256(key, expected_hash):
                    failures.append(f"sha256_mismatch:{document['document_id']}")
                    continue
                return True, f"storage_v2_verified:document_id={document['document_id']}"
            except Exception as exc:
                failures.append(f"document_id={document['document_id']}:{exc}")
        return False, "storage_v2_not_verified:" + "|".join(failures[:5])
    except Exception as exc:
        return False, f"storage_v2_verification_error:{exc}"


def inspect_recoverable_staging_item(
    *,
    root: Path,
    run: dict[str, Any],
    item: dict[str, Any],
    apply: bool = False,
    min_age_hours: int | None = None,
) -> list[CleanupCandidate]:
    """Évalue les copies staging d'un audit Power Automate terminé.

    Une copie devient supprimable si le scan d'origine est terminé avec succès,
    la source canonique existe encore et son SHA-256 correspond au SHA-256
    enregistré. L'item peut être déjà indexé ou seulement audité : le patch V1
    sait réhydrater le staging depuis la source avant une indexation ultérieure.
    """

    staging_root = (root / "staging").resolve()
    if not staging_root.is_dir():
        return []

    paths = _staging_paths(item, staging_root)
    if not paths:
        return []

    status = str(run.get("status") or "").strip().lower()
    run_ok = bool(run.get("ok")) and status in {"completed", "complete", "success", "succeeded"}
    indexed = bool(item.get("indexed"))
    index_ok = bool((item.get("index_result") or {}).get("ok"))
    expected_hash = str(item.get("sha256") or "").strip().lower()
    source_path = _source_path_for_item(run, item)
    min_age = min_age_hours if min_age_hours is not None else _env_int(
        "ENNOSMART_CLEANUP_STAGING_MIN_AGE_HOURS", 1, maximum=24 * 30
    )

    # Par défaut un staging métier est considéré permanent. Seul un item
    # explicitement marqué permanent_required=false peut être traité comme un
    # audit temporaire réhydratable depuis la source.
    permanent_required = bool(item.get("permanent_required", True)) or indexed
    destination_verified = False
    destination_detail = "permanent_destination_not_required"
    if permanent_required:
        destination_verified, destination_detail = _verified_storage_v2_destination(expected_hash)

    source_verified = False
    source_detail = "canonical_source_not_required_after_storage_v2_verification"
    source_verification_required = bool(not run_ok or not permanent_required)
    if permanent_required and destination_verified and run_ok:
        source_verified = True
    elif not source_verification_required:
        source_detail = "canonical_source_check_skipped_destination_not_verified"
    elif not expected_hash:
        source_detail = "missing_expected_sha256"
    elif source_path is None or not source_path.is_file():
        source_detail = "canonical_source_missing"
    else:
        try:
            source_hash = _sha256_file(source_path)
            source_verified = source_hash.lower() == expected_hash
            source_detail = "canonical_source_hash_ok" if source_verified else "canonical_source_hash_mismatch"
        except Exception as exc:
            source_detail = f"canonical_source_unreadable: {exc}"
    completion_verified = bool(
        run_ok
        and (not indexed or index_ok)
        and (not permanent_required or destination_verified)
    )
    error_ttl_hours = _env_int(
        "ENNOSMART_CLEANUP_STAGING_ERROR_TTL_DAYS", 7, maximum=3650
    ) * 24

    now = time.time()
    candidates: list[CleanupCandidate] = []
    for path in paths:
        size = _file_size(path)
        age_hours = 0.0
        if path.exists():
            try:
                age_hours = max(0.0, (now - path.stat().st_mtime) / 3600.0)
            except Exception:
                age_hours = 0.0
        success_eligible = bool(
            path.is_file()
            and source_verified
            and completion_verified
            and age_hours >= float(min_age)
        )
        error_eligible = bool(
            path.is_file()
            and source_verified
            and not run_ok
            and age_hours >= float(error_ttl_hours)
        )
        eligible = success_eligible or error_eligible
        detail = (
            f"{source_detail}; audit_ok={run_ok}; indexed={indexed}; index_ok={index_ok}; "
            f"permanent_required={permanent_required}; destination={destination_detail}; "
            f"age_hours={age_hours:.2f}; min_age_hours={min_age}; "
            f"error_ttl_hours={error_ttl_hours}"
        )
        reason = (
            "staging_error_ttl_source_recoverable"
            if error_eligible
            else "staging_success_fully_verified"
            if success_eligible
            else "staging_protected_incomplete_verification"
        )
        candidate = CleanupCandidate(
            category="power_automate_staging",
            path=str(path),
            bytes=size,
            reason=reason,
            eligible=eligible,
            detail=detail,
        )
        if eligible:
            ok, deleted_size, action = _safe_unlink(path, staging_root, apply=apply)
            candidate.action = action
            candidate.bytes = deleted_size or size
            candidate.eligible = ok
        else:
            candidate.action = "keep"
        candidates.append(candidate)

    if apply:
        _prune_empty_dirs(staging_root, apply=True)
    return candidates


def inspect_indexed_staging_item(
    *,
    root: Path,
    run: dict[str, Any],
    item: dict[str, Any],
    apply: bool = False,
    min_age_hours: int | None = None,
) -> list[CleanupCandidate]:
    """Compatibilité V1 : même vérification stricte, utilisée après indexation."""
    if not bool(item.get("indexed")) or not bool((item.get("index_result") or {}).get("ok")):
        return []
    return inspect_recoverable_staging_item(
        root=root,
        run=run,
        item=item,
        apply=apply,
        min_age_hours=min_age_hours,
    )

def cleanup_indexed_power_automate_item_from_metadata(
    *,
    root: Path,
    run: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, Any]:
    """Hook non bloquant appelé après une indexation Memory V2 réussie.

    Par défaut le hook reste en dry-run. Pour autoriser la suppression réelle:
      ENNOSMART_STORAGE_CLEANUP_ENABLED=true
      ENNOSMART_CLEANUP_STAGING_AFTER_INDEX=true
      ENNOSMART_STORAGE_CLEANUP_APPLY=true
    """

    enabled = _env_bool("ENNOSMART_STORAGE_CLEANUP_ENABLED", False)
    after_index = _env_bool("ENNOSMART_CLEANUP_STAGING_AFTER_INDEX", False)
    apply = bool(enabled and after_index and _env_bool("ENNOSMART_STORAGE_CLEANUP_APPLY", False))
    candidates = inspect_indexed_staging_item(root=root, run=run, item=item, apply=apply)
    deleted = [c for c in candidates if c.action == "deleted"]
    would_delete = [c for c in candidates if c.action == "would_delete"]
    return {
        "ok": True,
        "mode": "apply" if apply else "dry-run",
        "deleted_files": len(deleted),
        "deleted_bytes": sum(c.bytes for c in deleted),
        "would_delete_files": len(would_delete),
        "would_delete_bytes": sum(c.bytes for c in would_delete),
        "candidates": [asdict(c) for c in candidates],
    }


def _ttl_days_for(name: str) -> int:
    env_name = {
        "previews": "ENNOSMART_CLEANUP_PREVIEWS_TTL_DAYS",
        "cache": "ENNOSMART_CLEANUP_CACHE_TTL_DAYS",
        "ennoscholar_cache": "ENNOSMART_CLEANUP_ENNOSCHOLAR_TTL_DAYS",
        "mcp_results": "ENNOSMART_CLEANUP_MCP_RESULTS_TTL_DAYS",
        "terminal_tests": "ENNOSMART_CLEANUP_TERMINAL_TESTS_TTL_DAYS",
    }[name]
    return _env_int(env_name, DEFAULT_TTLS_DAYS[name], maximum=3650)


def inspect_ttl_cache(category: str, path: Path, *, apply: bool = False) -> list[CleanupCandidate]:
    root = storage_root().resolve()
    if _is_protected_top_level(path, root):
        raise PermissionError(f"Racine protégée refusée: {path}")
    if not _is_within(path, root):
        raise PermissionError(f"Cache hors storage refusé: {path}")
    if not path.is_dir():
        return []

    ttl_days = _ttl_days_for(category)
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    results: list[CleanupCandidate] = []
    for file_path in path.rglob("*"):
        if not file_path.is_file() or file_path.is_symlink():
            continue
        try:
            modified = datetime.fromtimestamp(file_path.stat().st_mtime, timezone.utc)
        except Exception:
            continue
        eligible = modified < cutoff
        size = _file_size(file_path)
        candidate = CleanupCandidate(
            category=category,
            path=str(file_path.resolve()),
            bytes=size,
            reason=f"ttl>{ttl_days}d",
            eligible=eligible,
            action="keep",
            detail=f"mtime={modified.isoformat(timespec='seconds')}",
        )
        if eligible:
            ok, deleted_size, action = _safe_unlink(file_path, path, apply=apply)
            candidate.action = action
            candidate.bytes = deleted_size or size
            candidate.eligible = ok
        results.append(candidate)
    if apply:
        _prune_empty_dirs(path, apply=True)
    return results


def _load_audit_run_map(root: Path) -> dict[str, dict[str, Any]]:
    runs_dir = root / "runs"
    result: dict[str, dict[str, Any]] = {}
    if not runs_dir.is_dir():
        return result
    for run_path in runs_dir.glob("*.json"):
        run = _json_read(run_path, None)
        if not isinstance(run, dict):
            continue
        scan_id = str(run.get("scan_id") or run_path.stem).strip()
        if scan_id:
            result[scan_id] = run
    return result


def _iter_audit_item_records(root: Path) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    """Retourne les items depuis les runs et les fiches items séparées.

    Les anciens audits peuvent conserver les fiches sous items/<scan_id> même si
    le run principal a été compacté ou modifié. On déduplique par scan_id + external_id.
    """
    run_map = _load_audit_run_map(root)
    seen: set[tuple[str, str]] = set()

    for scan_id, run in run_map.items():
        for item in run.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("external_id") or item.get("item_id") or "").strip()
            key = (scan_id, item_id)
            if key in seen:
                continue
            seen.add(key)
            yield run, item

    items_root = root / "items"
    if not items_root.is_dir():
        return
    for item_path in items_root.glob("*/*.json"):
        item = _json_read(item_path, None)
        if not isinstance(item, dict):
            continue
        scan_id = str(item.get("scan_id") or item_path.parent.name).strip()
        item_id = str(item.get("external_id") or item.get("item_id") or item_path.stem).strip()
        key = (scan_id, item_id)
        if key in seen:
            continue
        seen.add(key)
        run = run_map.get(scan_id)
        if run is None:
            run = {
                "scan_id": scan_id,
                "ok": False,
                "status": "run_metadata_missing",
                "provider": "",
                "source_scope": "",
            }
        yield run, item


SUPPORTED_IMPORT_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md"}


def _digest_from_staging_path(path: Path, staging_root: Path) -> str:
    """Extrait le SHA-256 content-addressed depuis staging/<xx>/<sha256>/..."""
    try:
        relative = path.resolve().relative_to(staging_root.resolve())
    except Exception:
        return ""
    for part in relative.parts:
        value = str(part).strip().lower()
        if len(value) == 64 and all(ch in "0123456789abcdef" for ch in value):
            return value
    return ""


def _orphan_source_root(root: Path) -> tuple[Path | None, str]:
    raw = str(getattr(settings, "POWER_AUTOMATE_IMPORT_ROOT", "") or "").strip()
    if not raw:
        return None, "power_automate_import_root_not_configured"
    candidate = Path(raw).expanduser()
    if not candidate.is_dir():
        return None, "power_automate_import_root_missing"
    try:
        resolved = candidate.resolve()
        # La source canonique et l'audit local doivent rester deux arbres distincts.
        if _is_within(resolved, root) or _is_within(root, resolved):
            return None, "power_automate_import_root_overlaps_audit_root"
        return resolved, "ok"
    except Exception as exc:
        return None, f"power_automate_import_root_invalid: {exc}"


def _representative_sizes_by_digest(
    staging_root: Path,
    digest_paths: dict[str, list[Path]],
) -> dict[str, set[int]]:
    """Trouve la taille des copies originales directement sous le dossier SHA.

    Les sous-dossiers converted/libreoffice_profile sont des dérivés recréables.
    On utilise uniquement les fichiers au niveau du dossier SHA pour filtrer la
    source avant calcul du hash, ce qui évite de hasher toute la bibliothèque.
    """
    result: dict[str, set[int]] = {}
    for digest, paths in digest_paths.items():
        sizes: set[int] = set()
        digest_dir: Path | None = None
        for path in paths:
            try:
                current = path.resolve()
                while current != staging_root and current.name.lower() != digest:
                    current = current.parent
                if current.name.lower() == digest:
                    digest_dir = current
                    break
            except Exception:
                continue
        if digest_dir is not None and digest_dir.is_dir():
            try:
                for child in digest_dir.iterdir():
                    if child.is_file() and not child.is_symlink():
                        sizes.add(_file_size(child))
            except Exception:
                pass
        if not sizes:
            # Fallback conservateur : tailles de tous les fichiers du groupe.
            sizes = {_file_size(path) for path in paths if path.is_file()}
        result[digest] = {size for size in sizes if size >= 0}
    return result


def _verify_orphan_digests_from_source(
    *,
    root: Path,
    staging_root: Path,
    digest_paths: dict[str, list[Path]],
) -> tuple[dict[str, Path], str]:
    """Vérifie les staging orphelins directement contre la source Power Automate.

    Aucune écriture n'est faite dans la source. Un groupe staging n'est considéré
    récupérable que si un fichier de la bibliothèque professionnelle possède
    exactement le SHA-256 encodé dans son dossier staging.
    """
    source_root, source_status = _orphan_source_root(root)
    if source_root is None or not digest_paths:
        return {}, source_status

    sizes_by_digest = _representative_sizes_by_digest(staging_root, digest_paths)
    size_to_digests: dict[int, set[str]] = {}
    for digest, sizes in sizes_by_digest.items():
        for size in sizes:
            size_to_digests.setdefault(size, set()).add(digest)

    remaining = set(digest_paths)
    verified: dict[str, Path] = {}
    try:
        iterator = source_root.rglob("*")
        for source in iterator:
            if not remaining:
                break
            try:
                if not source.is_file() or source.is_symlink():
                    continue
                if source.name.startswith("~$") or source.suffix.lower() not in SUPPORTED_IMPORT_EXTENSIONS:
                    continue
                size = _file_size(source)
                possible = size_to_digests.get(size)
                if not possible or not (possible & remaining):
                    continue
                digest = _sha256_file(source).lower()
                if digest in remaining:
                    verified[digest] = source.resolve()
                    remaining.remove(digest)
            except (OSError, PermissionError):
                continue
    except Exception as exc:
        return verified, f"source_scan_partial_error: {exc}"
    return verified, "ok"


def _append_orphan_staging_candidates(root: Path, candidates: list[CleanupCandidate]) -> None:
    """Classe les anciens staging sans run JSON en utilisant leur SHA content-addressed.

    Un orphelin devient éligible uniquement si le contenu original est encore
    présent dans POWER_AUTOMATE_IMPORT_ROOT avec exactement le même SHA-256.
    Sinon il reste protégé.
    """
    staging_root = (root / "staging").resolve()
    if not staging_root.is_dir():
        return

    known = {str(Path(c.path).resolve()).lower() for c in candidates}
    orphan_paths: list[Path] = []
    digest_paths: dict[str, list[Path]] = {}
    for path in staging_root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        key = str(path.resolve()).lower()
        if key in known:
            continue
        orphan_paths.append(path)
        digest = _digest_from_staging_path(path, staging_root)
        if digest:
            digest_paths.setdefault(digest, []).append(path)

    if _env_bool("ENNOSMART_CLEANUP_VERIFY_ORPHAN_SOURCE", False):
        verified, source_scan_status = _verify_orphan_digests_from_source(
            root=root,
            staging_root=staging_root,
            digest_paths=digest_paths,
        )
    else:
        verified, source_scan_status = {}, "disabled_by_default_orphans_need_review"
    min_age = _env_int("ENNOSMART_CLEANUP_STAGING_MIN_AGE_HOURS", 1, maximum=24 * 30)
    unknown_ttl_hours = 24 * _env_int(
        "ENNOSMART_CLEANUP_UNKNOWN_STAGING_TTL_DAYS", 1, maximum=3650
    )
    expire_unknown = _env_bool("ENNOSMART_CLEANUP_EXPIRE_UNKNOWN_STAGING", False)
    now = time.time()

    for path in orphan_paths:
        digest = _digest_from_staging_path(path, staging_root)
        size = _file_size(path)
        try:
            age_hours = max(0.0, (now - path.stat().st_mtime) / 3600.0)
        except Exception:
            age_hours = 0.0
        source_path = verified.get(digest) if digest else None
        source_verified = source_path is not None
        expired_unknown = bool(expire_unknown and age_hours >= float(unknown_ttl_hours))
        eligible = bool(
            (source_verified and age_hours >= float(min_age))
            or expired_unknown
        )
        cache_record = root / "documents" / f"{digest}.json" if digest else None

        if source_verified:
            detail = (
                f"orphan_source_hash_ok; source={source_path}; digest={digest}; "
                f"age_hours={age_hours:.2f}; min_age_hours={min_age}"
            )
            reason = "orphan_staging_source_recoverable"
        elif expired_unknown:
            detail = (
                f"unknown_staging_ttl_expired; age_hours={age_hours:.2f}; "
                f"ttl_hours={unknown_ttl_hours}"
            )
            reason = "unknown_staging_ttl_expired"
        elif not digest:
            detail = "orphan_staging_digest_missing"
            reason = "orphan_staging_protected"
        else:
            detail = (
                f"orphan_source_hash_not_found; source_scan={source_scan_status}; digest={digest}; "
                f"age_hours={age_hours:.2f}"
            )
            reason = "orphan_staging_protected"
        if cache_record is not None and cache_record.is_file():
            detail += "; cache_record_present"

        candidates.append(CleanupCandidate(
            category="power_automate_staging",
            path=str(path.resolve()),
            bytes=size,
            reason=reason,
            eligible=eligible,
            action="keep",
            detail=detail,
        ))


def cleanup_completed_audit_staging_from_metadata(*, root: Path, run: dict[str, Any]) -> dict[str, Any]:
    """Hook optionnel après audit réussi.

    Réel seulement si les trois flags sont actifs :
      ENNOSMART_STORAGE_CLEANUP_ENABLED=true
      ENNOSMART_CLEANUP_STAGING_AFTER_AUDIT=true
      ENNOSMART_STORAGE_CLEANUP_APPLY=true
    Sinon il reste en dry-run.
    """
    enabled = _env_bool("ENNOSMART_STORAGE_CLEANUP_ENABLED", False)
    after_audit = _env_bool("ENNOSMART_CLEANUP_STAGING_AFTER_AUDIT", False)
    apply = bool(enabled and after_audit and _env_bool("ENNOSMART_STORAGE_CLEANUP_APPLY", False))
    candidates: list[CleanupCandidate] = []
    for item in run.get("items") or []:
        if not isinstance(item, dict):
            continue
        candidates.extend(inspect_recoverable_staging_item(root=root, run=run, item=item, apply=False))

    dedup: dict[str, CleanupCandidate] = {}
    for candidate in candidates:
        key = str(Path(candidate.path).resolve()).lower()
        current = dedup.get(key)
        if current is None or (candidate.eligible and not current.eligible):
            dedup[key] = candidate
    candidates = list(dedup.values())

    deleted_files = 0
    deleted_bytes = 0
    if apply:
        staging_root = (root / "staging").resolve()
        for candidate in candidates:
            if not candidate.eligible:
                continue
            ok, size, action = _safe_unlink(Path(candidate.path), staging_root, apply=True)
            candidate.action = action
            if ok:
                deleted_files += 1
                deleted_bytes += size
        _prune_empty_dirs(staging_root, apply=True)
    else:
        for candidate in candidates:
            if candidate.eligible:
                candidate.action = "would_delete"

    return {
        "ok": True,
        "mode": "apply" if apply else "dry-run",
        "deleted_files": deleted_files,
        "deleted_bytes": deleted_bytes,
        "would_delete_files": len([c for c in candidates if c.eligible and c.action == "would_delete"]),
        "would_delete_bytes": sum(c.bytes for c in candidates if c.eligible and c.action == "would_delete"),
        "candidates": [asdict(c) for c in candidates],
    }

def run_storage_cleanup(
    *,
    apply: bool = False,
    categories: Iterable[str] | None = None,
    max_delete_gb: float = 50.0,
) -> dict[str, Any]:
    """Exécute le nettoyage sécurisé.

    categories: staging, previews, ennoscholar_cache, mcp_results, terminal_tests, all
    """

    storage = storage_root().resolve()
    requested = {str(x).strip().lower() for x in (categories or ["all"])}
    if "all" in requested:
        requested = {"staging", *DEFAULT_TTLS_DAYS.keys()}

    report = CleanupReport(
        version=PATCH_VERSION,
        mode="apply" if apply else "dry-run",
        started_at=_now_iso(),
        storage_root=str(storage),
        candidates=[],
        errors=[],
    )
    candidates: list[CleanupCandidate] = []

    try:
        if "staging" in requested:
            aroot = audit_root().resolve()
            if _is_within(aroot, storage):
                for run, item in _iter_audit_item_records(aroot):
                    candidates.extend(
                        inspect_recoverable_staging_item(root=aroot, run=run, item=item, apply=False)
                    )
                _append_orphan_staging_candidates(aroot, candidates)
            else:
                report.errors.append(f"audit_root hors storage, staging ignoré: {aroot}")

        for name in DEFAULT_TTLS_DAYS:
            if name not in requested:
                continue
            candidates.extend(inspect_ttl_cache(name, storage / name, apply=False))

        # Déduplique les chemins: plusieurs audits peuvent référencer le même fichier staging.
        dedup: dict[str, CleanupCandidate] = {}
        for candidate in candidates:
            key = str(Path(candidate.path).resolve()).lower()
            current = dedup.get(key)
            if current is None or (candidate.eligible and not current.eligible):
                dedup[key] = candidate
        candidates = list(dedup.values())

        eligible = [c for c in candidates if c.eligible]
        eligible_bytes = sum(c.bytes for c in eligible)
        report.eligible_bytes = eligible_bytes
        report.blocked_files = len([c for c in candidates if not c.eligible])

        limit_bytes = int(max(0.0, float(max_delete_gb)) * (1024 ** 3))
        if apply and eligible_bytes > limit_bytes:
            raise RuntimeError(
                f"Garde-fou: {eligible_bytes / (1024**3):.2f} Go éligibles > limite {max_delete_gb:.2f} Go. "
                "Augmentez explicitement --max-delete-gb après vérification du dry-run."
            )

        if apply:
            for candidate in eligible:
                path = Path(candidate.path)
                if candidate.category == "power_automate_staging":
                    allowed = audit_root().resolve() / "staging"
                else:
                    allowed = storage / candidate.category
                ok, size, action = _safe_unlink(path, allowed, apply=True)
                candidate.action = action
                if ok:
                    report.deleted_files += 1
                    report.deleted_bytes += size
                else:
                    report.errors.append(f"{path}: {action}")
            # Nettoyage des dossiers devenus vides.
            _prune_empty_dirs(audit_root().resolve() / "staging", apply=True)
            for name in DEFAULT_TTLS_DAYS:
                _prune_empty_dirs(storage / name, apply=True)
        else:
            for candidate in eligible:
                candidate.action = "would_delete"

    except Exception as exc:
        report.errors.append(str(exc))

    report.candidates = [asdict(c) for c in candidates]
    report.completed_at = _now_iso()
    payload = report.to_dict()

    # Rapport hors storage pour éviter qu'un nettoyage ne supprime ses propres traces.
    try:
        report_dir = data_root().resolve() / "logs" / "storage_cleanup"
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        (report_dir / f"cleanup_{stamp}_{report.mode}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass

    return payload
