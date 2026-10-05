from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from core.deps import get_current_user, get_db
from db.models import DiagnosticRun, User
from services.project_service import get_project_for_user
from services.diagnostic_feedback_service import (
    MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES, build_feedback_email,
    parse_feedback, screenshot_type, send_feedback_email,
)

router = APIRouter(prefix="/projects/{project_id}/diagnostic-feedback", tags=["retours-consultants"])
logger = logging.getLogger(__name__)


@router.post("")
async def submit_diagnostic_feedback(
    project_id: int,
    payload: str = Form(...),
    attachment_sections: str = Form("[]"),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = get_project_for_user(db, project_id, current_user)
    try:
        comments, sections = parse_feedback(payload, attachment_sections)
        if len(files) != len(sections) or len(files) > MAX_FILES:
            raise ValueError("Les captures ne correspondent pas aux rubriques du formulaire.")
        screenshots = []
        total = 0
        for section, upload in zip(sections, files):
            data = await upload.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise ValueError("Chaque capture est limitée à 5 Mo.")
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise ValueError("L’ensemble des captures est limité à 25 Mo.")
            screenshot_type(data)
            screenshots.append((section, data))
        if not comments and not screenshots:
            raise ValueError("Ajoutez un commentaire ou une capture dans au moins une rubrique.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        for upload in files:
            await upload.close()
    run = db.query(DiagnosticRun).filter(DiagnosticRun.project_id == project_id).order_by(
        DiagnosticRun.created_at.desc(), DiagnosticRun.id.desc()
    ).first()
    message = build_feedback_email(project, current_user, run.id if run else None, comments, screenshots)
    try:
        await run_in_threadpool(send_feedback_email, message)
    except Exception as exc:
        # Aucun identifiant SMTP ni contenu consultant dans les logs.
        logger.warning("Envoi du retour projet %s échoué (%s)", project_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail=(
            "Votre retour n’a pas pu être envoyé. Réessayez plus tard ; "
            "vos commentaires et captures restent dans le formulaire."
        )) from exc
    return {"status": "sent", "message": "Votre retour a été envoyé. Merci."}
