from __future__ import annotations

from typing import Any

from agents.EnnoAmelioration.application.intention_service import understand_instruction
from agents.EnnoAmelioration.domain.models import TargetScope
from sqlalchemy.orm import Session

from db.models import ImprovementSession

POLICY_VERSION = "ennoamel_background_scope_router_v3_21_2"


def _scope_enum(value: Any) -> TargetScope:
    raw = str(value or "").strip().casefold()
    mapping = {
        "selection": TargetScope.SELECTION,
        "paragraph": TargetScope.PARAGRAPH,
        "section": TargetScope.SECTION,
        "multi_section": TargetScope.MULTI_SECTION,
        "full_document": TargetScope.FULL_DOCUMENT,
    }
    return mapping.get(raw, TargetScope.SECTION)


def _has_explicit_local_target(payload: Any) -> bool:
    selected_text = str(getattr(payload, "selected_text", None) or "").strip()
    target_section_id = str(getattr(payload, "target_section_id", None) or "").strip()
    target_section_title = str(getattr(payload, "target_section_title", None) or "").strip()
    return bool(selected_text or target_section_id or target_section_title)


def resolve_background_scope(
    db: Session,
    project_id: int,
    session_id: str,
    payload: Any,
) -> dict[str, Any]:
    session = (
        db.query(ImprovementSession)
        .filter(
            ImprovementSession.id == str(session_id),
            ImprovementSession.project_id == int(project_id),
        )
        .first()
    )
    if session is None:
        raise LookupError("Session EnnoAmelioration introuvable.")

    explicit_scope_raw = str(getattr(payload, "target_scope", None) or "").strip()
    explicit_scope = _scope_enum(explicit_scope_raw) if explicit_scope_raw else None
    stored_scope = _scope_enum(getattr(session, "target_scope", None))
    message = str(getattr(payload, "message", None) or "").strip()

    if _has_explicit_local_target(payload):
        effective = explicit_scope if explicit_scope is not None else TargetScope.SECTION
        print(
            "[V3.21.2][BackgroundRoute] "
            f"session={session_id} route=sync "
            f"reason=explicit_local_target scope={effective.value}"
        )
        return {
            "background": False,
            "scope": effective.value,
            "reason": "explicit_local_target",
            "semantic_scope": None,
        }

    if explicit_scope == TargetScope.FULL_DOCUMENT:
        session.target_scope = TargetScope.FULL_DOCUMENT.value
        db.flush()
        print(
            "[V3.21.2][BackgroundRoute] "
            f"session={session_id} route=background "
            "reason=explicit_full_document"
        )
        return {
            "background": True,
            "scope": TargetScope.FULL_DOCUMENT.value,
            "reason": "explicit_full_document",
            "semantic_scope": TargetScope.FULL_DOCUMENT.value,
        }

    semantic = understand_instruction(message, stored_scope)
    semantic_scope = semantic.target_scope

    if semantic_scope == TargetScope.FULL_DOCUMENT:
        session.target_scope = TargetScope.FULL_DOCUMENT.value
        db.flush()
        print(
            "[V3.21.2][BackgroundRoute] "
            f"session={session_id} route=background "
            f"reason=semantic_full_document "
            f"stored={stored_scope.value} "
            f"payload={explicit_scope_raw or '-'}"
        )
        return {
            "background": True,
            "scope": TargetScope.FULL_DOCUMENT.value,
            "reason": "semantic_full_document",
            "semantic_scope": TargetScope.FULL_DOCUMENT.value,
        }

    if (
        stored_scope == TargetScope.FULL_DOCUMENT
        and str(getattr(session, "state", "") or "").casefold()
        not in {"candidate_ready", "accepted", "completed"}
    ):
        print(
            "[V3.21.2][BackgroundRoute] "
            f"session={session_id} route=background "
            "reason=stored_full_document"
        )
        return {
            "background": True,
            "scope": TargetScope.FULL_DOCUMENT.value,
            "reason": "stored_full_document",
            "semantic_scope": semantic_scope.value,
        }

    effective = explicit_scope if explicit_scope is not None else semantic_scope
    print(
        "[V3.21.2][BackgroundRoute] "
        f"session={session_id} route=sync "
        f"reason=non_full_document scope={effective.value}"
    )
    return {
        "background": False,
        "scope": effective.value,
        "reason": "non_full_document",
        "semantic_scope": semantic_scope.value,
    }
