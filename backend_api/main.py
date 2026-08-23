# -*- coding: utf-8 -*-
from __future__ import annotations

# Chargement explicite des DLL FFmpeg sous Windows avant TorchCodec.
import os

_FFMPEG_DLL_DIR_HANDLE = None

if os.name == "nt":
    _ffmpeg_bin = os.getenv("ENNOSMART_FFMPEG_BIN", r"C:\ffmpeg\bin")

    if os.path.isdir(_ffmpeg_bin):
        if _ffmpeg_bin not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = _ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")

        _FFMPEG_DLL_DIR_HANDLE = os.add_dll_directory(_ffmpeg_bin)

from typing import Any

from anyio.to_thread import current_default_thread_limiter
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.config import settings
from db.database import Base, database_pool_status, engine
from modules.LLM.llm_concurrency import (
    LLMCapacityTimeoutError,
    llm_concurrency_status,
)

from routers import auth
from routers import projects
from routers import documents
from routers import diagnostic
from routers import diagnostic_chat
from routers import scholar
from routers import cir_final_consultant
from routers import cir_memory
from routers import improvement
from routers import admin
from routers import sharepoint_audit

from routers.source_highlight import router as source_highlight_router
from routers.cir_source_view import router as cir_source_view_router
from routers.document_binary import router as document_binary_router
from routers.document_db_source import router as document_db_source_router


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        description=(
            "Backend API EnnoSmart : authentification, projets, documents, "
            "EnnoDiagnostic, chat RAG, EnnoScholar et mémoire CIR."
        ),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(LLMCapacityTimeoutError)
    async def llm_capacity_timeout_handler(
        request: Request,
        exc: LLMCapacityTimeoutError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "15"},
            content={"detail": str(exc), "code": "LLM_CAPACITY_TIMEOUT"},
        )

    Base.metadata.create_all(bind=engine)

    @app.on_event("startup")
    async def configure_request_thread_capacity() -> None:
        # Les routes ``def`` de FastAPI utilisent ce pool. 80 threads laissent
        # les lectures/authentifications répondre pendant que les appels LLM
        # longs attendent leur créneau borné.
        limiter = current_default_thread_limiter()
        limiter.total_tokens = max(40, settings.WEB_THREAD_LIMIT)
        app.state.web_thread_limit = limiter.total_tokens

    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(documents.router)
    app.include_router(diagnostic.router)
    app.include_router(diagnostic_chat.router)
    app.include_router(scholar.router)

    app.include_router(cir_final_consultant.router)
    app.include_router(cir_memory.router)
    app.include_router(improvement.router)
    app.include_router(admin.router)
    app.include_router(sharepoint_audit.router)

    app.include_router(source_highlight_router)
    app.include_router(cir_source_view_router)

    app.include_router(document_binary_router)
    app.include_router(document_db_source_router)

    @app.get("/health", tags=["health"])
    def health_check() -> dict[str, Any]:
        return {
            "status": "ok",
            "app": settings.APP_NAME,
            "env": settings.ENV,
            "capacity": {
                "web_thread_limit": getattr(
                    app.state,
                    "web_thread_limit",
                    settings.WEB_THREAD_LIMIT,
                ),
                "database": database_pool_status(),
                "llm": llm_concurrency_status(),
            },
        }

    return app


app = create_app()

# EnnoScholar Guided Research Chat
from guided_research_bootstrap import register_guided_research

register_guided_research(app)
