from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class DiagnosticRead(BaseModel):
    id: int
    project_id: int
    status: str
    report_path: str | None
    nlp_result_path: str | None
    selected_verrous_path: str | None
    raw_result_json: Any | None
    created_at: datetime
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class VerrouDecisionRequest(BaseModel):
    consultant_status: str = Field(
        description="Valeurs recommandées : garde, rejete, reformuler, en_attente"
    )


class VerrouManualCreate(BaseModel):
    title: str = Field(min_length=5, max_length=500)
    description: str = Field(default="", max_length=4000)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    force_create_distinct: bool = False


class VerrouRead(BaseModel):
    id: int
    diagnostic_run_id: int
    title: str
    tag_cir: str | None
    score: float | None
    consultant_status: str
    justification: str | None
    source_json: Any | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
