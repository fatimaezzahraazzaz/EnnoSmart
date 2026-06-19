# modules/chat/schemas.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatDecision:
    """
    Décision produite par le module de chat intelligent.

    Principe :
    - Le LLM de chat comprend l'intention utilisateur.
    - Il ne fait pas l'analyse documentaire lui-même.
    - Il décrit comment l'orchestrateur/RAG doit répondre.

    handled=True :
        Le chat répond directement.
        Exemples : bonjour, merci, ok, aide, ou agent spécialisé encore en construction.

    handled=False :
        L'orchestrateur continue vers le RAG documentaire.
    """

    handled: bool
    answer: str = ""

    intent: str = "unknown"
    action: str = "chat_understanding"

    use_rag: bool = False
    use_llm: bool = True

    recommended_agent: str = "Orchestrateur"
    needs_specialized_agent: bool = False

    # Compréhension fine de la demande utilisateur
    topic: str = "general"
    answer_style: str = "natural"
    requested_format: str = "free_text"
    detail_level: str = "normal"
    max_points: int | None = None

    # Instructions transmises au RAG / QueryEngine
    rag_search_query: str = ""
    rag_instruction: str = ""

    confidence: float = 0.75
    normalized_question: str = ""

    debug: dict[str, Any] = field(default_factory=dict)
