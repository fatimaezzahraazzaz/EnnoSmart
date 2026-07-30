# -*- coding: utf-8 -*-
from pathlib import Path
import hashlib
import mimetypes

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from core.config import settings
from core.deps import get_current_user, get_db
from db.models import Document, User
from schemas.document import DocumentRead
from services.file_service import project_output_dir
from services.project_service import get_project_for_user


router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])


def _normalise_path(path: str | Path | None) -> str:
    if not path:
        return ""
    return str(Path(path)).replace("\\", "/")


def _guess_document_type(path: Path) -> str:
    ext = path.suffix.lower()

    if ext == ".pdf":
        return "PDF"
    if ext in {".docx", ".doc"}:
        return "Word"
    if ext in {".xlsx", ".xls"}:
        return "Excel"
    if ext in {".pptx", ".ppt"}:
        return "PowerPoint"
    if ext in {".png", ".jpg", ".jpeg"}:
        return "Image"
    if ext == ".msg":
        return "Email"
    if ext == ".txt":
        return "Texte"

    return "Autre"


def _make_stored_filename(original_filename: str, sha256: str) -> str:
    path = Path(original_filename or "document")
    suffix = path.suffix.lower()
    stem = path.stem or "document"

    safe_stem = (
        stem.replace("\\", "_")
        .replace("/", "_")
        .replace(":", "_")
        .replace("*", "_")
        .replace("?", "_")
        .replace('"', "_")
        .replace("<", "_")
        .replace(">", "_")
        .replace("|", "_")
    )

    return f"{safe_stem}_{sha256[:12]}{suffix}"


def _existing_file_paths_for_project(project) -> list[Path]:
    """
    Récupère les documents déjà présents dans les dossiers IA du projet.

    Cas historique EnnoSmart :
    C:/EnnoSmart/outputs/safe_rag_upload/{organisme}/{projet}/{annee}/uploaded

    Cette fonction sert seulement à importer d'anciens fichiers disque
    vers PostgreSQL. Les nouveaux uploads ne passent plus par le disque.
    """
    output_dir = project_output_dir(project)

    candidate_dirs = [
        output_dir / "uploaded",
        output_dir / "raw",
        output_dir / "documents",
        output_dir / "input",
        output_dir / "inputs",
    ]

    allowed = settings.allowed_extensions_set
    found: list[Path] = []

    for directory in candidate_dirs:
        if not directory.exists() or not directory.is_dir():
            continue

        for path in directory.rglob("*"):
            if not path.is_file():
                continue

            if path.suffix.lower() not in allowed:
                continue

            found.append(path)

    unique: list[Path] = []
    seen: set[str] = set()

    for path in found:
        key = _normalise_path(path.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(path)

    return unique


@router.get("", response_model=list[DocumentRead])
def list_documents(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    return (
        db.query(Document)
        .filter(Document.project_id == project.id)
        .order_by(Document.created_at.desc())
        .all()
    )


@router.post("/upload", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    project_id: int,
    file: UploadFile = File(...),
    document_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload document brut EnnoDiagnostic.

    Nouvelle logique :
    - le fichier complet est stocké dans PostgreSQL : documents.file_data
    - aucun fichier permanent n'est écrit dans storage/uploads
    - file_path devient seulement un identifiant logique db://...
    """
    project = get_project_for_user(db, project_id, current_user)

    original_filename = file.filename or "document"
    file_bytes = await file.read()

    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fichier vide ou illisible.",
        )

    sha256 = hashlib.sha256(file_bytes).hexdigest()
    stored_filename = _make_stored_filename(original_filename, sha256)

    content_type = (
        file.content_type
        or mimetypes.guess_type(original_filename)[0]
        or "application/octet-stream"
    )

    document = Document(
        project_id=project.id,
        filename=original_filename,
        stored_filename=stored_filename,

        # Pas de chemin disque réel pour les nouveaux uploads.
        file_path=f"db://documents/{sha256}",

        content_type=content_type,
        file_size=len(file_bytes),
        document_type=document_type or _guess_document_type(Path(original_filename)),
        upload_status="importé_en_base",

        file_data=file_bytes,
        file_sha256=sha256,
        storage_mode="database",
    )

    db.add(document)
    db.commit()
    db.refresh(document)

    return document


@router.post("/import-existing", response_model=list[DocumentRead])
def import_existing_documents(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Importe en base les anciens fichiers présents sur disque.

    Ancien comportement :
    - lier le chemin disque dans documents.file_path.

    Nouveau comportement :
    - lire le fichier disque,
    - stocker son contenu dans documents.file_data,
    - mettre storage_mode='database',
    - utiliser file_path='db://documents/<sha256>'.

    Cette route ne supprime pas les fichiers disque automatiquement.
    La suppression doit être faite après vérification avec un script dédié.
    """
    project = get_project_for_user(db, project_id, current_user)
    paths = _existing_file_paths_for_project(project)

    if not paths:
        return []

    existing_sha = {
        doc.file_sha256
        for doc in db.query(Document).filter(Document.project_id == project.id).all()
        if doc.file_sha256
    }

    existing_logical_paths = {
        _normalise_path(doc.file_path)
        for doc in db.query(Document).filter(Document.project_id == project.id).all()
        if doc.file_path
    }

    created: list[Document] = []

    for path in paths:
        if not path.exists() or not path.is_file():
            continue

        file_bytes = path.read_bytes()
        if not file_bytes:
            continue

        sha256 = hashlib.sha256(file_bytes).hexdigest()
        logical_path = f"db://documents/{sha256}"

        if sha256 in existing_sha or _normalise_path(logical_path) in existing_logical_paths:
            continue

        stored_filename = _make_stored_filename(path.name, sha256)

        document = Document(
            project_id=project.id,
            filename=path.name,
            stored_filename=stored_filename,
            file_path=logical_path,
            content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            file_size=len(file_bytes),
            document_type=_guess_document_type(path),
            upload_status="importé_en_base_depuis_outputs",
            file_data=file_bytes,
            file_sha256=sha256,
            storage_mode="database",
        )

        db.add(document)
        created.append(document)
        existing_sha.add(sha256)
        existing_logical_paths.add(_normalise_path(logical_path))

    db.commit()

    for document in created:
        db.refresh(document)

    return created