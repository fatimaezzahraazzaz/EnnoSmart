from __future__ import annotations

from types import SimpleNamespace

from agents.EnnoAmelioration.application.intention_service import understand_instruction
from agents.EnnoAmelioration.application.research_context_service import (
    build_lightweight_research_context,
)
from agents.EnnoAmelioration.application.research_orchestration_service import (
    RESEARCH_LAUNCH_TARGETED,
    detect_research_choice,
)
from agents.EnnoAmelioration.application.semantic_routing_service import (
    SemanticRoutingService,
)
from agents.EnnoAmelioration.application.section_parser import parse_sections
from agents.EnnoAmelioration.domain.models import (
    ImprovementIntent,
    ImprovementRequest,
    ImprovementState,
    RoutingDecision,
    SectionFunction,
    SectionRoutingPlan,
    SpecialistRoute,
    TargetScope,
)
from agents.EnnoScholar.paper_ranker import score_paper
from agents.EnnoScholar.query_builder import (
    attach_queries_to_intent,
    select_best_queries_for_intent,
)
from agents.EnnoScholar.scientific_intent_builder import (
    build_scientific_intent,
    source_passages,
)
from services import improvement_service


def _request(function: SectionFunction, text: str | None = None) -> ImprovementRequest:
    target = text or (
        "Les images SAR synthetiques sont comparees aux mesures radar reelles "
        "pour evaluer la generalisation des modeles ATR de detection de cibles."
    )
    return ImprovementRequest(
        instruction="Cherche des articles pertinents pour renforcer cette section.",
        full_text=target,
        target_text=target,
        target_scope=TargetScope.SECTION,
        target_section_id="1.2",
        target_section_title="Analyse scientifique",
        project_name="Projet radar",
        project_domain="Imagerie radar",
        research_target_type=function.value,
    )


def _semantic_decision(function: SectionFunction, instruction: str):
    service = SemanticRoutingService()
    service._fastjudge = lambda rows: {
        "target": {
            "function": function,
            "confidence": 0.96,
            "classifier": "test",
        }
    }
    service._semantic_functions = lambda rows: {}
    return service.route(
        instruction,
        TargetScope.SECTION,
        _request(function).target_text,
    )


def test_natural_request_searching_articles_is_understood_as_explicit_research():
    instruction = (
        "Cette section est faible. Renforce-la avec des arguments et des "
        "justifications plus solides en cherchant des articles pertinents."
    )
    decision = understand_instruction(instruction, TargetScope.SECTION)

    assert ImprovementIntent.ARGUMENTATION in decision.intents
    assert ImprovementIntent.SCIENTIFIC_ENRICHMENT in decision.intents
    assert ImprovementIntent.RESEARCH in decision.intents
    assert decision.needs_new_research
    assert detect_research_choice(instruction) == RESEARCH_LAUNCH_TARGETED


def test_publications_can_strengthen_a_lock_without_implicitly_starting_research():
    decision = understand_instruction(
        "Rends ce verrou plus convaincant avec des publications scientifiques.",
        TargetScope.SECTION,
    )

    assert ImprovementIntent.ARGUMENTATION in decision.intents
    assert ImprovementIntent.SCIENTIFIC_ENRICHMENT in decision.intents
    assert ImprovementIntent.RESEARCH not in decision.intents
    assert decision.needs_scholar
    assert not decision.needs_new_research


def test_each_section_role_gets_a_specific_research_target():
    expected = {
        SectionFunction.CONTEXT: "context_enrichment",
        SectionFunction.SCIENTIFIC_LANDSCAPE: "scientific_landscape",
        SectionFunction.UNCERTAINTY: "lock_search",
        SectionFunction.METHOD: "method_search",
        SectionFunction.PARAMETER: "parameter_search",
        SectionFunction.RESULT: "result_interpretation",
        SectionFunction.LIMITATION: "limitation_search",
        SectionFunction.CONTRIBUTION: "contribution_positioning",
        SectionFunction.SYNTHESIS: "scientific_synthesis",
        SectionFunction.OTHER: "scientific_enrichment",
    }

    for function, target_type in expected.items():
        context = build_lightweight_research_context(
            SimpleNamespace(domain_label="Imagerie radar"),
            _request(function),
        )
        assert context["research_target_type"] == target_type
        assert context["search_strategy"]["evidence_axes"]
        assert context["search_readiness"]["ready"] is True


def test_only_a_true_lock_requires_diagnostic_before_scholar():
    instruction = "Renforce cette section et cherche des publications scientifiques pertinentes."

    context = _semantic_decision(SectionFunction.CONTEXT, instruction)
    limitation = _semantic_decision(SectionFunction.LIMITATION, instruction)
    lock = _semantic_decision(SectionFunction.UNCERTAINTY, instruction)

    assert context.needs_scholar and not context.needs_diagnostic
    assert limitation.needs_scholar and not limitation.needs_diagnostic
    assert lock.needs_scholar and lock.needs_diagnostic


def test_sparse_section_is_not_considered_ready_for_broad_research():
    context = build_lightweight_research_context(
        SimpleNamespace(domain_label=""),
        _request(SectionFunction.OTHER, text="Le seuil a ete ajuste."),
    )

    assert context["search_readiness"]["ready"] is False
    assert context["search_readiness"]["final_keyword_builder"].endswith(
        "scientific_intent_builder"
    )


def test_full_cir_research_is_split_into_real_section_targets():
    text = (
        "1.1. Contexte scientifique\n"
        "Le systeme QZX traite des observations mesurees dans plusieurs conditions.\n\n"
        "1.2. Incertitude de transfert\n"
        "Les representations QZX apprises sur les simulations varient lors du "
        "passage aux observations mesurees QZX.\n\n"
        "1.3. Methode experimentale\n"
        "Le protocole compare plusieurs configurations QZX et analyse leurs ecarts."
    )
    sections = parse_sections(text)
    functions = [
        SectionFunction.CONTEXT,
        SectionFunction.UNCERTAINTY,
        SectionFunction.METHOD,
    ]
    plans = [
        SectionRoutingPlan(
            section_id=section.section_id,
            title=section.title,
            function=function,
            route=SpecialistRoute.SCHOLAR,
            needs_scholar=True,
        )
        for section, function in zip(sections, functions)
    ]
    request = ImprovementRequest(
        instruction="Renforce le CIR complet et cherche des publications pertinentes.",
        full_text=text,
        target_text=text,
        target_scope=TargetScope.FULL_DOCUMENT,
        project_domain="Ingenierie",
        research_section_plan=plans,
        sections=sections,
    )

    context = build_lightweight_research_context(
        SimpleNamespace(domain_label="Ingenierie"),
        request,
    )

    assert context["mode"] == "direct_multi_section_scholar_without_mandatory_diagnostic"
    assert context["diagnostic_required"] is False
    assert len(context["research_targets"]) == 3
    assert set(context["research_target_ids"]) == {
        section.section_id for section in sections
    }
    assert all(target["text"] != text for target in context["research_targets"])
    assert context["search_readiness"]["source"] == (
        "semantic_section_plan_and_current_document_only"
    )


def test_scholar_queries_use_anchors_derived_from_the_current_section():
    target = (
        "Incertitude sur la transferabilite des representations apprises entre "
        "donnees SAR synthetiques et donnees SAR mesurees. "
        "Un modele entraine sur des donnees SAR synthetiques peut perdre sa "
        "capacite de generalisation lorsqu'il est applique a des observations "
        "SAR mesurees. La distribution des donnees et les representations "
        "apprises peuvent alors evoluer entre les deux domaines."
    )
    context = build_lightweight_research_context(
        SimpleNamespace(domain_label="Imagerie radar"),
        _request(SectionFunction.UNCERTAINTY, text=target),
    )
    target = context["research_targets"][0]
    intent = build_scientific_intent(
        target,
        domain_detection=context["domain_detection"],
        diagnostic_context=context["research_context"],
    )
    enriched = attach_queries_to_intent(intent)
    selected = select_best_queries_for_intent(
        enriched["search_queries"], enriched, max_queries=3
    )

    assert intent["literal_source_acronyms"] == ["SAR"]
    assert intent["literal_source_phrases"]
    assert selected
    assert all("SAR" in row["query"] for row in selected)
    assert any(
        "transferabilite" in row["query"] or "sim to real" in row["query"]
        for row in selected
    )


def test_full_section_is_preserved_before_scientific_intent_extraction():
    target = (
        "Titre technique avec l'acronyme QZX. "
        + "Le passage contient des observations experimentales detaillees. " * 12
        + "La difficulte discriminante et la generalisation QZX apparaissent a la fin."
    )
    context = build_lightweight_research_context(
        SimpleNamespace(domain_label="Domaine projet"),
        _request(SectionFunction.UNCERTAINTY, text=target),
    )

    passages = source_passages(context["research_targets"][0])

    assert len(passages[0]) > 500
    assert "apparaissent a la fin" in passages[0]


def test_repeated_literal_anchor_rejects_generic_articles_without_domain_dictionary():
    target = (
        "Dans ce passage, SAR designe le synthetic aperture radar. "
        "La transferabilite entre donnees SAR synthetiques et donnees SAR mesurees "
        "reste incertaine. Les representations apprises sur les donnees SAR "
        "synthetiques peuvent subir un ecart de distribution lors de l'evaluation SAR."
    )
    context = build_lightweight_research_context(
        SimpleNamespace(domain_label="Imagerie radar"),
        _request(SectionFunction.UNCERTAINTY, text=target),
    )
    intent = build_scientific_intent(
        context["research_targets"][0],
        domain_detection=context["domain_detection"],
        diagnostic_context=context["research_context"],
    )

    generic = score_paper(
        {
            "title": "Condition Monitoring of Ball Bearings with Synthetic Data",
            "abstract": (
                "Synthetic training data from simulations are validated on measured "
                "bearing vibration signals."
            ),
        },
        intent,
    )
    aligned = score_paper(
        {
            "title": "Synthetic-to-real domain adaptation for SAR target recognition",
            "abstract": (
                "The study transfers representations learned from synthetic SAR "
                "training data to measured SAR imagery under domain shift."
            ),
        },
        intent,
    )
    expanded_identity = score_paper(
        {
            "title": "Synthetic-to-real transfer for synthetic aperture radar",
            "abstract": (
                "Representations learned from synthetic training data are evaluated "
                "on real measurements under a distribution shift."
            ),
        },
        intent,
    )

    assert generic["tag"] == "Hors sujet"
    assert generic["score_details"]["literal_source_acronym_hits"] == []
    assert aligned["tag"] in {"Direct", "Connexe"}
    assert aligned["score_details"]["literal_source_acronym_hits"] == ["SAR"]
    assert expanded_identity["tag"] in {"Direct", "Connexe"}
    assert "synthetic aperture radar" in expanded_identity["score_details"][
        "literal_source_identity_hits"
    ]


def test_write_after_source_validation_does_not_restart_research_or_diagnostic(monkeypatch):
    from agents.EnnoAmelioration.application import agent as agent_module
    from agents.EnnoAmelioration.application.agent import EnnoAmeliorationAgent

    initial_routing = RoutingDecision(
        intents=[
            ImprovementIntent.CLARITY,
            ImprovementIntent.STYLE,
            ImprovementIntent.ARGUMENTATION,
            ImprovementIntent.SCIENTIFIC_ENRICHMENT,
        ],
        target_scope=TargetScope.SECTION,
        needs_diagnostic=True,
        needs_scholar=True,
        needs_new_research=False,
        needs_project_evidence=True,
        specialist_route=SpecialistRoute.DIAGNOSTIC_SCHOLAR,
        section_function=SectionFunction.UNCERTAINTY,
    )

    class FixedRouting:
        @staticmethod
        def route(*args, **kwargs):
            return initial_routing

    class RecordingWriter:
        llm = None

        def __init__(self):
            self.calls = 0

        def rewrite(self, request, routing, audit, evidence):
            self.calls += 1
            assert routing.needs_diagnostic is False
            assert routing.needs_scholar is True
            assert routing.needs_new_research is False
            assert routing.forbids_new_research is True
            assert (evidence.get("scholar") or {}).get("available") is True
            return request.target_text + "\n\nVersion renforcee [A1].", {
                "provider": "fake",
                "model": "test",
            }

    def evidence_package(db, project, request, routing):
        assert routing.needs_diagnostic is False
        assert routing.needs_project_evidence is False
        return {
            "scholar": {
                "available": True,
                "evidence_items": [
                    {
                        "evidence_id": "A1",
                        "citation_id": "A1",
                        "article_id": 101,
                        "title": "Publication validee",
                    }
                ],
            },
            "gaps": [],
        }

    writer = RecordingWriter()
    monkeypatch.setattr(
        EnnoAmeliorationAgent,
        "_evidence_package",
        staticmethod(evidence_package),
    )
    monkeypatch.setattr(
        agent_module,
        "launch_targeted_guided_research",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Aucune nouvelle recherche ne doit etre lancee")
        ),
    )

    target = "Une incertitude subsiste entre les donnees synthetiques et mesurees."
    request = ImprovementRequest(
        instruction=(
            "Redige maintenant une version renforcee avec les sources deja "
            "selectionnees."
        ),
        full_text=target,
        target_text=target,
        target_scope=TargetScope.SECTION,
        target_section_id="1.3.2.2",
        target_section_title="Incertitude scientifique",
        evidence_article_ids=[101],
        evidence_scope_id="guided-current",
    )
    result = EnnoAmeliorationAgent(
        writer=writer,
        routing_service=FixedRouting(),
    ).improve(object(), SimpleNamespace(id=1), request)

    assert result.ok is True
    assert result.state == ImprovementState.CANDIDATE_READY
    assert writer.calls >= 1
    assert "EnnoDiagnostic" not in result.agents_used
    assert result.routing.specialist_route == SpecialistRoute.SCHOLAR


def test_internal_scholar_result_is_projected_into_agent3_without_frontend_handoff():
    result = SimpleNamespace(
        research={
            "session_id": "guided-internal-1",
            "state": "waiting_consultant_feedback",
            "engine": "ennoscholar_core",
            "research_mode": "direct_typed_research_target",
            "candidates": [
                {
                    "candidate_id": "article-1",
                    "title": "Comparable experimental study",
                    "authors": ["A. Auteur"],
                    "year": 2024,
                    "doi": "10.1/example",
                    "source_providers": ["OpenAlex"],
                    "role_reason": "La methode est directement comparable.",
                    "consultant_decision": "proposed",
                }
            ],
        }
    )

    handoff = improvement_service._research_handoff_from_agent_result(
        result,
        SimpleNamespace(id="improvement-1"),
    )

    assert handoff is not None
    assert handoff["frontend_owner"] == "ennoamelioration"
    assert handoff["writing_owner"] == "ennoamelioration"
    assert handoff["guided_session_id"] == "guided-internal-1"
    assert handoff["sources"][0]["provider"] == "OpenAlex"
    assert handoff["sources"][0]["reason"].startswith("La methode")
