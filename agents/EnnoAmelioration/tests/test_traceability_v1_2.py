from agents.EnnoAmelioration.application.intention_service import understand_instruction
from agents.EnnoAmelioration.application.traceability_service import build_revision_trace
from agents.EnnoAmelioration.domain.models import TargetScope


def _routing():
    return understand_instruction("Améliore la clarté à faits constants.", TargetScope.SECTION)


def test_structural_section_number_is_not_treated_as_new_fact():
    original = "Contexte de l’opération\nLe projet exploite des données SAR synthétiques."
    improved = "1.2.1 Contexte de l’opération\nLe projet exploite des données SAR synthétiques."
    trace = build_revision_trace(original, improved, _routing(), {"cir_style": {"available": False}})
    assert not trace["blocking"]


def test_numbered_list_prefix_is_not_treated_as_new_fact():
    original = "Le contexte est présenté de manière progressive."
    improved = "1. Le contexte est présenté de manière progressive."
    trace = build_revision_trace(original, improved, _routing(), {"cir_style": {"available": False}})
    assert not trace["blocking"]


def test_real_new_metric_is_warned_but_candidate_remains_visible():
    original = "Le modèle a été évalué sur les données disponibles."
    improved = "Le modèle a été évalué sur les données disponibles avec une précision de 97 %."
    trace = build_revision_trace(original, improved, _routing(), {"cir_style": {"available": False}})
    assert not trace["blocking"]
    assert trace["has_warnings"]
    assert any("97%" in claim.get("markers", []) for claim in trace["unsupported_claims"])
    assert any(claim.get("severity") == "warning" for claim in trace["unsupported_claims"])


def test_scientific_notation_remains_a_factual_marker():
    original = "Le taux d'apprentissage est configuré."
    improved = "Le taux d'apprentissage est configuré à 1e-4."
    trace = build_revision_trace(original, improved, _routing(), {"cir_style": {"available": False}})
    assert not trace["blocking"]
    assert trace["has_warnings"]
    assert any("1e-4" in claim.get("markers", []) for claim in trace["unsupported_claims"])

from types import SimpleNamespace

from agents.EnnoAmelioration.application.agent import EnnoAmeliorationAgent
from agents.EnnoAmelioration.domain.models import (
    ImprovementIntent,
    ImprovementRequest,
    RoutingDecision,
    SectionFunction,
)


class _FixedContextRouter:
    def route(self, *args, **kwargs):
        return RoutingDecision(
            intents=[ImprovementIntent.CLARITY],
            target_scope=TargetScope.SECTION,
            needs_diagnostic=False,
            needs_scholar=False,
            needs_new_research=False,
            section_function=SectionFunction.CONTEXT,
        )


class _RepairingWriter:
    def __init__(self):
        self.calls = 0

    def rewrite(self, request, routing, audit, evidence):
        self.calls += 1
        if self.calls == 1:
            return (
                request.target_text.rstrip()
                + "\n\nCette présentation est adaptée à un dossier CIR.",
                {"provider": "fake", "attempt": 1},
            )
        return (
            "Le recours aux données SAR synthétiques répond à la disponibilité limitée des données réelles.",
            {"provider": "fake", "attempt": 2},
        )


def test_agent_keeps_candidate_visible_when_traceability_warns(monkeypatch):
    original = "Le recours aux données SAR synthétiques répond au manque de données réelles."
    writer = _RepairingWriter()
    agent = EnnoAmeliorationAgent(writer=writer, routing_service=_FixedContextRouter())
    monkeypatch.setattr(
        EnnoAmeliorationAgent,
        "_evidence_package",
        staticmethod(lambda *args, **kwargs: {"cir_style": {"available": False}}),
    )
    request = ImprovementRequest(
        instruction="Améliore la clarté et l'argumentation de cette section à faits constants.",
        full_text=original,
        target_text=original,
        target_scope=TargetScope.SECTION,
        target_section_title="Contexte de l’opération",
    )
    project = SimpleNamespace(
        id=1,
        organisme="Test",
        project_name="Projet",
        year="2024",
        domain_label="Radar",
    )

    result = agent.improve(None, project, request)

    assert result.ok
    assert result.state.value == "candidate_ready"
    assert writer.calls == 1
    assert result.improved_target
    assert "CIR" in result.improved_target
    assert not result.generation["trace"]["blocking"]
    assert result.generation["trace"]["has_warnings"]
    assert result.requires_confirmation is True
    assert any("CIR" in claim.get("markers", []) for claim in result.unsupported_claims)


class _AlwaysDropsProtectedMeasureWriter:
    def __init__(self):
        self.calls = 0

    def rewrite(self, request, routing, audit, evidence):
        self.calls += 1
        return "Le protocole a été évalué sur les données disponibles.", {
            "provider": "fake",
            "attempt": self.calls,
        }


def test_conservation_issue_is_visible_as_candidate_warning_not_rejected(monkeypatch):
    original = "Le protocole a été évalué avec une précision mesurée de 97 %."
    writer = _AlwaysDropsProtectedMeasureWriter()
    agent = EnnoAmeliorationAgent(writer=writer, routing_service=_FixedContextRouter())
    monkeypatch.setattr(
        EnnoAmeliorationAgent,
        "_evidence_package",
        staticmethod(lambda *args, **kwargs: {"cir_style": {"available": False}}),
    )
    request = ImprovementRequest(
        instruction="Améliore la clarté sans inventer de fait.",
        full_text=original,
        target_text=original,
        target_scope=TargetScope.SECTION,
        target_section_title="Contexte de l’opération",
    )
    project = SimpleNamespace(
        id=1,
        organisme="Test",
        project_name="Projet",
        year="2024",
        domain_label="Radar",
    )

    result = agent.improve(None, project, request)

    assert result.ok
    assert result.state.value == "candidate_ready"
    assert writer.calls == 1
    assert result.improved_target == "Le protocole a été évalué sur les données disponibles."
    assert result.requires_confirmation is True
    assert result.generation["conservation_validation"] == "consultant_review_required"
    assert result.generation["quality_control_mode"] == "advisory_only"
    assert result.generation["automatic_integrity_retry"] is False
    assert any(
        "mesures_perdues" in str(claim.get("reason") or "")
        for claim in result.unsupported_claims
    )
