# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import List, Optional

import requests

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


def _load_env() -> None:
    if load_dotenv is None:
        return

    candidates = [
        Path.cwd() / ".env",
        Path(r"C:\EnnoSmart\.env"),
        Path(r"C:\EnnoSmart\backend_api\.env"),
        Path(__file__).resolve().parents[2] / ".env",
    ]

    for p in candidates:
        try:
            if p.exists():
                load_dotenv(p, override=True)
        except Exception:
            pass


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except Exception:
        return default


def _verbose() -> bool:
    return _env("ENNOSMART_LLM_VERBOSE", "0") == "1"


def _log(message: str) -> None:
    if _verbose():
        print(f"[LLMClient] {message}", flush=True)


def _split_models(value: str) -> List[str]:
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


class LLMClient:
    def __init__(self, model: Optional[str] = None, **kwargs):
        _load_env()

        self.provider = _env("ENNOSMART_LLM_PROVIDER", "openrouter").lower()

        self.openrouter_api_key = _env("OPENROUTER_API_KEY")
        self.openrouter_model = model or _env("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")
        self.openrouter_fallback_models = _split_models(_env("OPENROUTER_FALLBACK_MODELS", ""))

        self.ollama_base_url = _env("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self.ollama_model = model or _env("OLLAMA_MODEL", "qwen2.5:7b-instruct")
        self.ollama_fallback_models = _split_models(_env("OLLAMA_FALLBACK_MODELS", ""))

        self.gemini_api_key = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
        self.gemini_model = _env("ENNOSMART_GEMINI_MODEL", "")

        self.connect_timeout = _env_int("ENNOSMART_LLM_CONNECT_TIMEOUT", 20)
        self.read_timeout = _env_int("ENNOSMART_LLM_READ_TIMEOUT", 180)
        self.max_prompt_chars = _env_int("ENNOSMART_LLM_MAX_PROMPT_CHARS", 12000)

        self.ollama_num_ctx = _env_int("OLLAMA_NUM_CTX", 8192)
        self.ollama_num_predict = _env_int("OLLAMA_NUM_PREDICT", 1600)
        self.ollama_keep_alive = _env("OLLAMA_KEEP_ALIVE", "10m")

        _log(f"Provider = {self.provider}")
        _log(f"OpenRouter model principal = {self.openrouter_model}")
        _log(f"OpenRouter fallback models = {self.openrouter_fallback_models}")
        _log(f"Ollama base_url = {self.ollama_base_url}")
        _log(f"Ollama model principal = {self.ollama_model}")
        _log(f"Ollama fallback models = {self.ollama_fallback_models}")
        _log(f"Gemini model = {self.gemini_model}")
        _log(f"Timeout connect/read = {self.connect_timeout}/{self.read_timeout}")
        _log(f"Max prompt chars = {self.max_prompt_chars}")

    def _safe_prompt(self, prompt: str) -> str:
        prompt = str(prompt or "").strip()
        if len(prompt) <= self.max_prompt_chars:
            return prompt

        head = prompt[: int(self.max_prompt_chars * 0.68)]
        tail = prompt[-int(self.max_prompt_chars * 0.32):]
        return (
            head
            + "\n\n[NOTE SYSTEME : prompt tronqué automatiquement pour respecter la limite LLM.]\n\n"
            + tail
        )

    def _system_message(self) -> str:
        return (
            "Tu es EnnoSmart, assistant expert CIR/R&D. "
            "Tu réponds en français, clairement, sans halluciner. "
            "Tu utilises uniquement les sources fournies. "
            "Pour les demandes JSON, tu réponds uniquement avec du JSON valide, sans markdown. "
            "N'utilise pas de gras autour des titres Markdown : écris ## Titre, pas **## Titre**."
        )

    def _provider_order(self) -> List[str]:
        p = self.provider
        if p == "openrouter":
            return ["openrouter", "ollama", "gemini"]
        if p == "ollama":
            return ["ollama", "openrouter", "gemini"]
        if p == "gemini":
            return ["gemini", "openrouter", "ollama"]
        return ["openrouter", "ollama", "gemini"]

    def generate(
        self,
        prompt: str,
        temperature: float = 0.10,
        max_output_tokens: int = 1400,
        retries: int = 1,
        **kwargs,
    ) -> str:
        prompt = self._safe_prompt(prompt)
        if not prompt:
            return ""

        errors: List[str] = []

        for provider in self._provider_order():
            if provider == "openrouter":
                models = []
                if self.openrouter_model:
                    models.append(self.openrouter_model)
                models.extend(self.openrouter_fallback_models)

                if not self.openrouter_api_key:
                    errors.append("OpenRouter ignoré : OPENROUTER_API_KEY vide.")
                    continue

                for model in models:
                    try:
                        _log(f"Essai OpenRouter model={model}")
                        return self._generate_openrouter(prompt, model, temperature, max_output_tokens, retries)
                    except Exception as exc:
                        err = f"OpenRouter {model}: {exc}"
                        errors.append(err)
                        _log(err)

            elif provider == "ollama":
                models = []
                if self.ollama_model:
                    models.append(self.ollama_model)
                models.extend(self.ollama_fallback_models)

                for model in models:
                    try:
                        _log(f"Essai Ollama model={model}")
                        return self._generate_ollama(prompt, model, temperature, max_output_tokens, retries)
                    except Exception as exc:
                        err = f"Ollama {model}: {exc}"
                        errors.append(err)
                        _log(err)

            elif provider == "gemini":
                if not self.gemini_api_key or not self.gemini_model:
                    errors.append("Gemini ignoré : clé ou modèle vide.")
                    continue
                try:
                    _log(f"Essai Gemini model={self.gemini_model}")
                    return self._generate_gemini(prompt, self.gemini_model, temperature, max_output_tokens, retries)
                except Exception as exc:
                    err = f"Gemini {self.gemini_model}: {exc}"
                    errors.append(err)
                    _log(err)

        raise RuntimeError(
            "Aucun LLM disponible. Vérifie OpenRouter/Ollama/Gemini. "
            "Erreurs : " + " | ".join(errors[-8:])
        )

    def _generate_openrouter(self, prompt: str, model: str, temperature: float, max_output_tokens: int, retries: int) -> str:
        url = "https://openrouter.ai/api/v1/chat/completions"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._system_message()},
                {"role": "user", "content": prompt},
            ],
            "temperature": float(temperature),
            "max_tokens": int(max_output_tokens),
            "stream": False,
        }

        headers = {
            "Authorization": f"Bearer {self.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8501",
            "X-Title": "EnnoSmart",
        }

        last_error = None
        for attempt in range(max(1, int(retries) + 1)):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=(self.connect_timeout, self.read_timeout))
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:1500]}")
                data = response.json()
                content = str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
                if not content:
                    raise RuntimeError(f"Réponse OpenRouter vide: {json.dumps(data, ensure_ascii=False)[:800]}")
                _log(f"OpenRouter OK model={model}, chars={len(content)}")
                return content
            except Exception as exc:
                last_error = exc
                _log(f"OpenRouter tentative {attempt + 1} échouée : {exc}")
                if attempt < retries:
                    time.sleep(1.2 * (attempt + 1))
        raise RuntimeError(last_error)

    def _generate_ollama(self, prompt: str, model: str, temperature: float, max_output_tokens: int, retries: int) -> str:
        url = f"{self.ollama_base_url}/api/chat"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._system_message()},
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

        last_error = None
        for attempt in range(max(1, int(retries) + 1)):
            try:
                response = requests.post(url, json=payload, timeout=(self.connect_timeout, self.read_timeout))
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:1500]}")
                data = response.json()
                content = str((data.get("message") or {}).get("content") or data.get("response") or "").strip()
                if not content:
                    raise RuntimeError(f"Réponse Ollama vide: {json.dumps(data, ensure_ascii=False)[:800]}")
                _log(f"Ollama OK model={model}, chars={len(content)}")
                return content
            except Exception as exc:
                last_error = exc
                _log(f"Ollama tentative {attempt + 1} échouée : {exc}")
                if attempt < retries:
                    time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(last_error)

    def _generate_gemini(self, prompt: str, model: str, temperature: float, max_output_tokens: int, retries: int) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.gemini_api_key}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": self._system_message() + "\n\n" + prompt}]}],
            "generationConfig": {"temperature": float(temperature), "maxOutputTokens": int(max_output_tokens)},
        }
        last_error = None
        for attempt in range(max(1, int(retries) + 1)):
            try:
                response = requests.post(url, json=payload, timeout=(self.connect_timeout, self.read_timeout))
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:1500]}")
                data = response.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    raise RuntimeError(f"Aucun candidate Gemini: {json.dumps(data, ensure_ascii=False)[:800]}")
                parts = candidates[0].get("content", {}).get("parts", [])
                content = "\n".join(str(p.get("text", "")).strip() for p in parts if isinstance(p, dict) and p.get("text")).strip()
                if not content:
                    raise RuntimeError(f"Réponse Gemini vide: {json.dumps(data, ensure_ascii=False)[:800]}")
                _log(f"Gemini OK model={model}, chars={len(content)}")
                return content
            except Exception as exc:
                last_error = exc
                _log(f"Gemini tentative {attempt + 1} échouée : {exc}")
                if attempt < retries:
                    time.sleep(1.2 * (attempt + 1))
        raise RuntimeError(last_error)


GeminiClient = LLMClient
GeminiLLM = LLMClient
OpenRouterClient = LLMClient
