
from __future__ import annotations

import logging

from worker.celery_app import celery_app


LOGGER = logging.getLogger("ennoscholar.worker")

@celery_app.task(
    name="ennosmart.preflight_article",
    bind=True,
    autoretry_for=(OSError,),
    retry_backoff=True,
    max_retries=2,
)
def preflight_article(self, project_id: int, article_id: int):
    from services.scholar_evidence_preflight_service import _process_access_probe
    return _process_access_probe(int(project_id), int(article_id))

@celery_app.task(
    name="ennosmart.preflight_run",
    bind=True,
    autoretry_for=(OSError,),
    retry_backoff=True,
    max_retries=2,
)
def preflight_run(self, project_id: int, run_id: int):
    from db.database import SessionLocal
    from db.models import Project, ScholarRun
    from services.scholar_evidence_preflight_service import (
        inspect_scholar_run_access,
        _save_report_on_run,
    )

    db = SessionLocal()
    try:
        project = db.get(Project, int(project_id))
        run = db.get(ScholarRun, int(run_id))
        if project is None or run is None:
            return {
                "ok": False,
                "status": "project_or_run_not_found",
                "project_id": project_id,
                "run_id": run_id,
            }

        current_preflight = (
            (run.raw_result_json or {}).get("evidence_preflight")
            if isinstance(run.raw_result_json, dict)
            else None
        )
        if isinstance(current_preflight, dict) and current_preflight.get("status") in {
            "cancelled",
            "cancelled_by_user",
        }:
            LOGGER.warning(
                "[EnnoScholar extraction] run=%s ignoré car il a été annulé",
                run.id,
            )
            return {
                "ok": True,
                "status": "cancelled",
                "run_id": int(run.id),
            }

        latest_run_id = (
            db.query(ScholarRun.id)
            .filter(ScholarRun.project_id == int(project_id))
            .order_by(ScholarRun.created_at.desc(), ScholarRun.id.desc())
            .limit(1)
            .scalar()
        )
        if latest_run_id is not None and int(latest_run_id) != int(run.id):
            raw = dict(run.raw_result_json or {})
            previous = dict(raw.get("evidence_preflight") or {})
            previous.update({
                "status": "superseded",
                "execution_mode": "celery",
                "superseded_by_run_id": int(latest_run_id),
                "message": "Extraction ignorée : un run EnnoScholar plus récent existe.",
            })
            raw["evidence_preflight"] = previous
            run.raw_result_json = raw
            db.add(run)
            db.commit()
            LOGGER.warning(
                "[EnnoScholar extraction] run=%s ignoré, remplacé par run=%s",
                run.id,
                latest_run_id,
            )
            return {
                "ok": True,
                "status": "superseded",
                "run_id": int(run.id),
                "superseded_by_run_id": int(latest_run_id),
            }

        LOGGER.info(
            "[EnnoScholar acces] démarrage project=%s run=%s task=%s",
            project_id,
            run_id,
            getattr(self.request, "id", None),
        )
        report = inspect_scholar_run_access(db, project, run)
        report["execution_mode"] = "celery"
        report["status"] = "completed"
        _save_report_on_run(db, run, report)
        LOGGER.info(
            "[EnnoScholar acces] terminé run=%s processed=%s/%s statuts=%s",
            run_id,
            report.get("processed"),
            report.get("available_candidates"),
            report.get("counts"),
        )
        return report
    finally:
        db.close()
