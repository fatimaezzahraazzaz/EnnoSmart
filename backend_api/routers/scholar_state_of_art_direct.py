# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from core.deps import get_current_user, get_db
from db.models import User
from services.project_service import get_project_for_user
from services.scholar_state_of_art_service import (
    build_state_of_art_selection_payload,
    read_latest_state_of_art,
    write_state_of_art_from_kept_articles,
)

router = APIRouter(prefix="/projects", tags=["EnnoScholar - State of Art"])


@router.get("/{project_id}/scholar/state-of-art/selection-preview")
def state_of_art_selection_preview(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    return build_state_of_art_selection_payload(db, project)


@router.post("/{project_id}/scholar/state-of-art/write-from-selection")
def write_state_of_art_from_frontend_selection(
    project_id: int,
    payload: Dict[str, Any] | None = Body(default=None),
    writer_mode: str = Query("auto", pattern="^(auto|template|llm)$"),
    force: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Route appelée par le frontend après sélection consultant.

    Deux modes compatibles :
    1. Le frontend envoie un payload avec verrous + selected_articles.
    2. Le frontend n'envoie rien : le backend reconstruit la sélection depuis
       les articles en base avec consultant_status='garde'.

    Le résultat est sauvegardé dans :
    storage/.../ennoscholar/ennoscholar_state_of_art_report.json
    """
    project = get_project_for_user(db, project_id, current_user)

    result = write_state_of_art_from_kept_articles(
        db=db,
        project=project,
        writer_mode=writer_mode,
        force=force,
        frontend_payload=payload if isinstance(payload, dict) else None,
    )

    if not result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("reason") or "État de l'art non généré.",
        )

    return result


@router.get("/{project_id}/scholar/state-of-art/latest-from-selection")
def latest_state_of_art_from_selection(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    return read_latest_state_of_art(project)
