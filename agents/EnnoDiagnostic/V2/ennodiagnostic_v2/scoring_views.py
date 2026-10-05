from __future__ import annotations

from typing import Iterable, List, Tuple

from .schemas import CanonicalEntity, ExtractionItem


def build_project_scoring_view(
    entities: Iterable[CanonicalEntity],
    extractions: Iterable[ExtractionItem],
) -> Tuple[List[CanonicalEntity], List[ExtractionItem]]:
    """Vue de scoring limitée aux preuves réellement attribuées au projet."""
    entities = list(entities)
    extractions = list(extractions)

    project_extractions = [
        item for item in extractions
        if str(item.metadata.get("source_role") or "unknown").strip().lower()
        == "project_evidence"
    ]

    scoring_entities = []
    for entity in entities:
        if entity.kind == "lock" and not bool(
            entity.metadata.get("has_project_evidence", False)
        ):
            continue
        scoring_entities.append(entity)

    return scoring_entities, project_extractions
