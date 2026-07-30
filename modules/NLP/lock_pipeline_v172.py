# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable, Dict, Iterable, Mapping, Optional, Sequence
from .evidence_graph import build_technical_lock_groups
from .frascati_guard import assess_groups

VERSION = "nlp_lock_pipeline_v177_single_grouping_then_assessment"


def run_lock_pipeline_v172(
    raw_candidates: Iterable[Mapping[str, object]],
    *,
    encode_texts: Optional[Callable[[list[str]], Sequence[Sequence[float]]]] = None,
) -> Dict[str, object]:
    """Nom conservé pour compatibilité ; implémentation V177."""
    grouping = build_technical_lock_groups(raw_candidates, encode_texts=encode_texts)
    assessed = assess_groups(grouping.get("groups") or [])
    all_groups = assessed["technical_lock_groups"]
    main_groups = assessed["verrous_rnd_locaux"]
    secondary_groups = assessed.get("secondary_technical_groups") or []
    return {
        "version": VERSION,
        "logic": "source-text-only seeds, topic-gated grouping, separate main locks from local subproblems, assess Frascati without filtering",
        "technical_lock_groups": all_groups,
        "verrous_rnd_locaux": main_groups,
        "secondary_technical_groups": secondary_groups,
        "frascati_assessment": assessed["frascati_assessment"],
        "grouping_report": grouping,
        "stats": {
            "candidates_input": grouping.get("candidates_count", 0),
            "seed_candidates": grouping.get("seed_count", 0),
            "supporting_evidence": grouping.get("support_count", 0),
            "technical_groups_count": len(all_groups),
            "main_lock_groups_count": len(main_groups),
            "secondary_technical_groups_count": len(secondary_groups),
            "groups_removed_by_frascati": 0,
            "human_validation_required": True,
        },
    }
