from __future__ import annotations

import logging
import re
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Path, Query, File, Form, UploadFile

from core.deps import get_current_user
from agents.EnnoScholarV4.config import ConfigurationError
from agents.EnnoScholarV4.models import ChatRequest, SessionCreate, SelectionRequest, OrderRequest, DiagnosticContextUpdate
from agents.EnnoScholarV4.service import get_service, SessionNotFound, SessionBusy
from agents.EnnoScholarV4.manuscript import DocumentAction
from fastapi.responses import Response

router = APIRouter(prefix="/scholar-v4", tags=["ennoscholar-v4"])
logger = logging.getLogger(__name__)


@router.on_event("startup")
def start_writing_job_watchdog():
    """Automatic drafting jobs are resumed after a backend restart without any request."""
    from agents.EnnoScholarV4.service import start_watchdog
    try:
        start_watchdog()
    except Exception as exc:
        logger.warning("EnnoScholar V4 watchdog not started type=%s", type(exc).__name__)


@router.on_event("shutdown")
def stop_writing_job_watchdog():
    from agents.EnnoScholarV4.service import stop_watchdog
    stop_watchdog()
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
    return invoke(lambda: get_service().create(int(user.id), payload.title, options=payload))


@router.get("/diagnostic-projects")
def diagnostic_projects(user=Depends(get_current_user)):
    return invoke(lambda: get_service().diagnostic_projects(int(user.id)))


@router.get("/diagnostic-projects/{project_id}")
def diagnostic_preview(project_id: int, user=Depends(get_current_user)):
    return invoke(lambda: get_service().diagnostic_preview(int(user.id), project_id))


@router.get("/sessions/{session_id}/project-context")
def project_context(session_id: str = identifier(), kind: Literal["facts", "locks", "evidence", "synthesis"] = "facts",
                    offset: int = Query(0, ge=0), limit: int = Query(5, ge=1, le=20), scope: Literal["active", "general", "all"] = "active", user=Depends(get_current_user)):
    return invoke(lambda: get_service().project_context(int(user.id), session_id, kind, offset, limit, scope))


@router.patch("/sessions/{session_id}/project-context")
def update_project_context(payload: DiagnosticContextUpdate, session_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().update_project_context(int(user.id), session_id, payload))


@router.get("/spending")
def spending(limit: int = Query(100, ge=1, le=500), user=Depends(get_current_user)):
    # The authenticated consultant's own ledger and balances only.
    return invoke(lambda: get_service().spending(int(user.id), limit))


@router.get("/sessions")
def sessions(user=Depends(get_current_user)):
    return invoke(lambda: get_service().sessions(int(user.id)))


@router.get("/sessions/{session_id}")
def session(session_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().read(int(user.id), session_id))


@router.get("/sessions/{session_id}/articles")
def articles(session_id: str = identifier(), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), query: str = Query("", max_length=1500), exclude_ids: list[str] = Query(default=[], max_length=20), category: Literal["all", "relevant", "retained_complementary", "unexamined", "examined_pending", "rejected", "on_hold"] = "all", user=Depends(get_current_user)):
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", value) for value in exclude_ids):
        raise HTTPException(422, "Identifiant de publication invalide.")
    return invoke(lambda: get_service().articles(int(user.id), session_id, offset, limit, query, exclude_ids, category))


@router.get("/sessions/{session_id}/articles/{article_id}")
def article(session_id: str = identifier(), article_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().article(int(user.id), session_id, article_id))


@router.get("/sessions/{session_id}/memory")
def memory(session_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().memory(int(user.id), session_id))


@router.get("/sessions/{session_id}/document")
def document(session_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().document_read(int(user.id), session_id))


@router.post("/sessions/{session_id}/document")
def edit_document(payload: DocumentAction, session_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().document_action(int(user.id), session_id, payload))


@router.get("/sessions/{session_id}/document/export/{format}")
def export_document(format: Literal["docx","pdf"], version_id: str = Query(min_length=8,max_length=80,pattern=r"^[a-zA-Z0-9_-]+$"), session_id: str = identifier(), user=Depends(get_current_user)):
    data=invoke(lambda:get_service().export_document(int(user.id),session_id,version_id,format))
    media="application/pdf" if format=="pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(data,media_type=media,headers={"Content-Disposition":f'attachment; filename="EnnoScholar-V4-{version_id}.{format}"',"Cache-Control":"no-store"})


@router.get("/sessions/{session_id}/document/figures/{figure_id}")
def document_figure(session_id: str = identifier(),figure_id: str = identifier(),user=Depends(get_current_user)):
    data=invoke(lambda:get_service().document_figure(int(user.id),session_id,figure_id))
    return Response(data,media_type="image/png",headers={"Cache-Control":"no-store"})


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
    return invoke(lambda: get_service().select_article(int(user.id), session_id, article_id, payload.selected, payload.decision))


@router.post("/sessions/{session_id}/articles/order")
def order_articles(payload: OrderRequest, session_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().order_articles(int(user.id), session_id, payload.article_ids))


@router.get("/runs/{run_id}")
def run(run_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().run(int(user.id), run_id))


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().cancel(int(user.id), run_id))


@router.get("/sessions/{session_id}/writing-job")
def writing_job(session_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().writing_job(int(user.id), session_id))


@router.post("/sessions/{session_id}/writing-job/cancel")
def cancel_writing_job(session_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().cancel_job(int(user.id), session_id))


@router.post("/sessions/{session_id}/writing-job/resume")
def resume_writing_job(session_id: str = identifier(), user=Depends(get_current_user)):
    return invoke(lambda: get_service().resume_job(int(user.id), session_id))
