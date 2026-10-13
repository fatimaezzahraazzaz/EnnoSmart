from __future__ import annotations

import os
import traceback
from typing import Any

from langgraph.checkpoint.redis import RedisSaver

from services.cir_background_service_v321 import (
    acquire_background_lock,
    read_background_status,
    release_background_lock,
    write_background_status,
)
from backend_api.workers.celery_app import celery_app
from backend_api.workers.cir_graph_v321 import (
    build_full_cir_graph,
)

from backend_api.workers.cir_runtime_config_v3214 import (
    CIR_LANGGRAPH_REDIS_URL,
)

GRAPH_REDIS_URL = CIR_LANGGRAPH_REDIS_URL


@celery_app.task(
    bind=True,
    name="ennosmart.cir.run_full_cir",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
)
def run_full_cir(
    self,
    *,
    project_id: int,
    session_id: str,
    user_id: int,
    initial_payload: dict[str, Any],
) -> dict[str, Any]:
    project_id = int(project_id)
    session_id = str(session_id)
    task_id = str(self.request.id)

    status = read_background_status(
        project_id,
        session_id,
    ) or {}

    # Si un autre worker traite déjà cette session, on ne lance jamais deux
    # chaînes LLM en parallèle.
    lock_ok = acquire_background_lock(
        project_id,
        session_id,
        owner=task_id,
    )

    if not lock_ok:
        existing_job = str(
            status.get("job_id") or ""
        )
        if existing_job and existing_job != task_id:
            return {
                "ok": True,
                "deduplicated": True,
                "job_id": existing_job,
                "status": status.get("status"),
            }

    write_background_status(
        project_id,
        session_id,
        job_id=task_id,
        status="running",
        stage="worker_started",
        error=None,
    )

    # Un thread LangGraph par job Celery. Un retry Celery conserve le même
    # task_id et reprend donc les mêmes checkpoints ; une nouvelle demande CIR
    # dans la même conversation obtient un nouveau thread propre.
    thread_id = (
        f"ennosmart:cir:{project_id}:{session_id}:{task_id}"
    )

    try:
        with RedisSaver.from_conn_string(
            GRAPH_REDIS_URL
        ) as checkpointer:
            # setup est idempotent et nécessaire lors de la première utilisation.
            checkpointer.setup()

            graph = build_full_cir_graph(
                checkpointer=checkpointer,
            )

            config = {
                "configurable": {
                    "thread_id": thread_id,
                },
                # 74 sections × plusieurs étapes graph : 1000 laisse une marge
                # suffisante tout en évitant une boucle réellement infinie.
                "recursion_limit": int(
                    os.getenv(
                        "ENNOSMART_LANGGRAPH_RECURSION_LIMIT",
                        "1000",
                    )
                ),
            }

            current = graph.get_state(config)
            values = dict(
                getattr(current, "values", None)
                or {}
            )

            initial_state = {
                "job_id": task_id,
                "project_id": project_id,
                "session_id": session_id,
                "user_id": int(user_id),
                "initial_payload": dict(
                    initial_payload or {}
                ),
                "started": bool(
                    values.get("started")
                ),
                "status": (
                    str(values.get("status"))
                    if values
                    else "queued"
                ),
                "stage": (
                    str(values.get("stage"))
                    if values
                    else "queued"
                ),
                "iteration": int(
                    values.get("iteration") or 0
                ),
                "cursor": int(
                    values.get("cursor") or 0
                ),
                "total": values.get("total"),
                "current_section": values.get(
                    "current_section"
                ),
                "candidate_version_id": values.get(
                    "candidate_version_id"
                ),
                "error": None,
            }

            # Si le checkpoint existe mais le graph n'est pas terminé, l'invocation
            # repart du state durable. start_node est idempotent côté DB.
            result = graph.invoke(
                initial_state,
                config=config,
            )

        final = write_background_status(
            project_id,
            session_id,
            job_id=task_id,
            status="completed",
            stage="completed",
            progress={
                "cursor": int(
                    result.get("cursor") or 0
                ),
                "total": result.get("total"),
                "current_section": result.get(
                    "current_section"
                ),
            },
            candidate_version_id=result.get(
                "candidate_version_id"
            ),
            error=None,
            traceback=None,
            retry_number=None,
        )
        return {
            "ok": True,
            **final,
        }

    except Exception as exc:
        trace = traceback.format_exc(limit=20)
        retries = int(
            getattr(self.request, "retries", 0)
            or 0
        )

        if retries < int(self.max_retries or 0):
            write_background_status(
                project_id,
                session_id,
                job_id=task_id,
                status="retrying",
                stage="retrying",
                error=(
                    f"{exc.__class__.__name__}: {exc}"
                ),
                traceback=trace[-8000:],
                retry_number=retries + 1,
            )
            # Libère le lock : le retry reprend avec le même thread LangGraph
            # et les mêmes checkpoints.
            release_background_lock(
                project_id,
                session_id,
            )
            raise self.retry(
                exc=exc,
                countdown=min(
                    60,
                    5 * (2 ** retries),
                ),
            )

        write_background_status(
            project_id,
            session_id,
            job_id=task_id,
            status="failed",
            stage="failed",
            error=(
                f"{exc.__class__.__name__}: {exc}"
            ),
            traceback=trace[-8000:],
        )
        raise

    finally:
        # Un retry l'a déjà libéré ; DEL est idempotent.
        release_background_lock(
            project_id,
            session_id,
        )
