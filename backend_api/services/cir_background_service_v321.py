from __future__ import annotations

from backend_api.workers.cir_runtime_config_v3214 import CIR_STATUS_REDIS_URL

from agents.EnnoAmelioration.application.background_scope_router_v3212 import (
    resolve_background_scope,
)

import json
import os
import time
import uuid
from typing import Any

import redis
from sqlalchemy.orm import Session

from db.models import ImprovementSession, Project

POLICY_VERSION = "ennoamel_cir_background_v3_21"

REDIS_URL = CIR_STATUS_REDIS_URL
STATUS_TTL_SECONDS = int(
    os.getenv(
        "ENNOSMART_CIR_STATUS_TTL",
        "86400",
    )
)
LOCK_TTL_SECONDS = int(
    os.getenv(
        "ENNOSMART_CIR_LOCK_TTL",
        "43200",
    )
)


def _redis() -> redis.Redis:
    return redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=5,
        health_check_interval=30,
    )


def _status_key(project_id: int, session_id: str) -> str:
    return (
        f"ennosmart:cir:job:{int(project_id)}:{str(session_id)}"
    )


def _lock_key(project_id: int, session_id: str) -> str:
    return (
        f"ennosmart:cir:lock:{int(project_id)}:{str(session_id)}"
    )


def _now() -> float:
    return time.time()


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def read_background_status(
    project_id: int,
    session_id: str,
) -> dict[str, Any] | None:
    client = _redis()
    raw = client.get(
        _status_key(project_id, session_id)
    )
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def write_background_status(
    project_id: int,
    session_id: str,
    **updates: Any,
) -> dict[str, Any]:
    client = _redis()
    current = (
        read_background_status(
            project_id,
            session_id,
        )
        or {}
    )
    current.update(
        {
            "policy_version": POLICY_VERSION,
            "project_id": int(project_id),
            "session_id": str(session_id),
            "updated_at_epoch": _now(),
            **updates,
        }
    )
    client.setex(
        _status_key(project_id, session_id),
        STATUS_TTL_SECONDS,
        _json_dump(current),
    )
    return current


def acquire_background_lock(
    project_id: int,
    session_id: str,
    *,
    owner: str,
) -> bool:
    client = _redis()
    return bool(
        client.set(
            _lock_key(project_id, session_id),
            owner,
            nx=True,
            ex=LOCK_TTL_SECONDS,
        )
    )


def refresh_background_lock(
    project_id: int,
    session_id: str,
) -> None:
    _redis().expire(
        _lock_key(project_id, session_id),
        LOCK_TTL_SECONDS,
    )


def release_background_lock(
    project_id: int,
    session_id: str,
) -> None:
    _redis().delete(
        _lock_key(project_id, session_id)
    )


def redis_health() -> dict[str, Any]:
    client = _redis()
    started = time.perf_counter()
    pong = bool(client.ping())
    return {
        "ok": pong,
        "redis_url": REDIS_URL.rsplit("@", 1)[-1],
        "latency_ms": round(
            (time.perf_counter() - started) * 1000,
            2,
        ),
    }


def _session_scope(
    db: Session,
    project_id: int,
    session_id: str,
) -> str:
    row = (
        db.query(ImprovementSession)
        .filter(
            ImprovementSession.id == str(session_id),
            ImprovementSession.project_id == int(project_id),
        )
        .first()
    )
    if row is None:
        raise LookupError(
            "Session EnnoAmelioration introuvable."
        )
    return str(row.target_scope or "").strip()


def should_background_message(
    db: Session,
    project_id: int,
    session_id: str,
    payload: Any,
) -> bool:
    decision = resolve_background_scope(
        db,
        project_id,
        session_id,
        payload,
    )
    return bool(decision.get("background"))



def enqueue_full_cir_job(
    *,
    project_id: int,
    session_id: str,
    user_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    # V3.21.5 : producer et worker utilisent exactement la même Celery app,
    # le même Exchange direct, la même Queue et la même routing_key.
    from backend_api.workers.celery_app import (
        CIR_EXCHANGE,
        CIR_ROUTING_KEY,
        celery_app,
    )
    from backend_api.workers.cir_runtime_config_v3214 import (
        CIR_BROKER_URL,
        CIR_QUEUE,
        CIR_RESULT_BACKEND,
        public_config,
    )

    existing = read_background_status(
        project_id,
        session_id,
    )
    if existing and str(
        existing.get("status") or ""
    ) in {"queued", "running", "retrying"}:
        print(
            "[V3.21.5][Dispatch] "
            f"reuse session={session_id} "
            f"job_id={existing.get('job_id')}"
        )
        return existing

    # Le backend charge le .env historique, qui contient encore
    # CELERY_BROKER_URL=/0 pour EnnoScholar. Celery donne priorité à cette
    # variable générique sur ``celery_app.conf.broker_url``. Sans connexion
    # explicite, les tâches CIR partent donc silencieusement dans Redis /0,
    # tandis que le worker CIR écoute /1.
    #
    # On fixe aussi l'identifiant et l'état AVANT la publication : un worker
    # local peut prendre la tâche avant le retour de ``send_task``. L'ancien
    # ordre pouvait alors réécrire ``queued`` par-dessus ``running``.
    job_id = str(uuid.uuid4())
    queued = write_background_status(
        project_id,
        session_id,
        job_id=job_id,
        status="queued",
        stage="queued",
        dispatch={
            "task_name": "ennosmart.cir.run_full_cir",
            "queue": CIR_QUEUE,
            "exchange": CIR_EXCHANGE,
            "routing_key": CIR_ROUTING_KEY,
            "broker": CIR_BROKER_URL,
            "result_backend": CIR_RESULT_BACKEND,
            "runtime": public_config(),
        },
        progress={
            "cursor": 0,
            "total": None,
            "current_section": None,
        },
        error=None,
        candidate_version_id=None,
    )

    try:
        with celery_app.connection_for_write(
            CIR_BROKER_URL
        ) as connection:
            celery_app.send_task(
                "ennosmart.cir.run_full_cir",
                kwargs={
                    "project_id": int(project_id),
                    "session_id": str(session_id),
                    "user_id": int(user_id),
                    "initial_payload": dict(payload),
                },
                task_id=job_id,
                queue=CIR_QUEUE,
                exchange=CIR_EXCHANGE,
                routing_key=CIR_ROUTING_KEY,
                serializer="json",
                connection=connection,
            )
    except Exception as exc:
        write_background_status(
            project_id,
            session_id,
            job_id=job_id,
            status="failed",
            stage="dispatch_failed",
            error=f"{exc.__class__.__name__}: {exc}",
        )
        raise

    print(
        "[V3.21.5][Dispatch] "
        f"session={session_id} "
        f"task_id={job_id} "
        "task_name=ennosmart.cir.run_full_cir "
        f"queue={CIR_QUEUE} "
        f"exchange={CIR_EXCHANGE} "
        f"routing_key={CIR_ROUTING_KEY} "
        f"broker={CIR_BROKER_URL} "
        f"results={CIR_RESULT_BACKEND}"
    )

    # Si le worker a déjà démarré, ne jamais écraser son heartbeat par l'état
    # initial. À défaut, ``queued`` est bien l'état que nous venons d'écrire.
    current = read_background_status(
        project_id,
        session_id,
    )
    if current and str(current.get("job_id") or "") == job_id:
        return current
    return queued





def mirror_status_into_session(
    db: Session,
    project_id: int,
    session_id: str,
    status_payload: dict[str, Any],
) -> None:
    session = (
        db.query(ImprovementSession)
        .filter(
            ImprovementSession.id == str(session_id),
            ImprovementSession.project_id == int(project_id),
        )
        .first()
    )
    if session is None:
        return

    context = dict(
        session.context_json or {}
    )
    context["cir_background_job"] = dict(
        status_payload or {}
    )
    session.context_json = context
    db.commit()
