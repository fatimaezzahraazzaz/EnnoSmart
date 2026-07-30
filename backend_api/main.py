# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from db.database import Base, engine

from routers import auth
from routers import projects
from routers import documents
from routers import diagnostic
from routers import scholar
from routers import cir_final_consultant
from routers import cir_memory

from routers.source_highlight import router as source_highlight_router
from routers.cir_source_view import router as cir_source_view_router
from routers.document_binary import router as document_binary_router
from routers.document_db_source import router as document_db_source_router


def create_app() -> FastAPI:
    """
    Crée et configure l'application FastAPI EnnoSmart.

    Les routes EnnoScholar, y compris la préparation des articles
    et la génération de l'état de l'art, doivent maintenant être
    exposées par routers.scholar.

    L'ancien routeur scholar_state_of_art_direct n'est plus chargé,
    car il dépendait du service supprimé
    services.scholar_state_of_art_service.
    """
    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        description=(
            "Backend API EnnoSmart : authentification, projets, documents, "
            "EnnoDiagnostic, EnnoScholar et mémoire CIR."
        ),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Création automatique des tables si elles n'existent pas.
    Base.metadata.create_all(bind=engine)

    # ---------------------------------------------------------
    # Routes principales
    # ---------------------------------------------------------
    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(documents.router)
    app.include_router(diagnostic.router)

    # EnnoScholar :
    # - recherche scientifique ;
    # - sélection des articles ;
    # - récupération des textes intégraux ;
    # - Article Cards ;
    # - orchestration de l'état de l'art.
    app.include_router(scholar.router)

    # ---------------------------------------------------------
    # CIR
    # ---------------------------------------------------------
    app.include_router(cir_final_consultant.router)
    app.include_router(cir_memory.router)

    # ---------------------------------------------------------
    # Prévisualisation, sources et surlignage
    # ---------------------------------------------------------
    app.include_router(source_highlight_router)
    app.include_router(cir_source_view_router)

    # ---------------------------------------------------------
    # Documents stockés en PostgreSQL BYTEA
    # ---------------------------------------------------------
    app.include_router(document_binary_router)
    app.include_router(document_db_source_router)

    # ---------------------------------------------------------
    # Santé API
    # ---------------------------------------------------------
    @app.get("/health", tags=["health"])
    def health_check() -> dict[str, str]:
        return {
            "status": "ok",
            "app": settings.APP_NAME,
            "env": settings.ENV,
        }

    return app


app = create_app()
# EnnoScholar Guided Research Chat
from guided_research_bootstrap import register_guided_research
register_guided_research(app)
