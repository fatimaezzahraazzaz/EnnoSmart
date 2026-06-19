import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile, status

from core.config import settings
from db.models import Project


def clean_path_segment(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value)
    return value[:120] or "unknown"


def project_output_dir(project: Project) -> Path:
    if project.ai_folder:
        candidate = Path(project.ai_folder)
        if candidate.is_absolute():
            return candidate
        return settings.ai_output_root_path / candidate

    return (
        settings.ai_output_root_path
        / clean_path_segment(project.organisme)
        / clean_path_segment(project.project_name)
        / clean_path_segment(str(project.year))
    )


def project_upload_dir(user_id: int, project_id: int) -> Path:
    return settings.upload_root_path / str(user_id) / str(project_id) / "uploaded"


def safe_stored_filename(original_filename: str) -> str:
    ext = Path(original_filename).suffix.lower()
    clean_name = clean_path_segment(Path(original_filename).stem)
    return f"{clean_name}_{uuid.uuid4().hex[:12]}{ext}"


def validate_upload_file(file: UploadFile, size_bytes: int) -> None:
    ext = Path(file.filename or "").suffix.lower()

    if ext not in settings.allowed_extensions_set:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Extension non autorisée : {ext}",
        )

    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if size_bytes > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Fichier trop volumineux. Limite : {settings.MAX_UPLOAD_SIZE_MB} MB.",
        )


async def save_upload_file(file: UploadFile, user_id: int, project_id: int) -> tuple[Path, int, str]:
    upload_dir = project_upload_dir(user_id=user_id, project_id=project_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    stored_filename = safe_stored_filename(file.filename or "document")
    target_path = upload_dir / stored_filename

    size = 0
    with target_path.open("wb") as buffer:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)

            if size > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
                target_path.unlink(missing_ok=True)
                validate_upload_file(file, size)

            buffer.write(chunk)

    validate_upload_file(file, size)
    return target_path, size, stored_filename


def load_json_file(path: Path) -> Any | None:
    if not path.exists() or not path.is_file():
        return None

    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except UnicodeDecodeError:
            continue
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"JSON invalide : {path}",
            ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Impossible de lire le fichier JSON : {path}",
    )


def run_optional_ai_script(script_path: str | None, args: list[str], timeout_seconds: int) -> dict:
    if not script_path:
        return {
            "executed": False,
            "message": "Aucun script IA configuré dans .env.",
        }

    script = Path(script_path)
    if not script.exists():
        return {
            "executed": False,
            "message": f"Script IA introuvable : {script}",
        }

    command = [sys.executable, str(script), *args]

    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )

        return {
            "executed": True,
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "executed": True,
            "command": command,
            "timeout": True,
            "message": f"Timeout après {timeout_seconds} secondes.",
            "stdout": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
        }
