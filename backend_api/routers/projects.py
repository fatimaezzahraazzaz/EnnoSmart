from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from core.deps import get_current_user, get_db
from db.models import (
    Article,
    DiagnosticRun,
    Document,
    ImprovementSession,
    Project,
    ProjectAccessRequest,
    ScholarRun,
    User,
    Verrou,
)
from schemas.project import (
    ProjectAccessDecision,
    ProjectCreate,
    ProjectRead,
    ProjectSelection,
    ProjectUpdate,
)
from services.diagnostic_eligibility_service import extract_diagnostic_eligibility_score
from services.experience_memory_v2_service import get_memory_v2_catalog
from services.file_service import clean_path_segment
from services.project_service import get_project_for_user


router = APIRouter(prefix="/projects", tags=["projects"])


def _visible_projects_query(db: Session, current_user: User):
    query = db.query(Project)
    if current_user.role not in {"admin", "superadmin"}:
        query = query.filter(
            or_(
                Project.consultant_id == current_user.id,
                Project.access_requests.any(
                    and_(
                        ProjectAccessRequest.requester_id == current_user.id,
                        ProjectAccessRequest.status == "accepted",
                    )
                ),
            )
        )
    return query


def _clean_optional(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _identity_query(db: Session, payload: ProjectSelection):
    subproject = (_clean_optional(payload.subproject_name) or "").lower()
    return db.query(Project).filter(
        func.lower(func.trim(Project.organisme)) == payload.organisme.strip().lower(),
        func.lower(func.trim(Project.project_name)) == payload.project_name.strip().lower(),
        func.lower(func.trim(func.coalesce(Project.subproject_name, ""))) == subproject,
        func.lower(func.trim(Project.year)) == payload.year.strip().lower(),
    )


def _activity_labels(db: Session, project_id: int) -> list[str]:
    labels: list[str] = []
    if db.query(DiagnosticRun.id).filter(DiagnosticRun.project_id == project_id).first():
        labels.append("EnnoDiagnostic")
    if db.query(ScholarRun.id).filter(ScholarRun.project_id == project_id).first():
        labels.append("EnnoScholar")
    if db.query(ImprovementSession.id).filter(ImprovementSession.project_id == project_id).first():
        labels.append("EnnoAmélioration")
    return labels


def _active_identity_projects(db: Session, payload: ProjectSelection) -> list[tuple[Project, list[str]]]:
    result: list[tuple[Project, list[str]]] = []
    for project in _identity_query(db, payload).order_by(Project.created_at.desc()).all():
        activity = _activity_labels(db, project.id)
        if activity:
            result.append((project, activity))
    return result


@router.get("/catalog")
def project_catalog(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Catalogue partagé pour les listes Organisme → Projet → Sous-projet."""

    del current_user
    identities: list[tuple[str, str, str | None]] = [
        (row.organisme, row.project_name, row.subproject_name)
        for row in db.query(Project).order_by(Project.created_at.asc()).all()
    ]
    try:
        memory_catalog = get_memory_v2_catalog()
        identities.extend(
            (
                str(row.get("organisme") or "").strip(),
                str(row.get("project") or "").strip(),
                _clean_optional(row.get("subproject")),
            )
            for row in memory_catalog.get("projects") or []
        )
    except Exception:
        # Le formulaire reste utilisable même si Chroma est momentanément indisponible.
        pass

    organisations: dict[str, dict] = {}
    for organisme, project_name, subproject_name in identities:
        organisme = str(organisme or "").strip()
        project_name = str(project_name or "").strip()
        if not organisme or not project_name:
            continue
        org_key = organisme.casefold()
        org = organisations.setdefault(org_key, {"name": organisme, "projects": {}})
        project_key = project_name.casefold()
        project = org["projects"].setdefault(
            project_key,
            {"name": project_name, "subprojects": {}},
        )
        subproject = _clean_optional(subproject_name)
        if subproject:
            project["subprojects"].setdefault(subproject.casefold(), subproject)

    return {
        "organisations": [
            {
                "name": org["name"],
                "projects": [
                    {
                        "name": project["name"],
                        "subprojects": sorted(
                            project["subprojects"].values(), key=str.casefold
                        ),
                    }
                    for project in sorted(
                        org["projects"].values(),
                        key=lambda item: item["name"].casefold(),
                    )
                ],
            }
            for org in sorted(organisations.values(), key=lambda item: item["name"].casefold())
        ]
    }


@router.post("/selection-status")
def project_selection_status(
    payload: ProjectSelection,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    active_projects = _active_identity_projects(db, payload)
    if not active_projects:
        return {"status": "available", "can_create": True}

    for project, activity in active_projects:
        if project.consultant_id == current_user.id:
            return {
                "status": "owned",
                "can_create": False,
                "project_id": project.id,
                "activity": activity,
                "message": "Ce dossier existe déjà dans vos projets.",
            }
        accepted = (
            db.query(ProjectAccessRequest)
            .filter(
                ProjectAccessRequest.project_id == project.id,
                ProjectAccessRequest.requester_id == current_user.id,
                ProjectAccessRequest.status == "accepted",
            )
            .first()
        )
        if accepted:
            return {
                "status": "granted",
                "can_create": False,
                "project_id": project.id,
                "activity": activity,
                "message": "Ce projet est déverrouillé pour votre compte.",
            }

    project, activity = active_projects[0]
    owner = db.query(User).filter(User.id == project.consultant_id).first()
    request = (
        db.query(ProjectAccessRequest)
        .filter(
            ProjectAccessRequest.project_id == project.id,
            ProjectAccessRequest.requester_id == current_user.id,
        )
        .first()
    )
    owner_name = owner.full_name if owner else "un autre consultant"
    return {
        "status": "locked",
        "can_create": False,
        "project_id": project.id,
        "owner_name": owner_name,
        "activity": activity,
        "access_request_id": request.id if request else None,
        "access_request_status": request.status if request else None,
        "message": (
            f"Ce projet est déjà en cours par {owner_name}. "
            "Envoyez une demande pour obtenir l’accès."
        ),
    }


@router.get("/access-requests")
def list_access_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(ProjectAccessRequest)
        .filter(
            or_(
                ProjectAccessRequest.owner_id == current_user.id,
                ProjectAccessRequest.requester_id == current_user.id,
            )
        )
        .order_by(ProjectAccessRequest.created_at.desc())
        .limit(50)
        .all()
    )
    items = [_access_request_payload(db, row, current_user) for row in rows]
    return {"unread_count": sum(1 for item in items if item["unread"]), "items": items}


@router.post("/access-requests/{request_id}/seen")
def mark_access_request_seen(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.query(ProjectAccessRequest).filter(ProjectAccessRequest.id == request_id).first()
    if not row or current_user.id not in {row.owner_id, row.requester_id}:
        raise HTTPException(status_code=404, detail="Notification introuvable.")
    if row.owner_id == current_user.id:
        row.owner_seen_at = datetime.utcnow()
    if row.requester_id == current_user.id:
        row.requester_seen_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _access_request_payload(db, row, current_user)


@router.patch("/access-requests/{request_id}")
def respond_to_access_request(
    request_id: int,
    payload: ProjectAccessDecision,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.query(ProjectAccessRequest).filter(ProjectAccessRequest.id == request_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Demande d’accès introuvable.")
    if current_user.id != row.owner_id:
        raise HTTPException(status_code=403, detail="Seul le consultant responsable peut répondre.")
    row.status = payload.status
    row.responded_at = datetime.utcnow()
    row.owner_seen_at = datetime.utcnow()
    row.requester_seen_at = None
    db.commit()
    db.refresh(row)
    return _access_request_payload(db, row, current_user)


def _access_request_payload(db: Session, row: ProjectAccessRequest, current_user: User) -> dict:
    project = row.project
    owner = db.query(User).filter(User.id == row.owner_id).first()
    requester = db.query(User).filter(User.id == row.requester_id).first()
    is_owner = row.owner_id == current_user.id
    unread = (
        row.owner_seen_at is None
        if is_owner
        else row.status != "pending" and row.requester_seen_at is None
    )
    return {
        "id": row.id,
        "project_id": row.project_id,
        "requester_id": row.requester_id,
        "requester_name": requester.full_name if requester else "Consultant",
        "owner_id": row.owner_id,
        "owner_name": owner.full_name if owner else "Consultant",
        "organisme": project.organisme,
        "project_name": project.project_name,
        "subproject_name": project.subproject_name,
        "year": project.year,
        "status": row.status,
        "direction": "incoming" if is_owner else "outgoing",
        "unread": unread,
        "created_at": row.created_at,
        "responded_at": row.responded_at,
    }


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
    eligibility_by_run = {}
    if diagnostic_ids:
        eligibility_rows = (
            db.query(DiagnosticRun.id, DiagnosticRun.raw_result_json)
            .filter(DiagnosticRun.id.in_(diagnostic_ids))
            .all()
        )
        eligibility_by_run = {
            row[0]: extract_diagnostic_eligibility_score(row[1])
            for row in eligibility_rows
        }

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
        diagnostic_id = diagnostic.id if diagnostic is not None else None
        eligibility_score = eligibility_by_run.get(diagnostic_id)
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
                    "eligibility": {
                        "score": eligibility_score,
                        "available": eligibility_score is not None,
                    },
                    "verrous": verrous_by_run.get(
                        diagnostic_id,
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
    selection = ProjectSelection(
        organisme=payload.organisme,
        project_name=payload.project_name,
        subproject_name=payload.subproject_name,
        year=payload.year,
    )
    active_projects = _active_identity_projects(db, selection)
    if active_projects:
        project, _activity = active_projects[0]
        owner = db.query(User).filter(User.id == project.consultant_id).first()
        accepted = (
            db.query(ProjectAccessRequest.id)
            .filter(
                ProjectAccessRequest.project_id == project.id,
                ProjectAccessRequest.requester_id == current_user.id,
                ProjectAccessRequest.status == "accepted",
            )
            .first()
        )
        if project.consultant_id == current_user.id or accepted:
            message = "Ce dossier existe déjà et vous est accessible. Ouvrez le projet existant."
        else:
            owner_name = owner.full_name if owner else "un autre consultant"
            message = (
                f"Ce projet est déjà en cours par {owner_name}. "
                "Envoyez-lui une demande d’accès."
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": message, "project_id": project.id, "code": "PROJECT_LOCKED"},
        )

    subproject_name = _clean_optional(payload.subproject_name)
    folder_segments = [
        clean_path_segment(payload.organisme),
        clean_path_segment(payload.project_name),
    ]
    if subproject_name:
        folder_segments.extend(["subprojects", clean_path_segment(subproject_name)])
    folder_segments.extend(["years", clean_path_segment(str(payload.year))])
    ai_folder = "/".join(folder_segments)

    project = Project(
        consultant_id=current_user.id,
        organisme=payload.organisme.strip(),
        project_name=payload.project_name.strip(),
        subproject_name=subproject_name,
        year=str(payload.year).strip(),
        domain_label=payload.domain_label,
        status="Créé",
        ai_folder=ai_folder,
    )

    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/access-requests", status_code=status.HTTP_201_CREATED)
def request_project_access(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    if project.consultant_id == current_user.id:
        raise HTTPException(status_code=400, detail="Vous êtes déjà responsable de ce projet.")
    if not _activity_labels(db, project.id):
        raise HTTPException(status_code=400, detail="Ce projet n’a pas encore d’activité à partager.")

    row = (
        db.query(ProjectAccessRequest)
        .filter(
            ProjectAccessRequest.project_id == project.id,
            ProjectAccessRequest.requester_id == current_user.id,
        )
        .first()
    )
    if row and row.status == "accepted":
        return _access_request_payload(db, row, current_user)
    if row and row.status == "pending":
        return _access_request_payload(db, row, current_user)
    if row:
        row.status = "pending"
        row.created_at = datetime.utcnow()
        row.responded_at = None
        row.owner_seen_at = None
        row.requester_seen_at = None
        row.owner_id = project.consultant_id
    else:
        row = ProjectAccessRequest(
            project_id=project.id,
            requester_id=current_user.id,
            owner_id=project.consultant_id,
            status="pending",
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return _access_request_payload(db, row, current_user)


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
    if project.consultant_id != current_user.id and current_user.role not in {"admin", "superadmin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seul le consultant responsable peut modifier l’identité du projet.",
        )

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
    if project.consultant_id != current_user.id and current_user.role not in {"admin", "superadmin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seul le consultant responsable peut supprimer ce projet.",
        )
    db.delete(project)
    db.commit()

    return {
        "status": "deleted",
        "project_id": project_id,
    }
