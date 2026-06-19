from pathlib import Path
import mimetypes

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from core.config import settings
from core.deps import get_current_user, get_db
from db.models import Document, User
from schemas.document import DocumentRead
from services.file_service import project_output_dir, save_upload_file
from services.project_service import get_project_for_user


router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])


def _normalise_path(path: str | Path) -> str:
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


def _existing_file_paths_for_project(project) -> list[Path]:
    """
    Récupère les documents déjà présents dans les dossiers IA du projet.

    Cas attendu EnnoSmart :
    C:/EnnoSmart/outputs/safe_rag_upload/{organisme}/{projet}/{annee}/uploaded

    On scanne aussi raw/documents/input au cas où une version précédente
    a rangé les documents dans un autre sous-dossier.
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

    # Dédoublonnage en gardant l’ordre
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
    project = get_project_for_user(db, project_id, current_user)

    target_path, size, stored_filename = await save_upload_file(
        file=file,
        user_id=current_user.id,
        project_id=project.id,
    )

    document = Document(
        project_id=project.id,
        filename=file.filename or stored_filename,
        stored_filename=stored_filename,
        file_path=str(target_path),
        content_type=file.content_type or mimetypes.guess_type(target_path.name)[0],
        file_size=size,
        document_type=document_type or _guess_document_type(target_path),
        upload_status="importé",
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
    Lie en base les documents déjà présents dans outputs/safe_rag_upload.

    Ce endpoint règle le problème "Documents = 0" quand l'IA a déjà traité
    les fichiers mais que les fichiers n'ont pas encore été uploadés via l'API.
    """
    project = get_project_for_user(db, project_id, current_user)
    paths = _existing_file_paths_for_project(project)

    if not paths:
        return []

    existing_paths = {
        _normalise_path(doc.file_path)
        for doc in db.query(Document).filter(Document.project_id == project.id).all()
    }

    created: list[Document] = []

    for path in paths:
        path_key = _normalise_path(path)
        path_resolved_key = _normalise_path(path.resolve())

        if path_key in existing_paths or path_resolved_key in existing_paths:
            continue

        if not path.exists():
            continue

        document = Document(
            project_id=project.id,
            filename=path.name,
            stored_filename=path.name,
            file_path=str(path),
            content_type=mimetypes.guess_type(path.name)[0],
            file_size=path.stat().st_size,
            document_type=_guess_document_type(path),
            upload_status="lié_depuis_outputs",
        )

        db.add(document)
        created.append(document)
        existing_paths.add(path_resolved_key)

    db.commit()

    for document in created:
        db.refresh(document)

    return created
