import hashlib
from typing import Iterable
from .schemas import CanonicalEntity, ReviewItem

def build_review_queue(entities: Iterable[CanonicalEntity], require_all_locks=True):
    out = []
    for entity in entities:
        if not (entity.needs_review or (require_all_locks and entity.kind == "lock")):
            continue
        reason = []
        if entity.kind == "lock":
            reason.append("validation_consultant_lock")
        if entity.needs_review:
            reason.append("automatic_uncertainty_or_disagreement")
        rid = hashlib.sha1(entity.entity_id.encode()).hexdigest()[:10]
        out.append(ReviewItem(
            review_id=f"rev_{rid}",
            entity_id=entity.entity_id,
            kind=entity.kind,
            statement=entity.statement,
            reason=";".join(reason),
            evidence_ids=[x.evidence_id for x in entity.evidences],
        ))
    return out
