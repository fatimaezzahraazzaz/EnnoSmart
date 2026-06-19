# modules/chat/ollama_client.py

from __future__ import annotations

import requests


def clean_ollama_model(model: str) -> str:
    """
    Accepte :
      - ollama:llama3.2:3b
      - llama3.2:3b
      - ollama:qwen3:4b-instruct
      - qwen3:4b-instruct

    Retourne le nom attendu par Ollama.
    """
    model = str(model or "qwen3:4b-instruct").strip()

    if model.startswith("ollama:"):
        model = model.replace("ollama:", "", 1)

    return model or "qwen3:4b-instruct"


def ollama_chat(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.15,
    num_predict: int = 350,
    timeout: int = 45,
) -> str:
    """
    Appel court à Ollama /api/chat.

    Utilisé par le module chat pour produire une décision JSON.
    """
    payload = {
        "model": clean_ollama_model(model),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "keep_alive": "10m",
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            "num_ctx": 3072,
        },
    }

    response = requests.post(
        "http://127.0.0.1:11434/api/chat",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()

    data = response.json()
    return data.get("message", {}).get("content", "").strip()
