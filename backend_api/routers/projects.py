from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from core.deps import get_current_user, get_db
from db.models import Project, User
from schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from services.file_service import clean_path_segment
from services.project_service import get_project_for_user


router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectRead])
def list_projects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Project)

    if current_user.role != "admin":
        query = query.filter(Project.consultant_id == current_user.id)

    return query.order_by(Project.created_at.desc()).all()


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ai_folder = "/".join(
        [
            clean_path_segment(payload.organisme),
            clean_path_segment(payload.project_name),
            clean_path_segment(str(payload.year)),
        ]
    )

    project = Project(
        consultant_id=current_user.id,
        organisme=payload.organisme.strip(),
        project_name=payload.project_name.strip(),
        year=str(payload.year).strip(),
        domain_label=payload.domain_label,
        status="Créé",
        ai_folder=ai_folder,
    )

    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_project_for_user(db, project_id, current_user)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(project, field, value)

    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}")
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    db.delete(project)
    db.commit()

    return {
        "status": "deleted",
        "project_id": project_id,
    }
