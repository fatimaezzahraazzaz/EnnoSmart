# modules/chat/schemas.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatDecision:
    """
    Résultat retourné par le module de chat intelligent.

    handled=True :
        Le module chat a répondu directement.
        Exemple : small talk, merci, conversation humaine.

    handled=False :
        EnnoAmel doit continuer :
        - résumé
        - éligibilité
        - verrous
        - mots-clés
        - RAG
        - agent spécialisé
    """

    handled: bool
    answer: str = ""

    intent: str = "unknown"
    action: str = "chat_understanding"

    use_rag: bool = False
    use_llm: bool = True

    recommended_agent: str = "EnnoAmel"
    needs_specialized_agent: bool = False

    confidence: float = 0.75
    normalized_question: str = ""

    debug: dict[str, Any] = field(default_factory=dict)