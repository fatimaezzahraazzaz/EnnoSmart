from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from core.deps import get_current_user
from agents.EnnoScholarV2.config import ScholarConfigurationError
from agents.EnnoScholarV2.models import ArticlePage, ChatRequest, ChatResponse
from agents.EnnoScholarV2.service import SessionBusy, SessionNotFound, get_service

router = APIRouter(prefix="/scholar-v2", tags=["ennoscholar-v2"])
logger = logging.getLogger(__name__)


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, current_user=Depends(get_current_user)) -> ChatResponse:
    try:
        return get_service().chat(int(current_user.id), payload)
    except ScholarConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except SessionBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:
        # Do not log request bodies or credential-bearing upstream errors.
        logger.warning("EnnoScholar V2 upstream failure: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="EnnoScholar V2 ne peut pas terminer cette demande. Vérifiez la configuration API ou réessayez.") from None


@router.get("/sessions/{session_id}/articles", response_model=ArticlePage)
def articles(session_id: str = Path(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$"),
        offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=50),
        current_user=Depends(get_current_user)) -> ArticlePage:
    try:
        return get_service().list_articles(int(current_user.id), session_id, offset, limit)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
