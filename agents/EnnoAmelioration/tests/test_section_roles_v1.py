from __future__ import annotations

from agents.EnnoAmelioration.application.audit_service import audit_text
from agents.EnnoAmelioration.application.section_improvement_policy import (
    render_section_improvement_contract,
)
from agents.EnnoAmelioration.application.semantic_routing_service import (
    SemanticRoutingService,
)
from agents.EnnoAmelioration.application.traceability_service import (
    build_revision_trace,
)
from agents.EnnoAmelioration.domain.models import (
    ImprovementIntent,
    RoutingDecision,
    SectionFunction,
    TargetScope,
)


def _routing(function: SectionFunction, *, diagnostic: bool = True) -> RoutingDecision:
    return RoutingDecision(
        intents=[ImprovementIntent.CIR_ELIGIBILITY],
        target_scope=TargetScope.SECTION,
        needs_diagnostic=diagnostic,
        section_function=function,
    )


def test_context_does_not_receive_universal_rd_chain_audit():
    findings = audit_text(
        "Le projet vise à améliorer un système existant afin de répondre au besoin décrit.",
        _routing(SectionFunction.CONTEXT),
    )
    assert all(item.code != "incomplete_rd_chain" for item in findings)
    assert all(item.code != "uncertainty_not_explicit" for item in findings)


def test_uncertainty_audit_is_specific_to_uncertainty_role():
    findings = audit_text(
        "Le système doit fonctionner dans différentes configurations.",
        _routing(SectionFunction.UNCERTAINTY),
    )
    assert any(item.code == "uncertainty_not_explicit" for item in findings)


def test_context_contract_explicitly_forbids_turning_intro_into_lock():
    contract = render_section_improvement_contract(SectionFunction.CONTEXT)
    assert "Présenter le contexte" in contract
    assert "un verrou scientifique" in contract


def test_known_context_does_not_automatically_route_to_diagnostic(monkeypatch):
    service = SemanticRoutingService()
    monkeypatch.setattr(
        service,
        "_fastjudge",
        lambda rows: {
            "target": {
                "function": SectionFunction.CONTEXT,
                "confidence": 0.95,
                "classifier": "test",
            }
        },
    )
    monkeypatch.setattr(service, "_semantic_functions", lambda rows: {})
    decision = service.route(
        "Améliore cette section et renforce son argumentation R&D/CIR uniquement avec les preuves du projet.",
        TargetScope.SECTION,
        "Le projet répond à un besoin technique.",
    )
    assert decision.section_function == SectionFunction.CONTEXT
    assert not decision.needs_diagnostic
    assert not decision.needs_scholar


def test_known_uncertainty_can_still_route_to_diagnostic(monkeypatch):
    service = SemanticRoutingService()
    monkeypatch.setattr(
        service,
        "_fastjudge",
        lambda rows: {
            "target": {
                "function": SectionFunction.UNCERTAINTY,
                "confidence": 0.95,
                "classifier": "test",
            }
        },
    )
    monkeypatch.setattr(service, "_semantic_functions", lambda rows: {})
    decision = service.route(
        "Renforce l'argumentation R&D/CIR uniquement avec les preuves du projet.",
        TargetScope.SECTION,
        "Une difficulté technique persiste.",
    )
    assert decision.needs_diagnostic


def test_style_rewrite_is_not_rejected_just_because_scholar_is_missing():
    routing = RoutingDecision(
        intents=[ImprovementIntent.GENERAL_REVISION],
        target_scope=TargetScope.SECTION,
        needs_scholar=True,
        section_function=SectionFunction.SCIENTIFIC_LANDSCAPE,
    )
    trace = build_revision_trace(
        "Les travaux existants utilisent plusieurs approches.",
        "Les travaux existants mobilisent plusieurs approches, présentées ici de manière structurée.",
        routing,
        {"cir_style": {"available": False}, "scholar": {"available": False}},
    )
    assert not trace["blocking"]
    assert "EnnoScholar" not in trace["agents_used"]
    assert trace["questions_for_consultant"] == []


def test_editorial_judgement_je_trouve_is_not_a_research_request():
    from agents.EnnoAmelioration.application.intention_service import understand_instruction

    prompt = (
        "Améliore la section 1.2.1 « Contexte de l’opération ». "
        "Je trouve le texte trop descriptif et pas assez clair pour un dossier CIR. "
        "Je veux mieux faire comprendre le contexte du projet, le besoin auquel il répond "
        "et pourquoi le recours aux données SAR synthétiques devient nécessaire. "
        "Renforce la cohérence et l’argumentation de cette section à partir des informations "
        "déjà disponibles dans le projet, sans inventer de faits, de résultats ou de chiffres."
    )
    decision = understand_instruction(prompt, TargetScope.SECTION)
    assert not decision.needs_new_research
    assert decision.forbids_new_research
    assert decision.forbids_scholar


def test_context_prompt_with_je_trouve_routes_to_writer_without_research(monkeypatch):
    service = SemanticRoutingService()
    monkeypatch.setattr(
        service,
        "_fastjudge",
        lambda rows: {
            "target": {
                "function": SectionFunction.CONTEXT,
                "confidence": 0.95,
                "classifier": "test",
            }
        },
    )
    monkeypatch.setattr(service, "_semantic_functions", lambda rows: {})
    decision = service.route(
        (
            "Je trouve le texte trop descriptif et pas assez clair pour un dossier CIR. "
            "Renforce la cohérence et l’argumentation de cette section à partir des informations "
            "déjà disponibles dans le projet, sans inventer de faits, de résultats ou de chiffres."
        ),
        TargetScope.SECTION,
        "Le projet s'inscrit dans le contexte de l'imagerie SAR.",
    )
    assert decision.section_function == SectionFunction.CONTEXT
    assert not decision.needs_new_research
    assert decision.forbids_new_research
    assert not decision.needs_scholar
    assert not decision.needs_diagnostic
    assert decision.specialist_route.value == "writer"


def test_synthetic_data_does_not_mean_concision():
    from agents.EnnoAmelioration.application.intention_service import understand_instruction

    decision = understand_instruction(
        "Explique pourquoi le recours aux données SAR synthétiques devient nécessaire.",
        TargetScope.SECTION,
    )
    assert ImprovementIntent.CONCISION not in decision.intents


def test_explicit_synthesis_or_concision_is_still_detected():
    from agents.EnnoAmelioration.application.intention_service import understand_instruction

    decision = understand_instruction(
        "Rends cette section plus synthétique et plus concise.",
        TargetScope.SECTION,
    )
    assert ImprovementIntent.CONCISION in decision.intents
