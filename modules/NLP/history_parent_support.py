# -*- coding: utf-8 -*-
"""
Attach confirmed CIR history to consolidated current parent locks without
creating an additional current main lock.

The installer keeps this helper isolated because the local HISTORY pipeline can
be newer than the GitHub branch. It never rewrites an unknown history stage.
"""

from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any, Dict, List, Mapping, Sequence
import re
import unicodedata

VERSION = "history_parent_support_v1_20260916"
LOG_PREFIX = "[EnnoDiagnostic][HISTORY_SUPPORT_ATTACHED]"


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: Any) -> set[str]:
    stop = {
        "avec", "dans", "pour", "sans", "sous", "entre", "ainsi", "cette",
        "comme", "plus", "moins", "the", "and", "with", "from", "that",
        "this", "des", "les", "une", "sur", "est", "sont",
    }
    return {x for x in _norm(value).split() if len(x) >= 4 and x not in stop}


def _text(item: Mapping[str, Any]) -> str:
    return str(
        item.get("text")
        or item.get("title")
        or item.get("verrou")
        or item.get("incertitude")
        or item.get("label")
        or ""
    )


def _similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    a, b = _text(left), _text(right)
    ta, tb = _tokens(a), _tokens(b)
    union = ta | tb
    jaccard = len(ta & tb) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, _norm(a), _norm(b)).ratio()
    return max(jaccard, sequence * 0.80)


def attach_confirmed_history(
    parent_locks: Sequence[Mapping[str, Any]],
    historical_items: Sequence[Mapping[str, Any]],
    *,
    minimum_similarity: float = 0.28,
) -> Dict[str, Any]:
    parents = [deepcopy(dict(x)) for x in parent_locks if isinstance(x, Mapping)]
    unmatched: List[Dict[str, Any]] = []

    for raw in historical_items:
        if not isinstance(raw, Mapping):
            continue
        item = deepcopy(dict(raw))
        if not parents:
            unmatched.append(item)
            continue

        scores = sorted(
            [(_similarity(parent, item), index) for index, parent in enumerate(parents)],
            reverse=True,
        )
        best_score, best_index = scores[0]
        second_score = scores[1][0] if len(scores) > 1 else 0.0

        if best_score < minimum_similarity or (
            len(scores) > 1 and best_score < second_score + 0.04
        ):
            unmatched.append(item)
            continue

        parent = parents[best_index]
        parent.setdefault("historical_supports", []).append(item)
        historical_id = str(
            item.get("id")
            or item.get("memory_id")
            or item.get("passage_id")
            or item.get("lock_group_id")
            or "history"
        )
        parent_id = str(
            parent.get("lock_group_id")
            or parent.get("passage_id")
            or best_index
        )
        print(
            f"{LOG_PREFIX} historical_id={historical_id} "
            f"-> parent_id={parent_id} similarity={best_score:.3f}"
        )

    return {
        "version": VERSION,
        "parents": parents,
        "unmatched_history": unmatched,
    }
