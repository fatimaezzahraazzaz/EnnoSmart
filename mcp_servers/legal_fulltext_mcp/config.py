from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_files() -> tuple[str, ...]:
    root = Path(os.getenv("ENNOSMART_ROOT") or os.getenv("ENNOSMART_PROJECT_ROOT") or "C:/EnnoSmart")
    return (str(root / ".env"), ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    enabled: bool = Field(True, alias="ENNOSCHOLAR_LEGAL_MCP_ENABLED")
    host: str = Field("127.0.0.1", alias="ENNOSCHOLAR_LEGAL_MCP_HOST")
    port: int = Field(8010, alias="ENNOSCHOLAR_LEGAL_MCP_PORT")
    mcp_url: str = Field("http://127.0.0.1:8010/mcp", alias="ENNOSCHOLAR_LEGAL_MCP_URL")

    timeout_seconds: float = Field(25.0, alias="ENNOSCHOLAR_LEGAL_MCP_TIMEOUT_SECONDS")
    max_retries: int = Field(3, alias="ENNOSCHOLAR_LEGAL_MCP_MAX_RETRIES")
    verify_pdf: bool = Field(True, alias="ENNOSCHOLAR_LEGAL_MCP_VERIFY_PDF")
    stop_on_first_verified: bool = Field(True, alias="ENNOSCHOLAR_LEGAL_MCP_STOP_ON_FIRST_VERIFIED")
    max_candidates_per_provider: int = Field(12, alias="ENNOSCHOLAR_LEGAL_MCP_MAX_CANDIDATES_PER_PROVIDER")
    max_landing_pdf_links: int = Field(6, alias="ENNOSCHOLAR_LEGAL_MCP_MAX_LANDING_PDF_LINKS")
    validate_public_network_urls: bool = Field(
        True, alias="ENNOSCHOLAR_LEGAL_MCP_VALIDATE_PUBLIC_NETWORK_URLS"
    )
    provider_min_interval_seconds: float = Field(
        0.15, alias="ENNOSCHOLAR_LEGAL_MCP_PROVIDER_MIN_INTERVAL_SECONDS"
    )
    semantic_scholar_enabled: bool = Field(
        False, alias="ENNOSCHOLAR_SEMANTIC_SCHOLAR_ENABLED"
    )
    semantic_scholar_min_interval_seconds: float = Field(
        1.10, alias="ENNOSCHOLAR_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS"
    )
    provider_cooldown_seconds: float = Field(
        30.0, alias="ENNOSCHOLAR_LEGAL_MCP_PROVIDER_COOLDOWN_SECONDS"
    )

    min_identity_score: float = Field(0.90, alias="ENNOSCHOLAR_FULLTEXT_MIN_IDENTITY_SCORE")
    min_title_score: float = Field(0.82, alias="ENNOSCHOLAR_FULLTEXT_MIN_TITLE_SCORE")
    allow_title_match: bool = Field(True, alias="ENNOSCHOLAR_FULLTEXT_ALLOW_TITLE_MATCH")
    doi_title_conflict_score: float = Field(0.55, alias="ENNOSCHOLAR_FULLTEXT_DOI_TITLE_CONFLICT_SCORE")
    exact_title_repository_score: float = Field(0.985, alias="ENNOSCHOLAR_FULLTEXT_EXACT_TITLE_REPOSITORY_SCORE")
    allow_exact_title_repository_match: bool = Field(
        True, alias="ENNOSCHOLAR_FULLTEXT_ALLOW_EXACT_TITLE_REPOSITORY_MATCH"
    )

    metadata_enrichment_enabled: bool = Field(
        True, alias="ENNOSCHOLAR_METADATA_ENRICHMENT_ENABLED"
    )
    metadata_title_accept_score: float = Field(
        0.72, alias="ENNOSCHOLAR_METADATA_TITLE_ACCEPT_SCORE"
    )
    metadata_title_conflict_score: float = Field(
        0.55, alias="ENNOSCHOLAR_METADATA_TITLE_CONFLICT_SCORE"
    )
    metadata_title_reconcile_score: float = Field(
        0.94, alias="ENNOSCHOLAR_METADATA_TITLE_RECONCILE_SCORE"
    )
    metadata_exact_title_reconcile_score: float = Field(
        0.985, alias="ENNOSCHOLAR_METADATA_EXACT_TITLE_RECONCILE_SCORE"
    )

    cache_enabled: bool = Field(True, alias="ENNOSCHOLAR_LEGAL_MCP_CACHE_ENABLED")
    cache_ttl_seconds: int = Field(86400, alias="ENNOSCHOLAR_LEGAL_MCP_CACHE_TTL_SECONDS")
    cache_negative_results: bool = Field(False, alias="ENNOSCHOLAR_LEGAL_MCP_CACHE_NEGATIVE_RESULTS")
    cache_db: str = Field(
        "C:/EnnoSmart/storage/mcp/legal_fulltext_cache.sqlite3",
        alias="ENNOSCHOLAR_LEGAL_MCP_CACHE_DB",
    )
    audit_log: str = Field(
        "C:/EnnoSmart/logs/legal_fulltext_mcp_audit.jsonl",
        alias="ENNOSCHOLAR_LEGAL_MCP_AUDIT_LOG",
    )

    unpaywall_email: str = Field("", alias="UNPAYWALL_EMAIL")
    crossref_mailto: str = Field("", alias="CROSSREF_MAILTO")
    openalex_api_key: str = Field("", alias="OPENALEX_API_KEY")
    core_api_key: str = Field("", alias="CORE_API_KEY")
    semantic_scholar_api_key: str = Field("", alias="SEMANTIC_SCHOLAR_API_KEY")

    core_detail_limit: int = Field(3, alias="ENNOSCHOLAR_CORE_DETAIL_LIMIT")

    provider_order_raw: str = Field(
        "unpaywall,openalex,crossref,core,hal,arxiv,europe_pmc,zenodo",
        alias="ENNOSCHOLAR_LEGAL_MCP_PROVIDER_ORDER",
    )

    user_agent: str = Field(
        "EnnoSmart-EnnoScholar-LegalFulltext/1.5 (+mailto:contact@example.invalid)",
        alias="ENNOSCHOLAR_FULLTEXT_USER_AGENT",
    )
    browser_user_agent: str = Field(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        alias="ENNOSCHOLAR_FULLTEXT_BROWSER_USER_AGENT",
    )

    @property
    def provider_order(self) -> List[str]:
        values = [x.strip().lower() for x in self.provider_order_raw.split(",") if x.strip()]
        return values or [
            "unpaywall",
            "openalex",
            "crossref",
            "core",
            "hal",
            "arxiv",
            "europe_pmc",
            "zenodo",
        ]

    @property
    def effective_crossref_mailto(self) -> str:
        return (self.crossref_mailto or self.unpaywall_email or "").strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
