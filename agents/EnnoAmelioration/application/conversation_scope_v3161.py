from __future__ import annotations

import re
import unicodedata
from typing import Any

POLICY_VERSION = "ennoamel_targeted_resume_v3_16_1"

ACTION_NORMAL = "normal"
ACTION_START_PROGRESSIVE = "start_progressive"
ACTION_RESUME_PROGRESSIVE = "resume_progressive"
ACTION_CANCEL_PROGRESSIVE_FOR_TARGET = "cancel_progressive_for_target"


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def is_progressive_continue_message(message: str | None) -> bool:
    text = _norm(message)
    if not text:
        return False
    return bool(
        re.fullmatch(
            r"(?:continue|continuer|poursuis|poursuivre|reprends?|reprendre|suivant|"
            r"continue\s+(?:le|la|l')?\s*(?:cir|document|dossier|analyse)|"
            r"poursuis\s+(?:le|la|l')?\s*(?:cir|document|dossier|analyse))",
            text,
            flags=re.I,
        )
    )


def message_explicitly_requests_full_document(message: str | None) -> bool:
    text = _norm(message)
    if not text:
        return False
    patterns = (
        r"\b(?:tout|toute|entier|entiere|integralite|global|complet|complete)\w*\b"
        r"[^.!?;]{0,80}\b(?:cir|document|dossier|texte)\b",
        r"\b(?:cir|document|dossier|texte)\b[^.!?;]{0,80}"
        r"\b(?:tout|toute|entier|entiere|integralite|global|complet|complete)\w*\b",
        r"\bparagraphe\s+par\s+paragraphe\b",
        r"\b(?:analyse|ameliore|ameliorer|renforce|reformule|traite)\w*\b"
        r"[^.!?;]{0,100}\b(?:ensemble|integralite)\b",
    )
    return any(re.search(p, text, flags=re.I) for p in patterns)


def effective_scope_value(
    *,
    requested_scope: str,
    selected_text_present: bool,
    resolved_section_present: bool,
) -> str:
    if selected_text_present:
        return "selection"
    if resolved_section_present:
        return "section"
    if requested_scope == "section":
        return "full_document"
    return requested_scope


def progressive_action(
    *,
    workflow_active: bool,
    explicit_target_present: bool,
    explicit_full_document_request: bool,
    message: str,
    small_talk: bool,
) -> str:
    if workflow_active:
        if small_talk:
            return ACTION_NORMAL
        if is_progressive_continue_message(message):
            return ACTION_RESUME_PROGRESSIVE
        if explicit_target_present:
            return ACTION_CANCEL_PROGRESSIVE_FOR_TARGET
        if explicit_full_document_request:
            return ACTION_RESUME_PROGRESSIVE
        return ACTION_NORMAL

    if explicit_full_document_request and not small_talk:
        return ACTION_START_PROGRESSIVE

    return ACTION_NORMAL
