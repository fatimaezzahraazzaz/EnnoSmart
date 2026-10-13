"""Canonical lock references for private standalone conversations only."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def resolve_standalone_verrou_ids(
    values: Iterable[Any],
    verrous: Iterable[Mapping[str, Any]],
    *,
    legacy_titles: bool = False,
) -> list[str]:
    """Resolve IDs/titles uniquely, never guess a lock or broaden its scope.

    Older source records truncated titles as though they were IDs. Long unique
    title prefixes are accepted only when reading that legacy conversation data.
    """
    rows = [row for row in verrous if isinstance(row, Mapping) and row.get("id") and row.get("title")]
    output: list[str] = []
    for value in values:
        key = _key(value)
        matches = {str(row["id"]) for row in rows if key == _key(row["id"])}
        if not matches and key:
            matches = {str(row["id"]) for row in rows if key == _key(row["title"])}
        if not matches and legacy_titles and len(key) >= 80:
            matches = {str(row["id"]) for row in rows if _key(row["title"]).startswith(key)}
        if len(matches) == 1:
            identifier = next(iter(matches))
            if identifier not in output:
                output.append(identifier)
    return output


def canonicalize_standalone_links(value: Any, verrous: Iterable[Mapping[str, Any]]) -> Any:
    """Repair known scope fields on copies; leave stored evidence/text intact."""
    rows = list(verrous)
    list_keys = {"target_verrous", "covered_verrou_ids", "verrou_ids", "active_verrou_ids", "related_verrou_ids"}
    scalar_keys = {"verrou_id", "target_verrou_id"}

    def reference(raw: Any) -> Any:
        resolved = resolve_standalone_verrou_ids([raw], rows, legacy_titles=True)
        return resolved[0] if resolved else raw

    def visit(node: Any) -> Any:
        if isinstance(node, Mapping):
            result = {}
            for key, child in node.items():
                if key in list_keys and isinstance(child, (list, tuple, set)):
                    result[key] = []
                    for item in child:
                        fixed = reference(item) if isinstance(item, (str, int)) else visit(item)
                        if fixed not in result[key]:
                            result[key].append(fixed)
                elif key in scalar_keys and isinstance(child, (str, int)):
                    result[key] = reference(child)
                else:
                    result[key] = visit(child)
            return result
        if isinstance(node, (list, tuple)):
            return [visit(child) for child in node]
        return node

    return visit(value)
