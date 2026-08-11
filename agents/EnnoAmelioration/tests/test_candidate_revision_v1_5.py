from __future__ import annotations

from agents.EnnoAmelioration.application.intention_service import understand_instruction
from agents.EnnoAmelioration.application.semantic_routing_service import SemanticRoutingService
from agents.EnnoAmelioration.domain.models import (
    ImprovementIntent,
    SectionFunction,
    SpecialistRoute,
    TargetScope,
)


FOLLOWUP = """
La proposition est globalement meilleure, mais certaines formulations renforcent ou ajoutent légèrement des informations par rapport au texte source.
Corrige uniquement la proposition actuelle sans repartir de zéro.
Supprime ou reformule toute intensification qui n'est pas strictement justifiée par le texte source.
Conserve en revanche la nouvelle organisation logique.
Corrige également les éventuelles erreurs grammaticales.
N'ajoute aucun nouveau fait, résultat, chiffre ou interprétation.
"""


def test_followup_is_candidate_revision_not_scientific_enrichment():
    decision = understand_instruction(FOLLOWUP, TargetScope.SECTION)
    assert decision.candidate_revision
    assert ImprovementIntent.CANDIDATE_REVISION in decision.intents
    assert ImprovementIntent.SCIENTIFIC_ENRICHMENT not in decision.intents
    assert decision.target_scope == TargetScope.SECTION
    assert decision.forbids_new_research
    assert decision.forbids_scholar
    assert not decision.needs_new_research
    assert not decision.needs_scholar
    assert not decision.needs_diagnostic
    assert not decision.revision_allows_evidence_enrichment


def test_candidate_revision_stays_writer_even_for_scientific_section(monkeypatch):
    service = SemanticRoutingService()
    monkeypatch.setattr(
        service,
        "_fastjudge",
        lambda rows: {
            "target": {
                "function": SectionFunction.SCIENTIFIC_LANDSCAPE,
                "confidence": 0.97,
                "classifier": "test",
            }
        },
    )
    monkeypatch.setattr(service, "_semantic_functions", lambda rows: {})
    decision = service.route(
        FOLLOWUP,
        TargetScope.SECTION,
        "Une proposition scientifique déjà générée.",
    )
    assert decision.candidate_revision
    assert decision.section_function == SectionFunction.SCIENTIFIC_LANDSCAPE
    assert not decision.needs_scholar
    assert not decision.needs_diagnostic
    assert decision.specialist_route == SpecialistRoute.WRITER
    assert decision.section_plan[0].route == SpecialistRoute.WRITER


def test_candidate_revision_can_explicitly_reuse_validated_scientific_sources():
    decision = understand_instruction(
        "Corrige la proposition actuelle et ajoute les références scientifiques validées déjà disponibles.",
        TargetScope.SECTION,
    )
    assert decision.candidate_revision
    assert decision.revision_allows_evidence_enrichment
    assert decision.needs_scholar
    assert not decision.needs_new_research


def test_candidate_revision_can_explicitly_request_new_research(monkeypatch):
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
        "Corrige la proposition actuelle puis recherche de nouveaux articles scientifiques pour étayer ce passage.",
        TargetScope.SECTION,
        "Proposition courante.",
    )
    assert decision.candidate_revision
    assert decision.revision_allows_evidence_enrichment
    assert decision.needs_new_research
    assert decision.needs_scholar
    assert not decision.forbids_new_research


def test_candidate_revision_can_explicitly_use_project_evidence():
    decision = understand_instruction(
        "Corrige la proposition actuelle avec uniquement les preuves du projet déjà disponibles.",
        TargetScope.SECTION,
    )
    assert decision.candidate_revision
    assert decision.revision_allows_evidence_enrichment
    assert decision.needs_diagnostic
    assert not decision.needs_scholar
    assert decision.forbids_scholar


def test_word_globalement_does_not_turn_candidate_followup_into_full_document():
    decision = understand_instruction(
        "La proposition est globalement meilleure. Corrige uniquement la proposition actuelle.",
        TargetScope.SECTION,
    )
    assert decision.candidate_revision
    assert decision.target_scope == TargetScope.SECTION
