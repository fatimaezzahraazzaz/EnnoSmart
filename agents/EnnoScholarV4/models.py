from __future__ import annotations

import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Article(BaseModel):
    id: str = ""
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str = ""
    arxiv_id: str = ""
    external_ids: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    pdf_url: str = ""
    work_urls: list[str] = Field(default_factory=list)
    document_candidates: list[dict] = Field(default_factory=list)
    abstract: str = ""
    journal: str = ""
    publisher: str = ""
    publication_date: str = ""
    access_status: Literal["metadata_only", "abstract_only", "fulltext_available", "fulltext_extracted", "fulltext_analyzed", "access_blocked", "extraction_failed"] = "metadata_only"
    citation_count: int = 0
    sources: list[str] = Field(default_factory=list)
    matched_queries: list[str] = Field(default_factory=list)
    access_level: Literal["metadata_only", "abstract_only", "fulltext"] = "metadata_only"
    verified: bool = False
    verification_sources: list[str] = Field(default_factory=list)
    metadata_errors: list[str] = Field(default_factory=list)
    publisher_access: Literal["open_access", "paywalled", "unknown"] = "unknown"
    fulltext_available: bool | None = None
    analysis_basis: Literal["metadata", "abstract", "fulltext"] | None = None
    fulltext_source: str = ""
    selected: bool = False
    selection_override: bool | None = None
    analysis: dict = Field(default_factory=dict)
    fulltext: dict = Field(default_factory=dict, exclude=True)
    documents: dict = Field(default_factory=dict, exclude=True)
    canonical_work_id: str = ""
    provider_notices: list[dict] = Field(default_factory=list)
    related_article_ids: list[str] = Field(default_factory=list)
    scientific_families: list[str] = Field(default_factory=list)
    # Provider provenance of the notice: provider, rank, query sent (bounded; diagnostic only, never a score).
    search_hits: list[dict] = Field(default_factory=list)
    assessment: dict = Field(default_factory=dict)
    source_version: str = ""

    def durable(self):
        return {**self.model_dump(), "fulltext": self.fulltext, "documents": self.documents}

    def compact(self):
        row = self.model_dump(exclude={"search_hits"})
        row["abstract"] = self.abstract[:3500]
        return row


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = Field(default=None, min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    message: str = Field(min_length=1, max_length=30000)

    @field_validator("message")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Une question scientifique est nécessaire.")
        return value.strip()


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="Nouvelle conversation", min_length=1, max_length=160)
    mode: Literal["express", "diagnostic_linked"] = "express"
    project_id: int | None = Field(default=None, gt=0)
    diagnostic_id: int | None = Field(default=None, gt=0)
    source_version: str | None = Field(default=None, max_length=64)
    selected_lock_ids: list[str] = Field(default_factory=list, max_length=100)
    confirmed: bool = False
    allow_unkept: bool = False

    @model_validator(mode="after")
    def linked_selection(self):
        if self.mode == "diagnostic_linked" and not (self.project_id and self.diagnostic_id and self.source_version and self.selected_lock_ids and self.confirmed):
            raise ValueError("Confirmer le projet, la version du diagnostic et les verrous avant création.")
        if self.mode == "express" and (self.project_id or self.diagnostic_id or self.selected_lock_ids):
            raise ValueError("Une conversation Express ne possède pas de diagnostic lié.")
        return self


class DiagnosticContextUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["scope", "refresh", "selection"]
    expected_revision: int = Field(ge=1)
    lock_ids: list[str] = Field(min_length=1, max_length=100)
    source_version: str | None = Field(default=None, max_length=64)
    allow_unkept: bool = False
    confirmed: bool = False


class SelectionRequest(BaseModel):
    """selected keeps the original contract; decision adds hold and restore."""
    model_config = ConfigDict(extra="forbid")
    selected: bool | None = None
    decision: Literal["accept", "reject", "hold", "restore"] | None = None

    @model_validator(mode="after")
    def one_choice(self):
        if self.decision is None and self.selected is None:
            raise ValueError("selected ou decision est nécessaire.")
        return self


class OrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    article_ids: list[str] = Field(min_length=1, max_length=50)


def public_link(value: str) -> bool:
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password)
    except ValueError:
        return False


def normalize_doi(value: str) -> str:
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I).lower()
    return value if re.fullmatch(r"10\.\d{4,9}/\S+", value) else ""
