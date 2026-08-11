from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session, load_only

from agents.EnnoAmelioration.application.cir_style_context import (
    has_safe_cir_style_memory,
)
from db.models import (
    Article,
    DiagnosticRun,
    Document,
    ImprovementSession,
    Project,
    ScholarRun,
)


def get_improvement_project_context(db: Session, project: Project) -> dict[str, Any]:
    """Retourne un statut léger ; aucun rapport ou document lourd n'est chargé."""

    document_count = (
        db.query(func.count(Document.id))
        .filter(Document.project_id == project.id)
        .scalar()
        or 0
    )
    latest_diagnostic = (
        db.query(DiagnosticRun)
        .options(
            load_only(
                DiagnosticRun.id,
                DiagnosticRun.status,
                DiagnosticRun.created_at,
                DiagnosticRun.completed_at,
            )
        )
        .filter(DiagnosticRun.project_id == project.id)
        .order_by(DiagnosticRun.created_at.desc(), DiagnosticRun.id.desc())
        .first()
    )
    latest_scholar = (
        db.query(ScholarRun)
        .options(
            load_only(
                ScholarRun.id,
                ScholarRun.status,
                ScholarRun.created_at,
                ScholarRun.completed_at,
            )
        )
        .filter(ScholarRun.project_id == project.id)
        .order_by(ScholarRun.created_at.desc(), ScholarRun.id.desc())
        .first()
    )
    accepted_articles = 0
    if latest_scholar is not None:
        accepted_articles = (
            db.query(func.count(Article.id))
            .filter(
                Article.scholar_run_id == latest_scholar.id,
                Article.consultant_status == "garde",
            )
            .scalar()
            or 0
        )
    latest_improvement = (
        db.query(ImprovementSession)
        .options(
            load_only(
                ImprovementSession.id,
                ImprovementSession.title,
                ImprovementSession.state,
                ImprovementSession.updated_at,
                ImprovementSession.created_at,
            )
        )
        .filter(ImprovementSession.project_id == project.id)
        .order_by(ImprovementSession.updated_at.desc(), ImprovementSession.created_at.desc())
        .first()
    )
    try:
        from services.cir_memory_service import cir_memory_paths

        paths = cir_memory_paths(project)
        source_memory_available = any(
            paths[key].exists()
            for key in ("validated_style", "validated_chunks", "organism_style_index")
        )
    except Exception:
        source_memory_available = False
    safe_style_available = has_safe_cir_style_memory(project)

    return {
        "project": {
            "id": project.id,
            "organisme": project.organisme,
            "project_name": project.project_name,
            "year": project.year,
            "domain_label": project.domain_label,
            "status": project.status,
        },
        "documents": {"available": bool(document_count), "count": int(document_count)},
        "diagnostic": {
            "available": latest_diagnostic is not None,
            "latest_run_id": latest_diagnostic.id if latest_diagnostic else None,
            "status": latest_diagnostic.status if latest_diagnostic else None,
        },
        "scholar": {
            "available": latest_scholar is not None,
            "latest_run_id": latest_scholar.id if latest_scholar else None,
            "status": latest_scholar.status if latest_scholar else None,
            "accepted_article_count": int(accepted_articles),
        },
        "cir_memory": {
            "available": safe_style_available,
            "source_memory_available": source_memory_available,
            "policy": "safe_style_patterns_only",
        },
        "last_improvement": (
            {
                "session_id": latest_improvement.id,
                "title": latest_improvement.title,
                "state": latest_improvement.state,
                "updated_at": latest_improvement.updated_at.isoformat()
                if latest_improvement.updated_at
                else None,
            }
            if latest_improvement
            else None
        ),
    }
