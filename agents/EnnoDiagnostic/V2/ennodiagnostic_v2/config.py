from dataclasses import dataclass, field

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
    use_fastjudge: bool = True
    run_frascati: bool = True
    require_human_review_for_locks: bool = True
