from __future__ import annotations

from modules.LLM.usage_budget import normalize_usage


def test_top_level_cached_tokens_are_counted() -> None:
    usage = normalize_usage(
        {
            "prompt_tokens": 20_000,
            "completion_tokens": 1_000,
            "total_tokens": 21_000,
            "cached_tokens": 18_000,
        }
    )

    assert usage["cached_input_tokens"] == 18_000
    assert usage["uncached_input_tokens"] == 2_000
