# -*- coding: utf-8 -*-
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from db.database import Base, engine

from routers import auth
from routers import projects
from routers import documents
from routers import diagnostic
from routers import scholar
from routers import scholar_state_of_art_direct
from routers import cir_final_consultant

from routers.source_preview import router as source_preview_router
from routers.source_highlight_preview import router as source_highlight_preview_router
from routers.cir_source_view import router as cir_source_view_router

# Nouveau router : ouverture des documents stockés directement en PostgreSQL
from routers.document_binary import router as document_binary_router
from routers.document_db_source import router as document_db_source_router


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        description="Backend API EnnoSmart : auth, projets, documents, EnnoDiagnostic, EnnoScholar.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Création automatique des tables si elles n'existent pas
    Base.metadata.create_all(bind=engine)

    # Routes principales
    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(documents.router)
    app.include_router(diagnostic.router)
    app.include_router(scholar.router)
    app.include_router(scholar_state_of_art_direct.router)

    # CIR final consultant
    app.include_router(cir_final_consultant.router)

    # Prévisualisation / sources / surlignage
    app.include_router(source_preview_router)
    app.include_router(source_highlight_preview_router)
    app.include_router(cir_source_view_router)

    # Documents stockés en base PostgreSQL BYTEA
    app.include_router(document_binary_router)

    app.include_router(document_db_source_router)

    @app.get("/health", tags=["health"])
    def health_check():
        return {
            "status": "ok",
            "app": settings.APP_NAME,
            "env": settings.ENV,
        }

    return app


app = create_app()