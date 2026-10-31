from __future__ import annotations

import os
from dataclasses import dataclass
from .model_policy import MODEL


class ConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScholarV4Config:
    enabled: bool = True
    model: str = MODEL
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
    max_pdf_disk_bytes: int = 134217728
    max_pdf_storage_bytes: int = 2147483648
    min_pdf_free_bytes: int = 268435456
    pdf_extraction_batch_pages: int = 20
    max_extracted_chars: int = 1000000
    max_pdf_pages: int = 500
    max_download_seconds: float = 45
    max_pdf_download_seconds: float = 90
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
    # USD per million tokens. 0 = the versioned catalogue (pricing_catalog.json, or
    # ENNOSCHOLAR_V4_PRICING_FILE) for this model and tier; see pricing.resolve.
    input_price: float = 0.0
    output_price: float = 0.0
    cache_read_price: float = 0.0
    cache_write_price: float = 0.0
    service_tier: str = "default"
    pricing_file: str = ""
    web_call_price: float = 0.01
    # Reserve the documented hosted search context ceiling before each request.
    web_token_reserve: int = 128000
    request_timeout_seconds: float = 90
    # Persistent spending controller (finance.py). Off until an administrator has
    # applied the migration and validated a budget policy; when on, every provider
    # call is reserved in the ledger first and refused if a budget is exhausted.
    finance_enabled: bool = False
    organization_id: str = "default"
    budget_policy_file: str = ""
    # Legacy compatibility field, neutralized by the exclusive model policy.
    task_models: str = ""
    # Provider prompt cache layout (prompt_cache.py): "explicit" caches only the stable
    # prefix (instructions, tools) of models that support it; "implicit" leaves the
    # provider's default (on GPT-5.6, a cache write of the whole prefix at 1.25x the input
    # price on every request); "off" disables writes. Deployments read from_env, where
    # explicit is the default; a direct construction keeps the request layout unchanged.
    prompt_cache_mode: str = "implicit"
    # Automatic full-manuscript drafting (writing_job.py): one budget for the whole
    # manuscript, never renewed automatically, and bounded segments and repairs.
    writing_job_enabled: bool = True
    writing_job_budget_usd: float = 10.0
    writing_job_max_segments: int = 12
    writing_job_max_section_attempts: int = 6
    writing_job_stalled_segments: int = 2
    # thread (default): this process. celery: the existing worker, verified before use and
    # never replaced silently. auto: celery when verified, else thread (recorded in the job).
    writing_job_executor: str = "thread"
    # Same durable driver and ledger, a research-specific completion policy.
    # Direct constructions are opt-in (offline clients); the application enables
    # it from_env. No running process or configuration file is changed here.
    research_job_enabled: bool = False
    research_job_budget_usd: float = 4.0
    research_job_max_segments: int = 6
    research_discovery_rounds: int = 3
    research_finalization_seconds: float = 75.0
    fulltext_prefetch_enabled: bool = False
    # Engine for searches, selections, analyses and questions. "original" (default, restored 2026-10-23): the V4 of
    # 2026-10-13 (backup/ennosmart-v4-before-corrections-20261013, commit 8acbad7, module original.py) — one adaptive
    # conversation where the model plans its searches (providers and native web), judges, records its finalists,
    # reads and publishes. Plans and drafting keep the current pipeline. "controlled": the research controller added
    # later (research.transition, reading gate, selection batches, discovery reviews, research jobs), kept for rollback.
    discovery_mode: str = "original"

    def __post_init__(self):
        # Legacy env, callers and dataclasses.replace cannot reopen stage routing.
        object.__setattr__(self, "model", MODEL)
        object.__setattr__(self, "task_models", "")

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
        values.setdefault("prompt_cache_mode", "explicit")
        values.setdefault("research_job_enabled", True)
        values.setdefault("fulltext_prefetch_enabled", True)
        return cls(**values)

    def validate(self):
        if not (0 < self.max_pdf_bytes <= self.max_pdf_disk_bytes <= 536870912):
            raise ConfigurationError("Limites PDF : mémoire <= disque <= 512 Mio, valeurs positives requises.")
        if not (self.max_pdf_disk_bytes <= self.max_pdf_storage_bytes <= 21474836480) or self.min_pdf_free_bytes < 0:
            raise ConfigurationError("Quota PDF requis entre la limite par fichier et 20 Gio ; réserve disque positive ou nulle.")
        if not 1 <= self.pdf_extraction_batch_pages <= 500:
            raise ConfigurationError("Lot d'extraction PDF incompatible avec la limite de pages.")
        if not self.enabled:
            raise ConfigurationError("EnnoScholar V4 est désactivé (ENNOSCHOLAR_V4_ENABLED).")
        if not self.api_key:
            raise ConfigurationError("OPENAI_API_KEY est nécessaire pour EnnoScholar V4.")
        if not self.model.strip():
            raise ConfigurationError("Configurez ENNOSCHOLAR_V4_MODEL avec un modèle Responses compatible Web Search.")
        if self.reasoning_effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise ConfigurationError("ENNOSCHOLAR_V4_REASONING_EFFORT doit être low, medium, high ou xhigh.")
        for name in ("max_tool_calls", "max_research_rounds", "max_tool_rounds", "max_finalization_attempts", "finalization_reserved_seconds", "finalization_reserved_tokens", "finalization_reserved_cost_usd", "finalization_context_chars", "max_search_results", "max_deep_analysis", "max_web_calls", "max_tokens", "max_pdf_bytes", "max_extracted_chars", "max_pdf_pages", "max_download_seconds", "max_pdf_download_seconds", "max_download_attempts", "max_retry_attempts", "workers", "timeout_seconds", "max_cost_usd",
                     "max_output_tokens", "max_context_chars", "recent_messages", "max_message_context_chars", "max_articles",
                     "web_call_price", "web_token_reserve", "request_timeout_seconds"):
            if getattr(self, name) <= 0:
                raise ConfigurationError(f"ENNOSCHOLAR_V4_{name.upper()} doit être positif.")
        if self.prompt_cache_mode not in {"explicit", "implicit", "off"}:
            raise ConfigurationError("ENNOSCHOLAR_V4_PROMPT_CACHE_MODE doit valoir explicit, implicit ou off.")
        if self.discovery_mode not in {"original", "controlled"}:
            raise ConfigurationError("ENNOSCHOLAR_V4_DISCOVERY_MODE doit valoir original ou controlled.")
        if self.writing_job_executor not in {"auto", "celery", "thread"}:
            raise ConfigurationError("ENNOSCHOLAR_V4_WRITING_JOB_EXECUTOR doit valoir auto, celery ou thread.")
        for name in ("writing_job_budget_usd", "writing_job_max_segments", "writing_job_max_section_attempts",
                "writing_job_stalled_segments", "research_job_budget_usd", "research_job_max_segments",
                "research_discovery_rounds", "research_finalization_seconds"):
            if getattr(self, name) <= 0:
                raise ConfigurationError(f"ENNOSCHOLAR_V4_{name.upper()} doit être positif.")
        for name in ("input_price", "output_price", "cache_read_price", "cache_write_price"):
            if getattr(self, name) < 0:
                raise ConfigurationError(f"ENNOSCHOLAR_V4_{name.upper()} ne peut pas être négatif.")
        # Every model used (main and per task) needs an attested price before any call.
        from .pricing import resolve, PriceUnknown
        from .routing import task_models
        if self.service_tier != "default":
            raise ConfigurationError("EnnoScholar V4 exige Standard (service_tier default).")
        if self.max_output_tokens > 128000:
            raise ConfigurationError(f"{MODEL} accepte au maximum 128000 tokens de sortie.")
        try:
            for model in {self.model, *task_models(self).values()}:
                resolve(self, model)
        except (PriceUnknown, ValueError) as exc:
            raise ConfigurationError(str(exc)) from None
