import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable, List, Set
from .config import FusionConfig
from .schemas import CanonicalEntity, EvidenceRef, ExtractionItem

STOP = {
    "de","la","le","les","des","du","un","une","et","ou","a","au","aux",
    "dans","pour","par","sur","avec","sans","est","sont","the","of","and","to",
}

def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def _tokens(text: str) -> Set[str]:
    return {x for x in _norm(text).split() if len(x) >= 3 and x not in STOP}

def token_jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

def is_similar(a: ExtractionItem, b: ExtractionItem, cfg: FusionConfig) -> bool:
    if a.kind != b.kind:
        return False
    threshold = cfg.lock_token_jaccard if a.kind == "lock" else cfg.default_token_jaccard
    jac = token_jaccard(a.statement, b.statement)
    seq = SequenceMatcher(None, _norm(a.statement), _norm(b.statement)).ratio()
    return jac >= threshold or seq >= cfg.sequence_ratio

def _entity_id(kind: str, statement: str) -> str:
    d = hashlib.sha1(f"{kind}|{_norm(statement)}".encode()).hexdigest()[:12]
    return f"ent_{d}"

def _dedupe_evidence(evs: List[EvidenceRef]) -> List[EvidenceRef]:
    seen, out = set(), []
    for ev in evs:
        if ev.evidence_id not in seen:
            seen.add(ev.evidence_id)
            out.append(ev)
    return out

def fuse_items(items: Iterable[ExtractionItem], cfg: FusionConfig):
    clusters = []
    for item in items:
        target = next(
            (cluster for cluster in clusters if any(is_similar(item, x, cfg) for x in cluster)),
            None,
        )
        if target is None:
            clusters.append([item])
        else:
            target.append(item)

    entities = []
    for cluster in clusters:
        representative = max(
            cluster,
            key=lambda x: (int(bool(x.unresolved_question)) if x.kind == "lock" else 0, len(x.statement)),
        )
        evidences = _dedupe_evidence([x.evidence for x in cluster])
        docs = sorted({x.document_id for x in evidences})
        entities.append(CanonicalEntity(
            entity_id=_entity_id(representative.kind, representative.statement),
            kind=representative.kind,
            statement=representative.statement,
            source_item_ids=[x.item_id for x in cluster],
            evidences=evidences,
            document_ids=docs,
            needs_review=any(x.needs_review for x in cluster),
            metadata={
                "source_count": len(cluster),
                "document_count": len(docs),
            },
        ))
    return entities
