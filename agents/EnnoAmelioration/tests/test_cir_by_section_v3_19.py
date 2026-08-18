from types import SimpleNamespace

import agents.EnnoAmelioration.application.cir_section_progressive_v319 as workflow
from agents.EnnoAmelioration.application.section_parser import parse_sections
from agents.EnnoAmelioration.domain.models import (
    ImprovementIntent,
    RoutingDecision,
    SectionFunction,
    SectionRoutingPlan,
    SpecialistRoute,
    TargetScope,
)


TEXT = """1. Intitulé

Projet de recherche.

1.1. État de l'art

Les travaux existants décrivent plusieurs approches scientifiques et techniques dans ce domaine. Cette section présente les principales méthodes rapportées dans la littérature, leurs hypothèses, leurs conditions d'utilisation, leurs limites expérimentales et les difficultés de généralisation qui restent observées. Elle expose également les différences entre les approches et les besoins de validation qui demeurent ouverts. Afin de disposer d'un passage suffisamment long pour le test du moteur de section, ce texte complète la description avec plusieurs éléments argumentatifs génériques sans citation bibliographique directement attachée au contenu.

1.2. Méthode

La méthode proposée suit plusieurs étapes clairement décrites et conserve les paramètres, résultats et conditions déjà documentés dans le dossier.
"""


def _routing(sections):
    plans = []
    for section in sections:
        if "État de l'art" in section.title:
            function = SectionFunction.SCIENTIFIC_LANDSCAPE
            scholar = True
            route = SpecialistRoute.SCHOLAR
        elif "Intitulé" in section.title:
            function = SectionFunction.CONTEXT
            scholar = False
            route = SpecialistRoute.WRITER
        else:
            function = SectionFunction.METHOD
            scholar = False
            route = SpecialistRoute.WRITER
        plans.append(
            SectionRoutingPlan(
                section_id=section.section_id,
                title=section.title,
                function=function,
                confidence=0.99,
                classifier="test",
                route=route,
                needs_scholar=scholar,
                needs_diagnostic=False,
            )
        )
    return RoutingDecision(
        intents=[
            ImprovementIntent.STYLE,
            ImprovementIntent.ARGUMENTATION,
            ImprovementIntent.SCIENTIFIC_ENRICHMENT,
        ],
        target_scope=TargetScope.FULL_DOCUMENT,
        needs_scholar=True,
        needs_new_research=True,
        specialist_route=SpecialistRoute.SCHOLAR,
        section_function=SectionFunction.OTHER,
        section_plan=plans,
    )


def test_full_cir_units_are_sections_not_paragraphs():
    sections = parse_sections(TEXT)
    units = workflow.split_document_into_units(TEXT, sections)
    assert len(units) == len(sections)
    assert all(row["unit_id"].startswith("sec-") for row in units)
    assert all("paragraph_in_section" not in row for row in units)


def test_each_section_span_is_non_overlapping():
    units = workflow.split_document_into_units(TEXT, parse_sections(TEXT))
    spans = [(row["start"], row["end"]) for row in units]
    assert spans == sorted(spans)
    for left, right in zip(spans, spans[1:]):
        assert left[1] <= right[0]


def test_short_context_never_launches_research(monkeypatch):
    monkeypatch.setattr(workflow, "audit_text", lambda *_args, **_kwargs: [])
    sections = parse_sections(TEXT)
    units = workflow.split_document_into_units(TEXT, sections)
    workflow.annotate_units(TEXT, units, _routing(sections))
    context = next(row for row in units if "Intitulé" in row["section_title"])
    assert context["action"] != "research"


def test_scientific_section_without_sources_launches_section_research(monkeypatch):
    monkeypatch.setattr(workflow, "audit_text", lambda *_args, **_kwargs: [])
    sections = parse_sections(TEXT)
    units = workflow.split_document_into_units(TEXT, sections)
    workflow.annotate_units(TEXT, units, _routing(sections))
    scientific = next(row for row in units if "État de l'art" in row["section_title"])
    assert scientific["action"] == "research"
    assert scientific["needs_research"] is True


def test_section_patch_reconstructs_document_without_mutating_base():
    sections = parse_sections(TEXT)
    routing = _routing(sections)
    flow = workflow.build_workflow(
        base_text=TEXT,
        base_version_id="active-v1",
        base_version_number=1,
        instruction="Améliore le CIR complet.",
        sections=sections,
        routing=routing,
    )
    unit = flow["units"][1]
    source = workflow.unit_source(TEXT, unit)
    replacement = source + "\nAjout validé [A1]."
    workflow.add_patch(flow, unit, replacement, mode="scientific")
    rebuilt = workflow.apply_patches(TEXT, flow["patches"])
    assert TEXT != rebuilt
    assert "Ajout validé [A1]." in rebuilt
    assert flow["granularity"] == "section"


def test_progress_label_says_section():
    sections = parse_sections(TEXT)
    flow = workflow.build_workflow(
        base_text=TEXT,
        base_version_id="active-v1",
        base_version_number=1,
        instruction="Améliore le CIR complet.",
        sections=sections,
        routing=_routing(sections),
    )
    label = workflow.progress_label(flow, workflow.current_unit(flow))
    assert label.startswith("Section 1/")
    assert "Paragraphe" not in label
