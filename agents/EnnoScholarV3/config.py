from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScholarV3Config:
    enabled: bool = True
    model: str = "gpt-5.5"
    api_key: str = ""
    reasoning_effort: str = "medium"
    max_tool_calls: int = 48
    max_research_rounds: int = 14
    max_response_rounds: int = 18
    timeout_seconds: float = 600
    max_cost_usd: float = 4.0
    max_output_tokens: int = 5000
    max_context_chars: int = 65000
    max_articles: int = 300
    max_stagnant_searches: int = 3
    input_price: float = 5.0
    output_price: float = 30.0
    web_call_price: float = 0.01
    # Reserve the documented hosted search context ceiling before each request.
    web_token_reserve: int = 128000

    @classmethod
    def from_env(cls):
        prefix = "ENNOSCHOLAR_V3_"
        defaults = cls()
        values = {}
        for name in cls.__dataclass_fields__:
            default = getattr(defaults, name)
            raw = os.getenv(prefix + name.upper())
            if raw is None:
                continue
            if isinstance(default, bool):
                values[name] = raw.lower() in {"true", "1", "yes"}
            else:
                values[name] = type(default)(raw)
        values["api_key"] = os.getenv("OPENAI_API_KEY", "").strip()
        return cls(**values)

    def validate(self):
        if not self.enabled:
            raise ConfigurationError("EnnoScholar V3 est désactivé (ENNOSCHOLAR_V3_ENABLED).")
        if not self.api_key:
            raise ConfigurationError("OPENAI_API_KEY est nécessaire pour EnnoScholar V3.")
        if not self.model.strip():
            raise ConfigurationError("Configurez ENNOSCHOLAR_V3_MODEL avec un modèle Responses compatible Web Search.")
        if self.reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            raise ConfigurationError("ENNOSCHOLAR_V3_REASONING_EFFORT doit être low, medium, high ou xhigh.")
        for name in ("max_tool_calls", "max_research_rounds", "max_response_rounds", "timeout_seconds", "max_cost_usd",
                     "max_output_tokens", "max_context_chars", "max_articles", "max_stagnant_searches",
                     "input_price", "output_price", "web_call_price", "web_token_reserve"):
            if getattr(self, name) <= 0:
                raise ConfigurationError(f"ENNOSCHOLAR_V3_{name.upper()} doit être positif.")
        if self.model != "gpt-5.5" and not (os.getenv("ENNOSCHOLAR_V3_INPUT_PRICE") and os.getenv("ENNOSCHOLAR_V3_OUTPUT_PRICE")):
            raise ConfigurationError("Un autre modèle nécessite ses tarifs ENNOSCHOLAR_V3_INPUT_PRICE et OUTPUT_PRICE (USD / million de tokens).")
