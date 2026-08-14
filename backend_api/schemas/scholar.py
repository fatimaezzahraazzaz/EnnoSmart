from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ScholarRead(BaseModel):
    id: int
    project_id: int
    status: str
    report_path: str | None
    raw_result_json: Any | None
    created_at: datetime
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class ArticleDecisionRequest(BaseModel):
    consultant_status: str = Field(
        description="Valeurs recommandées : garde, rejete, en_attente"
    )


class ArticleRead(BaseModel):
    id: int
    scholar_run_id: int
    verrou_id: int | None
    title: str
    year: int | None
    source: str | None
    tag_article: str | None
    score: float | None
    url: str | None
    doi: str | None
    consultant_status: str
    source_json: Any | None
    evidence_status: str | None = None
    evidence_label: str | None = None
    evidence_usable: bool | None = None
    fulltext_ready: bool | None = None
    candidate_only: bool | None = None
    access_check_status: str | None = None
    evidence_reason_code: str | None = None
    evidence_reason_detail: str | None = None
    evidence_recommended_action: str | None = None
    evidence_access_kind: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
