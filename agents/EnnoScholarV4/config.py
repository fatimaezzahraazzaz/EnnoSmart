from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScholarV4Config:
    enabled: bool = True
    model: str = "gpt-6.1-sol"
    api_key: str = ""
    reasoning_effort: str = "medium"
    max_tool_calls: int = 48
    max_research_rounds: int = 12
    max_tool_rounds: int = 6
    max_finalization_attempts: int = 3
    finalization_reserved_seconds: float = 150
    finalization_reserved_tokens: int = 60000
    finalization_reserved_cost_usd: float = 0.25
    finalization_context_chars: int = 24000
    web_search_enabled: bool = True
    max_search_results: int = 80
    max_deep_analysis: int = 10
    max_web_calls: int = 4
    max_tokens: int = 400000
    max_pdf_bytes: int = 15728640
    max_extracted_chars: int = 1000000
    max_pdf_pages: int = 500
    max_download_seconds: float = 45
    max_download_attempts: int = 8
    max_retry_attempts: int = 2
    workers: int = 4
    timeout_seconds: float = 600
    max_cost_usd: float = 4.0
    max_output_tokens: int = 7000
    max_context_chars: int = 85000
    recent_messages: int = 8
    max_message_context_chars: int = 6000
    max_articles: int = 2000
    input_price: float = 2.0
    output_price: float = 10.0
    web_call_price: float = 0.01
    # Reserve the documented hosted search context ceiling before each request.
    web_token_reserve: int = 128000
    request_timeout_seconds: float = 90

    @property
    def max_response_rounds(self):
        return self.max_tool_rounds + self.max_finalization_attempts

    @classmethod
    def from_env(cls):
        prefix = "ENNOSCHOLAR_V4_"
        defaults = cls()
        values = {}
        for name in cls.__dataclass_fields__:
            default = getattr(defaults, name)
            raw = os.getenv(prefix + name.upper())
            if raw is None:
                continue
            try:
                if isinstance(default, bool):
                    if raw.lower() not in {"true", "false", "1", "0", "yes", "no"}:
                        raise ValueError()
                    values[name] = raw.lower() in {"true", "1", "yes"}
                else:
                    values[name] = type(default)(raw)
            except ValueError:
                raise ConfigurationError(f"ENNOSCHOLAR_V4_{name.upper()} est invalide.") from None
        values["api_key"] = os.getenv("OPENAI_API_KEY", "").strip()
        return cls(**values)

    def validate(self):
        if not self.enabled:
            raise ConfigurationError("EnnoScholar V4 est désactivé (ENNOSCHOLAR_V4_ENABLED).")
        if not self.api_key:
            raise ConfigurationError("OPENAI_API_KEY est nécessaire pour EnnoScholar V4.")
        if not self.model.strip():
            raise ConfigurationError("Configurez ENNOSCHOLAR_V4_MODEL avec un modèle Responses compatible Web Search.")
        if self.reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ConfigurationError("ENNOSCHOLAR_V4_REASONING_EFFORT doit être low, medium, high ou xhigh.")
        for name in ("max_tool_calls", "max_research_rounds", "max_tool_rounds", "max_finalization_attempts", "finalization_reserved_seconds", "finalization_reserved_tokens", "finalization_reserved_cost_usd", "finalization_context_chars", "max_search_results", "max_deep_analysis", "max_web_calls", "max_tokens", "max_pdf_bytes", "max_extracted_chars", "max_pdf_pages", "max_download_seconds", "max_download_attempts", "max_retry_attempts", "workers", "timeout_seconds", "max_cost_usd",
                     "max_output_tokens", "max_context_chars", "recent_messages", "max_message_context_chars", "max_articles",
                     "input_price", "output_price", "web_call_price", "web_token_reserve", "request_timeout_seconds"):
            if getattr(self, name) <= 0:
                raise ConfigurationError(f"ENNOSCHOLAR_V4_{name.upper()} doit être positif.")
        if self.model != "gpt-6.1-sol" and not (os.getenv("ENNOSCHOLAR_V4_INPUT_PRICE") and os.getenv("ENNOSCHOLAR_V4_OUTPUT_PRICE")):
            raise ConfigurationError("Un autre modèle nécessite ses tarifs ENNOSCHOLAR_V4_INPUT_PRICE et OUTPUT_PRICE (USD / million de tokens).")
