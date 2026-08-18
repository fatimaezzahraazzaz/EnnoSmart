from types import SimpleNamespace

from agents.EnnoAmelioration.application.auto_evidence_selector_v320 import (
    bind_prepared_sources,
    build_traceable_evidence,
    select_sources,
)
from agents.EnnoAmelioration.application.cir_section_progressive_v320 import (
    annotate_units,
    build_workflow,
    refresh_pending_units_for_current_policy,
    split_document_into_units,
    unit_is_title_only,
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


class FakeLLM:
    def generate(self, *args, **kwargs):
        return """{
          "decisions": [
            {
              "candidate_id": "C1",
              "decision": "select",
              "relevance": "direct",
              "reason": "La source traite directement le décalage de domaine décrit dans la section.",
              "supported_need": "Justifier la difficulté de généralisation entre données synthétiques et mesurées.",
              "evidence_hint": "The model struggles with domain shift between synthetic and measured SAR data."
            },
            {
              "candidate_id": "C2",
              "decision": "reject",
              "relevance": "partial",
              "reason": "Le thème SAR est proche mais la preuve ne répond pas au besoin précis.",
              "supported_need": "",
              "evidence_hint": ""
            }
          ]
        }"""

    def get_last_generation_meta(self):
        return {"model": "fake"}


class FakeGuidedCandidateLLM(FakeLLM):
    def generate(self, *args, **kwargs):
        return super().generate(*args, **kwargs).replace(
            '"candidate_id": "C1"',
            '"candidate_id": "guided-C42"',
        )


def test_heading_words_do_not_turn_an_empty_section_into_content():
    text = """1.3. État de l'art et verrous

1.3.1. Verrou scientifique

Cette sous-section contient un corps réel et doit rester traitable.
"""
    units = split_document_into_units(
        text,
        parse_sections(text),
    )
    heading = next(
        row
        for row in units
        if row["section_title"] == "État de l'art et verrous"
    )
    assert heading["source_total_words"] > 5
    assert heading["source_words"] == 0
    assert heading["title_only"] is True
    assert unit_is_title_only(text, heading) is True


def test_runtime_title_guard_repairs_a_legacy_v320_unit():
    text = "1.3. Etat de l’art et verrous\n\n"
    legacy = {
        "start": 0,
        "end": len(text),
        "section_title": "Etat de l’art et verrous",
        "source_words": 7,
        "title_only": False,
        "action": "rewrite",
    }
    assert unit_is_title_only(text, legacy) is True


def _routing_for_section(
    section_id: str,
    *,
    function: SectionFunction,
    confidence: float = 0.92,
    scholar: bool = False,
) -> RoutingDecision:
    plan = SectionRoutingPlan(
        section_id=section_id,
        title="Section scientifique",
        function=function,
        confidence=confidence,
        classifier="test",
        route=(
            SpecialistRoute.SCHOLAR
            if scholar
            else SpecialistRoute.DIAGNOSTIC
        ),
        needs_diagnostic=not scholar,
        needs_scholar=scholar,
    )
    return RoutingDecision(
        intents=[ImprovementIntent.SCIENTIFIC_ENRICHMENT],
        target_scope=TargetScope.FULL_DOCUMENT,
        needs_diagnostic=not scholar,
        needs_scholar=scholar,
        specialist_route=plan.route,
        section_plan=[plan],
    )


def test_weak_state_of_art_routes_directly_to_scholar_even_on_legacy_diagnostic_plan():
    text = """1.3. État de l'art

Les travaux existants décrivent des modèles radar et des jeux de données pour
l'apprentissage supervisé. Plusieurs approches utilisent des représentations
profondes afin de reconnaître les cibles. Les publications présentent aussi
des architectures adaptées aux images complexes et aux variations de mesure.
Ces familles de méthodes sont citées successivement dans la littérature mais
le passage courant ne compare pas leurs conditions de validité, leurs limites,
leur transférabilité ni les preuves expérimentales qui permettraient de
justifier le choix technique retenu dans le projet. Cette argumentation reste
donc insuffisamment étayée pour situer précisément le travail réalisé.
"""
    sections = parse_sections(text)
    units = split_document_into_units(text, sections)
    routing = _routing_for_section(
        sections[0].section_id,
        function=SectionFunction.SCIENTIFIC_LANDSCAPE,
        scholar=False,
    )
    annotate_units(text, units, routing)
    assert units[0]["action"] == "research"
    assert units[0]["needs_research"] is True
    assert units[0]["section_route"] == "scholar"
    assert "automatic_scholar_target" in units[0]["weakness_reasons"]
    assert units[0]["diagnostic_policy"] == "reuse_initial_cache_only"


def test_only_low_confidence_section_allows_scoped_diagnostic():
    text = "1.4. Section indéterminée\n\n" + ("contenu scientifique " * 80)
    sections = parse_sections(text)
    units = split_document_into_units(text, sections)
    routing = _routing_for_section(
        sections[0].section_id,
        function=SectionFunction.OTHER,
        confidence=0.31,
        scholar=False,
    )
    annotate_units(text, units, routing)
    assert units[0]["diagnostic_ambiguous"] is True
    assert units[0]["diagnostic_policy"] == "scoped_only_if_needed"


def test_existing_workflow_upgrades_only_pending_units_to_direct_scholar_policy():
    text = "1.3. État de l'art\n\n" + ("travaux scientifiques existants " * 90)
    sections = parse_sections(text)
    routing = _routing_for_section(
        sections[0].section_id,
        function=SectionFunction.SCIENTIFIC_LANDSCAPE,
        scholar=False,
    )
    workflow = build_workflow(
        base_text=text,
        base_version_id="v1",
        base_version_number=1,
        instruction="amélioration et renforcement scientifique",
        sections=sections,
        routing=routing,
    )
    workflow.pop("research_policy_version", None)
    workflow["units"][0]["action"] = "rewrite"
    assert refresh_pending_units_for_current_policy(workflow, text) is True
    assert workflow["units"][0]["action"] == "research"
    assert refresh_pending_units_for_current_policy(workflow, text) is False


def test_probabilistic_legacy_scholar_forbid_does_not_override_actual_cir_instruction():
    text = "1.3. État de l'art\n\n" + ("travaux scientifiques existants " * 90)
    sections = parse_sections(text)
    routing = _routing_for_section(
        sections[0].section_id,
        function=SectionFunction.SCIENTIFIC_LANDSCAPE,
        scholar=False,
    ).model_copy(
        update={
            "forbids_scholar": True,
            "forbids_new_research": True,
        }
    )
    workflow = build_workflow(
        base_text=text,
        base_version_id="v1",
        base_version_number=1,
        instruction=(
            "Voici le CIR complet, je veux améliorer le style et renforcer "
            "les arguments des sections faibles."
        ),
        sections=sections,
        routing=routing,
    )
    assert workflow["units"][0]["action"] == "research"


def test_auto_selector_selects_only_direct_source():
    sources = [
        {
            "article_id": 10,
            "title": "Domain generalized SAR ATR",
            "abstract": (
                "Synthetic SAR data cause domain shift when applied to measured "
                "data with different clutter distributions."
            ),
            "tag": "Direct",
            "score": 0.91,
        },
        {
            "article_id": 11,
            "title": "Generic radar review",
            "abstract": "This article reviews radar signal processing.",
            "tag": "Connexe",
            "score": 0.88,
        },
    ]
    result = select_sources(
        section_text=(
            "Les classifieurs entraînés sur des images SAR synthétiques "
            "généralisent mal aux mesures réelles."
        ),
        section_title="État de l'art",
        weakness_reasons=["scientific_source_gap"],
        candidate_sources=sources,
        max_selected=3,
        llm=FakeLLM(),
    )
    assert result["selected_article_ids"] == [10]
    assert result["decision_mode"] == "llm_semantic"


def test_auto_selector_can_select_guided_candidate_before_article_creation():
    result = select_sources(
        section_text="Texte scientifique suffisamment détaillé.",
        section_title="Section",
        weakness_reasons=[],
        candidate_sources=[
            {
                "candidate_id": "guided-C42",
                "title": "Paper",
                "abstract": "Scientific abstract.",
                "tag": "Direct",
                "score": 0.9,
            }
        ],
        llm=FakeGuidedCandidateLLM(),
    )
    assert result["selected_article_ids"] == []
    assert result["selected_candidate_ids"] == ["guided-C42"]
    assert result["selected"][0]["article_id"] is None


def test_prepared_article_id_is_bound_back_to_guided_selection():
    selection = {
        "selected_article_ids": [],
        "selected_candidate_ids": ["guided-C42"],
        "selected": [
            {
                "candidate_id": "guided-C42",
                "article_id": None,
                "title": "Paper",
                "reason": "direct",
            }
        ],
    }
    bound = bind_prepared_sources(
        selection=selection,
        prepared_sources=[
            {
                "candidate_id": "guided-C42",
                "article_id": 15760,
                "title": "Paper",
                "article_card_ready": True,
                "fulltext_status": "valid_with_warnings",
            }
        ],
    )
    assert bound["selected_article_ids"] == [15760]
    assert bound["selected"][0]["article_id"] == 15760
    assert bound["prepared_binding"]["bound_count"] == 1


def test_bound_guided_selection_survives_final_traceability_check():
    selection = bind_prepared_sources(
        selection={
            "selected_candidate_ids": ["guided-C42"],
            "selected": [
                {
                    "candidate_id": "guided-C42",
                    "article_id": None,
                    "title": "Paper",
                }
            ],
        },
        prepared_sources=[
            {
                "candidate_id": "guided-C42",
                "article_id": 15760,
                "title": "Paper",
                "article_card_ready": True,
            }
        ],
    )
    result = SimpleNamespace(
        evidence={
            "scholar": {
                "evidence": [
                    {
                        "article_id": 15760,
                        "citation_id": "A1",
                        "title": "Paper",
                        "evidence_text": "Extracted scientific evidence.",
                    }
                ]
            }
        },
        sources_used=[{"article_id": 15760, "title": "Paper"}],
    )
    trace = build_traceable_evidence(result=result, selection=selection)
    assert trace["writing_ready_count"] == 1
    assert trace["auto_accepted_article_ids"] == [15760]


def test_final_acceptance_requires_traceable_evidence():
    result = SimpleNamespace(
        evidence={
            "scholar": {
                "evidence": [
                    {
                        "article_id": 10,
                        "citation_id": "A1",
                        "title": "Paper A",
                        "evidence_text": "Exact evidence extracted from the article.",
                    }
                ]
            }
        },
        sources_used=[
            {
                "article_id": 10,
                "title": "Paper A",
            }
        ],
    )
    selection = {
        "selected": [
            {
                "article_id": 10,
                "title": "Paper A",
                "year": 2024,
                "reason": "direct",
                "supported_need": "need",
                "evidence_hint": "hint",
            }
        ]
    }
    trace = build_traceable_evidence(
        result=result,
        selection=selection,
    )
    assert trace["writing_ready_count"] == 1
    assert trace["auto_accepted_article_ids"] == [10]
    assert trace["auto_accepted"][0]["evidence_excerpt"].startswith("Exact evidence")


def test_source_without_evidence_is_not_finally_accepted():
    result = SimpleNamespace(
        evidence={"scholar": {"evidence": []}},
        sources_used=[],
    )
    selection = {
        "selected": [
            {
                "article_id": 10,
                "title": "Paper A",
            }
        ]
    }
    trace = build_traceable_evidence(
        result=result,
        selection=selection,
    )
    assert trace["writing_ready_count"] == 0
    assert trace["prepared_source_count"] == 1
    assert trace["advisory_sources"][0]["article_id"] == 10
    assert trace["rejected_after_evidence"][0]["final_decision"] == "reject"
