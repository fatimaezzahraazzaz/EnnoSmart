# -*- coding: utf-8 -*-
from __future__ import annotations

"""Client LLM central et unique d'EnnoSmart.

Tous les agents utilisent la même API :

    client = LLMClient()
    text = client.generate(...)

Le fournisseur et les modèles sont lus dans le fichier ``.env`` du projet.
Les agents ne contiennent ni clé API, ni URL fournisseur, ni logique de
basculement. Le routage du modèle de rédaction est centralisé ici à partir du
``request_name`` : les appels ``ennoscholar:phase5:*`` utilisent le modèle
writer, tandis que les tâches courtes utilisent le modèle général.
"""

import json
import math
import os
import re
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import requests

from modules.LLM.llm_concurrency import llm_capacity_slot

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover
    dotenv_values = None


_CONFIG: Optional[Dict[str, str]] = None


def _project_root() -> Path:
    configured = os.getenv("ENNOSMART_ROOT") or os.getenv("ENNOSMART_BASE_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2]


def reload_config() -> Dict[str, str]:
    """Recharge le .env central sans écraser globalement ``os.environ``."""
    global _CONFIG
    _CONFIG = None
    return _load_config()


def _load_config() -> Dict[str, str]:
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG

    root = _project_root()
    explicit = str(os.getenv("ENNOSMART_ENV_FILE") or "").strip()
    primary = Path(explicit) if explicit else root / ".env"
    backend_fallback = root / "backend_api" / ".env"

    merged: Dict[str, str] = {}
    if dotenv_values is not None:
        # Le backend est seulement un fallback. Le .env racine porte la config IA.
        for path in (backend_fallback, primary):
            try:
                if path.exists():
                    for key, value in (dotenv_values(path) or {}).items():
                        if value is not None:
                            merged[str(key)] = str(value)
            except Exception:
                continue

    # Les réglages non secrets publiés depuis l'interface superadmin prennent
    # effet sans modifier le .env. Les variables du processus gardent la
    # priorité finale afin de respecter les contraintes de déploiement.
    runtime_settings = root / "config" / "runtime_ai_settings.json"
    try:
        if runtime_settings.exists():
            payload = json.loads(runtime_settings.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                for key, value in payload.items():
                    if str(key).startswith("ENNOSMART_") or str(key) in {
                        "OPENAI_FALLBACK_MODELS",
                        "OPENROUTER_MODEL",
                        "OPENROUTER_FALLBACK_MODELS",
                    }:
                        merged[str(key)] = str(value)
    except Exception:
        pass

    # Les variables du processus gardent la priorité pour les tests et déploiements.
    merged.update({str(k): str(v) for k, v in os.environ.items()})
    _CONFIG = merged
    return merged


def _env(name: str, default: str = "") -> str:
    return str(_load_config().get(name, default) or "").strip()


def _env_first(names: Sequence[str], default: str = "") -> str:
    for name in names:
        value = _env(name)
        if value:
            return value
    return default


def _env_int(name: str, default: int, minimum: Optional[int] = None) -> int:
    try:
        value = int(str(_env(name, str(default))).strip())
    except Exception:
        value = default
    return max(minimum, value) if minimum is not None else value


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(_env(name, str(default))).strip())
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name, "1" if default else "0").lower()
    return value in {"1", "true", "yes", "oui", "on"}


def _split_models(value: str) -> List[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _dedupe(values: Iterable[str]) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


def estimate_tokens(value: Any) -> int:
    text = str(value or "")
    return 0 if not text else max(1, math.ceil(len(text) / 3.6))


def _verbose() -> bool:
    return _env_bool("ENNOSMART_LLM_VERBOSE", False)


def _log(message: str) -> None:
    if _verbose():
        print(f"[LLMClient] {message}", flush=True)


def _clean_api_key(value: Any) -> str:
    key = str(value or "").strip().strip('"').strip("'")
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    return key


def _retry_after_seconds(response: Any) -> Optional[float]:
    """Lit Retry-After ou le délai annoncé dans le corps OpenAI."""
    header = str(getattr(response, "headers", {}).get("Retry-After") or "").strip()
    try:
        if header:
            return max(0.0, float(header))
    except (TypeError, ValueError):
        pass
    text = str(getattr(response, "text", "") or "")
    for pattern in (
        r"please\s+try\s+again\s+in\s+([0-9]+(?:\.[0-9]+)?)\s*s",
        r"try\s+again\s+in\s+([0-9]+(?:\.[0-9]+)?)\s*seconds?",
        r"retry[-_\s]*after[^0-9]*([0-9]+(?:\.[0-9]+)?)",
    ):
        match = re.search(pattern, text, flags=re.I)
        if match:
            try:
                return max(0.0, float(match.group(1)))
            except (TypeError, ValueError):
                continue
    return None


class LLMClient:
    """Client commun à EnnoDiagnostic, EnnoScholar et aux futurs agents."""

    SUPPORTED_PROVIDERS = {"openai", "ollama", "openrouter", "gemini"}
    WRITER_REQUEST_MARKERS = (
        "ennoscholar:phase5",
        "phase5",
        "state_of_art",
        "state-of-art",
        "etat_de_l_art",
        "writer",
    )

    def __init__(
        self,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        **_: Any,
    ) -> None:
        raw_provider = (
            provider
            or _env("ENNOSMART_LLM_PROVIDER")
            or _env("LLM_PROVIDER")
            or "ollama"
        ).strip().lower()

        aliases = {
            "local": "ollama",
            "ollama_local": "ollama",
            "open_router": "openrouter",
            "google": "gemini",
            "gpt": "openai",
        }
        normalized = aliases.get(raw_provider, raw_provider)
        if normalized not in self.SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Fournisseur LLM inconnu : {raw_provider!r}. "
                f"Valeurs autorisées : {sorted(self.SUPPORTED_PROVIDERS)}"
            )

        self.provider = normalized
        self.explicit_model = str(model or "").strip()
        self.allow_cross_provider_fallback = _env_bool(
            "ENNOSMART_LLM_ALLOW_CROSS_PROVIDER_FALLBACK",
            False,
        )

        # OpenAI
        self.openai_api_key = _clean_api_key(_env("OPENAI_API_KEY"))
        self.openai_base_url = _env_first(
            ["ENNOSMART_OPENAI_BASE_URL", "OPENAI_BASE_URL"],
            "https://api.openai.com/v1",
        ).rstrip("/")
        self.openai_model = _env_first(
            ["ENNOSMART_OPENAI_MODEL", "OPENAI_MODEL"],
            "gpt-4.1-mini",
        )
        self.openai_writer_model = _env_first(
            ["ENNOSMART_LLM_WRITER_MODEL", "OPENAI_WRITER_MODEL"],
            self.openai_model,
        )
        self.openai_fallback_models = _split_models(_env("OPENAI_FALLBACK_MODELS"))
        self.openai_web_search_model = _env_first(
            ["ENNOSMART_WEB_SEARCH_MODEL", "OPENAI_WEB_SEARCH_MODEL"],
            "gpt-5.6-luna",
        )
        self.openai_web_search_fallback_models = _split_models(
            _env_first(
                [
                    "ENNOSMART_WEB_SEARCH_FALLBACK_MODELS",
                    "OPENAI_WEB_SEARCH_FALLBACK_MODELS",
                ],
                "gpt-5",
            )
        )
        self.openai_web_search_reasoning_effort = _env_first(
            [
                "ENNOSMART_WEB_SEARCH_REASONING_EFFORT",
                "OPENAI_WEB_SEARCH_REASONING_EFFORT",
            ],
            "low",
        )
        self.openai_web_search_enabled = _env_bool(
            "ENNOSMART_WEB_SEARCH_ENABLED",
            True,
        )
        self.openai_context_window = _env_int("OPENAI_CONTEXT_WINDOW", 400000, 4096)
        self.openai_store = _env_bool("OPENAI_STORE", False)
        self.openai_send_temperature = _env_bool("OPENAI_SEND_TEMPERATURE", True)

        # Ollama
        self.ollama_base_url = _env_first(
            ["ENNOSMART_OLLAMA_BASE_URL", "OLLAMA_BASE_URL", "LOCAL_LLM_BASE_URL"],
            "http://127.0.0.1:11434",
        ).rstrip("/")
        self.ollama_model = _env_first(
            ["ENNOSMART_OLLAMA_MODEL", "OLLAMA_MODEL", "LOCAL_LLM_MODEL"],
            "qwen2.5:7b-instruct",
        )
        self.ollama_fallback_models = _split_models(
            _env_first(
                [
                    "ENNOSMART_OLLAMA_FALLBACK_MODELS",
                    "OLLAMA_FALLBACK_MODELS",
                    "LOCAL_LLM_FALLBACK_MODELS",
                ],
                "",
            )
        )
        self.ollama_num_ctx = _env_int(
            "ENNOSMART_OLLAMA_NUM_CTX",
            _env_int("OLLAMA_NUM_CTX", 8192, 2048),
            2048,
        )
        self.ollama_num_predict = _env_int("OLLAMA_NUM_PREDICT", 1600, 64)
        self.ollama_keep_alive = _env("OLLAMA_KEEP_ALIVE", "10m")

        # OpenRouter
        self.openrouter_api_key = _clean_api_key(_env("OPENROUTER_API_KEY"))
        self.openrouter_base_url = _env(
            "OPENROUTER_BASE_URL",
            "https://openrouter.ai/api/v1",
        ).rstrip("/")
        self.openrouter_model = _env("OPENROUTER_MODEL")
        self.openrouter_writer_model = _env_first(
            ["ENNOSMART_LLM_WRITER_OPENROUTER_MODEL", "OPENROUTER_WRITER_MODEL"],
            self.openrouter_model,
        )
        self.openrouter_fallback_models = _split_models(_env("OPENROUTER_FALLBACK_MODELS"))
        self.openrouter_context_window = _env_int("OPENROUTER_CONTEXT_WINDOW", 131072, 4096)

        # Gemini
        self.gemini_api_key = _clean_api_key(_env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"))
        self.gemini_model = _env("ENNOSMART_GEMINI_MODEL")
        self.gemini_writer_model = _env_first(
            ["ENNOSMART_LLM_WRITER_GEMINI_MODEL", "GEMINI_WRITER_MODEL"],
            self.gemini_model,
        )
        self.gemini_context_window = _env_int("GEMINI_CONTEXT_WINDOW", 1000000, 4096)

        # Réseau et budgets communs
        self.connect_timeout = _env_int("ENNOSMART_LLM_CONNECT_TIMEOUT", 20, 1)
        self.read_timeout = _env_int("ENNOSMART_LLM_READ_TIMEOUT", 360, 10)
        self.max_prompt_chars = _env_int("ENNOSMART_LLM_MAX_PROMPT_CHARS", 30000, 1000)
        self.writer_max_prompt_chars = _env_int(
            "ENNOSMART_LLM_WRITER_MAX_PROMPT_CHARS",
            180000,
            5000,
        )
        # Un même agent peut servir plusieurs requêtes simultanées. ContextVar
        # empêche les métadonnées d'un utilisateur d'écraser celles d'un autre.
        self._generation_meta_context: ContextVar[Dict[str, Any]] = ContextVar(
            f"ennosmart_llm_generation_meta_{id(self)}",
            default={},
        )
        self._last_generation_meta = {}

        _log(
            f"provider={self.provider} default_model={self.model_name} "
            f"writer_model={self._default_model_for_request('ennoscholar:phase5')} "
            f"cross_provider={self.allow_cross_provider_fallback}"
        )

    def _is_writer_request(self, request_name: Optional[str]) -> bool:
        value = str(request_name or "").strip().lower()
        return any(marker in value for marker in self.WRITER_REQUEST_MARKERS)

    def _default_model_for_request(
        self,
        request_name: Optional[str],
        provider: Optional[str] = None,
    ) -> str:
        current_provider = provider or self.provider
        if self.explicit_model and current_provider == self.provider:
            return self.explicit_model
        writer = self._is_writer_request(request_name)
        if current_provider == "openai":
            return self.openai_writer_model if writer else self.openai_model
        if current_provider == "ollama":
            return self.ollama_model
        if current_provider == "openrouter":
            return self.openrouter_writer_model if writer else self.openrouter_model
        return self.gemini_writer_model if writer else self.gemini_model

    @property
    def model_name(self) -> str:
        return self._default_model_for_request(None)

    def get_last_generation_meta(self) -> Dict[str, Any]:
        return dict(self._last_generation_meta)

    @property
    def _last_generation_meta(self) -> Dict[str, Any]:
        return dict(self._generation_meta_context.get())

    @_last_generation_meta.setter
    def _last_generation_meta(self, value: Mapping[str, Any]) -> None:
        self._generation_meta_context.set(dict(value or {}))

    @property
    def last_generation_meta(self) -> Dict[str, Any]:
        return self.get_last_generation_meta()

    def _system_message(self, json_mode: bool = False) -> str:
        message = (
            "Tu es EnnoSmart, assistant expert CIR/R&D. Réponds en français, "
            "sans inventer et uniquement à partir des preuves fournies. "
            "Ne révèle jamais ton raisonnement interne."
        )
        if json_mode:
            message += " Retourne uniquement un objet JSON valide, sans Markdown ni texte autour."
        return message

    def _provider_order(self) -> List[str]:
        if not self.allow_cross_provider_fallback:
            return [self.provider]
        return [self.provider] + [
            item
            for item in ("openai", "ollama", "openrouter", "gemini")
            if item != self.provider
        ]

    def _models_for_provider(self, provider: str, request_name: Optional[str]) -> List[str]:
        primary = self._default_model_for_request(request_name, provider)
        if provider == "openai":
            fallbacks = self.openai_fallback_models
            # Le modèle général est un fallback naturel du modèle writer.
            if self._is_writer_request(request_name):
                fallbacks = [self.openai_model] + fallbacks
        elif provider == "ollama":
            fallbacks = self.ollama_fallback_models
        elif provider == "openrouter":
            fallbacks = self.openrouter_fallback_models
        else:
            fallbacks = []
        return _dedupe([primary, *fallbacks])

    def _context_window_for_provider(self, provider: str) -> int:
        if provider == "openai":
            return self.openai_context_window
        if provider == "ollama":
            return self.ollama_num_ctx
        if provider == "openrouter":
            return self.openrouter_context_window
        return self.gemini_context_window

    def _prepare_prompt(
        self,
        prompt: str,
        provider: str,
        request_name: Optional[str],
        max_input_tokens: Optional[int],
        max_output_tokens: int,
    ) -> Tuple[str, Dict[str, Any]]:
        text = str(prompt or "").strip()
        context_window = self._context_window_for_provider(provider)
        requested_budget = int(
            max_input_tokens
            or max(256, context_window - int(max_output_tokens) - 1024)
        )
        context_budget = max(256, context_window - int(max_output_tokens) - 512)
        input_budget = min(requested_budget, context_budget)

        char_limit = (
            self.writer_max_prompt_chars
            if self._is_writer_request(request_name)
            else self.max_prompt_chars
        )
        char_budget = min(char_limit, max(900, int(input_budget * 3.6)))
        truncated = len(text) > char_budget

        if truncated:
            head_len = int(char_budget * 0.78)
            tail_len = char_budget - head_len
            text = (
                text[:head_len].rstrip()
                + "\n\n[CONTEXTE REDUIT AUTOMATIQUEMENT]\n\n"
                + text[-tail_len:].lstrip()
            )

        return text, {
            "input_budget_tokens": input_budget,
            "estimated_prompt_tokens": estimate_tokens(text),
            "prompt_chars": len(text),
            "prompt_truncated": truncated,
            "max_output_tokens": int(max_output_tokens),
            "context_window": context_window,
            "writer_request": self._is_writer_request(request_name),
        }

    def generate(
        self,
        prompt: str,
        temperature: float = 0.10,
        max_output_tokens: int = 1400,
        retries: int = 1,
        max_input_tokens: Optional[int] = None,
        json_mode: bool = False,
        request_name: Optional[str] = None,
        response_schema: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> str:
        """Exécute un appel après admission dans la file de capacité globale."""
        with llm_capacity_slot(request_name or "llm:generate") as queue_meta:
            try:
                return self._generate_without_capacity_limit(
                    prompt=prompt,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    retries=retries,
                    max_input_tokens=max_input_tokens,
                    json_mode=json_mode,
                    request_name=request_name,
                    response_schema=response_schema,
                    **kwargs,
                )
            finally:
                meta = self.get_last_generation_meta()
                meta.update(queue_meta)
                self._last_generation_meta = meta

    def _generate_without_capacity_limit(
        self,
        prompt: str,
        temperature: float = 0.10,
        max_output_tokens: int = 1400,
        retries: int = 1,
        max_input_tokens: Optional[int] = None,
        json_mode: bool = False,
        request_name: Optional[str] = None,
        response_schema: Optional[Mapping[str, Any]] = None,
        **_: Any,
    ) -> str:
        if not str(prompt or "").strip():
            return ""

        # Garde-fous globaux pilotables par le superadmin. Le plafond ne peut
        # que réduire une demande d'agent, jamais l'augmenter implicitement.
        runtime_temperature = _env("ENNOSMART_LLM_DEFAULT_TEMPERATURE")
        if runtime_temperature:
            try:
                temperature = min(2.0, max(0.0, float(runtime_temperature)))
            except (TypeError, ValueError):
                pass
        output_cap = _env_int("ENNOSMART_LLM_MAX_OUTPUT_TOKENS_CAP", 0, 0)
        if output_cap:
            max_output_tokens = min(int(max_output_tokens), output_cap)

        schema = dict(response_schema) if isinstance(response_schema, Mapping) else None
        json_mode = bool(json_mode or schema)
        errors: List[str] = []
        for current_provider in self._provider_order():
            provider_prompt, budget_meta = self._prepare_prompt(
                prompt=prompt,
                provider=current_provider,
                request_name=request_name,
                max_input_tokens=max_input_tokens,
                max_output_tokens=max_output_tokens,
            )

            if current_provider == "openai" and not self.openai_api_key:
                errors.append("OpenAI désactivé : OPENAI_API_KEY absente.")
                continue
            if current_provider == "openrouter" and not self.openrouter_api_key:
                errors.append("OpenRouter désactivé : OPENROUTER_API_KEY absente.")
                continue
            if current_provider == "gemini" and (
                not self.gemini_api_key or not self.gemini_model
            ):
                errors.append("Gemini désactivé : clé ou modèle absent.")
                continue

            models = self._models_for_provider(current_provider, request_name)
            if not models:
                errors.append(f"{current_provider}: aucun modèle configuré.")
                continue

            for current_model in models:
                started = time.time()
                try:
                    if current_provider == "openai":
                        content, usage = self._generate_openai(
                            provider_prompt,
                            current_model,
                            temperature,
                            max_output_tokens,
                            retries,
                            json_mode,
                            schema,
                        )
                    elif current_provider == "ollama":
                        content, usage = self._generate_ollama(
                            provider_prompt,
                            current_model,
                            temperature,
                            max_output_tokens,
                            retries,
                            json_mode,
                            schema,
                        )
                    elif current_provider == "openrouter":
                        content, usage = self._generate_openrouter(
                            provider_prompt,
                            current_model,
                            temperature,
                            max_output_tokens,
                            retries,
                            json_mode,
                            schema,
                        )
                    else:
                        content, usage = self._generate_gemini(
                            provider_prompt,
                            current_model,
                            temperature,
                            max_output_tokens,
                            retries,
                            json_mode,
                            schema,
                        )

                    self._last_generation_meta = {
                        **budget_meta,
                        **usage,
                        "request_name": request_name,
                        "provider": current_provider,
                        "model": current_model,
                        "temperature": float(temperature),
                        "elapsed_seconds": round(time.time() - started, 3),
                        "estimated_output_tokens": estimate_tokens(content),
                        "output_chars": len(content),
                        "json_mode": bool(json_mode),
                        "structured_output": bool(schema),
                    }
                    _log(
                        f"OK request={request_name or '-'} provider={current_provider} "
                        f"model={current_model} total_tokens="
                        f"{self._last_generation_meta.get('total_tokens')}"
                    )
                    return content
                except Exception as exc:
                    error = f"{current_provider}/{current_model}: {exc}"
                    errors.append(error)
                    _log(error)

        self._last_generation_meta = {
            "request_name": request_name,
            "provider": self.provider,
            "errors": errors[-10:],
        }
        raise RuntimeError("Aucun LLM disponible : " + " | ".join(errors[-10:]))

    def web_search(
        self,
        query: str,
        *,
        allowed_domains: Optional[Sequence[str]] = None,
        blocked_domains: Optional[Sequence[str]] = None,
        max_output_tokens: int = 1400,
        retries: int = 1,
        request_name: str = "ennoscholar:guided_research:web_search",
    ) -> Dict[str, Any]:
        """Exécute la recherche Web dans la même capacité que les autres LLM."""
        with llm_capacity_slot(request_name) as queue_meta:
            try:
                return self._web_search_without_capacity_limit(
                    query,
                    allowed_domains=allowed_domains,
                    blocked_domains=blocked_domains,
                    max_output_tokens=max_output_tokens,
                    retries=retries,
                    request_name=request_name,
                )
            finally:
                meta = self.get_last_generation_meta()
                meta.update(queue_meta)
                self._last_generation_meta = meta

    def _web_search_without_capacity_limit(
        self,
        query: str,
        *,
        allowed_domains: Optional[Sequence[str]] = None,
        blocked_domains: Optional[Sequence[str]] = None,
        max_output_tokens: int = 1400,
        retries: int = 1,
        request_name: str = "ennoscholar:guided_research:web_search",
    ) -> Dict[str, Any]:
        """Recherche Web OpenAI avec URLs complètes et citations traçables.

        Cette méthode reste dans le client central afin que les agents ne
        dupliquent ni clé, ni URL, ni sélection de modèle fournisseur.
        """
        prompt = str(query or "").strip()
        if not prompt:
            return {
                "ok": False,
                "provider": "openai_web_search",
                "error": "empty_query",
                "sources": [],
            }
        if not self.openai_web_search_enabled:
            return {
                "ok": False,
                "provider": "openai_web_search",
                "error": "disabled",
                "sources": [],
            }
        if not self.openai_api_key:
            return {
                "ok": False,
                "provider": "openai_web_search",
                "error": "missing_openai_api_key",
                "sources": [],
            }

        filters: Dict[str, Any] = {}
        clean_allowed = _dedupe(
            str(value or "")
            .strip()
            .removeprefix("https://")
            .removeprefix("http://")
            .strip("/")
            for value in (allowed_domains or [])
        )[:100]
        clean_blocked = _dedupe(
            str(value or "")
            .strip()
            .removeprefix("https://")
            .removeprefix("http://")
            .strip("/")
            for value in (blocked_domains or [])
        )[:100]
        if clean_allowed:
            filters["allowed_domains"] = clean_allowed
        if clean_blocked:
            filters["blocked_domains"] = clean_blocked

        tool: Dict[str, Any] = {
            "type": "web_search",
            "search_context_size": "high",
        }
        if filters:
            tool["filters"] = filters

        models = _dedupe([
            self.openai_web_search_model,
            *self.openai_web_search_fallback_models,
        ])
        errors: List[str] = []
        for model in models:
            payload: Dict[str, Any] = {
                "model": model,
                "input": prompt,
                "tools": [tool],
                "tool_choice": "auto",
                "include": ["web_search_call.action.sources"],
                "max_output_tokens": max(256, int(max_output_tokens)),
                "store": bool(self.openai_store),
            }
            if self.openai_web_search_reasoning_effort:
                payload["reasoning"] = {
                    "effort": self.openai_web_search_reasoning_effort,
                }
            headers = {
                "Authorization": f"Bearer {self.openai_api_key}",
                "Content-Type": "application/json",
            }
            started = time.time()
            try:
                data = self._post_with_retry(
                    f"{self.openai_base_url}/responses",
                    payload,
                    headers,
                    retries,
                    "OpenAI Web Search",
                )
                parsed = self._parse_openai_web_search_response(data)
                usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                result = {
                    "ok": True,
                    "provider": "openai_web_search",
                    "model": model,
                    "response_id": data.get("id"),
                    "answer": parsed["answer"],
                    "sources": parsed["sources"],
                    "elapsed_seconds": round(time.time() - started, 3),
                    "usage": usage,
                    "request_name": request_name,
                }
                self._last_generation_meta = {
                    "request_name": request_name,
                    "provider": "openai",
                    "model": model,
                    "response_id": data.get("id"),
                    "elapsed_seconds": result["elapsed_seconds"],
                    "prompt_tokens": usage.get("input_tokens"),
                    "completion_tokens": usage.get("output_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "web_sources_count": len(parsed["sources"]),
                    "tool": "web_search",
                }
                return result
            except Exception as exc:
                errors.append(f"{model}: {exc}")

        raise RuntimeError(
            "Recherche Web OpenAI indisponible : " + " | ".join(errors[-5:])
        )

    @staticmethod
    def _parse_openai_web_search_response(data: Mapping[str, Any]) -> Dict[str, Any]:
        """Normalise le texte et toutes les URLs consultées par Responses API."""
        answer_parts: List[str] = []
        source_by_url: Dict[str, Dict[str, Any]] = {}

        def remember_source(
            url: Any,
            *,
            title: Any = "",
            source_excerpt: Any = "",
            citation_context: Any = "",
            cited: bool = False,
        ) -> None:
            target = str(url or "").strip()
            if not target.startswith(("http://", "https://")):
                return
            source_key = (
                target.split("#", 1)[0]
                .split("?", 1)[0]
                .rstrip("/")
                .casefold()
            )
            row = source_by_url.setdefault(
                source_key,
                {
                    "url": target,
                    "title": "",
                    "snippet": "",
                    "source_excerpt": "",
                    "citation_context": "",
                    "cited": False,
                },
            )
            if title and not row["title"]:
                row["title"] = str(title).strip()
            if (
                source_excerpt
                and len(str(source_excerpt).strip())
                > len(row["source_excerpt"])
            ):
                row["source_excerpt"] = str(source_excerpt).strip()
            if (
                citation_context
                and len(str(citation_context).strip())
                > len(row["citation_context"])
            ):
                row["citation_context"] = str(citation_context).strip()
            row["snippet"] = (
                row["source_excerpt"] or row["citation_context"]
            )
            row["cited"] = bool(row["cited"] or cited)

        for item in data.get("output") or []:
            if not isinstance(item, Mapping):
                continue
            action = item.get("action")
            if isinstance(action, Mapping):
                for source in action.get("sources") or []:
                    if isinstance(source, Mapping):
                        remember_source(
                            source.get("url"),
                            title=source.get("title"),
                            source_excerpt=(
                                source.get("snippet")
                                or source.get("description")
                            ),
                        )
            for source in item.get("sources") or []:
                if isinstance(source, Mapping):
                    remember_source(
                        source.get("url"),
                        title=source.get("title"),
                        source_excerpt=(
                            source.get("snippet")
                            or source.get("description")
                        ),
                    )
            if item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if not isinstance(content, Mapping):
                    continue
                text = str(content.get("text") or "").strip()
                if text:
                    answer_parts.append(text)
                for annotation in content.get("annotations") or []:
                    if not isinstance(annotation, Mapping):
                        continue
                    url = annotation.get("url")
                    if not url:
                        continue
                    start = annotation.get("start_index")
                    end = annotation.get("end_index")
                    snippet = ""
                    if text and isinstance(start, int) and isinstance(end, int):
                        snippet = text[
                            max(0, start - 260):min(len(text), end + 260)
                        ]
                    remember_source(
                        url,
                        title=annotation.get("title"),
                        citation_context=snippet,
                        cited=True,
                    )

        return {
            "answer": "\n".join(answer_parts).strip(),
            "sources": list(source_by_url.values()),
        }

    def _generate_openai(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_output_tokens: int,
        retries: int,
        json_mode: bool,
        response_schema: Optional[Mapping[str, Any]],
    ) -> Tuple[str, Dict[str, Any]]:
        url = f"{self.openai_base_url}/chat/completions"
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._system_message(json_mode)},
                {"role": "user", "content": prompt},
            ],
            "max_completion_tokens": int(max_output_tokens),
            "store": bool(self.openai_store),
        }
        if self.openai_send_temperature:
            payload["temperature"] = float(temperature)
        if response_schema:
            schema_name = re.sub(
                r"[^a-zA-Z0-9_-]+",
                "_",
                str(response_schema.get("title") or "structured_response"),
            )[:64]
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name or "structured_response",
                    "strict": False,
                    "schema": dict(response_schema),
                },
            }
        elif json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }
        data = self._post_with_retry(url, payload, headers, retries, "OpenAI")
        choices = data.get("choices") or []
        content = str(
            (((choices[0] if choices else {}).get("message") or {}).get("content"))
            or ""
        ).strip()
        if not content:
            raise RuntimeError("Réponse OpenAI vide.")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return content, {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cached_tokens": (
                ((usage.get("prompt_tokens_details") or {}).get("cached_tokens"))
                if isinstance(usage.get("prompt_tokens_details"), dict)
                else None
            ),
            "response_id": data.get("id"),
        }

    def _generate_ollama(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_output_tokens: int,
        retries: int,
        json_mode: bool,
        response_schema: Optional[Mapping[str, Any]],
    ) -> Tuple[str, Dict[str, Any]]:
        url = f"{self.ollama_base_url}/api/chat"
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._system_message(json_mode)},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "keep_alive": self.ollama_keep_alive,
            "options": {
                "temperature": float(temperature),
                "num_predict": int(max_output_tokens or self.ollama_num_predict),
                "num_ctx": int(self.ollama_num_ctx),
            },
        }
        if response_schema:
            payload["format"] = dict(response_schema)
        elif json_mode:
            payload["format"] = "json"

        data = self._post_with_retry(url, payload, None, retries, "Ollama")
        content = str(
            (data.get("message") or {}).get("content")
            or data.get("response")
            or ""
        ).strip()
        if not content:
            raise RuntimeError("Réponse Ollama vide.")
        prompt_tokens = data.get("prompt_eval_count")
        completion_tokens = data.get("eval_count")
        return content, {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": (
                int(prompt_tokens or 0) + int(completion_tokens or 0)
            ),
        }

    def _generate_openrouter(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_output_tokens: int,
        retries: int,
        json_mode: bool,
        response_schema: Optional[Mapping[str, Any]],
    ) -> Tuple[str, Dict[str, Any]]:
        url = f"{self.openrouter_base_url}/chat/completions"
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._system_message(json_mode)},
                {"role": "user", "content": prompt},
            ],
            "temperature": float(temperature),
            "max_tokens": int(max_output_tokens),
        }
        if response_schema:
            schema_name = re.sub(
                r"[^a-zA-Z0-9_-]+",
                "_",
                str(response_schema.get("title") or "structured_response"),
            )[:64]
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name or "structured_response",
                    "strict": False,
                    "schema": dict(response_schema),
                },
            }
        elif json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": _env("OPENROUTER_HTTP_REFERER", "http://localhost"),
            "X-Title": _env("OPENROUTER_X_TITLE", "EnnoSmart"),
        }
        data = self._post_with_retry(url, payload, headers, retries, "OpenRouter")
        choices = data.get("choices") or []
        content = str(
            (((choices[0] if choices else {}).get("message") or {}).get("content"))
            or ""
        ).strip()
        if not content:
            raise RuntimeError("Réponse OpenRouter vide.")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return content, {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }

    def _generate_gemini(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_output_tokens: int,
        retries: int,
        json_mode: bool,
        response_schema: Optional[Mapping[str, Any]],
    ) -> Tuple[str, Dict[str, Any]]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={self.gemini_api_key}"
        )
        generation_config: Dict[str, Any] = {
            "temperature": float(temperature),
            "maxOutputTokens": int(max_output_tokens),
        }
        if response_schema:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseJsonSchema"] = dict(response_schema)
        elif json_mode:
            generation_config["responseMimeType"] = "application/json"
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": self._system_message(json_mode)
                            + "\n\n"
                            + prompt
                        }
                    ],
                }
            ],
            "generationConfig": generation_config,
        }
        data = self._post_with_retry(url, payload, None, retries, "Gemini")
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError("Réponse Gemini sans candidat.")
        parts = candidates[0].get("content", {}).get("parts", [])
        content = "\n".join(
            str(part.get("text", "")).strip()
            for part in parts
            if isinstance(part, dict) and part.get("text")
        ).strip()
        if not content:
            raise RuntimeError("Réponse Gemini vide.")
        usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
        return content, {
            "prompt_tokens": usage.get("promptTokenCount"),
            "completion_tokens": usage.get("candidatesTokenCount"),
            "total_tokens": usage.get("totalTokenCount"),
        }

    def _post_with_retry(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]],
        retries: int,
        label: str,
    ) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        requested_attempts = max(1, int(retries) + 1)
        openai_request = str(label or "").casefold().startswith("openai")
        rate_limit_retries = (
            _env_int("ENNOSMART_OPENAI_429_MAX_RETRIES", 4, 0)
            if openai_request
            else 0
        )
        attempts = max(requested_attempts, rate_limit_retries + 1)
        rate_limit_attempt = 0
        for attempt in range(attempts):
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=(self.connect_timeout, self.read_timeout),
                )
                if response.status_code == 429 and openai_request:
                    last_error = RuntimeError(
                        f"HTTP 429: {response.text[:1600]}"
                    )
                    if attempt + 1 >= attempts:
                        break
                    provider_delay = _retry_after_seconds(response)
                    exponential_delay = min(
                        _env_float(
                            "ENNOSMART_OPENAI_429_MAX_DELAY_SECONDS", 90.0
                        ),
                        2.0 ** rate_limit_attempt,
                    )
                    delay = max(
                        provider_delay or 0.0,
                        exponential_delay,
                    ) + _env_float(
                        "ENNOSMART_OPENAI_429_RETRY_BUFFER_SECONDS", 1.5
                    )
                    rate_limit_attempt += 1
                    print(
                        f"[LLM-RATE][429] label={label} "
                        f"attempt={attempt + 1}/{attempts} "
                        f"retry_after={provider_delay if provider_delay is not None else 'unknown'} "
                        f"sleep={delay:.2f}s",
                        flush=True,
                    )
                    time.sleep(delay)
                    continue
                if response.status_code != 200:
                    error = RuntimeError(
                        f"HTTP {response.status_code}: {response.text[:1600]}"
                    )
                    if (
                        response.status_code >= 500
                        and attempt + 1 < requested_attempts
                    ):
                        last_error = error
                        time.sleep(min(4.0, 1.0 * (attempt + 1)))
                        continue
                    raise error
                data = response.json()
                if not isinstance(data, dict):
                    raise RuntimeError("Réponse JSON fournisseur invalide.")
                return data
            except Exception as exc:
                last_error = exc
                if attempt + 1 < requested_attempts:
                    time.sleep(min(4.0, 1.0 * (attempt + 1)))
                    continue
                break
        raise RuntimeError(f"{label}: {last_error}")


# Compatibilité avec les anciens imports du projet.
GeminiClient = LLMClient
GeminiLLM = LLMClient
OpenRouterClient = LLMClient
OpenAIClient = LLMClient

# BEGIN ENNOSMART_BUDGET_LOGGING_V1
try:
    from modules.LLM.usage_budget import install_llm_budget_hooks
    install_llm_budget_hooks(LLMClient)
except Exception as _budget_error:
    print(f"[BUDGET][WARN] Hook non installé: {_budget_error}", flush=True)
# END ENNOSMART_BUDGET_LOGGING_V1
