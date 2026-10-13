# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from core.deps import get_current_user, get_db
from db.models import DiagnosticRun, User
from modules.RAG.diagnostic_chat_service import DiagnosticRAGChatService
from modules.RAG.chroma_client import chroma_connection_info, create_chroma_client
from modules.RAG.project_store import ProjectStore
from modules.RAG.config import EMBEDDING_MODEL_NAME, FULLTEXT_CHAT_COLLECTION_SUFFIX
from modules.RAG.indexer import (
    FULLTEXT_INDEX_MANIFEST,
    FULLTEXT_INDEX_VERSION,
    fulltext_corpus_fingerprint,
)
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
    document_scopes: List[DiagnosticDocumentScope] = Field(default_factory=list, max_length=50)


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
        subproject=getattr(project, "subproject_name", None),
        year=project.year,
    ).ensure()

    base_collection_name = store.collection_name
    fulltext_collection_name = base_collection_name + FULLTEXT_CHAT_COLLECTION_SUFFIX
    fulltext_dir = store.documents_processed_dir / "fulltext_rag_v1"
    fulltext_available = bool(
        fulltext_dir.is_dir()
        and any(
            path.is_file() and path.name.lower() not in {"manifest.json", "nlp_result.json"}
            for path in fulltext_dir.glob("*.json")
        )
    )

    base_chunks_count = 0
    fulltext_chunks_count = 0
    try:
        client = create_chroma_client(store.chroma_dir)
        try:
            base_chunks_count = int(client.get_collection(base_collection_name).count())
        except Exception:
            base_chunks_count = 0
        try:
            fulltext_chunks_count = int(client.get_collection(fulltext_collection_name).count())
        except Exception:
            fulltext_chunks_count = 0
    except Exception:
        pass

    fulltext_fingerprint = ""
    fulltext_manifest: Dict[str, Any] = {}
    fulltext_index_current = False
    if fulltext_available:
        try:
            fulltext_fingerprint = fulltext_corpus_fingerprint(fulltext_dir)
        except Exception:
            fulltext_fingerprint = ""

        manifest_path = store.rag_dir / FULLTEXT_INDEX_MANIFEST
        if manifest_path.is_file():
            try:
                raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(raw_manifest, dict):
                    fulltext_manifest = raw_manifest
            except Exception:
                fulltext_manifest = {}

        fulltext_index_current = bool(
            fulltext_chunks_count > 0
            and fulltext_fingerprint
            and fulltext_manifest.get("version") == FULLTEXT_INDEX_VERSION
            and fulltext_manifest.get("fingerprint") == fulltext_fingerprint
            and fulltext_manifest.get("collection_name") == fulltext_collection_name
            and fulltext_manifest.get("embedding_model") == EMBEDDING_MODEL_NAME
        )

    # Pour V2, seul l'index fulltext correspondant EXACTEMENT au corpus courant
    # fait foi. Une ancienne collection NLP ou une collection fulltext obsolète
    # ne doit jamais rendre le chat artificiellement "prêt".
    if fulltext_available:
        active_chunks_count = (
            fulltext_chunks_count if fulltext_index_current else 0
        )
    else:
        active_chunks_count = max(base_chunks_count, fulltext_chunks_count)

    return {
        "collection_name": fulltext_collection_name if fulltext_available else base_collection_name,
        "base_collection_name": base_collection_name,
        "fulltext_collection_name": fulltext_collection_name,
        "fulltext_available": fulltext_available,
        "chroma_dir": str(store.chroma_dir),
        "chroma_connection": chroma_connection_info(store.chroma_dir),
        "chunks_count": active_chunks_count,
        "base_chunks_count": base_chunks_count,
        "fulltext_chunks_count": fulltext_chunks_count,
        "fulltext_fingerprint": fulltext_fingerprint or None,
        "fulltext_index_current": fulltext_index_current,
        "fulltext_index_manifest": fulltext_manifest if fulltext_available else {},
        "index_source": "fulltext_rag_v1" if fulltext_available else "legacy",
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
        reason = "Lancez EnnoDiagnostic afin de créer l'index fulltext Chroma du projet."
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
        "subproject_name": getattr(project, "subproject_name", None),
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
                "Lancez d'abord EnnoDiagnostic."
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
        subproject=getattr(project, "subproject_name", None),
        year=project.year,
    )

    try:
        answer_kwargs = {
            "question": request.message,
            "history": [item.model_dump() for item in request.history],
            "diagnostic_payload": diagnostic_payload,
            "document_scope": (
                request.document_scope.model_dump()
                if request.document_scope is not None
                else None
            ),
            "document_scopes": [item.model_dump() for item in request.document_scopes],
        }

        if collection.get("fulltext_available"):
            # V2 : aucun nlp_result.json n'est matérialisé pour le chat.
            return await run_in_threadpool(service.answer, **answer_kwargs)

        # Compatibilité anciens projets uniquement : si aucun fulltext V2
        # n'existe, conserver le fallback historique nlp_result.
        from services.project_artifact_service import materialized_json_artifact

        with materialized_json_artifact(
            db,
            project.id,
            "nlp/nlp_result.json",
            service.store.nlp_dir / "nlp_result.json",
            artifact_kind="nlp_result",
            required=False,
        ):
            return await run_in_threadpool(service.answer, **answer_kwargs)

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
