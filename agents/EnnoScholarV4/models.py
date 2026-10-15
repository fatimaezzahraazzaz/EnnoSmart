from __future__ import annotations

import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    assessment: dict = Field(default_factory=dict)
    source_version: str = ""

    def durable(self):
        return {**self.model_dump(), "fulltext": self.fulltext, "documents": self.documents}

    def compact(self):
        row = self.model_dump()
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


class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected: bool


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
