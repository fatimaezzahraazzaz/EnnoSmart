from __future__ import annotations

import re

from typing import Any

from agents.EnnoAmelioration.application.intention_service import understand_instruction
from agents.EnnoAmelioration.application.conversation_scope_v3161 import (
    message_explicitly_requests_full_document,
)
from agents.EnnoAmelioration.application.section_parser import (
    infer_section_from_instruction,
    parse_sections,
)
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


def _message_targets_existing_section(
    session: ImprovementSession,
    message: str,
) -> bool:
    """Vérifie la cible dans la version active, sans dépendre du scope mémorisé."""

    versions = list(session.versions or [])
    active = next(
        (
            row
            for row in versions
            if str(row.id) == str(session.active_version_id or "")
        ),
        None,
    )
    if active is None and versions:
        active = max(versions, key=lambda row: int(row.version_number or 0))
    text = str(getattr(active, "content", "") or "")
    if not text.strip() or not str(message or "").strip():
        return False
    return infer_section_from_instruction(
        message,
        parse_sections(text),
    ) is not None


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

    # Important : target_scope peut avoir une valeur par défaut dans le modèle
    # Pydantic même lorsque le frontend ne l'a PAS envoyé.
    # On ne le considère donc comme explicite que s'il était réellement présent
    # dans la requête HTTP.
    fields_set = set(getattr(payload, "model_fields_set", set()) or set())
    scope_was_sent = "target_scope" in fields_set

    explicit_scope_raw = (
        str(getattr(payload, "target_scope", None) or "").strip()
        if scope_was_sent
        else ""
    )
    explicit_scope = _scope_enum(explicit_scope_raw) if explicit_scope_raw else None
    stored_scope = _scope_enum(getattr(session, "target_scope", None))
    message = str(getattr(payload, "message", None) or "").strip()

    # Une demande explicite sur l'ensemble du CIR gagne toujours,
    # même si le frontend conserve une ancienne cible de section.
    normalized_early_message = " ".join(str(message or "").casefold().split())
    explicit_full_message = bool(
        message_explicitly_requests_full_document(message)
        or re.search(
            r"\\bensemble\\b.{0,100}\\b(?:cir|document|dossier|texte)\\b",
            normalized_early_message,
        )
        or re.search(
            r"\\b(?:tout|toute|entier|entiere|complet|complete|global|integralite)\\w*\\b"
            r".{0,100}\\b(?:cir|document|dossier|texte)\\b",
            normalized_early_message,
        )
        or re.search(
            r"\\b(?:cir|document|dossier|texte)\\b.{0,100}"
            r"\\b(?:tout|toute|entier|entiere|complet|complete|global|integralite)\\w*\\b",
            normalized_early_message,
        )
    )

    if explicit_full_message:
        session.target_scope = TargetScope.FULL_DOCUMENT.value
        session.target_section_id = None
        session.target_section_title = None
        db.flush()
        print(
            "[V3.21.6][BackgroundRoute] "
            f"session={session_id} route=background "
            "reason=message_explicit_full_document_priority"
        )
        return {
            "background": True,
            "scope": TargetScope.FULL_DOCUMENT.value,
            "reason": "message_explicit_full_document_priority",
            "semantic_scope": TargetScope.FULL_DOCUMENT.value,
        }

    # Le choix local du frontend prime sur les mots du message et sur un
    # ancien scope FULL_DOCUMENT mémorisé par la conversation.
    if explicit_scope is not None and explicit_scope != TargetScope.FULL_DOCUMENT:
        return {
            "background": False,
            "scope": explicit_scope.value,
            "reason": "explicit_local_scope",
            "semantic_scope": None,
        }

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

    # Le message courant est prioritaire sur le scope mémorisé de la session.
    # Une session peut rester FULL_DOCUMENT tout en ciblant une seule partie.
    # Une demande explicite sur l'ensemble du CIR est prioritaire sur toute
    # similarité lexicale avec une section particulière.
    normalized_message = " ".join(str(message or "").casefold().split())
    message_requests_full_document = bool(
        message_explicitly_requests_full_document(message)
        or re.search(
            r"\bensemble\b.{0,100}\b(?:cir|document|dossier|texte)\b",
            normalized_message,
        )
        or re.search(
            r"\b(?:tout|toute|entier|entiere|complet|complete|global|integralite)\w*\b"
            r".{0,100}\b(?:cir|document|dossier|texte)\b",
            normalized_message,
        )
        or re.search(
            r"\b(?:cir|document|dossier|texte)\b.{0,100}"
            r"\b(?:tout|toute|entier|entiere|complet|complete|global|integralite)\w*\b",
            normalized_message,
        )
    )

    if message_requests_full_document:
        session.target_scope = TargetScope.FULL_DOCUMENT.value
        db.flush()
        print(
            "[V3.21.2][BackgroundRoute] "
            f"session={session_id} route=background "
            "reason=message_explicit_full_document"
        )
        return {
            "background": True,
            "scope": TargetScope.FULL_DOCUMENT.value,
            "reason": "message_explicit_full_document",
            "semantic_scope": TargetScope.FULL_DOCUMENT.value,
        }

    message_targets_section = _message_targets_existing_section(session, message)

    if message_targets_section:
        print(
            "[V3.21.2][BackgroundRoute] "
            f"session={session_id} route=sync "
            "reason=message_targets_existing_section"
        )
        return {
            "background": False,
            "scope": TargetScope.SECTION.value,
            "reason": "message_targets_existing_section",
            "semantic_scope": TargetScope.SECTION.value,
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
