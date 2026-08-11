from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from core.deps import get_db, require_admin, require_superadmin
from core.security import hash_password
from db.models import (
    AdminAuditLog,
    DiagnosticRun,
    Document,
    PlatformSetting,
    Project,
    ProjectWorkflow,
    ScholarRun,
    User,
    UserPreference,
    UserProfile,
)
from schemas.admin import (
    AIModelSettings,
    AdminUserCreate,
    AdminUserUpdate,
    ProjectAssignmentUpdate,
    ProjectWorkflowUpdate,
)
from services.platform_settings_service import merge_ai_settings, write_runtime_ai_settings


router = APIRouter(prefix="/admin", tags=["administration"])


def _audit(
    db: Session,
    actor: User,
    action: str,
    entity_type: str,
    entity_id: int | str | None,
    metadata: dict | None = None,
) -> None:
    db.add(
        AdminAuditLog(
            actor_user_id=actor.id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            metadata_json=metadata,
        )
    )


def _profile_for(db: Session, user_id: int) -> UserProfile | None:
    return db.query(UserProfile).filter(UserProfile.user_id == user_id).first()


def _user_payload(db: Session, user: User) -> dict:
    profile = _profile_for(db, user.id)
    project_count = db.query(func.count(Project.id)).filter(Project.consultant_id == user.id).scalar() or 0
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
        "company": profile.company if profile else None,
        "job_title": profile.job_title if profile else None,
        "project_count": int(project_count),
    }


def _workflow_for(db: Session, project: Project, create: bool = False) -> ProjectWorkflow | None:
    workflow = db.query(ProjectWorkflow).filter(ProjectWorkflow.project_id == project.id).first()
    if not workflow and create:
        workflow = ProjectWorkflow(project_id=project.id)
        db.add(workflow)
        db.flush()
    return workflow


def _project_payload(db: Session, project: Project) -> dict:
    workflow = _workflow_for(db, project)
    consultant = db.query(User).filter(User.id == project.consultant_id).first()
    return {
        "id": project.id,
        "organisme": project.organisme,
        "project_name": project.project_name,
        "year": project.year,
        "domain_label": project.domain_label,
        "status": project.status,
        "created_at": project.created_at,
        "consultant": {
            "id": consultant.id,
            "full_name": consultant.full_name,
            "email": consultant.email,
        } if consultant else None,
        "workflow": {
            "stage": workflow.stage,
            "progress_percent": workflow.progress_percent,
            "priority": workflow.priority,
            "due_date": workflow.due_date,
            "notes": workflow.notes,
            "updated_at": workflow.updated_at,
        } if workflow else {
            "stage": "collecte",
            "progress_percent": 10,
            "priority": "normale",
            "due_date": None,
            "notes": None,
            "updated_at": None,
        },
        "counts": {
            "documents": db.query(func.count(Document.id)).filter(Document.project_id == project.id).scalar() or 0,
            "diagnostics": db.query(func.count(DiagnosticRun.id)).filter(DiagnosticRun.project_id == project.id).scalar() or 0,
            "scholar_runs": db.query(func.count(ScholarRun.id)).filter(ScholarRun.project_id == project.id).scalar() or 0,
        },
    }


@router.get("/overview")
def overview(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    users = db.query(User).all()
    projects = db.query(Project).all()
    workflows = db.query(ProjectWorkflow).all()
    stages: dict[str, int] = {}
    for workflow in workflows:
        stages[workflow.stage] = stages.get(workflow.stage, 0) + 1
    stages["collecte"] = stages.get("collecte", 0) + max(0, len(projects) - len(workflows))
    return {
        "users": {
            "total": len(users),
            "active": sum(1 for user in users if user.is_active),
            "consultants": sum(1 for user in users if user.role == "consultant"),
            "admins": sum(1 for user in users if user.role in {"admin", "superadmin"}),
        },
        "projects": {
            "total": len(projects),
            "completed": sum(1 for workflow in workflows if workflow.stage == "finalise"),
            "unassigned": sum(1 for project in projects if not project.consultant_id),
            "by_stage": stages,
        },
        "generated_at": datetime.utcnow(),
    }


@router.get("/users")
def list_users(
    search: str | None = Query(default=None, max_length=100),
    role: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    query = db.query(User)
    if role:
        query = query.filter(User.role == role)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(or_(User.full_name.ilike(term), User.email.ilike(term)))
    return [_user_payload(db, user) for user in query.order_by(User.full_name.asc()).all()]


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: AdminUserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if payload.role == "superadmin" and current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Seul un superadmin peut créer ce rôle.")
    email = str(payload.email).lower().strip()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="Cet email est déjà utilisé.")
    user = User(
        full_name=payload.full_name.strip(),
        email=email,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(UserProfile(user_id=user.id, company=payload.company, job_title=payload.job_title))
    db.add(UserPreference(user_id=user.id))
    _audit(db, current_user, "user.created", "user", user.id, {"role": user.role})
    db.commit()
    db.refresh(user)
    return _user_payload(db, user)


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    payload: AdminUserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    if target.role == "superadmin" and current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Ce compte est protégé.")
    if payload.role is not None:
        if current_user.role != "superadmin":
            raise HTTPException(status_code=403, detail="Seul un superadmin peut modifier les rôles.")
        if target.id == current_user.id and payload.role != "superadmin":
            raise HTTPException(status_code=400, detail="Vous ne pouvez pas retirer votre propre rôle superadmin.")
        target.role = payload.role
    if payload.is_active is not None:
        if target.id == current_user.id and not payload.is_active:
            raise HTTPException(status_code=400, detail="Vous ne pouvez pas désactiver votre propre compte.")
        target.is_active = payload.is_active
    if payload.full_name is not None:
        target.full_name = payload.full_name.strip()
    _audit(db, current_user, "user.updated", "user", target.id, payload.model_dump(exclude_unset=True))
    db.commit()
    db.refresh(target)
    return _user_payload(db, target)


@router.get("/projects")
def list_all_projects(
    search: str | None = Query(default=None, max_length=100),
    consultant_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    query = db.query(Project)
    if consultant_id:
        query = query.filter(Project.consultant_id == consultant_id)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(or_(Project.organisme.ilike(term), Project.project_name.ilike(term)))
    return [_project_payload(db, project) for project in query.order_by(Project.created_at.desc()).all()]


@router.patch("/projects/{project_id}/assignment")
def assign_project(
    project_id: int,
    payload: ProjectAssignmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    consultant = db.query(User).filter(User.id == payload.consultant_id, User.is_active.is_(True)).first()
    if not project:
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    if not consultant or consultant.role not in {"consultant", "admin"}:
        raise HTTPException(status_code=400, detail="Consultant actif introuvable.")
    previous_id = project.consultant_id
    project.consultant_id = consultant.id
    _audit(
        db,
        current_user,
        "project.assigned",
        "project",
        project.id,
        {"previous_consultant_id": previous_id, "consultant_id": consultant.id},
    )
    db.commit()
    return _project_payload(db, project)


@router.patch("/projects/{project_id}/workflow")
def update_project_workflow(
    project_id: int,
    payload: ProjectWorkflowUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    workflow = _workflow_for(db, project, create=True)
    for field, value in payload.model_dump().items():
        setattr(workflow, field, value)
    workflow.updated_by_user_id = current_user.id
    workflow.updated_at = datetime.utcnow()
    project.status = payload.stage
    _audit(db, current_user, "project.workflow.updated", "project", project.id, payload.model_dump(mode="json"))
    db.commit()
    return _project_payload(db, project)


@router.get("/ai-settings")
def get_ai_settings(
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    setting = db.query(PlatformSetting).filter(PlatformSetting.key == "ai_models").first()
    return merge_ai_settings(setting.value_json if setting else None)


@router.put("/ai-settings")
def update_ai_settings(
    payload: AIModelSettings,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_superadmin),
):
    value = payload.model_dump()
    setting = db.query(PlatformSetting).filter(PlatformSetting.key == "ai_models").first()
    if not setting:
        setting = PlatformSetting(key="ai_models", value_json=value, description="Configuration IA active")
        db.add(setting)
    else:
        setting.value_json = value
    setting.updated_by_user_id = current_user.id
    setting.updated_at = datetime.utcnow()
    runtime_path = write_runtime_ai_settings(value)
    _audit(db, current_user, "ai_settings.updated", "platform_setting", "ai_models", {"provider": value["provider"]})
    db.commit()
    return {**value, "runtime_config": str(runtime_path), "applied": True}


@router.get("/audit-log")
def audit_log(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    logs = db.query(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id": item.id,
            "actor_user_id": item.actor_user_id,
            "action": item.action,
            "entity_type": item.entity_type,
            "entity_id": item.entity_id,
            "metadata": item.metadata_json,
            "created_at": item.created_at,
        }
        for item in logs
    ]
