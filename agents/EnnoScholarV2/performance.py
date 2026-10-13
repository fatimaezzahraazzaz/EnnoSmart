"""Cache compatibility and shared-work lookup; no scientific selection or scores."""
from __future__ import annotations

import hashlib
import json

from .tools.fulltext import valid_fulltext


def cached_work_fulltext(identifier, registry):
    record = registry[identifier]
    if valid_fulltext(record.fulltext):
        return record.fulltext, record.fulltext_article_id or identifier
    related = list(dict.fromkeys([record.article.canonical_article_id, *record.article.related_article_ids]))
    for key in related:
        candidate = registry.get(key)
        if candidate and valid_fulltext(candidate.fulltext):
            return candidate.fulltext, candidate.fulltext_article_id or key
    return None, None


def analysis_signature(record, config, user_context):
    # Match actual input and model. A richer abstract, new fulltext, version,
    # provenance or changed user context must invalidate a previous analysis.
    payload = {"model": config.model, "user_context": (user_context or "")[:6000],
        "article": record.article.model_dump(exclude={"analysis_basis"}),
        "fulltext": record.fulltext.model_dump() if valid_fulltext(record.fulltext) else None}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
