from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path, PureWindowsPath
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
for candidate in (PROJECT_ROOT, PROJECT_ROOT / "backend_api"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from core.config import settings  # noqa: E402
from db.database import SessionLocal  # noqa: E402
from db.models import DiagnosticRun, Document, Project, ScholarRun  # noqa: E402


def _normalised_parts(value: str) -> list[str]:
    return [part for part in re.split(r"[\\/]+", value) if part]


def _after_marker(value: str, marker: str) -> list[str] | None:
    parts = _normalised_parts(value)
    lowered = [part.lower() for part in parts]
    if marker.lower() not in lowered:
        return None
    return parts[lowered.index(marker.lower()) + 1 :]


def _is_absolute(value: str) -> bool:
    return Path(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _rebase_path(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw or raw.startswith("db://") or not _is_absolute(raw):
        return raw or None

    suffix = _after_marker(raw, "safe_rag_upload")
    if suffix is not None:
        return str(settings.ai_output_root_path.joinpath(*suffix))

    suffix = _after_marker(raw, "storage")
    if suffix is not None:
        return str(Path(settings.ENNOSMART_STORAGE_ROOT).joinpath(*suffix))

    # Un chemin OneDrive ou une bibliothèque externe reste inchangé.
    return raw


def _relative_ai_folder(project: Project) -> str:
    raw = str(project.ai_folder or "").strip()
    if raw and not _is_absolute(raw):
        return raw.replace("\\", "/")
    suffix = _after_marker(raw, "safe_rag_upload") if raw else None
    if suffix:
        return "/".join(suffix)

    def clean(value: Any) -> str:
        text = re.sub(r"[\\/:*?\"<>|]+", "_", str(value or "").strip())
        return re.sub(r"\s+", " ", text)[:120] or "unknown"

    parts = [clean(project.organisme), clean(project.project_name)]
    if str(getattr(project, "subproject_name", None) or "").strip():
        parts.extend(["subprojects", clean(project.subproject_name)])
    parts.extend(["years", clean(project.year)])
    return "/".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalise les anciens chemins absolus apres migration OVH."
    )
    parser.add_argument("--apply", action="store_true", help="Valide les modifications en base.")
    args = parser.parse_args()

    session = SessionLocal()
    changes: list[str] = []
    try:
        for project in session.query(Project).all():
            replacement = _relative_ai_folder(project)
            if replacement != (project.ai_folder or ""):
                changes.append(f"projects[{project.id}].ai_folder: {project.ai_folder!r} -> {replacement!r}")
                project.ai_folder = replacement

        for document in session.query(Document).all():
            replacement = _rebase_path(document.file_path)
            if replacement != document.file_path:
                changes.append(f"documents[{document.id}].file_path: {document.file_path!r} -> {replacement!r}")
                document.file_path = replacement

        for run in session.query(DiagnosticRun).all():
            for field in ("report_path", "nlp_result_path", "selected_verrous_path"):
                current = getattr(run, field)
                replacement = _rebase_path(current)
                if replacement != current:
                    changes.append(f"diagnostic_runs[{run.id}].{field}: {current!r} -> {replacement!r}")
                    setattr(run, field, replacement)

        for run in session.query(ScholarRun).all():
            replacement = _rebase_path(run.report_path)
            if replacement != run.report_path:
                changes.append(f"scholar_runs[{run.id}].report_path: {run.report_path!r} -> {replacement!r}")
                run.report_path = replacement

        for change in changes:
            print(change)
        print(f"changes={len(changes)} mode={'apply' if args.apply else 'dry-run'}")

        if args.apply:
            session.commit()
        else:
            session.rollback()
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
