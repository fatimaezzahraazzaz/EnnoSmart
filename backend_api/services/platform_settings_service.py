from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from modules.common.runtime_paths import data_root


RUNTIME_SETTINGS_PATH = data_root() / "config" / "runtime_ai_settings.json"


DEFAULT_AI_SETTINGS: dict[str, Any] = {
    "provider": "ollama",
    "primary_model": "qwen2.5:7b-instruct",
    "writer_model": None,
    "fallback_models": [],
    "allow_cross_provider_fallback": False,
    "default_temperature": 0.1,
    "max_output_tokens_cap": 16000,
    "max_prompt_chars": 30000,
    "writer_max_prompt_chars": 180000,
    "monthly_budget_eur": 500,
    "enabled_agents": {
        "diagnostic": True,
        "scholar": True,
        "improvement": True,
        "cir_memory": True,
    },
}


def merge_ai_settings(value: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_AI_SETTINGS)
    if value:
        merged.update(value)
    merged["enabled_agents"] = {
        **DEFAULT_AI_SETTINGS["enabled_agents"],
        **dict((value or {}).get("enabled_agents") or {}),
    }
    return merged


def _runtime_env(settings: dict[str, Any]) -> dict[str, str]:
    provider = str(settings["provider"])
    primary = str(settings["primary_model"])
    writer = str(settings.get("writer_model") or primary)
    fallbacks = ",".join(str(item).strip() for item in settings.get("fallback_models", []) if str(item).strip())
    runtime = {
        "ENNOSMART_LLM_PROVIDER": provider,
        "ENNOSMART_LLM_ALLOW_CROSS_PROVIDER_FALLBACK": "1" if settings.get("allow_cross_provider_fallback") else "0",
        "ENNOSMART_LLM_DEFAULT_TEMPERATURE": str(settings["default_temperature"]),
        "ENNOSMART_LLM_MAX_OUTPUT_TOKENS_CAP": str(settings["max_output_tokens_cap"]),
        "ENNOSMART_LLM_MAX_PROMPT_CHARS": str(settings["max_prompt_chars"]),
        "ENNOSMART_LLM_WRITER_MAX_PROMPT_CHARS": str(settings["writer_max_prompt_chars"]),
    }
    if provider == "openai":
        runtime.update({
            "ENNOSMART_OPENAI_MODEL": primary,
            "ENNOSMART_LLM_WRITER_MODEL": writer,
            "OPENAI_FALLBACK_MODELS": fallbacks,
        })
    elif provider == "ollama":
        runtime.update({
            "ENNOSMART_OLLAMA_MODEL": primary,
            "ENNOSMART_OLLAMA_FALLBACK_MODELS": fallbacks,
        })
    elif provider == "openrouter":
        runtime.update({
            "OPENROUTER_MODEL": primary,
            "ENNOSMART_LLM_WRITER_OPENROUTER_MODEL": writer,
            "OPENROUTER_FALLBACK_MODELS": fallbacks,
        })
    else:
        runtime.update({
            "ENNOSMART_GEMINI_MODEL": primary,
            "ENNOSMART_LLM_WRITER_GEMINI_MODEL": writer,
        })
    return runtime


def write_runtime_ai_settings(settings: dict[str, Any]) -> Path:
    """Publie uniquement des réglages non secrets consommés par le client LLM."""
    RUNTIME_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    target = RUNTIME_SETTINGS_PATH
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(_runtime_env(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)
    try:
        from modules.LLM.llm_client import reload_config

        reload_config()
    except Exception:
        pass
    return target
