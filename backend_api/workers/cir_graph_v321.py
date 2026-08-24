from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from db.database import SessionLocal
from db.models import Project
from services.cir_background_service_v321 import (
    mirror_status_into_session,
    refresh_background_lock,
    write_background_status,
)
from services.improvement_service import (
    background_advance_full_cir,
    get_session,
    send_message,
)


class FullCIRState(TypedDict, total=False):
    job_id: str
    project_id: int
    session_id: str
    user_id: int
    initial_payload: dict[str, Any]
    started: bool
    status: str
    stage: str
    iteration: int
    cursor: int
    total: int
    current_section: dict[str, Any] | None
    candidate_version_id: str | None
    error: str | None


def _open_project(
    db: Session,
    project_id: int,
) -> Project:
    project = (
        db.query(Project)
        .filter(Project.id == int(project_id))
        .first()
    )
    if project is None:
        raise LookupError(
            f"Projet {project_id} introuvable."
        )
    return project


def _workflow_snapshot(
    db: Session,
    project_id: int,
    session_id: str,
) -> dict[str, Any]:
    session = get_session(
        db,
        int(project_id),
        str(session_id),
    )
    context = dict(
        session.context_json or {}
    )
    workflow = dict(
        context.get("cir_progressive_workflow")
        or {}
    )
    units = list(
        workflow.get("units") or []
    )
    cursor = max(
        0,
        int(workflow.get("cursor") or 0),
    )
    current = (
        dict(units[cursor])
        if cursor < len(units)
        and isinstance(units[cursor], dict)
        else None
    )
    candidate = next(
        (
            row
            for row in session.versions
            if str(row.status) == "candidate"
        ),
        None,
    )

    return {
        "workflow": workflow,
        "cursor": cursor,
        "total": len(units),
        "current": current,
        "active": bool(
            workflow.get("active")
        ),
        "phase": str(
            workflow.get("phase") or ""
        ),
        "candidate_version_id": (
            str(candidate.id)
            if candidate is not None
            else None
        ),
        "session_state": str(
            session.state or ""
        ),
    }


def _publish(
    state: FullCIRState,
    *,
    status: str,
    stage: str,
    extra: dict[str, Any] | None = None,
) -> None:
    project_id = int(state["project_id"])
    session_id = str(state["session_id"])

    payload = write_background_status(
        project_id,
        session_id,
        job_id=str(
            state.get("job_id")
            or ""
        ),
        status=status,
        stage=stage,
        iteration=int(
            state.get("iteration") or 0
        ),
        progress={
            "cursor": int(
                state.get("cursor") or 0
            ),
            "total": state.get("total"),
            "current_section": state.get(
                "current_section"
            ),
        },
        candidate_version_id=state.get(
            "candidate_version_id"
        ),
        error=state.get("error"),
        **dict(extra or {}),
    )

    db = SessionLocal()
    try:
        mirror_status_into_session(
            db,
            project_id,
            session_id,
            payload,
        )
    finally:
        db.close()


def start_node(
    state: FullCIRState,
) -> FullCIRState:
    project_id = int(state["project_id"])
    session_id = str(state["session_id"])
    payload = dict(
        state.get("initial_payload") or {}
    )

    db = SessionLocal()
    try:
        project = _open_project(
            db,
            project_id,
        )

        # Idempotence : si V3.20 a déjà un workflow actif, un retry Celery ne
        # rejoue pas le message consultant.
        snapshot = _workflow_snapshot(
            db,
            project_id,
            session_id,
        )
        workflow = snapshot["workflow"]

        if (
            workflow
            and str(workflow.get("version") or "").endswith(
                "v3_20"
            )
        ):
            # Workflow actif OU déjà terminé : un retry Celery ne doit jamais
            # rejouer le message initial et créer une seconde candidate.
            started = True
        else:
            send_message(
                db,
                project,
                session_id,
                **payload,
            )
            db.commit()
            started = True

        snapshot = _workflow_snapshot(
            db,
            project_id,
            session_id,
        )
    finally:
        db.close()

    update: FullCIRState = {
        **state,
        "started": started,
        "status": "running",
        "stage": "started",
        "iteration": int(
            state.get("iteration") or 0
        ) + 1,
        "cursor": snapshot["cursor"],
        "total": snapshot["total"],
        "current_section": snapshot["current"],
        "candidate_version_id": snapshot[
            "candidate_version_id"
        ],
        "error": None,
    }
    refresh_background_lock(
        project_id,
        session_id,
    )
    _publish(
        update,
        status="running",
        stage="started",
    )
    return update


def inspect_node(
    state: FullCIRState,
) -> FullCIRState:
    project_id = int(state["project_id"])
    session_id = str(state["session_id"])

    db = SessionLocal()
    try:
        snapshot = _workflow_snapshot(
            db,
            project_id,
            session_id,
        )
    finally:
        db.close()

    workflow = snapshot["workflow"]
    # Un workflow inactif est terminal pour le worker, même lorsqu'une
    # demande ciblée a annulé le parcours CIR complet. Exiger uniquement la
    # phase ``completed`` faisait alors boucler inspect -> advance sans qu'un
    # nouvel avancement soit possible, jusqu'à GraphRecursionError.
    complete = not snapshot["active"]

    # Si aucune unité n'existe mais une candidate est déjà prête, on considère
    # le job terminé.
    complete = bool(
        complete
        or (
            snapshot["candidate_version_id"]
            and snapshot["session_state"]
            == "candidate_ready"
        )
    )

    update: FullCIRState = {
        **state,
        "status": (
            "completed"
            if complete
            else "running"
        ),
        "stage": (
            "completed"
            if complete
            else "inspect"
        ),
        "cursor": snapshot["cursor"],
        "total": snapshot["total"],
        "current_section": snapshot["current"],
        "candidate_version_id": snapshot[
            "candidate_version_id"
        ],
    }

    refresh_background_lock(
        project_id,
        session_id,
    )
    _publish(
        update,
        status=update["status"],
        stage=update["stage"],
    )
    return update


def advance_node(
    state: FullCIRState,
) -> FullCIRState:
    project_id = int(state["project_id"])
    session_id = str(state["session_id"])

    db = SessionLocal()
    try:
        project = _open_project(
            db,
            project_id,
        )
        background_advance_full_cir(
            db,
            project,
            session_id,
        )
        db.commit()
        snapshot = _workflow_snapshot(
            db,
            project_id,
            session_id,
        )
    finally:
        db.close()

    update: FullCIRState = {
        **state,
        "status": "running",
        "stage": "advance",
        "iteration": int(
            state.get("iteration") or 0
        ) + 1,
        "cursor": snapshot["cursor"],
        "total": snapshot["total"],
        "current_section": snapshot["current"],
        "candidate_version_id": snapshot[
            "candidate_version_id"
        ],
    }

    refresh_background_lock(
        project_id,
        session_id,
    )
    _publish(
        update,
        status="running",
        stage="advance",
    )
    return update


def route_after_inspect(
    state: FullCIRState,
) -> Literal["advance", "end"]:
    return (
        "end"
        if str(state.get("status") or "")
        == "completed"
        else "advance"
    )


def build_full_cir_graph(
    *,
    checkpointer: Any = None,
):
    builder = StateGraph(FullCIRState)
    builder.add_node(
        "start",
        start_node,
    )
    builder.add_node(
        "inspect",
        inspect_node,
    )
    builder.add_node(
        "advance",
        advance_node,
    )

    builder.add_edge(
        START,
        "start",
    )
    builder.add_edge(
        "start",
        "inspect",
    )
    builder.add_conditional_edges(
        "inspect",
        route_after_inspect,
        {
            "advance": "advance",
            "end": END,
        },
    )
    builder.add_edge(
        "advance",
        "inspect",
    )

    return builder.compile(
        checkpointer=checkpointer
    )
