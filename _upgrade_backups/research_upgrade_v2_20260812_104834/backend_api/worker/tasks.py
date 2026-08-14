from __future__ import annotations
from worker.celery_app import celery_app

@celery_app.task(name="ennosmart.preflight_article", bind=True, autoretry_for=(OSError,), retry_backoff=True, max_retries=2)
def preflight_article(self, project_id: int, article_id: int):
    from services.scholar_evidence_preflight_service import _process_one
    return _process_one(int(project_id), int(article_id))
