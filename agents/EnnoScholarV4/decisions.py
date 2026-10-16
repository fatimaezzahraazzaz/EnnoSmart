"""Consultant decisions on publications, shared by the chat tool and the API.

A consultant decision is distinct from the model's own relevance judgment
(record_candidate_decisions): it is attributed to an exact consultant message or
to an authenticated interface action, and it never approves an unverified claim,
a plan, a manuscript or a CIR eligibility.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

DECISIONS = ("accept", "reject", "hold", "restore")


def held_ids(state):
    return set(state.get("held_article_ids", []))


def apply_decision(state, aid, decision, set_flags, *, verified, origin, source=None):
    """set_flags(aid, selected, selection_override) writes the article record."""
    if decision not in DECISIONS:
        raise ValueError("Décision inconnue : accept, reject, hold ou restore.")
    evaluation = state.setdefault("evaluation", {})
    recommended = evaluation.setdefault("recommended_article_ids", [])
    before = copy.deepcopy(evaluation)
    held = [x for x in state.get("held_article_ids", []) if x != aid]
    if decision == "accept":
        # An unverified reference stays out of citations until verified.
        set_flags(aid, bool(verified), True)
        if aid not in recommended:
            recommended.append(aid)
    elif decision == "reject":
        set_flags(aid, False, False)
        evaluation["recommended_article_ids"] = [x for x in recommended if x != aid]
    elif decision == "hold":
        set_flags(aid, False, None)
        held.append(aid)
        evaluation["recommended_article_ids"] = [x for x in recommended if x != aid]
    else:
        # Return to the model's own judgment; nothing analyzed is deleted.
        set_flags(aid, False, None)
    state["held_article_ids"] = held
    if evaluation != before:
        state.setdefault("evaluation_history", []).append(before)
    row = {"article_id": aid, "decision": decision, "origin": origin,
        "verification_pending": decision == "accept" and not verified,
        "at": datetime.now(timezone.utc).isoformat(), **(source or {})}
    state.setdefault("consultant_decisions", []).append(row)
    return row


def apply_order(state, ordered_ids, *, origin, source=None):
    """Consultant order first; other finalists keep the model's relative order."""
    evaluation = state.setdefault("evaluation", {})
    recommended = evaluation.setdefault("recommended_article_ids", [])
    before = copy.deepcopy(evaluation)
    ordered = list(dict.fromkeys(ordered_ids))
    evaluation["recommended_article_ids"] = [*ordered, *[x for x in recommended if x not in ordered]]
    state["consultant_order"] = ordered
    if evaluation != before:
        state.setdefault("evaluation_history", []).append(before)
    state.setdefault("consultant_decisions", []).append({"order": ordered, "origin": origin,
        "at": datetime.now(timezone.utc).isoformat(), **(source or {})})
    return evaluation["recommended_article_ids"]


def consultant_source(state, message_id, quote):
    """The exact words of a consultant message authorize a chat decision."""
    users = [m for m in state.get("messages", []) if m.get("role") == "user"]
    message = next((m for m in users if m.get("id") == message_id), None)
    if message is None and message_id == "latest" and users:
        message = users[-1]
    if message is None or not quote.strip() or quote.strip() not in message["content"]:
        raise ValueError("Décision du consultant absente de son message exact : citer ses mots.")
    return {"source_message_id": message.get("id", "latest"), "exact_quote": quote.strip()}
