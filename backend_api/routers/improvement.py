from __future__ import annotations

from services.cir_background_service_v321 import (
    enqueue_full_cir_job,
    read_background_status,
    mirror_status_into_session,
    redis_health,
    should_background_message,
)

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from core.deps import get_current_user, get_db, require_agent_enabled
from db.models import User
from schemas.improvement import (
    ImprovementDecisionCreate,
    ImprovementMessageCreate,
    ImprovementRestoreCreate,
    ImprovementSessionCreate,
    ImprovementSourceDecisionCreate,
)
from services.improvement_service import (
    create_session,
    decide_version,
    delete_session,
    decide_research_sources,
    get_session,
    list_session_summaries,
    restore_version,
    send_message,
    serialize_session,
)
from services.project_service import get_project_for_user
from services.improvement_context_service import get_improvement_project_context


router = APIRouter(
    prefix="/api/projects/{project_id}/improvements",
    tags=["ennoamelioration"],
    dependencies=[Depends(require_agent_enabled("improvement"))],
)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/context")
def get_project_improvement_context(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    return {"ok": True, "context": get_improvement_project_context(db, project)}


@router.get("/sessions")
def list_improvement_sessions(
    project_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    return {
        "ok": True,
        "sessions": list_session_summaries(db, project.id, limit),
    }


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_improvement_session(
    project_id: int,
    payload: ImprovementSessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    try:
        session = create_session(
            db,
            project,
            user_id=current_user.id,
            **payload.model_dump(),
        )
        return {"ok": True, "session": serialize_session(session)}
    except Exception as exc:
        db.rollback()
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}")
def get_improvement_session(
    project_id: int,
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    try:
        return {"ok": True, "session": serialize_session(get_session(db, project.id, session_id))}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.delete("/sessions/{session_id}")
def remove_improvement_session(
    project_id: int,
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    try:
        delete_session(db, project.id, session_id)
        return {"ok": True, "session_id": session_id}
    except Exception as exc:
        db.rollback()
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/messages")
def create_improvement_message(
    project_id: int,
    session_id: str,
    payload: ImprovementMessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(
        db,
        project_id,
        current_user,
    )
    try:
        # V3.21 : le CIR complet quitte la requête FastAPI immédiatement.
        # Les demandes de SECTION restent totalement synchrones et conservent
        # la validation humaine des sources.
        if should_background_message(
            db,
            project.id,
            session_id,
            payload,
        ):
            job = enqueue_full_cir_job(
                project_id=project.id,
                session_id=session_id,
                user_id=current_user.id,
                payload=payload.model_dump(),
            )
            mirror_status_into_session(
                db,
                project.id,
                session_id,
                job,
            )
            session = get_session(
                db,
                project.id,
                session_id,
            )
            return {
                "ok": True,
                "background": True,
                "background_job": job,
                "session": serialize_session(session),
                "candidate_version_id": None,
            }

        session, candidate = send_message(
            db,
            project,
            session_id,
            **payload.model_dump(),
        )
        return {
            "ok": True,
            "background": False,
            "session": serialize_session(session),
            "candidate_version_id": (
                candidate.id
                if candidate
                else None
            ),
        }
    except Exception as exc:
        db.rollback()
        raise _http_error(exc) from exc



@router.post("/sessions/{session_id}/versions/{version_id}/decision")
def decide_improvement_version(
    project_id: int,
    session_id: str,
    version_id: str,
    payload: ImprovementDecisionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    try:
        session = decide_version(
            db,
            project.id,
            session_id,
            version_id,
            decision=payload.decision,
            reason=payload.reason,
        )
        return {"ok": True, "session": serialize_session(session)}
    except Exception as exc:
        db.rollback()
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/versions/{version_id}/restore")
def restore_improvement_version(
    project_id: int,
    session_id: str,
    version_id: str,
    payload: ImprovementRestoreCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    try:
        session = restore_version(db, project.id, session_id, version_id, reason=payload.reason)
        return {"ok": True, "session": serialize_session(session)}
    except Exception as exc:
        db.rollback()
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/sources/decision")
def decide_improvement_sources(
    project_id: int,
    session_id: str,
    payload: ImprovementSourceDecisionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    try:
        session = decide_research_sources(
            db,
            project,
            session_id,
            candidate_ids=payload.candidate_ids,
            decision=payload.decision,
            reason=payload.reason,
        )
        return {"ok": True, "session": serialize_session(session)}
    except Exception as exc:
        db.rollback()
        raise _http_error(exc) from exc

@router.get("/sessions/{session_id}/background")
def get_improvement_background_job(
    project_id: int,
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(
        db,
        project_id,
        current_user,
    )
    # Vérifie aussi que la session appartient au projet/utilisateur.
    get_session(
        db,
        project.id,
        session_id,
    )
    return {
        "ok": True,
        "background_job": read_background_status(
            project.id,
            session_id,
        ),
    }


@router.get("/background/health")
def get_improvement_background_health(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_project_for_user(
        db,
        project_id,
        current_user,
    )
    return {
        "ok": True,
        "redis": redis_health(),
    }
