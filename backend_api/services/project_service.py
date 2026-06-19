from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from db.models import Project, User


def get_project_for_user(db: Session, project_id: int, current_user: User) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Projet introuvable.",
        )

    if current_user.role != "admin" and project.consultant_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès interdit à ce projet.",
        )

    return project
