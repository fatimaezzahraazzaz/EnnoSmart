from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

AccessLevel = Literal["metadata_only", "abstract_only", "fulltext"]
AnalysisBasis = Literal["metadata", "abstract", "fulltext"]


class SearchIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_terms: list[str] = Field(min_length=1, max_length=12)
    primary_operations: list[str] = Field(min_length=1, max_length=12)
    secondary_operations: list[str] = Field(max_length=12)
    adjacent_operations: list[str] = Field(max_length=12)
    method_terms: list[str] = Field(max_length=12)
    context_terms: list[str] = Field(max_length=12)

    @field_validator("target_terms", "primary_operations", "secondary_operations", "adjacent_operations", "method_terms", "context_terms")
    @classmethod
    def bounded_terms(cls, values):
        if any(not value.strip() or len(value) > 120 for value in values):
            raise ValueError("Les termes d'intention doivent être courts et non vides.")
        return list(dict.fromkeys(value.strip() for value in values))


class Article(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = ""
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    doi: str = ""
    arxiv_id: str = ""
    url: str = ""
    pdf_url: str = ""
    source: str = ""
    sources: list[str] = Field(default_factory=list)
    citation_count: int = 0
    access_level: AccessLevel = "metadata_only"
    analysis_basis: AnalysisBasis | None = None
    relevance_score: float | None = None
    relevance_tag: Literal["DIRECT", "CONNEXE", "FONDAMENTAL"] | None = None
    ranking_reason: str = ""
    matched_queries: list[str] = Field(default_factory=list)
    retrieval_score: float | None = None
    relevance_label: Literal["DIRECT", "CONNEXE", "FONDAMENTAL"] | None = None
    relevance_origin: str = ""
    relevance_for: str = ""
    canonical_article_id: str = ""
    related_article_ids: list[str] = Field(default_factory=list)
    work_urls: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    provider_notices: list[dict] = Field(default_factory=list)


class TextChunk(BaseModel):
    page: int | None = None
    section: str = ""
    text: str


class FulltextResult(BaseModel):
    access_level: AccessLevel
    fulltext_source: str = ""
    fulltext_url: str = ""
    text: str = ""
    reason: str = ""
    chunks: list[TextChunk] = Field(default_factory=list)
    truncated: bool = False
    source_article_id: str = ""
    source_version: str = ""
    source_format: str = ""


class Evidence(BaseModel):
    evidence_id: str = ""
    page: int | None = None
    section: str = ""
    excerpt: str = Field(max_length=600)
    supports: list[Literal["problem", "method", "data", "results", "limitations", "future_work"]] = Field(default_factory=list)
    source_article_id: str = ""
    canonical_work_id: str = ""
    source_version: str = ""
    source_url: str = ""

    @computed_field
    @property
    def exact_quote(self) -> str:
        return self.excerpt

    @computed_field
    @property
    def page_number(self) -> int | None:
        return self.page


class ScientificMemory(BaseModel):
    """Server-owned selection state, independent from discovered records."""
    active_article_ids: list[str] = Field(default_factory=list)
    candidate_window: list[str] = Field(default_factory=list)
    candidate_excerpts: dict[str, str] = Field(default_factory=dict)
    inspected_article_ids: list[str] = Field(default_factory=list)
    pinned_article_ids: list[str] = Field(default_factory=list)
    finalist_article_ids: list[str] = Field(default_factory=list)
    detailed_article_ids: list[str] = Field(default_factory=list)
    pending_clarification: dict | None = None


class ArticleAnalysis(BaseModel):
    problem: str = ""
    method: str = ""
    data: str = ""
    results: str = ""
    limitations: list[str] = Field(default_factory=list)
    future_work: list[str] = Field(default_factory=list)
    relevance: str = ""
    access_level: AccessLevel
    analysis_basis: AnalysisBasis
    evidence: list[Evidence] = Field(default_factory=list)
    analysis_limit: str = ""


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = Field(default=None, max_length=80)
    message: str = Field(min_length=1, max_length=20000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=60)

    @field_validator("message")
    @classmethod
    def nonempty_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Le message ne peut pas être vide.")
        return value.strip()

    @field_validator("session_id")
    @classmethod
    def safe_id(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", value):
            raise ValueError("Identifiant de conversation invalide.")
        return value


class Citation(BaseModel):
    url: str
    title: str = ""
    start_index: int | None = None
    end_index: int | None = None
    cited: bool = True


class ToolActivity(BaseModel):
    tool: str
    status: Literal["completed", "error", "skipped"]


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    articles: list[Article] = Field(default_factory=list)
    articles_total: int = 0
    canonical_work_count: int = 0
    record_count: int = 0
    version_count: int = 0
    active_article_ids: list[str] = Field(default_factory=list)
    tool_activity: list[ToolActivity] = Field(default_factory=list)
    partial: bool = False
    reason: str | None = None
    tool_calls: int = 0
    tool_counts: dict[str, int] = Field(default_factory=dict)
    rounds: int = 0


class ArticlePage(BaseModel):
    session_id: str
    total: int
    offset: int
    limit: int
    next_offset: int | None
    articles: list[Article]
    canonical_work_count: int = 0
    record_count: int = 0
    version_count: int = 0
