"""Attach actual version provenance without changing the verified fulltext cache."""
from __future__ import annotations

import re

from .canonical import version_kind


def analysis_material(identifier, registry):
    record = registry[identifier]
    fulltext = record.fulltext
    if not fulltext or fulltext.access_level != "fulltext":
        return fulltext
    source_id = fulltext.source_article_id or record.fulltext_article_id or identifier
    source = registry.get(source_id, record)
    chunks = fulltext.chunks
    if not chunks:
        # A flattened copy may still have its original version's paginated chunks.
        # Recover only for an identical verified document, never guess page numbers.
        candidates = list(dict.fromkeys([source_id, *record.article.related_article_ids]))
        for key in candidates:
            original = registry.get(key)
            original = original.fulltext if original else None
            if original and original.chunks and original.fulltext_url == fulltext.fulltext_url and re.sub(r"\s+", " ", original.text).strip() == re.sub(r"\s+", " ", fulltext.text).strip():
                chunks = original.chunks
                source_id = original.source_article_id or key
                source = registry.get(source_id, source)
                break
    source_format = fulltext.source_format or ("pdf" if any(chunk.page is not None for chunk in chunks) else "html" if chunks else "unpaginated_text")
    return fulltext.model_copy(update={"chunks": chunks, "source_article_id": source_id,
        "source_version": fulltext.source_version or version_kind(source.article), "source_format": source_format})
