# -*- coding: utf-8 -*-
from __future__ import annotations

"""
LLMClient EnnoSmart V2

But :
- Garder la logique .env :
  OpenRouter principal -> OPENROUTER_MODEL
  OpenRouter fallback -> OPENROUTER_FALLBACK_MODELS
  Gemini fallback -> ENNOSMART_GEMINI_MODEL
- Éviter que l'agent reste bloqué sur OpenRouter.
- Timeout court par défaut :
  ENNOSMART_LLM_CONNECT_TIMEOUT=20
  ENNOSMART_LLM_READ_TIMEOUT=60
- Si OpenRouter échoue ou bloque trop longtemps, passage à Gemini.
"""

import os
import time
import json
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

    for p in [
        Path.cwd() / ".env",
        Path(r"C:\EnnoSmart\.env"),
        Path(__file__).resolve().parents[2] / ".env",
    ]:
        if p.exists():
            load_dotenv(p, override=True)


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except Exception:
        return default


def _verbose() -> bool:
    return _env("ENNOSMART_LLM_VERBOSE", "0") == "1"


def _log(msg: str) -> None:
    if _verbose():
        print(f"[LLMClient] {msg}", flush=True)


def _split_models(value: str) -> List[str]:
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


class LLMClient:
    def __init__(self, model: Optional[str] = None, **kwargs):
        _load_env()

        self.openrouter_api_key = _env("OPENROUTER_API_KEY")
        self.openrouter_model = model or _env("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")
        self.openrouter_fallback_models = _split_models(_env("OPENROUTER_FALLBACK_MODELS", ""))

        self.gemini_api_key = _env("GEMINI_API_KEY")
        self.gemini_model = _env("ENNOSMART_GEMINI_MODEL", "gemini-2.5-flash-lite")

        # Timeout séparé : connexion / lecture.
        self.connect_timeout = _env_int("ENNOSMART_LLM_CONNECT_TIMEOUT", 20)
        self.read_timeout = _env_int("ENNOSMART_LLM_READ_TIMEOUT", 60)

        # Limite sécurité du prompt envoyé.
        self.max_prompt_chars = _env_int("ENNOSMART_LLM_MAX_PROMPT_CHARS", 45000)

        _log(f"OpenRouter model principal = {self.openrouter_model}")
        _log(f"OpenRouter fallback models = {self.openrouter_fallback_models}")
        _log(f"Gemini fallback model = {self.gemini_model}")
        _log(f"Timeout connect/read = {self.connect_timeout}/{self.read_timeout}")
        _log(f"Max prompt chars = {self.max_prompt_chars}")

    def _safe_prompt(self, prompt: str) -> str:
        prompt = str(prompt or "").strip()
        if len(prompt) <= self.max_prompt_chars:
            return prompt

        # On garde le début + la fin pour préserver consignes et dernières sections.
        head = prompt[: int(self.max_prompt_chars * 0.65)]
        tail = prompt[-int(self.max_prompt_chars * 0.35):]
        return (
            head
            + "\n\n[NOTE SYSTEME : prompt tronqué pour éviter un blocage LLM.]\n\n"
            + tail
        )

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

        errors = []

        models = []
        if self.openrouter_model:
            models.append(self.openrouter_model)
        models.extend(self.openrouter_fallback_models)

        if self.openrouter_api_key and models:
            for model in models:
                try:
                    _log(f"Essai OpenRouter model={model}")
                    return self._generate_openrouter(
                        prompt=prompt,
                        model=model,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                        retries=retries,
                    )
                except Exception as e:
                    err = f"OpenRouter {model}: {e}"
                    errors.append(err)
                    _log(err)

        if self.gemini_api_key and self.gemini_model:
            try:
                _log(f"Essai Gemini fallback model={self.gemini_model}")
                return self._generate_gemini(
                    prompt=prompt,
                    model=self.gemini_model,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    retries=retries,
                )
            except Exception as e:
                err = f"Gemini {self.gemini_model}: {e}"
                errors.append(err)
                _log(err)

        raise RuntimeError(
            "Aucun LLM disponible. Vérifie OPENROUTER_API_KEY / GEMINI_API_KEY. "
            "Erreurs : " + " | ".join(errors[-5:])
        )

    def _generate_openrouter(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_output_tokens: int,
        retries: int,
    ) -> str:
        url = "https://openrouter.ai/api/v1/chat/completions"

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Tu es EnnoSmart, assistant expert CIR/R&D. "
                        "Tu réponds en français, clairement, sans halluciner. "
                        "Tu utilises uniquement les sources fournies. "
                        "N'utilise pas de gras autour des titres Markdown : écris ## Titre, pas **## Titre**."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
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
                response = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=(self.connect_timeout, self.read_timeout),
                )

                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:1500]}")

                data = response.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )

                content = str(content or "").strip()
                if not content:
                    raise RuntimeError(f"Réponse vide: {json.dumps(data)[:800]}")

                _log(f"OpenRouter OK model={model}, chars={len(content)}")
                return content

            except Exception as e:
                last_error = e
                _log(f"OpenRouter tentative {attempt + 1} échouée : {e}")
                if attempt < retries:
                    time.sleep(1.2 * (attempt + 1))

        raise RuntimeError(last_error)

    def _generate_gemini(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_output_tokens: int,
        retries: int,
    ) -> str:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={self.gemini_api_key}"
        )

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Tu es EnnoSmart, assistant expert CIR/R&D. "
                                "Réponds en français, clairement, sans halluciner. "
                                "Utilise uniquement les sources fournies. "
                                "N'utilise pas de gras autour des titres Markdown : écris ## Titre, pas **## Titre**. \n\n"
                                + prompt
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
            },
        }

        last_error = None

        for attempt in range(max(1, int(retries) + 1)):
            try:
                response = requests.post(
                    url,
                    json=payload,
                    timeout=(self.connect_timeout, self.read_timeout),
                )

                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:1500]}")

                data = response.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    raise RuntimeError(f"Aucun candidate Gemini: {json.dumps(data)[:800]}")

                parts = candidates[0].get("content", {}).get("parts", [])
                content = "\n".join(
                    str(p.get("text", "")).strip()
                    for p in parts
                    if isinstance(p, dict) and p.get("text")
                ).strip()

                if not content:
                    raise RuntimeError(f"Réponse Gemini vide: {json.dumps(data)[:800]}")

                _log(f"Gemini OK model={model}, chars={len(content)}")
                return content

            except Exception as e:
                last_error = e
                _log(f"Gemini tentative {attempt + 1} échouée : {e}")
                if attempt < retries:
                    time.sleep(1.2 * (attempt + 1))

        raise RuntimeError(last_error)


GeminiClient = LLMClient
GeminiLLM = LLMClient
