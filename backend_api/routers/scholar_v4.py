from __future__ import annotations

import logging
import re
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Path, Query, File, Form, UploadFile

from core.deps import get_current_user
from agents.EnnoScholarV4.config import ConfigurationError
from agents.EnnoScholarV4.models import ChatRequest, SessionCreate, SelectionRequest
from agents.EnnoScholarV4.service import get_service, SessionNotFound, SessionBusy

router = APIRouter(prefix="/scholar-v4", tags=["ennoscholar-v4"])
logger = logging.getLogger(__name__)
def identifier():
    # FastAPI binds each Path field to its parameter name; never reuse one instance.
    return Path(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")


def invoke(action):
    try:
        return action()
    except HTTPException:
        raise
    except ConfigurationError as exc:
        raise HTTPException(503, str(exc)) from None
    except SessionNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except SessionBusy as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception as exc:
        logger.warning("EnnoScholar V4 API error type=%s", type(exc).__name__)
        raise HTTPException(502, "EnnoScholar V4 est indisponible. Réessayez ou vérifiez la configuration.") from None


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
def articles(session_id: str = identifier(), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), query: str = Query("", max_length=1500), exclude_ids: list[str] = Query(default=[], max_length=20), category: Literal["all", "relevant", "retained_complementary", "unexamined", "examined_pending", "rejected"] = "all", user=Depends(get_current_user)):
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", value) for value in exclude_ids):
        raise HTTPException(422, "Identifiant de publication invalide.")
    return invoke(lambda: get_service().articles(int(user.id), session_id, offset, limit, query, exclude_ids, category))


@router.get("/sessions/{session_id}/articles/{article_id}")
def article(session_id: str = identifier(), article_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().article(int(user.id), session_id, article_id))


@router.get("/sessions/{session_id}/memory")
def memory(session_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().memory(int(user.id), session_id))


@router.post("/sessions/{session_id}/pdf", status_code=201)
def import_pdf(session_id: str = identifier(), file: UploadFile = File(...), title: str = Form("", max_length=1000), article_id: str = Form("", max_length=80), user=Depends(get_current_user)):
    # File.size is available after multipart parsing; bound reading before allocation.
    if file.size is not None and file.size > 15*1024*1024:
        raise HTTPException(413, "PDF limité à 15 Mo.")
    data = file.file.read(15*1024*1024+1)
    return invoke(lambda: get_service().import_pdf(int(user.id), session_id, data, file.filename or "article.pdf", title, article_id))


@router.post("/sessions/{session_id}/documents", status_code=201)
def import_document(session_id: str = identifier(), file: UploadFile = File(...), user=Depends(get_current_user)):
    def action():
        service = get_service()
        # Authorize before reading or parsing uploaded bytes.
        with service.session_factory() as db:
            service.owned(db, int(user.id), session_id)
        limit = service.config.max_pdf_bytes
        if file.size is not None and file.size > limit:
            raise HTTPException(413, "Document trop volumineux.")
        return service.import_document(int(user.id), session_id, file.file.read(limit+1), file.filename or "document")
    return invoke(action)


@router.patch("/sessions/{session_id}/articles/{article_id}")
def select_article(payload: SelectionRequest, session_id: str = identifier(), article_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().select_article(int(user.id), session_id, article_id, payload.selected))


@router.get("/runs/{run_id}")
def run(run_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().run(int(user.id), run_id))


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().cancel(int(user.id), run_id))
