"""One active HTML extractor in V5.

Every HTML document V5 extracts carries ``html_extractor == CURRENT``. A saved HTML extraction
without it (made by the previous extractor, in this or another session) is never current data:
  * with its stored source HTML, it is re-extracted locally from those exact bytes (no download,
    no API call, same document identity);
  * without stored HTML, it is retired as current text, so only a new acquisition can replace it.
The superseded record is archived in the session (``superseded_html_extractions``); the source
snapshots that old proofs reference stay in ``source_snapshots``, so saved evidence, analyses and
citations remain verifiable. PDF and JATS documents are not touched.
"""
import copy
import time

CURRENT = "v5_html_floats_1"
ARCHIVE = "superseded_html_extractions"


def stale(document):
    return bool(document and document.get("source_kind") == "html" and document.get("text")
        and document.get("html_extractor") != CURRENT)


def mark(document):
    """Stamp a document just produced by the current V5 HTML extractor."""
    if document.get("source_kind") == "html" and document.get("text"):
        document["html_extractor"] = CURRENT
    return document


def reextract(document, config):
    """Current extraction of the stored HTML bytes, or None when they are absent or unreadable."""
    raw = document.get("source_html") or ""
    if not raw:
        return None
    from .tools.extraction import bounded, html_document
    from .tools.extraction import ScientificChunk
    from .evidence import SEGMENTATION
    _, chunks, truncated = html_document(raw.encode("utf-8"), document.get("url") or "", config, allow_partial=True)
    if not chunks:
        return None
    linked = {s.get("url") for s in document.get("linked_table_sources") or []}
    if linked:
        # Linked table pages were fetched with the original; their records are reused as stored.
        extra = [ScientificChunk.model_validate(c) for c in document.get("chunks", [])
            if (c.get("table") or {}).get("source_url") in linked]
        if extra:
            chunks, more = bounded([*chunks, *extra], config)
            truncated |= more
    text = "\n\n".join(c.text for c in chunks)
    if len(text) < 500:
        return None
    fresh = copy.deepcopy({k: v for k, v in document.items() if k not in {"chunks", "text"}})
    fresh.update(chunks=[c.model_dump() for c in chunks], text=text, truncated=truncated,
        content_scope="partial_scientific_text" if truncated else "scientific_body",
        segmentation=SEGMENTATION, html_extractor=CURRENT,
        reextracted_from={"html_extractor": document.get("html_extractor") or "previous",
            "characters": len(document.get("text", "")), "chunks": len(document.get("chunks", [])),
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return fresh


def _archive(state, article_id, document, outcome):
    record = {k: v for k, v in copy.deepcopy(document).items() if k != "source_html"}
    record.update(superseded_outcome=outcome, superseded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    rows = state.setdefault(ARCHIVE, {}).setdefault(article_id, [])
    key = (record.get("document_version", {}).get("version_id"), record.get("html_extractor"), len(record.get("text", "")))
    if not any((r.get("document_version", {}).get("version_id"), r.get("html_extractor"), len(r.get("text", ""))) == key for r in rows):
        rows.append(record)


def refresh_article(article, state, snapshots, evidence, config):
    """Make this article's HTML text current. Returns 'reextracted', 'retired' or None."""
    if not stale(article.fulltext) and not any(stale(d) for d in article.documents.values()):
        return None
    from .evidence import snapshot
    outcome = None
    if stale(article.fulltext):
        old = article.fulltext
        source = snapshot(article)
        if source["fingerprint"] not in snapshots and any(
                e.get("source_fingerprint") == source["fingerprint"] for e in evidence.values()):
            snapshots[source["fingerprint"]] = source  # keep every saved proof verifiable
        fresh = reextract(old, config)
        _archive(state, article.id, old, "reextracted" if fresh else "retired")
        version = old.get("document_version", {}).get("version_id")
        if fresh:
            article.fulltext = fresh
            if version:
                article.documents[version] = copy.deepcopy(fresh)
            outcome = "reextracted"
        else:
            # No stored HTML: the previous extractor's text is no longer current data.
            article.fulltext = {"attempts": copy.deepcopy(old.get("attempts", [])),
                "remaining_document_urls": copy.deepcopy(old.get("remaining_document_urls", [])),
                "reason": "Extraction HTML d'une version précédente retirée ; une nouvelle acquisition est nécessaire."}
            if version:
                article.documents.pop(version, None)
            article.access_status = "fulltext_available"
            article.access_level = "abstract_only" if article.abstract else "metadata_only"
            article.fulltext_available, article.fulltext_source = True, ""
            outcome = "retired"
    for version, document in list(article.documents.items()):
        if not stale(document):
            continue
        fresh = reextract(document, config)
        _archive(state, article.id, document, "reextracted" if fresh else "retired")
        if fresh:
            article.documents[version] = fresh
        else:
            article.documents.pop(version)
        outcome = outcome or ("reextracted" if fresh else "retired")
    return outcome
