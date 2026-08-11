from fastapi import APIRouter, Depends, status
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from core.deps import get_current_user, get_db
from db.models import Article, DiagnosticRun, Document, Project, ScholarRun, User, Verrou
from schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from services.file_service import clean_path_segment
from services.project_service import get_project_for_user


router = APIRouter(prefix="/projects", tags=["projects"])


def _visible_projects_query(db: Session, current_user: User):
    query = db.query(Project)
    if current_user.role not in {"admin", "superadmin"}:
        query = query.filter(Project.consultant_id == current_user.id)
    return query


@router.get("", response_model=list[ProjectRead])
def list_projects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _visible_projects_query(db, current_user).order_by(Project.created_at.desc()).all()


@router.get("/overview")
def list_project_overviews(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Vue legere Dashboard/detail, sans aucun JSON de run ou d'article.

    Le nombre de requetes reste fixe, quel que soit le nombre de projets. Cela
    remplace l'ancien chargement frontend en N+1 (cinq appels par projet).
    """

    projects = (
        _visible_projects_query(db, current_user)
        .order_by(Project.created_at.desc())
        .all()
    )
    project_ids = [project.id for project in projects]
    if not project_ids:
        return []

    document_rows = (
        db.query(
            Document.project_id,
            func.count(Document.id),
            func.max(Document.created_at),
        )
        .filter(Document.project_id.in_(project_ids))
        .group_by(Document.project_id)
        .all()
    )
    documents_by_project = {
        row[0]: {"count": int(row[1] or 0), "latest_at": row[2]}
        for row in document_rows
    }

    diagnostic_rows = (
        db.query(
            DiagnosticRun.id,
            DiagnosticRun.project_id,
            DiagnosticRun.status,
            DiagnosticRun.created_at,
            DiagnosticRun.completed_at,
        )
        .filter(DiagnosticRun.project_id.in_(project_ids))
        .order_by(
            DiagnosticRun.project_id.asc(),
            DiagnosticRun.created_at.desc(),
            DiagnosticRun.id.desc(),
        )
        .all()
    )
    latest_diagnostics = {}
    for row in diagnostic_rows:
        latest_diagnostics.setdefault(row.project_id, row)

    diagnostic_ids = [row.id for row in latest_diagnostics.values()]
    verrou_rows = []
    if diagnostic_ids:
        verrou_rows = (
            db.query(
                Verrou.diagnostic_run_id,
                func.count(Verrou.id),
                func.sum(case((Verrou.consultant_status == "en_attente", 1), else_=0)),
                func.sum(case((Verrou.tag_cir.ilike("%PERTINENT%"), 1), else_=0)),
                func.sum(case((Verrou.tag_cir.ilike("%MOYEN%"), 1), else_=0)),
                func.avg(Verrou.score),
                func.max(Verrou.created_at),
            )
            .filter(Verrou.diagnostic_run_id.in_(diagnostic_ids))
            .group_by(Verrou.diagnostic_run_id)
            .all()
        )
    verrous_by_run = {
        row[0]: {
            "count": int(row[1] or 0),
            "pending": int(row[2] or 0),
            "pertinent": int(row[3] or 0),
            "moyen": int(row[4] or 0),
            "average_score": float(row[5]) if row[5] is not None else None,
            "latest_at": row[6],
        }
        for row in verrou_rows
    }

    scholar_rows = (
        db.query(
            ScholarRun.id,
            ScholarRun.project_id,
            ScholarRun.status,
            ScholarRun.created_at,
            ScholarRun.completed_at,
        )
        .filter(ScholarRun.project_id.in_(project_ids))
        .order_by(
            ScholarRun.project_id.asc(),
            ScholarRun.created_at.desc(),
            ScholarRun.id.desc(),
        )
        .all()
    )
    latest_scholars = {}
    for row in scholar_rows:
        latest_scholars.setdefault(row.project_id, row)

    scholar_ids = [row.id for row in latest_scholars.values()]
    article_rows = []
    if scholar_ids:
        article_rows = (
            db.query(
                Article.scholar_run_id,
                func.count(Article.id),
                func.sum(case((Article.consultant_status == "en_attente", 1), else_=0)),
                func.sum(
                    case(
                        (
                            or_(
                                Article.tag_article.is_(None),
                                ~Article.tag_article.ilike("%hors%"),
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(case((Article.tag_article.ilike("%direct%"), 1), else_=0)),
                func.sum(case((Article.tag_article.ilike("%fondamental%"), 1), else_=0)),
                func.sum(case((Article.tag_article.ilike("%connexe%"), 1), else_=0)),
                func.sum(case((Article.tag_article.ilike("%hors%"), 1), else_=0)),
                func.max(Article.created_at),
            )
            .filter(Article.scholar_run_id.in_(scholar_ids))
            .group_by(Article.scholar_run_id)
            .all()
        )
    articles_by_run = {
        row[0]: {
            "count": int(row[1] or 0),
            "pending": int(row[2] or 0),
            "useful": int(row[3] or 0),
            "direct": int(row[4] or 0),
            "fondamental": int(row[5] or 0),
            "connexe": int(row[6] or 0),
            "hors_sujet": int(row[7] or 0),
            "latest_at": row[8],
        }
        for row in article_rows
    }

    result = []
    for project in projects:
        diagnostic = latest_diagnostics.get(project.id)
        scholar = latest_scholars.get(project.id)
        result.append(
            {
                "project": ProjectRead.model_validate(project).model_dump(),
                "documents": documents_by_project.get(
                    project.id, {"count": 0, "latest_at": None}
                ),
                "diagnostic": {
                    "available": diagnostic is not None,
                    "latest_run": (
                        {
                            "id": diagnostic.id,
                            "status": diagnostic.status,
                            "created_at": diagnostic.created_at,
                            "completed_at": diagnostic.completed_at,
                        }
                        if diagnostic is not None
                        else None
                    ),
                    "verrous": verrous_by_run.get(
                        diagnostic.id if diagnostic is not None else None,
                        {
                            "count": 0,
                            "pending": 0,
                            "pertinent": 0,
                            "moyen": 0,
                            "average_score": None,
                            "latest_at": None,
                        },
                    ),
                },
                "scholar": {
                    "available": scholar is not None,
                    "latest_run": (
                        {
                            "id": scholar.id,
                            "status": scholar.status,
                            "created_at": scholar.created_at,
                            "completed_at": scholar.completed_at,
                        }
                        if scholar is not None
                        else None
                    ),
                    "articles": articles_by_run.get(
                        scholar.id if scholar is not None else None,
                        {
                            "count": 0,
                            "pending": 0,
                            "useful": 0,
                            "direct": 0,
                            "fondamental": 0,
                            "connexe": 0,
                            "hors_sujet": 0,
                            "latest_at": None,
                        },
                    ),
                },
            }
        )
    return result


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
