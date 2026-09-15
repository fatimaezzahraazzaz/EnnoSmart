from datetime import datetime
from pydantic import BaseModel, ConfigDict


class DocumentRead(BaseModel):
    id: int
    project_id: int
    filename: str
    stored_filename: str
    file_path: str | None
    content_type: str | None
    file_size: int
    document_type: str | None
    upload_status: str
    created_at: datetime
    organisme_id: str | None = None
    subproject: str | None = None
    year: str | None = None
    original_filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    storage_provider: str | None = None
    storage_key: str | None = None
    source_kind: str | None = None
    updated_at: datetime | None = None

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
