"""
modules/llm_gateway/mistral_client.py
──────────────────────────────────────────────────────────────────────────────
Client Mistral via Ollama SDK.

Modèle recommandé : mistral:7b-instruct
  → installer : ollama pull mistral:7b-instruct

Auteur  : EnnoSmart
Version : 1.0.0
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Modèle par défaut — changer ici si tu veux un autre modèle
DEFAULT_MODEL = "mistral:7b-instruct"

# Fallback si mistral non disponible
FALLBACK_MODEL = "llama3.2-vision"   # Déjà installé


def call_mistral(
    user_message: str,
    system_prompt: Optional[str] = None,
    max_tokens: int = 1000,
    temperature: float = 0.1,
    model: Optional[str] = None,
) -> str:
    """
    Appelle Mistral via le SDK ollama.

    Paramètres
    ----------
    user_message  : message utilisateur
    system_prompt : prompt système (contexte, instructions)
    max_tokens    : longueur max de la réponse
    temperature   : créativité [0.0–1.0]
    model         : override du modèle (défaut: mistral:7b-instruct)

    Retourne
    --------
    str : réponse du modèle

    Raises
    ------
    RuntimeError : si aucun modèle disponible
    """
    import ollama as ollama_sdk

    target_model = model or _resolve_model(ollama_sdk)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_message})

    try:
        response = ollama_sdk.chat(
            model=target_model,
            messages=messages,
            options={
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        )
        return response.message.content.strip()

    except Exception as exc:
        logger.error("Erreur appel %s : %s", target_model, exc)
        raise RuntimeError(f"Appel LLM échoué ({target_model}) : {exc}") from exc


def _resolve_model(ollama_sdk) -> str:
    """
    Résout le modèle disponible dans l'ordre de préférence.
    mistral:7b-instruct → mistral:7b → llama3.1:8b → fallback
    """
    try:
        available = [m.model for m in ollama_sdk.list().models]

        preference = [
            "mistral:7b-instruct",
            "mistral:7b",
            "mistral",
            "llama3.1:8b",
            "llama3.2-vision:latest",
        ]

        for model in preference:
            if any(model in name for name in available):
                logger.debug("Modèle LLM sélectionné : %s", model)
                return model

        # Dernier recours : premier modèle disponible
        if available:
            logger.warning(
                "Mistral non trouvé — utilisation de %s", available[0]
            )
            return available[0]

    except Exception as exc:
        logger.warning("Impossible de lister les modèles : %s", exc)

    return FALLBACK_MODEL