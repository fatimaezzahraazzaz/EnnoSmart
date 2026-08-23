from datetime import datetime
from pydantic import BaseModel, ConfigDict


class DocumentRead(BaseModel):
    id: int
    project_id: int
    filename: str
    stored_filename: str
    file_path: str
    content_type: str | None
    file_size: int
    document_type: str | None
    upload_status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DiagnosticCorpusDecision(BaseModel):
    document_id: int
    keep: bool = False


class DiagnosticCorpusDecisionRequest(BaseModel):
    decisions: list[DiagnosticCorpusDecision]


class DiagnosticCorpusReview(BaseModel):
    diagnostic_documents: list[DocumentRead]
    pending_improvement_documents: list[DocumentRead]
    excluded_improvement_documents: list[DocumentRead]
