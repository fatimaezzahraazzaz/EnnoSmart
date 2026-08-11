from __future__ import annotations

from pathlib import Path

from agents.EnnoAmelioration.application.intention_service import understand_instruction
from agents.EnnoAmelioration.application.semantic_routing_service import (
    SemanticRoutingService,
)
from agents.EnnoAmelioration.application.traceability_service import (
    build_revision_trace,
)
from agents.EnnoAmelioration.domain.models import (
    ParsedSection,
    SectionFunction,
    SpecialistRoute,
    TargetScope,
)


def test_argumentation_route_uses_diagnostic_without_requiring_cir_keyword():
    decision = understand_instruction(
        "Renforce les difficultés techniques et explique pourquoi le problème n'était pas trivial.",
        TargetScope.SECTION,
    )
    assert decision.needs_diagnostic
    assert not decision.needs_scholar


def test_improvement_router_is_registered_in_fastapi_main():
    main_source = (
        Path(__file__).resolve().parents[3] / "backend_api" / "main.py"
    ).read_text(encoding="utf-8")
    assert "from routers import improvement" in main_source
    assert "app.include_router(improvement.router)" in main_source


def test_project_only_instruction_never_triggers_scholar_or_research():
    decision = understand_instruction(
        """Améliore cette section pour renforcer l'argumentation R&D/CIR à partir des preuves réellement disponibles dans le projet. Utilise EnnoDiagnostic si des preuves projet sont nécessaires. N'invente aucun résultat, chiffre ou fait absent des sources. Ne fais pas de recherche scientifique externe pour ce test.""",
        TargetScope.SECTION,
    )
    assert decision.needs_diagnostic
    assert not decision.needs_scholar
    assert not decision.needs_new_research
    assert decision.forbids_new_research


def test_consultant_can_request_project_evidence_without_knowing_agent_names():
    decision = understand_instruction(
        "Renforce l'argumentation CIR uniquement à partir des preuves déjà disponibles dans le projet.",
        TargetScope.SECTION,
    )
    assert decision.needs_diagnostic
    assert not decision.needs_scholar
    assert not decision.needs_new_research
    assert decision.forbids_new_research
    assert decision.forbids_scholar


def test_project_sources_without_search_are_understood_as_local_corpus():
    decision = understand_instruction(
        "Utilise les sources du projet pour étayer le verrou, sans chercher de nouveaux éléments.",
        TargetScope.SECTION,
    )
    assert decision.needs_diagnostic
    assert not decision.needs_scholar
    assert not decision.needs_new_research
    assert decision.forbids_new_research
    assert decision.forbids_scholar


def test_explicit_no_scholar_has_priority_over_scientific_vocabulary():
    decision = understand_instruction(
        """Renforce le verrou avec EnnoDiagnostic et la mémoire CIR. N'effectue aucune recherche bibliographique ou scientifique externe et n'utilise pas EnnoScholar.""",
        TargetScope.SECTION,
    )
    assert decision.needs_diagnostic
    assert not decision.needs_scholar
    assert decision.forbids_scholar
    assert decision.forbids_new_research


def test_existing_validated_publications_can_be_used_without_new_research():
    decision = understand_instruction(
        "Réécris avec uniquement les publications validées disponibles, sans lancer de nouvelle recherche.",
        TargetScope.SECTION,
    )
    assert decision.needs_scholar
    assert not decision.needs_new_research
    assert decision.forbids_new_research


def test_revision_trace_uses_complete_sentences_instead_of_character_fragments():
    routing = understand_instruction("Améliore la clarté.", TargetScope.SECTION)
    original = (
        "Le projet vise à améliorer les performances du système. "
        "Les résultats montrent certaines limites."
    )
    improved = (
        "Le projet a pour objectif d'améliorer les performances du système. "
        "Les résultats mettent en évidence certaines limites."
    )
    trace = build_revision_trace(original, improved, routing, {"cir_style": {"available": False}})
    assert len(trace["changes"]) == 2
    assert all(len(change["before"].split()) >= 5 for change in trace["changes"])
    assert all(len(change["after"].split()) >= 5 for change in trace["changes"])
    assert all(not change["evidence_refs"] for change in trace["changes"])


def test_full_document_routes_renamed_sections_by_semantic_function(monkeypatch):
    text = "Bloc A\nComparaison scientifique.\n\nBloc B\nProtocole expérimental."
    sections = [
        ParsedSection(
            section_id="alpha",
            title="Bloc A",
            level=1,
            start=0,
            end=36,
            content=text[:36],
        ),
        ParsedSection(
            section_id="beta",
            title="Bloc B",
            level=1,
            start=36,
            end=len(text),
            content=text[36:],
        ),
    ]
    service = SemanticRoutingService()
    monkeypatch.setattr(service, "_fastjudge", lambda rows: {})
    monkeypatch.setattr(
        service,
        "_semantic_functions",
        lambda rows: {
            "alpha": {
                "function": SectionFunction.SCIENTIFIC_LANDSCAPE,
                "confidence": 0.91,
                "classifier": "semantic_zero_shot",
            },
            "beta": {
                "function": SectionFunction.METHOD,
                "confidence": 0.88,
                "classifier": "semantic_zero_shot",
            },
        },
    )

    decision = service.route(
        "Améliore le dossier complet sans inventer d'information.",
        TargetScope.FULL_DOCUMENT,
        text,
        sections=sections,
    )

    plans = {item.section_id: item for item in decision.section_plan}
    assert plans["alpha"].route == SpecialistRoute.SCHOLAR
    assert plans["beta"].route == SpecialistRoute.DIAGNOSTIC


def test_project_only_policy_overrides_scientific_section_fallback(monkeypatch):
    section = ParsedSection(
        section_id="alpha",
        title="Travaux antérieurs",
        level=1,
        start=0,
        end=27,
        content="Comparaison scientifique.",
    )
    service = SemanticRoutingService()
    monkeypatch.setattr(service, "_fastjudge", lambda rows: {})
    monkeypatch.setattr(
        service,
        "_semantic_functions",
        lambda rows: {
            "alpha": {
                "function": SectionFunction.SCIENTIFIC_LANDSCAPE,
                "confidence": 0.95,
                "classifier": "semantic_zero_shot",
            }
        },
    )

    decision = service.route(
        "Améliore le CIR uniquement avec les preuves disponibles dans le projet.",
        TargetScope.FULL_DOCUMENT,
        section.content,
        sections=[section],
    )

    assert decision.forbids_scholar
    assert not decision.needs_scholar
    assert decision.section_plan[0].route != SpecialistRoute.SCHOLAR


def test_style_memory_can_never_authorize_a_new_fact():
    routing = understand_instruction("Rends le texte plus professionnel.", TargetScope.SECTION)
    trace = build_revision_trace(
        "Le protocole a été évalué.",
        "Le protocole a été évalué avec une précision de 97 %.",
        routing,
        {
            "cir_style": {
                "available": True,
                "fact_eligible": False,
                "style_profile": {"example": "Une ancienne étude atteignait 97 %."},
            }
        },
    )
    assert not trace["blocking"]
    assert trace["has_warnings"]
    assert "97%" in trace["unsupported_claims"][0]["markers"]


def test_project_evidence_can_authorize_an_exact_new_measure():
    routing = understand_instruction("Renforce l'argumentation.", TargetScope.SECTION)
    trace = build_revision_trace(
        "Le protocole a été évalué.",
        "Le protocole a été évalué avec une précision mesurée de 97 %.",
        routing,
        {
            "diagnostic": {
                "available": True,
                "evidence_items": [
                    {
                        "evidence_id": "D:section:resultats",
                        "type": "diagnostic_section",
                        "text": "La précision mesurée et documentée est de 97 %.",
                        "fact_eligible": True,
                    }
                ],
            },
            "cir_style": {"available": False},
        },
    )
    assert not trace["blocking"]
    assert "D:section:resultats" in {
        reference
        for change in trace["changes"]
        for reference in change["evidence_refs"]
    }
