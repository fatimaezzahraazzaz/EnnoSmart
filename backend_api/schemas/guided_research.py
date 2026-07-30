# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class GuidedResearchSessionCreate(BaseModel):
    target_mode: Literal["global", "per_verrou", "section_improvement", "full_cir_improvement"] = "global"
    entry_module: Literal["ennoscholar", "ennoamel"] = "ennoscholar"


class GuidedResearchMessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=30000)


class GuidedResearchSourceDecision(BaseModel):
    candidate_ids: list[str] = Field(min_length=1)
    decision: Literal["accepted", "rejected"]
    reason: str = ""
    prepare_after_acceptance: bool = True


class GuidedResearchSessionResponse(BaseModel):
    ok: bool = True
    session: dict[str, Any]
    artifacts: dict[str, Any] | None = None


class GuidedResearchMessageResponse(BaseModel):
    ok: bool = True
    response: dict[str, Any]
