from types import SimpleNamespace

from agents.EnnoAmelioration.application.cir_progressive_workflow_v311 import (
    WORKFLOW_VERSION,
    add_patch,
    apply_patches,
    annotate_units,
    build_workflow,
    split_document_into_units,
)
from agents.EnnoAmelioration.application.section_parser import parse_sections
from agents.EnnoAmelioration.domain.models import (
    ImprovementIntent,
    RoutingDecision,
    SectionFunction,
    SectionRoutingPlan,
    SpecialistRoute,
    TargetScope,
)

TEXT = """1. Etat de l'art

Les méthodes existantes sont largement utilisées pour traiter ce problème, mais ce paragraphe ne relie aucune affirmation à une publication scientifique identifiable et reste difficile à défendre dans un état de l'art. Il évoque aussi plusieurs performances, conditions expérimentales et limites de généralisation sans indiquer quelles publications établissent réellement ces constats ni dans quelles conditions ils ont été observés.

Ce second paragraphe est court, clair et décrit uniquement une transition éditoriale sans nouvelle affirmation scientifique nécessitant une preuve externe.

1.1. Méthode

La démarche suit trois étapes clairement décrites. Elle commence par la préparation des données, puis applique le traitement prévu et termine par l'évaluation des résultats obtenus selon le protocole déjà défini.
"""


def _routing():
    sections = parse_sections(TEXT)
    plans = []
    for section in sections:
        scientific = "Etat de l'art" in section.title
        plans.append(
            SectionRoutingPlan(
                section_id=section.section_id,
                title=section.title,
                function=(
                    SectionFunction.SCIENTIFIC_LANDSCAPE
                    if scientific
                    else SectionFunction.METHOD
                ),
                confidence=0.99,
                classifier="test",
                route=SpecialistRoute.SCHOLAR if scientific else SpecialistRoute.WRITER,
                needs_scholar=scientific,
                needs_diagnostic=False,
            )
        )
    return RoutingDecision(
        intents=[
            ImprovementIntent.GENERAL_REVISION,
            ImprovementIntent.SCIENTIFIC_ENRICHMENT,
            ImprovementIntent.STYLE,
        ],
        target_scope=TargetScope.FULL_DOCUMENT,
        needs_scholar=True,
        needs_new_research=True,
        specialist_route=SpecialistRoute.SCHOLAR,
        section_function=SectionFunction.OTHER,
        section_plan=plans,
    )


def test_split_keeps_section_titles_outside_editable_units():
    sections = parse_sections(TEXT)
    units = split_document_into_units(TEXT, sections)
    assert units
    for unit in units:
        paragraph = TEXT[unit["start"]:unit["end"]]
        assert not paragraph.lstrip().startswith("1. Etat de l'art")
        assert not paragraph.lstrip().startswith("1.1. Méthode")


def test_scientific_weak_paragraph_gets_research_but_method_does_not():
    sections = parse_sections(TEXT)
    units = split_document_into_units(TEXT, sections)
    annotate_units(TEXT, units, _routing())
    state_of_art = [u for u in units if "Etat de l'art" in u["section_title"]]
    method = [u for u in units if u["section_title"] == "Méthode"]
    assert any(u["action"] == "research" for u in state_of_art)
    assert all(u["action"] != "research" for u in method)


def test_active_source_is_immutable_and_patches_reconstruct_draft():
    original = str(TEXT)
    sections = parse_sections(TEXT)
    workflow = build_workflow(
        base_text=TEXT,
        base_version_id="v-active",
        base_version_number=4,
        instruction="Améliore et renforce le CIR complet.",
        sections=sections,
        routing=_routing(),
    )
    unit = workflow["units"][0]
    replacement = TEXT[unit["start"]:unit["end"]] + " Ajout validé [A1]."
    add_patch(workflow, unit, replacement, mode="scientific")
    draft = apply_patches(TEXT, workflow["patches"])
    assert TEXT == original
    assert draft != original
    assert "Ajout validé [A1]." in draft
    assert workflow["version"] == WORKFLOW_VERSION


def test_each_research_unit_has_its_own_research_slot():
    workflow = build_workflow(
        base_text=TEXT,
        base_version_id="v-active",
        base_version_number=4,
        instruction="Améliore et renforce le CIR complet.",
        sections=parse_sections(TEXT),
        routing=_routing(),
    )
    research_units = [row for row in workflow["units"] if row["action"] == "research"]
    assert research_units
    for row in research_units:
        assert row["research"] == {}
        assert "unit_id" in row
        assert "source_sha256" in row


def test_strong_blocks_can_remain_unchanged():
    routing = _routing().model_copy(
        update={
            "intents": [ImprovementIntent.GENERAL_REVISION],
            "needs_scholar": False,
            "needs_new_research": False,
            "section_plan": [
                plan.model_copy(update={"needs_scholar": False, "route": SpecialistRoute.WRITER})
                for plan in _routing().section_plan
            ],
        }
    )
    units = split_document_into_units(TEXT, parse_sections(TEXT))
    annotate_units(TEXT, units, routing)
    assert any(row["action"] == "keep" for row in units)
