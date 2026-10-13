from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, Path, Query

from core.deps import get_current_user
from agents.EnnoScholarV3.config import ConfigurationError
from agents.EnnoScholarV3.models import ChatRequest, SessionCreate, SelectionRequest
from agents.EnnoScholarV3.service import get_service, SessionNotFound, SessionBusy

router = APIRouter(prefix="/scholar-v3", tags=["ennoscholar-v3"])
logger = logging.getLogger(__name__)
def identifier():
    # FastAPI binds each Path field to its parameter name; never reuse one instance.
    return Path(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")


def invoke(action):
    try:
        return action()
    except ConfigurationError as exc:
        raise HTTPException(503, str(exc)) from None
    except SessionNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except SessionBusy as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception as exc:
        logger.warning("EnnoScholar V3 API error type=%s", type(exc).__name__)
        raise HTTPException(502, "EnnoScholar V3 est indisponible. Réessayez ou vérifiez la configuration.") from None


@router.post("/chat", status_code=202)
def chat(payload: ChatRequest, user=Depends(get_current_user)):
    return invoke(lambda: get_service().start(int(user.id), payload))


@router.post("/sessions", status_code=201)
def create_session(payload: SessionCreate, user=Depends(get_current_user)):
    return invoke(lambda: get_service().create(int(user.id), payload.title))


@router.get("/sessions")
def sessions(user=Depends(get_current_user)):
    return invoke(lambda: get_service().sessions(int(user.id)))


@router.get("/sessions/{session_id}")
def session(session_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().read(int(user.id), session_id))


@router.get("/sessions/{session_id}/articles")
def articles(session_id: str = identifier(), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), user=Depends(get_current_user)):
    return invoke(lambda: get_service().articles(int(user.id), session_id, offset, limit))


@router.patch("/sessions/{session_id}/articles/{article_id}")
def select_article(payload: SelectionRequest, session_id: str = identifier(), article_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().select_article(int(user.id), session_id, article_id, payload.selected))


@router.get("/runs/{run_id}")
def run(run_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().run(int(user.id), run_id))


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().cancel(int(user.id), run_id))
