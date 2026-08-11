# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

import chromadb
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from core.deps import get_current_user, get_db
from db.models import DiagnosticRun, User
from modules.RAG.diagnostic_chat_service import DiagnosticRAGChatService
from modules.RAG.project_store import ProjectStore
from services.project_service import get_project_for_user


router = APIRouter(
    prefix="/projects/{project_id}/diagnostic-chat",
    tags=["ennodiagnostic-chat"],
)


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=5000)


class DiagnosticDocumentScope(BaseModel):
    document_id: Optional[int | str] = None
    document_name: Optional[str] = Field(default=None, max_length=1000)


class DiagnosticChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=5000)
    history: List[ChatHistoryMessage] = Field(default_factory=list, max_length=12)
    document_scope: Optional[DiagnosticDocumentScope] = None


def _latest_run_for_project(
    db: Session,
    project_id: int,
) -> DiagnosticRun | None:
    return (
        db.query(DiagnosticRun)
        .filter(DiagnosticRun.project_id == project_id)
        .order_by(DiagnosticRun.created_at.desc())
        .first()
    )


def _diagnostic_payload(run: DiagnosticRun | None) -> Dict[str, Any]:
    if run is None:
        return {}

    raw = getattr(run, "raw_result_json", None)
    return raw if isinstance(raw, dict) else {}


def _project_collection_status(project: Any) -> Dict[str, Any]:
    store = ProjectStore(
        organisme=project.organisme,
        project=project.project_name,
        year=project.year,
    ).ensure()

    collection_name = (
        f"ennosmart_{store.organisme_id}_{store.project_id}_{store.year_id}"
    )

    try:
        client = chromadb.PersistentClient(path=str(store.chroma_dir))
        collection = client.get_collection(collection_name)
        chunks_count = int(collection.count())
    except Exception:
        chunks_count = 0

    return {
        "collection_name": collection_name,
        "chroma_dir": str(store.chroma_dir),
        "chunks_count": chunks_count,
    }


@router.get("/status")
def diagnostic_chat_status(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    latest_run = _latest_run_for_project(db, project.id)
    collection = _project_collection_status(project)

    diagnostic_payload = _diagnostic_payload(latest_run)
    diagnostic_ready = bool(
        latest_run
        and (
            diagnostic_payload
            or getattr(latest_run, "report_path", None)
            or getattr(latest_run, "completed_at", None)
        )
    )
    chroma_ready = collection["chunks_count"] > 0
    ready = diagnostic_ready and chroma_ready

    if not chroma_ready:
        reason = "Préparez les sources afin de créer l'index Chroma du projet."
    elif not diagnostic_ready:
        reason = "Lancez EnnoDiagnostic afin d'activer le chat documentaire."
    else:
        reason = "Le chat RAG est prêt."

    return {
        "ok": True,
        "ready": ready,
        "reason": reason,
        "project_id": project.id,
        "organisme": project.organisme,
        "project_name": project.project_name,
        "year": str(project.year),
        "diagnostic_ready": diagnostic_ready,
        "chroma_ready": chroma_ready,
        "latest_run_id": getattr(latest_run, "id", None),
        **collection,
    }


@router.post("/messages")
async def diagnostic_chat_message(
    project_id: int,
    request: DiagnosticChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    latest_run = _latest_run_for_project(db, project.id)
    collection = _project_collection_status(project)
    diagnostic_payload = _diagnostic_payload(latest_run)

    if collection["chunks_count"] <= 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Le Chroma du projet sélectionné est vide. "
                "Lancez d'abord « Préparer les sources »."
            ),
        )

    if latest_run is None or not (
        diagnostic_payload
        or getattr(latest_run, "report_path", None)
        or getattr(latest_run, "completed_at", None)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "EnnoDiagnostic n'a pas encore été exécuté pour ce projet. "
                "Lancez d'abord le diagnostic."
            ),
        )

    service = DiagnosticRAGChatService(
        organisme=project.organisme,
        project=project.project_name,
        year=project.year,
    )

    try:
        result = await run_in_threadpool(
            service.answer,
            question=request.message,
            history=[item.model_dump() for item in request.history],
            diagnostic_payload=diagnostic_payload,
            document_scope=(
                request.document_scope.model_dump()
                if request.document_scope is not None
                else None
            ),
        )
        return result

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat RAG indisponible : {exc}",
        ) from exc
