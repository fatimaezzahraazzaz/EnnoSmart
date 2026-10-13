from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


RoleName = Literal["consultant", "admin", "superadmin"]


class AdminUserCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: RoleName = "consultant"
    company: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)


class AdminUserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    role: RoleName | None = None
    is_active: bool | None = None


class ProjectAssignmentUpdate(BaseModel):
    consultant_id: int


class ProjectWorkflowUpdate(BaseModel):
    stage: Literal[
        "collecte",
        "diagnostic",
        "validation_verrous",
        "recherche_scientifique",
        "redaction",
        "revue_consultant",
        "finalise",
    ]
    progress_percent: int = Field(ge=0, le=100)
    priority: Literal["basse", "normale", "haute", "urgente"] = "normale"
    due_date: datetime | None = None
    notes: str | None = Field(default=None, max_length=4000)


class AIModelSettings(BaseModel):
    provider: Literal["openai", "ollama", "openrouter", "gemini"] = "ollama"
    primary_model: str = Field(default="qwen2.5:7b-instruct", min_length=1, max_length=255)
    writer_model: str | None = Field(default=None, max_length=255)
    fallback_models: list[str] = Field(default_factory=list, max_length=10)
    allow_cross_provider_fallback: bool = False
    default_temperature: float = Field(default=0.1, ge=0, le=2)
    max_output_tokens_cap: int = Field(default=16000, ge=256, le=200000)
    max_prompt_chars: int = Field(default=30000, ge=1000, le=2000000)
    writer_max_prompt_chars: int = Field(default=180000, ge=5000, le=4000000)
    monthly_budget_eur: float = Field(default=500, ge=0, le=1000000)
    enabled_agents: dict[str, bool] = Field(
        default_factory=lambda: {
            "diagnostic": True,
            "scholar": True,
            "improvement": True,
            "cir_memory": True,
        }
    )
