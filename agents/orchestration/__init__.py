"""
agents/orchestration/__init__.py
Exports orchestration — compatibilité ancien nom + nouveau nom.
"""

from agents.orchestration.ennoamel import (
    EnnoAmelOrchestrator,
    EnnoAmelResponse,
    PreparationReport,
)

Orchestrator = EnnoAmelOrchestrator
OrchestratorResponse = EnnoAmelResponse

try:
    from agents.orchestration.intent_router import (
        detect_intent,
        Intent,
        AgentName,
        IntentDecision,
    )
except Exception:
    detect_intent = None
    Intent = None
    AgentName = None
    IntentDecision = None

__all__ = [
    "Orchestrator",
    "OrchestratorResponse",
    "EnnoAmelOrchestrator",
    "EnnoAmelResponse",
    "PreparationReport",
    "detect_intent",
    "Intent",
    "AgentName",
    "IntentDecision",
]
