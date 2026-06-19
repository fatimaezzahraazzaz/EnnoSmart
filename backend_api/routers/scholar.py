# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from core.deps import get_current_user, get_db
from db.models import Article, ScholarRun, User
from schemas.scholar import ArticleDecisionRequest, ArticleRead, ScholarRead
from services.project_service import get_project_for_user
from services.scholar_service import (
    build_scholar_payload_from_selected_verrous,
    create_scholar_run_from_files,
    get_all_current_verrous,
    get_selected_verrous_for_scholar,
    read_scholar_bundle,
    run_ennoscholar,
    run_ennoscholar_from_selected_verrous,
    sync_articles_from_scholar,
)


router = APIRouter(tags=["ennoscholar"])


@router.get("/projects/{project_id}/scholar/latest")
def get_latest_scholar(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    latest_run = (
        db.query(ScholarRun)
        .filter(ScholarRun.project_id == project.id)
        .order_by(ScholarRun.created_at.desc())
        .first()
    )

    bundle = read_scholar_bundle(project)

    return {
        "project": {
            "id": project.id,
            "organisme": project.organisme,
            "project_name": project.project_name,
            "year": project.year,
            "domain_label": project.domain_label,
            "status": project.status,
        },
        "latest_run": ScholarRead.model_validate(latest_run).model_dump() if latest_run else None,
        "bundle": bundle,
    }


@router.get("/projects/{project_id}/scholar/selected-verrous")
def get_scholar_selected_verrous(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Verrous EnnoDiagnostic qui seront envoyés vers EnnoScholar.
    Règle stricte : consultant_status = garde.
    """
    project = get_project_for_user(db, project_id, current_user)

    selected = get_selected_verrous_for_scholar(db, project)
    all_verrous = get_all_current_verrous(db, project)

    return {
        "ok": True,
        "selection_rule": "consultant_status == garde",
        "total_verrous": len(all_verrous),
        "selected_count": len(selected),
        "selected_verrous": [
            {
                "id": v.id,
                "title": v.title,
                "score": v.score,
                "tag_cir": v.tag_cir,
                "consultant_status": v.consultant_status,
                "justification": v.justification,
                "source_json": v.source_json,
            }
            for v in selected
        ],
    }


@router.get("/projects/{project_id}/scholar/payload-preview")
def get_scholar_payload_preview(
    project_id: int,
    max_verrous: int = Query(8, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Aperçu du payload envoyé à EnnoScholar, sans lancer la recherche scientifique.
    """
    project = get_project_for_user(db, project_id, current_user)
    payload = build_scholar_payload_from_selected_verrous(db, project, max_verrous=max_verrous)

    return payload


@router.post("/projects/{project_id}/scholar/import-existing", response_model=ScholarRead)
def import_existing_scholar(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    run = create_scholar_run_from_files(db, project)
    return run


@router.post("/projects/{project_id}/scholar/run-from-selected-verrous", response_model=ScholarRead)
def run_scholar_from_selected_verrous(
    project_id: int,
    max_verrous: int = Query(8, ge=1, le=20),
    limit_per_query: int = Query(3, ge=1, le=10),
    offline_dry_run: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lance EnnoScholar uniquement sur les verrous sélectionnés/gardés par le consultant.
    """
    project = get_project_for_user(db, project_id, current_user)

    try:
        run = run_ennoscholar_from_selected_verrous(
            db=db,
            project=project,
            max_verrous=max_verrous,
            limit_per_query=limit_per_query,
            offline_dry_run=offline_dry_run,
        )
        return run

    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"EnnoScholar impossible : {exc}",
        )


@router.post("/projects/{project_id}/scholar/run", response_model=ScholarRead)
def run_scholar(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Alias historique : lance maintenant EnnoScholar depuis les verrous sélectionnés.
    """
    project = get_project_for_user(db, project_id, current_user)

    try:
        return run_ennoscholar(db, project)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"EnnoScholar impossible : {exc}",
        )


@router.post("/projects/{project_id}/scholar/{run_id}/sync-articles", response_model=list[ArticleRead])
def sync_articles(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    run = (
        db.query(ScholarRun)
        .filter(ScholarRun.id == run_id, ScholarRun.project_id == project.id)
        .first()
    )

    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scholar run introuvable.",
        )

    return sync_articles_from_scholar(db, run)


@router.get("/projects/{project_id}/articles", response_model=list[ArticleRead])
def list_articles(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    return (
        db.query(Article)
        .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
        .filter(ScholarRun.project_id == project.id)
        .order_by(Article.created_at.desc())
        .all()
    )


@router.patch("/projects/{project_id}/articles/{article_id}/decision", response_model=ArticleRead)
def update_article_decision(
    project_id: int,
    article_id: int,
    payload: ArticleDecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    article = (
        db.query(Article)
        .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
        .filter(Article.id == article_id, ScholarRun.project_id == project.id)
        .first()
    )

    if not article:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Article introuvable.",
        )

    allowed = {"garde", "rejete", "en_attente"}
    if payload.consultant_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Statut invalide. Valeurs autorisées : {sorted(allowed)}",
        )

    article.consultant_status = payload.consultant_status
    db.commit()
    db.refresh(article)
    return article

# ============================================================
# État de l'art EnnoScholar — rédaction après sélection consultant
# ============================================================

@router.get("/projects/{project_id}/scholar/state-of-art/selection-preview")
def preview_state_of_art_selection(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Vérifie les articles gardés par le consultant avant rédaction.
    Règle : il faut au moins un article Direct ou Connexe pour rédiger sans force.
    """
    project = get_project_for_user(db, project_id, current_user)
    from services.scholar_state_of_art_service import build_state_of_art_selection_payload
    return build_state_of_art_selection_payload(db, project)


@router.post("/projects/{project_id}/scholar/state-of-art/write")
def write_state_of_art(
    project_id: int,
    writer_mode: str = Query("auto", pattern="^(auto|template|llm)$"),
    force: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Rédige l'état de l'art par verrou à partir des articles consultant_status='garde'.

    writer_mode :
    - template : sans LLM, très sûr
    - auto : LLM si disponible sinon template
    - llm : essaye LLM

    force=false bloque si aucun article Direct/Connexe n'est gardé.
    """
    project = get_project_for_user(db, project_id, current_user)
    from services.scholar_state_of_art_service import write_state_of_art_from_kept_articles
    result = write_state_of_art_from_kept_articles(db, project, writer_mode=writer_mode, force=force)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason") or "État de l'art non généré.")
    return result


@router.get("/projects/{project_id}/scholar/state-of-art/latest")
def get_latest_state_of_art(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    from services.scholar_state_of_art_service import read_latest_state_of_art
    return read_latest_state_of_art(project)

