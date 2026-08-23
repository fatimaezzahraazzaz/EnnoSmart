# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.execution_lock import SessionBusyError, session_execution_lock
from modules.LLM.llm_concurrency import LLMCapacityTimeoutError
from schemas.guided_research import (
    GuidedResearchMessageCreate,
    GuidedResearchSessionCreate,
    GuidedResearchSourceDecision,
)
from services.guided_research_service import (
    create_guided_research_session,
    delete_guided_research_session,
    decide_guided_research_sources,
    list_guided_research_sessions,
    read_guided_research_corpus,
    read_guided_research_session,
    remove_guided_research_corpus_article,
    send_guided_research_message,
)
from services.project_service import get_project_for_user


def build_guided_research_router(
    *,
    get_db_dependency: Callable[..., Session],
    get_current_user_dependency: Callable[..., Any],
) -> APIRouter:
    """Construit le router sans imposer le nom de tes dépendances d'auth.

    Dans main.py :
        app.include_router(
            build_guided_research_router(
                get_db_dependency=get_db,
                get_current_user_dependency=get_current_user,
            ),
            prefix="/api",
        )
    """
    router = APIRouter(tags=["EnnoScholar Guided Research"])

    @router.get("/projects/{project_id}/guided-research/sessions")
    def list_sessions(
        project_id: int,
        limit: int = 50,
        db: Session = Depends(get_db_dependency),
        current_user: Any = Depends(get_current_user_dependency),
    ):
        project = get_project_for_user(db, project_id, current_user)
        try:
            sessions = list_guided_research_sessions(
                db,
                project,
                limit=limit,
            )
            return {"ok": True, "sessions": sessions}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/projects/{project_id}/guided-research/sessions")
    def create_session(
        project_id: int,
        payload: GuidedResearchSessionCreate,
        db: Session = Depends(get_db_dependency),
        current_user: Any = Depends(get_current_user_dependency),
    ):
        project = get_project_for_user(db, project_id, current_user)
        try:
            session = create_guided_research_session(
                db,
                project,
                user_id=getattr(current_user, "id", None),
                target_mode=payload.target_mode,
                entry_module=payload.entry_module,
                handoff=(
                    payload.handoff.model_dump(mode="json", exclude_none=True)
                    if payload.handoff is not None
                    else None
                ),
            )
            return {"ok": True, "session": session}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/projects/{project_id}/guided-research/sessions/{session_id}")
    def get_session(
        project_id: int,
        session_id: str,
        db: Session = Depends(get_db_dependency),
        current_user: Any = Depends(get_current_user_dependency),
    ):
        project = get_project_for_user(db, project_id, current_user)
        try:
            result = read_guided_research_session(db, session_id)
            if int(result["session"]["project_id"]) != int(project.id):
                raise HTTPException(status_code=403, detail="Session d'un autre projet.")
            return {"ok": True, **result}
        except HTTPException:
            raise
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get(
        "/projects/{project_id}/guided-research/sessions/{session_id}/corpus"
    )
    def get_session_corpus(
        project_id: int,
        session_id: str,
        db: Session = Depends(get_db_dependency),
        current_user: Any = Depends(get_current_user_dependency),
    ):
        project = get_project_for_user(db, project_id, current_user)
        try:
            return read_guided_research_corpus(
                db, project, session_id=session_id
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.delete(
        "/projects/{project_id}/guided-research/sessions/{session_id}"
        "/corpus/{article_id}"
    )
    def remove_session_corpus_article(
        project_id: int,
        session_id: str,
        article_id: int,
        db: Session = Depends(get_db_dependency),
        current_user: Any = Depends(get_current_user_dependency),
    ):
        project = get_project_for_user(db, project_id, current_user)
        try:
            return remove_guided_research_corpus_article(
                db,
                project,
                session_id=session_id,
                article_id=article_id,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.delete("/projects/{project_id}/guided-research/sessions/{session_id}")
    def delete_session(
        project_id: int,
        session_id: str,
        db: Session = Depends(get_db_dependency),
        current_user: Any = Depends(get_current_user_dependency),
    ):
        project = get_project_for_user(db, project_id, current_user)
        try:
            delete_guided_research_session(
                db,
                project,
                session_id=session_id,
            )
            return {"ok": True, "session_id": session_id}
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/projects/{project_id}/guided-research/sessions/{session_id}/messages")
    def send_message(
        project_id: int,
        session_id: str,
        payload: GuidedResearchMessageCreate,
        db: Session = Depends(get_db_dependency),
        current_user: Any = Depends(get_current_user_dependency),
    ):
        project = get_project_for_user(db, project_id, current_user)
        try:
            with session_execution_lock(
                "guided-research",
                f"{project.id}:{session_id}",
            ):
                response = send_guided_research_message(
                    db,
                    project,
                    session_id=session_id,
                    message=payload.message,
                )
            return {"ok": True, "response": response}
        except SessionBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LLMCapacityTimeoutError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/projects/{project_id}/guided-research/sessions/{session_id}/sources/decision")
    def decide_sources(
        project_id: int,
        session_id: str,
        payload: GuidedResearchSourceDecision,
        db: Session = Depends(get_db_dependency),
        current_user: Any = Depends(get_current_user_dependency),
    ):
        project = get_project_for_user(db, project_id, current_user)
        try:
            with session_execution_lock(
                "guided-research",
                f"{project.id}:{session_id}",
            ):
                response = decide_guided_research_sources(
                    db,
                    project,
                    session_id=session_id,
                    candidate_ids=payload.candidate_ids,
                    decision=payload.decision,
                    reason=payload.reason,
                    prepare_after_acceptance=payload.prepare_after_acceptance,
                )
            return {"ok": True, "response": response}
        except SessionBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LLMCapacityTimeoutError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return router
