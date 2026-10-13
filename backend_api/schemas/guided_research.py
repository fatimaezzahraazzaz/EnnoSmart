# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class GuidedResearchHandoffCreate(BaseModel):
    """Snapshot explicite Agent 1 -> EnnoScholar.

    Tous les champs sont optionnels pour préserver le frontend V5 actuel : si
    aucun handoff n'est fourni, le backend photographie automatiquement l'état
    courant du projet lors de la création de la conversation.
    """

    source_agent: Literal[
        "ennodiagnostic",
        "ennoscholar",
        "ennoamel",
        "manual",
        "standalone",
    ] = "ennodiagnostic"
    diagnostic_run_id: int | None = Field(default=None, ge=1)
    scholar_run_id: int | None = Field(default=None, ge=1)
    verrou_ids: list[int] = Field(default_factory=list)
    selected_article_ids: list[int] = Field(default_factory=list)
    review_scope: Literal["auto", "global", "per_verrou"] = "auto"


class GuidedResearchSessionCreate(BaseModel):
    target_mode: Literal["global", "per_verrou", "section_improvement", "full_cir_improvement"] = "global"
    entry_module: Literal["ennoscholar", "ennoamel"] = "ennoscholar"
    handoff: GuidedResearchHandoffCreate | None = None


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
