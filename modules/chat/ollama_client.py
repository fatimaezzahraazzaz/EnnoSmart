# modules/chat/ollama_client.py

from __future__ import annotations

import requests


def clean_ollama_model(model: str) -> str:
    """
    Accepte :
      - ollama:mistral:7b-instruct
      - mistral:7b-instruct
      - ollama:qwen2.5:1.5b
      - qwen2.5:1.5b

    Retourne :
      - mistral:7b-instruct
      - qwen2.5:1.5b
    """
    model = str(model or "qwen2.5:1.5b").strip()

    if model.startswith("ollama:"):
        model = model.replace("ollama:", "", 1)

    return model or "qwen2.5:1.5b"


def ollama_chat(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
    num_predict: int = 180,
    timeout: int = 30,
) -> str:
    """
    Appel simple à Ollama /api/chat.

    keep_alive évite de recharger le modèle à chaque message.
    num_ctx réduit le contexte pour accélérer les petites décisions de chat.
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
            "num_ctx": 2048,
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