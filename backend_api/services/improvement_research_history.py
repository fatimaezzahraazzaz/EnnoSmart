"""Conversation-local research snapshots; source identity includes its search."""
from __future__ import annotations

from typing import Any


def research_history(context: dict[str, Any], messages: list[Any] | None = None) -> list[dict[str, Any]]:
    history: dict[str, dict[str, Any]] = {}
    snapshots = []
    for message in messages or []:
        metadata = getattr(message, "metadata_json", None) or {}
        handoff = metadata.get("scholar_handoff")
        if isinstance(handoff, dict):
            snapshots.append({**handoff, "message_id": getattr(message, "id", None)})
    snapshots.extend(context.get("research_history") or [])
    snapshots.append(context.get("scholar_handoff") or {})
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        search_id = str(snapshot.get("guided_session_id") or "").strip()
        if not search_id:
            continue
        merged = {**history.get(search_id, {}), **snapshot}
        merged["sources"] = [
            {**source, "guided_session_id": search_id}
            for source in merged.get("sources") or [] if isinstance(source, dict)
        ]
        history[search_id] = merged
    return list(history.values())


def with_research_history(context: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    sources = [source for search in history for source in search.get("sources") or []]
    latest_id = str((context.get("scholar_handoff") or {}).get("guided_session_id") or "")
    latest = next((search for search in history if search["guided_session_id"] == latest_id), None)
    return {
        **context,
        "research_history": history,
        "scholar_handoff": latest or context.get("scholar_handoff"),
        "research_sources": sources,
        "accepted_research_sources": [source for source in sources if source.get("consultant_decision") == "accepted"],
    }


def record_research(context: dict[str, Any], handoff: dict[str, Any], *, message_id: str | None = None) -> dict[str, Any]:
    """Add/update one search without replacing earlier results or decisions."""
    history = research_history(context)
    search_id = str(handoff["guided_session_id"])
    previous = next((row for row in history if row["guided_session_id"] == search_id), {})
    snapshot = {**previous, **handoff}
    if message_id:
        snapshot["message_id"] = message_id
    snapshot["sources"] = [{**row, "guided_session_id": search_id} for row in snapshot.get("sources") or []]
    history = [snapshot if row["guided_session_id"] == search_id else row for row in history]
    if not previous:
        history.append(snapshot)
    return with_research_history({**context, "scholar_handoff": snapshot}, history)


def resolve_source_search(history: list[dict[str, Any]], candidate_ids: list[str], search_id: str | None = None) -> dict[str, Any]:
    """Reject foreign/ambiguous cards instead of sending them to the latest search."""
    wanted = {str(value).strip() for value in candidate_ids if str(value).strip()}
    matches = [
        search for search in history
        if (not search_id or search["guided_session_id"] == search_id)
        and wanted
        and wanted.issubset({str(row.get("candidate_id") or "") for row in search.get("sources") or []})
    ]
    if len(matches) != 1:
        raise ValueError("Ces sources ne sont pas rattachées sans ambiguïté à une recherche de cette conversation.")
    return matches[0]
