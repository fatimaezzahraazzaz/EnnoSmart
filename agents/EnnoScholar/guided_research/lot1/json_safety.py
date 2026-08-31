"""Text safety at the guided-research JSON/PostgreSQL boundary."""
from __future__ import annotations

from typing import Any


def sanitize_json_text(value: Any) -> Any:
    """Remove actual NUL characters without rewriting valid text or JSON types.

    JSON can decode ``\\u0000`` into NUL, which PostgreSQL text/JSONB rejects.
    Clean decoded strings, not serialized JSON: a literal backslash-u sequence,
    accents, arrows, punctuation spacing and line breaks must remain unchanged.
    Return new containers so caller-owned plans and evidence are not mutated.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            clean_key = sanitize_json_text(key) if isinstance(key, str) else key
            if clean_key in cleaned:
                raise ValueError("Deux clés JSON deviennent identiques après retrait d'un caractère nul.")
            cleaned[clean_key] = sanitize_json_text(item)
        return cleaned
    if isinstance(value, list):
        return [sanitize_json_text(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_json_text(item) for item in value)
    return value
