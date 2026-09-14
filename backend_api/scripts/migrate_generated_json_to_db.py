"""Migre les gros JSON historiques vers les artefacts PostgreSQL compressés.

Par défaut, le script ne modifie rien. Utiliser ``--apply`` pour écrire en base,
puis éventuellement ``--delete-after-verify`` pour retirer uniquement les
copies dont la relecture et le SHA-256 ont été vérifiés après commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
for candidate in (BACKEND_DIR, PROJECT_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from db.database import Base, SessionLocal, engine  # noqa: E402
from db.models import DiagnosticRun, Project  # noqa: E402
from services.diagnostic_service import (  # noqa: E402
    NLP_ARTIFACT_KEY,
    RAG_CHUNKS_ARTIFACT_KEY,
    _build_complete_run_payload,
    get_project_store,
)
from services.project_artifact_service import (  # noqa: E402
    artifact_uri,
    get_json_artifact,
    save_json_artifact,
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_sha(payload: Any) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _diagnostic_candidates(store: Any) -> list[Path]:
    return [
        store.diagnostics_dir / "ennodiagnostic_report.json",
        store.diagnostics_dir / "diagnostic_ennodiagnostic.json",
        store.project_dir / "ennodiagnostic" / "ennodiagnostic_report.json",
        store.project_dir / "ennodiagnostic" / "diagnostic_ennodiagnostic.json",
    ]


def _conversation_root(store: Any) -> Path:
    return (
        store.project_dir
        / "ennoscholar"
        / "state_of_art_payload"
        / "conversations"
    )


def _validate_delete_target(path: Path, project_root: Path) -> Path:
    resolved = path.resolve()
    root = project_root.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise RuntimeError(f"Refus de supprimer hors du projet : {path}")
    return resolved


def migrate_project(db: Any, project: Project, *, apply: bool, delete: bool) -> dict[str, Any]:
    store = get_project_store(project)
    sources: list[tuple[str, str, Path, Any, dict[str, Any]]] = []
    delete_files: set[Path] = set()
    delete_dirs: set[Path] = set()

    for key, kind, path in (
        (NLP_ARTIFACT_KEY, "nlp_result", store.nlp_dir / "nlp_result.json"),
        (RAG_CHUNKS_ARTIFACT_KEY, "rag_chunks", store.rag_dir / "chunks.json"),
    ):
        if path.is_file():
            sources.append((key, kind, path, _load_json(path) if apply else None, {}))
            delete_files.add(path)

    reports = [path for path in _diagnostic_candidates(store) if path.is_file()]
    report = None
    if reports:
        official = max(reports, key=lambda path: path.stat().st_mtime_ns)
        report = _load_json(official) if apply else None
        sources.append(
            (
                "diagnostics/latest_full_report.json",
                "diagnostic_full_report",
                official,
                report,
                {"migrated_from": str(official)},
            )
        )
        delete_files.update(reports)

    conversation_versions = 0
    conversations = _conversation_root(store)
    if conversations.is_dir():
        for session_root in conversations.iterdir():
            versions_root = session_root / "versions"
            if not versions_root.is_dir():
                continue
            session_versions = 0
            archived_payload_hashes: set[str] = set()
            for version_root in versions_root.iterdir():
                if not version_root.is_dir():
                    continue
                markdown_path = version_root / "state_of_art.md"
                payload_path = version_root / "state_of_art_payload.json"
                if not markdown_path.is_file() and not payload_path.is_file():
                    continue
                session_id = session_root.name
                version_id = version_root.name
                markdown = (
                    markdown_path.read_text(encoding="utf-8")
                    if apply and markdown_path.is_file()
                    else ""
                )
                payload = (
                    _load_json(payload_path)
                    if apply and payload_path.is_file()
                    else {}
                )
                if payload_path.is_file():
                    archived_payload_hashes.add(
                        hashlib.sha256(payload_path.read_bytes()).hexdigest()
                    )
                editorial_path = version_root / "editorial_report.json"
                scope_path = version_root / "scope_manifest.json"
                key = (
                    f"ennoscholar/conversations/{session_id}/versions/"
                    f"{version_id}.json"
                )
                uri = artifact_uri(project.id, key)
                metadata = {
                    "version_id": version_id,
                    "session_id": session_id,
                    "project_id": int(project.id),
                    "artifact_uri": uri,
                    "markdown_path": uri,
                    "payload_path": uri,
                    "editorial_report_path": uri,
                    "scope_manifest_path": uri,
                    "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                    "word_count": len(markdown.split()),
                    "status": payload.get("status") if isinstance(payload, dict) else None,
                    "ok": bool(payload.get("ok")) if isinstance(payload, dict) else False,
                    "storage_policy": "postgresql_gzip_artifact",
                }
                value = {
                    "version_id": version_id,
                    "markdown": markdown,
                    "payload": payload,
                    "editorial_report": (
                        _load_json(editorial_path)
                        if apply and editorial_path.is_file()
                        else {}
                    ),
                    "scope_manifest": (
                        _load_json(scope_path)
                        if apply and scope_path.is_file()
                        else {}
                    ),
                    "metadata": metadata,
                }
                sources.append((key, "scholar_state_of_art_version", version_root, value, metadata))
                delete_dirs.add(version_root)
                conversation_versions += 1
                session_versions += 1
            work_root = session_root / "work"
            work_payload = (
                work_root
                / "phase_5_state_of_art_writer"
                / "state_of_art_draft_payload.json"
            )
            # Un runtime inachevé peut contenir des choix non encore archivés.
            # On ne le supprime que si sa sortie finale correspond exactement à
            # une version déjà sauvegardée et vérifiée en base.
            if (
                session_versions
                and work_payload.is_file()
                and hashlib.sha256(work_payload.read_bytes()).hexdigest()
                in archived_payload_hashes
            ):
                delete_dirs.add(work_root)

    result = {
        "project_id": int(project.id),
        "project": project.project_name,
        "year": str(project.year),
        "artifacts_found": len(sources),
        "source_bytes": sum(
            path.stat().st_size if path.is_file() else sum(
                item.stat().st_size for item in path.rglob("*") if item.is_file()
            )
            for _, _, path, _, _ in sources
        ),
        "conversation_versions": conversation_versions,
        "applied": False,
        "deleted_paths": 0,
    }
    if not apply:
        return result

    saved: list[tuple[str, str, int]] = []
    saved_info: dict[str, dict[str, Any]] = {}
    for key, kind, path, payload, metadata in sources:
        info = save_json_artifact(
            db,
            project.id,
            key,
            payload,
            artifact_kind=kind,
            metadata=metadata or {"migrated_from": str(path)},
        )
        saved.append((key, info["content_sha256"], int(info["stored_size"])))
        saved_info[key] = info

    if report and isinstance(report, dict):
        latest_run = (
            db.query(DiagnosticRun)
            .filter(DiagnosticRun.project_id == project.id)
            .order_by(DiagnosticRun.created_at.desc(), DiagnosticRun.id.desc())
            .first()
        )
        if latest_run is not None:
            payload = _build_complete_run_payload(
                report=report,
                project=project,
                pipeline_name="historical_json_migration",
                button="migration",
            )
            artifact_info = saved_info["diagnostics/latest_full_report.json"]
            payload["full_report_artifact"] = artifact_info
            latest_run.raw_result_json = payload
            latest_run.report_path = artifact_info["uri"]
            if any(key == NLP_ARTIFACT_KEY for key, _sha, _size in saved):
                latest_run.nlp_result_path = artifact_uri(project.id, NLP_ARTIFACT_KEY)

    db.commit()

    for key, expected_sha, _stored_size in saved:
        stored = get_json_artifact(db, project.id, key, default=None)
        if stored is None or _json_sha(stored) != expected_sha:
            raise RuntimeError(f"Vérification après commit échouée : {key}")

    result["applied"] = True
    result["stored_bytes"] = sum(stored_size for _key, _sha, stored_size in saved)

    if delete:
        root = store.project_dir.resolve()
        for path in sorted(delete_files):
            target = _validate_delete_target(path, root)
            if target.is_file():
                target.unlink()
                result["deleted_paths"] += 1
        for path in sorted(delete_dirs, key=lambda value: len(value.parts), reverse=True):
            target = _validate_delete_target(path, root)
            if target.is_dir():
                shutil.rmtree(target)
                result["deleted_paths"] += 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--delete-after-verify", action="store_true")
    args = parser.parse_args()
    if args.delete_after_verify and not args.apply:
        parser.error("--delete-after-verify exige --apply")

    if args.apply:
        Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        projects = db.query(Project).order_by(Project.id.asc()).all()
        results = [
            migrate_project(
                db,
                project,
                apply=args.apply,
                delete=args.delete_after_verify,
            )
            for project in projects
        ]
        print(json.dumps({
            "mode": "apply" if args.apply else "dry-run",
            "delete_after_verify": bool(args.delete_after_verify),
            "projects": results,
            "totals": {
                "projects": len(results),
                "artifacts": sum(row["artifacts_found"] for row in results),
                "source_bytes": sum(row["source_bytes"] for row in results),
                "deleted_paths": sum(row["deleted_paths"] for row in results),
            },
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
