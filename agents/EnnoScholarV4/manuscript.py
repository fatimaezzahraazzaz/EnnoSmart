"""Human-only decisions and immutable manuscript revisions in existing session JSON."""
import copy
import uuid
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .tools.outline import OutlineArgs, resolve_section
from .scientific_scope import ScientificScope
from .scientific_visuals import VisualArgs


class DocumentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["outline_edit", "approve_outline", "approve_document", "set_scope", "edit_draft", "restore_draft", "remove_figure", "update_figure"]
    expected_version: int = Field(ge=0)
    outline: OutlineArgs | None = None
    scope: ScientificScope | None = None
    section_id: str | None = None
    draft_id: str | None = None
    paragraph_id: str | None = None
    text: str | None = Field(default=None, min_length=1, max_length=30000)
    figure_id: str | None = None
    figure: VisualArgs | None = None


def save_document_revision(state):
    doc = state.setdefault("document", {})
    doc["revision"] = doc.get("revision", 0)+1
    doc["review_stale"] = True
    doc.pop("approval", None)
    revision = {"id": "document_"+uuid.uuid4().hex, "revision": doc["revision"],
        "outline": copy.deepcopy(state.get("outline", {})),
        "scientific_scope": copy.deepcopy(state.get("scientific_scope", {})),
        "figures": copy.deepcopy(state.get("scientific_figures", [])),
        "created_at": datetime.now(timezone.utc).isoformat()}
    for section in revision["outline"].get("sections", []):
        section["drafts"] = section.get("drafts", [])[-1:]
    ids = {aid for s in revision["outline"].get("sections",[]) for aid in [
        *s["article_ids"], *(ref for draft in s.get("drafts", []) for ref in draft.get("article_ids", []))]}
    revision["article_metadata"] = {aid:{k:v for k,v in state.get("articles",{}).get(aid,{}).items()
        if k in {"id","title","authors","year","doi","arxiv_id","url","journal","verified","metadata_errors"}} for aid in ids}
    state.setdefault("document_versions", []).append(revision)
    doc["version_id"] = revision["id"]
    return revision


def approve_outline(state, owner):
    outline = state.get("outline", {})
    if not outline.get("sections"):
        raise ValueError("Aucun plan à valider.")
    outline["status"] = "approved"
    outline["approval"] = {"plan_revision": outline.get("plan_revision", outline["version"]),
        "owner_id": owner, "at": datetime.now(timezone.utc).isoformat(), "origin": "authenticated_human"}
    return outline
