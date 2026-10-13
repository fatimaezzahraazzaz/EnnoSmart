from __future__ import annotations

import os
from dataclasses import dataclass


class ScholarConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScholarConfig:
    model: str = ""
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    timeout: float = 45
    turn_timeout: float = 600
    soft_time_limit: float = 180
    analysis_reserve: float = 90
    synthesis_reserve: float = 90
    fulltext_article_timeout: float = 35
    fulltext_batch_timeout: float = 60
    fulltext_attempt_timeout: float = 12
    fulltext_connect_timeout: float = 5
    fulltext_headers_timeout: float = 10
    fulltext_read_timeout: float = 10
    fulltext_concurrency: int = 3
    analysis_concurrency: int = 3
    semantic_concurrency: int = 2
    openalex_concurrency: int = 3
    semantic_request_interval: float = 1
    openalex_request_interval: float = 0.1
    search_timeout: float = 120
    search_candidate_limit: int = 50
    max_rounds: int = 10
    soft_tool_budget: int = 16
    hard_tool_budget: int = 32
    max_input_tokens: int = 48000
    max_context_bytes: int = 4000000
    active_article_limit: int = 12
    compact_threshold: int = 12000
    max_output_tokens: int = 5000
    analysis_max_output_tokens: int = 3500
    analysis_retry_max_output_tokens: int = 6000
    synthesis_timeout: float = 90
    synthesis_retry_timeout: float = 60
    max_sessions: int = 64
    session_ttl: float = 3600
    max_articles: int = 10000
    semantic_key: str = ""
    openalex_key: str = ""
    contact_email: str = ""

    @classmethod
    def from_env(cls) -> "ScholarConfig":
        analysis_tokens = max(128, int(os.getenv("SCHOLAR_ANALYSIS_MAX_OUTPUT_TOKENS", "3500")))
        return cls(
            model=os.getenv("ENNOSCHOLAR_V2_MODEL", "").strip(),
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            base_url=os.getenv("ENNOSCHOLAR_V2_OPENAI_BASE_URL", "https://api.openai.com/v1").strip(),
            timeout=max(1, float(os.getenv("ENNOSCHOLAR_V2_HTTP_TIMEOUT", "45"))),
            analysis_max_output_tokens=analysis_tokens,
            analysis_retry_max_output_tokens=max(analysis_tokens + 1, int(os.getenv("SCHOLAR_ANALYSIS_RETRY_MAX_OUTPUT_TOKENS", "6000"))),
            synthesis_timeout=max(1, float(os.getenv("SCHOLAR_SYNTHESIS_TIMEOUT_SECONDS", "90"))),
            synthesis_retry_timeout=max(1, float(os.getenv("SCHOLAR_SYNTHESIS_RETRY_TIMEOUT_SECONDS", "60"))),
            turn_timeout=max(10, float(os.getenv("ENNOSCHOLAR_V2_HARD_TIME_LIMIT", os.getenv("ENNOSCHOLAR_V2_TURN_TIMEOUT", "600")))),
            soft_time_limit=max(1, float(os.getenv("ENNOSCHOLAR_V2_SOFT_TIME_LIMIT", "180"))),
            analysis_reserve=max(1, float(os.getenv("ENNOSCHOLAR_V2_ANALYSIS_RESERVE", "90"))),
            synthesis_reserve=max(1, float(os.getenv("ENNOSCHOLAR_V2_SYNTHESIS_RESERVE", "90"))),
            fulltext_article_timeout=max(1, float(os.getenv("ENNOSCHOLAR_V2_FULLTEXT_ARTICLE_TIMEOUT", "35"))),
            fulltext_batch_timeout=max(1, float(os.getenv("ENNOSCHOLAR_V2_FULLTEXT_BATCH_TIMEOUT", "60"))),
            fulltext_attempt_timeout=max(1, float(os.getenv("ENNOSCHOLAR_V2_FULLTEXT_ATTEMPT_TIMEOUT", "12"))),
            fulltext_connect_timeout=max(0.5, float(os.getenv("ENNOSCHOLAR_V2_FULLTEXT_CONNECT_TIMEOUT", "5"))),
            fulltext_headers_timeout=max(0.5, float(os.getenv("ENNOSCHOLAR_V2_FULLTEXT_HEADERS_TIMEOUT", "10"))),
            fulltext_read_timeout=max(0.5, float(os.getenv("ENNOSCHOLAR_V2_FULLTEXT_READ_TIMEOUT", "10"))),
            fulltext_concurrency=max(1, min(3, int(os.getenv("ENNOSCHOLAR_V2_FULLTEXT_CONCURRENCY", "3")))),
            analysis_concurrency=max(1, min(3, int(os.getenv("ENNOSCHOLAR_V2_ANALYSIS_CONCURRENCY", "3")))),
            semantic_concurrency=max(1, min(4, int(os.getenv("ENNOSCHOLAR_V2_SEMANTIC_CONCURRENCY", "2")))),
            openalex_concurrency=max(1, min(4, int(os.getenv("ENNOSCHOLAR_V2_OPENALEX_CONCURRENCY", "3")))),
            semantic_request_interval=max(1, float(os.getenv("ENNOSCHOLAR_V2_SEMANTIC_REQUEST_INTERVAL", "1"))),
            openalex_request_interval=max(0.1, float(os.getenv("ENNOSCHOLAR_V2_OPENALEX_REQUEST_INTERVAL", "0.1"))),
            search_timeout=max(1, float(os.getenv("ENNOSCHOLAR_V2_SEARCH_TIMEOUT", "120"))),
            search_candidate_limit=max(1, min(100, int(os.getenv("ENNOSCHOLAR_V2_SEARCH_CANDIDATES", "50")))),
            max_articles=max(600, int(os.getenv("ENNOSCHOLAR_V2_MAX_ARTICLES", "10000"))),
            max_rounds=max(1, int(os.getenv("ENNOSCHOLAR_V2_MAX_ROUNDS", "10"))),
            soft_tool_budget=max(1, int(os.getenv("ENNOSCHOLAR_V2_SOFT_TOOL_BUDGET", "16"))),
            hard_tool_budget=max(1, int(os.getenv("ENNOSCHOLAR_V2_HARD_TOOL_BUDGET", "32"))),
            max_input_tokens=max(8000, int(os.getenv("ENNOSCHOLAR_V2_MAX_INPUT_TOKENS", "48000"))),
            max_context_bytes=max(1000000, int(os.getenv("ENNOSCHOLAR_V2_MAX_CONTEXT_BYTES", "4000000"))),
            compact_threshold=max(1024, int(os.getenv("ENNOSCHOLAR_V2_COMPACT_THRESHOLD", "12000"))),
            semantic_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip(),
            openalex_key=os.getenv("OPENALEX_API_KEY", "").strip(),
            contact_email=os.getenv("ENNOSCHOLAR_V2_CONTACT_EMAIL", "").strip(),
        )

    def require_openai(self) -> None:
        if not self.model:
            raise ScholarConfigurationError("Configurez ENNOSCHOLAR_V2_MODEL avec un identifiant API accessible à votre compte.")
        if not self.api_key:
            raise ScholarConfigurationError("OPENAI_API_KEY est nécessaire pour EnnoScholar V2.")
