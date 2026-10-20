"""Shared public scientific library: key and eligibility only (not activated).

Eight consultants should not pay eight readings of the same public publication. This
module defines what a shared card would be keyed on and which cards could ever be
shared; no storage, lookup or copy exists yet. Activation needs the administrator's
validation of confidentiality and licence rules (see docs/ennoscholar-v4-audit-128698bb-corrections.md).

Shared: facts of a public work read from a public document (canonical identity, exact
document hash, card schema, passages and fields examined, provenance of the reading).
Never shared: uploaded or private documents, diagnostics, client R&D, consultant notes
and decisions, relevance judgments, project confrontations — they stay in their session
under the existing access rights (service.owned, permitted_project).
"""
from __future__ import annotations

from .scientific_reading import reading_identity, ANALYSIS_SCHEMA_VERSION

PRIVATE_SOURCE_KINDS = {"user_uploaded_document", "project_document", "diagnostic"}


def shareable(article):
    """Only a public work whose read text is itself public, identified by an exact hash."""
    fulltext = article.fulltext or {}
    if fulltext.get("source_kind") in PRIVATE_SOURCE_KINDS or (article.id or "").startswith(("upload_", "project_")):
        return False
    if not (article.doi or article.arxiv_id):
        return False                       # no public identity to share under
    if fulltext.get("text"):
        version = fulltext.get("document_version") or {}
        return bool(version.get("document_sha256")) and article.publisher_access == "open_access" or bool(
            (version.get("identifiers") or {}).get("arxiv"))
    return bool(article.abstract)          # public abstract of a public record


def library_key(article):
    """Key of a shared card (None when the card must stay in its session)."""
    if not shareable(article):
        return None
    return {"identity": reading_identity(article), "schema": ANALYSIS_SCHEMA_VERSION,
        "doi": (article.doi or "").casefold() or None, "arxiv": article.arxiv_id or None}
