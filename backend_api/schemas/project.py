from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    organisme: str = Field(min_length=1, max_length=255)
    project_name: str = Field(min_length=1, max_length=255)
    year: str = Field(min_length=4, max_length=20)
    domain_label: str | None = None


class ProjectUpdate(BaseModel):
    organisme: str | None = None
    project_name: str | None = None
    year: str | None = None
    domain_label: str | None = None
    status: str | None = None


class ProjectRead(BaseModel):
    id: int
    consultant_id: int
    organisme: str
    project_name: str
    year: str
    domain_label: str | None
    status: str
    ai_folder: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
