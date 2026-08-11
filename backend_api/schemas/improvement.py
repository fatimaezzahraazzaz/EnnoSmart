from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ImprovementSessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    source_text: str | None = None
    source_document_id: int | None = None
    target_scope: Literal[
        "selection", "paragraph", "section", "multi_section", "full_document"
    ] = "section"
    target_section_id: str | None = None
    target_section_title: str | None = None


class ImprovementMessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    selected_text: str | None = None
    target_scope: Literal[
        "selection", "paragraph", "section", "multi_section", "full_document"
    ] | None = None
    target_section_id: str | None = None
    target_section_title: str | None = None


class ImprovementDecisionCreate(BaseModel):
    decision: Literal["accepted", "rejected"]
    reason: str | None = Field(default=None, max_length=2000)


class ImprovementRestoreCreate(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class ImprovementSourceDecisionCreate(BaseModel):
    candidate_ids: list[str] = Field(min_length=1, max_length=50)
    decision: Literal["accepted", "rejected"]
    reason: str = Field(default="", max_length=2000)


class ImprovementSessionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    target_scope: Literal[
        "selection", "paragraph", "section", "multi_section", "full_document"
    ] | None = None
    target_section_id: str | None = None
    target_section_title: str | None = None
    context: dict[str, Any] | None = None
