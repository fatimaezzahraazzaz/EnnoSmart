from dataclasses import dataclass, field
import os


def _global_setting(name: str, default: str) -> str:
    # Lecture de la configuration centrale uniquement lors de la construction
    # du reviewer ; importer le pipeline de base ne dépend pas du client réseau.
    try:
        from modules.LLM.llm_client import _env
        return _env(name, default)
    except ImportError:
        return str(os.getenv(name, default)).strip()

@dataclass(frozen=True)
class ChunkConfig:
    max_chars: int = 3200
    overlap_chars: int = 450
    context_chars: int = 700
    min_chunk_chars: int = 80

@dataclass(frozen=True)
class FusionConfig:
    default_token_jaccard: float = 0.62
    lock_token_jaccard: float = 0.50
    sequence_ratio: float = 0.72

@dataclass(frozen=True)
class PipelineConfig:
    chunk: ChunkConfig = field(default_factory=ChunkConfig)
    fusion: FusionConfig = field(default_factory=FusionConfig)
    run_frascati: bool = True
    require_human_review_for_locks: bool = True


@dataclass(frozen=True)
class GlobalReviewerConfig:
    enabled: bool = field(default_factory=lambda: _global_setting("ENNOSMART_DIAGNOSTIC_GLOBAL_REVIEW_ENABLED", "1").lower() in {"1", "true", "yes", "on"})
    provider: str = field(default_factory=lambda: _global_setting("ENNOSMART_DIAGNOSTIC_GLOBAL_REASONER_PROVIDER", "openai"))
    global_reasoner_model: str = field(default_factory=lambda: _global_setting("ENNOSMART_DIAGNOSTIC_GLOBAL_REASONER_MODEL", "gpt-5"))
    reasoning_effort: str = field(default_factory=lambda: _global_setting("ENNOSMART_DIAGNOSTIC_GLOBAL_REASONER_EFFORT", "high"))
    max_prompt_chars: int = 110000
    max_output_tokens: int = 12000
    batch_size: int = 28


@dataclass(frozen=True)
class GlobalFastPathConfig:
    enabled: bool = field(default_factory=lambda: _global_setting("ENNOSMART_DIAGNOSTIC_GLOBAL_FAST_PATH", "1").lower() in {"1", "true", "yes", "on"})
    provider: str = field(default_factory=lambda: _global_setting("ENNOSMART_DIAGNOSTIC_GLOBAL_REASONER_PROVIDER", "openai"))
    model: str = field(default_factory=lambda: _global_setting("ENNOSMART_DIAGNOSTIC_GLOBAL_REASONER_MODEL", "gpt-5"))
    reasoning_effort: str = field(default_factory=lambda: _global_setting("ENNOSMART_DIAGNOSTIC_GLOBAL_FAST_EFFORT", "medium"))
    max_prompt_chars: int = 150000
    max_output_tokens: int = 64000
    context_window_tokens: int = 400000
    passage_max_chars: int = 650
    max_workers: int = 3
