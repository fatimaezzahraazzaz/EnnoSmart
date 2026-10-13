from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ArticleIdentity(BaseModel):
    article_id: int | str | None = None
    doi: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    known_urls: list[str] = Field(default_factory=list)
    source: str | None = None
    deterministic_oa_checked: bool = False

    # Traçabilité de l'enrichissement sans casser le contrat existant.
    input_doi: str | None = None
    doi_status: Literal[
        "missing",
        "provided",
        "verified",
        "reconciled",
        "conflict_ignored",
        "lookup_failed",
    ] = "missing"
    metadata_sources: list[str] = Field(default_factory=list)
    metadata_warnings: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def title_required(cls, value: str) -> str:
        clean = (value or "").strip()
        if not clean:
            raise ValueError("Le titre de l'article est obligatoire.")
        return clean


class FulltextCandidate(BaseModel):
    provider: str
    provider_priority: int = 100
    pdf_url: str | None = None
    landing_url: str | None = None
    license: str | None = None
    version: str | None = None
    host_type: str | None = None
    legal_access: bool = True

    access_type: str | None = None
    rights_status: str | None = None
    source_domain: str | None = None
    discovered_via: str | None = None

    candidate_doi: str | None = None
    candidate_title: str | None = None
    candidate_authors: list[str] = Field(default_factory=list)
    candidate_year: int | None = None

    verified_pdf: bool = False
    probe_status: str | None = None
    probe_http_status: int | None = None
    probe_failure_kind: str | None = None
    final_url: str | None = None
    content_type: str | None = None
    bytes_checked: int = 0
    discovered_from_landing: bool = False
    content_head_sha256: str | None = None
    resolution_status: str | None = None

    identity_score: float = 0.0
    identity_method: str | None = None
    same_article: bool = False
    warnings: list[str] = Field(default_factory=list)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class IdentityValidation(BaseModel):
    same_article: bool
    method: Literal[
        "same_doi",
        "metadata_match",
        "repository_doi_alias_match",
        "exact_title_repository_match",
        "doi_mismatch",
        "doi_title_conflict",
        "title_mismatch",
        "insufficient_metadata",
    ]
    score: float = 0.0
    title_score: float = 0.0
    author_score: float = 0.0
    year_score: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class ProviderAttempt(BaseModel):
    provider: str
    enabled: bool = True
    ok: bool = False
    status: str
    candidates_count: int = 0
    elapsed_seconds: float = 0.0
    error: str | None = None
    http_status: int | None = None
    transient: bool = False
    identity_rejected_count: int = 0
    access_blocked_count: int = 0
    landing_only_count: int = 0
    verified_count: int = 0


class FulltextProvenance(BaseModel):
    resolver_version: str
    provider: str
    original_url: str | None = None
    final_url: str
    discovered_via: str | None = None
    identity_method: str
    identity_score: float
    verified_pdf: bool
    access_type: str | None = None
    rights_status: str | None = None
    source_domain: str | None = None
    license: str | None = None
    version: str | None = None
    content_type: str | None = None
    content_head_sha256: str | None = None
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LegalFulltextResult(BaseModel):
    resolver_version: str | None = None
    ok: bool = True
    found: bool = False
    legal_access: bool = False
    same_article: bool = False
    status: str = "not_found"
    article: ArticleIdentity
    best_candidate: FulltextCandidate | None = None
    locations: list[FulltextCandidate] = Field(default_factory=list)
    attempts: list[ProviderAttempt] = Field(default_factory=list)
    needs_consultant_upload: bool = True
    retry_recommended: bool = False
    cache_hit: bool = False
    cache_write: bool = False
    cache_policy: str = "positive_only"
    failure_code: str | None = None
    reason: str | None = None
    provenance: FulltextProvenance | None = None
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ProviderHealth(BaseModel):
    provider: str
    configured: bool = True
    enabled: bool = False
    status: str = "not_tested"
    last_http_status: int | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    cooldown_until: str | None = None


class HealthResult(BaseModel):
    ok: bool
    server: str
    version: str
    enabled_providers: list[str]
    disabled_providers: list[str]
    configured_providers: list[str] = Field(default_factory=list)
    excluded_providers: list[str] = Field(default_factory=list)
    provider_statuses: list[ProviderHealth] = Field(default_factory=list)
