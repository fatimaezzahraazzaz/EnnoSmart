from __future__ import annotations

from types import SimpleNamespace

from agents.EnnoAmelioration.application import research_orchestration_service as ros
from agents.EnnoAmelioration.application.research_context_service import (
    build_lightweight_research_context,
)
from agents.EnnoAmelioration.application.semantic_routing_service import (
    SemanticRoutingService,
)
from agents.EnnoAmelioration.domain.models import (
    ImprovementIntent,
    ImprovementRequest,
    ImprovementState,
    RoutingDecision,
    SectionFunction,
    TargetScope,
)
from agents.EnnoScholar.scientific_intent_builder import build_scientific_intent
from backend_api.services.improvement_service import (
    _pasted_section_is_whole_target,
)


def _request(**updates) -> ImprovementRequest:
    values = {
        "instruction": "Cherche de nouvelles publications pour ajouter des arguments scientifiques.",
        "full_text": (
            "Contexte radar général.\n\n"
            "Les données SAR simulées diffèrent des mesures réelles utilisées par les modèles ATR.\n\n"
            "Le protocole compare plusieurs méthodes."
        ),
        "target_text": (
            "Les données SAR simulées diffèrent des mesures réelles utilisées par les modèles ATR."
        ),
        "target_scope": TargetScope.SECTION,
        "target_section_id": "1.3.2",
        "target_section_title": "Données SAR simulées et mesurées",
        "project_name": "Projet radar",
        "research_target_type": SectionFunction.METHOD.value,
    }
    values.update(updates)
    return ImprovementRequest(**values)


def test_lightweight_context_transmits_meaning_and_role_strategy(monkeypatch):
    monkeypatch.setattr(
        "modules.NLP.domain_classifier.classify_domain",
        lambda text: {
            "main_domain_label": "Traitement du signal",
            "sub_domain_label": "Imagerie radar",
            "display_label": "Traitement du signal → Imagerie radar",
            "confidence": 0.82,
        },
    )

    context = build_lightweight_research_context(
        SimpleNamespace(domain_label="Radar"),
        _request(),
    )

    assert context["mode"] == "direct_scholar_without_mandatory_diagnostic"
    assert context["diagnostic_required"] is False
    assert context["keywords_generated_here"] is False
    assert "keywords" not in context
    assert context["search_readiness"]["ready"] is True
    assert context["search_readiness"]["local_anchor_preview"]
    assert context["search_strategy"]["research_target_type"] == "method_search"
    assert context["search_strategy"]["diagnostic_policy"] == "not_required"
    assert context["domain_detection"]["main_domain_label"] == "Traitement du signal"
    target = context["research_targets"][0]
    assert target["research_target_type"] == "method_search"
    assert "SAR simulées" in target["raw_item"]["source_text"]
    assert target["research_target_id"] == "1.3.2"


def test_scholar_builds_keywords_from_a_generic_research_target():
    target = build_lightweight_research_context(
        SimpleNamespace(domain_label="Imagerie radar"),
        _request(),
    )["research_targets"][0]

    intent = build_scientific_intent(
        target,
        domain_detection={"main_domain_label": "Imagerie radar"},
        diagnostic_context=target["research_context"],
    )

    assert intent["subject_kind"] == "research_target"
    assert intent["research_target_id"] == "1.3.2"
    assert intent["research_target_type"] == "method_search"
    assert intent["key_terms_fr"]
    assert intent["key_terms_en"]
    assert intent["strong_anchors"]


def test_general_research_routes_to_scholar_without_diagnostic(monkeypatch):
    service = SemanticRoutingService()
    monkeypatch.setattr(
        service,
        "_fastjudge",
        lambda rows: {
            "target": {
                "function": SectionFunction.METHOD,
                "confidence": 0.96,
                "classifier": "test",
            }
        },
    )
    monkeypatch.setattr(service, "_semantic_functions", lambda rows: {})

    decision = service.route(
        "Il manque des arguments scientifiques : ajoute des justifications étayées par des publications.",
        TargetScope.SECTION,
        "Le protocole compare les données SAR simulées et mesurées.",
    )

    assert decision.needs_scholar
    assert not decision.needs_diagnostic
    assert not decision.needs_project_evidence
    assert decision.specialist_route.value == "scholar"


def test_request_for_more_arguments_alone_triggers_scholar(monkeypatch):
    service = SemanticRoutingService()
    monkeypatch.setattr(
        service,
        "_fastjudge",
        lambda rows: {
            "target": {
                "function": SectionFunction.RESULT,
                "confidence": 0.93,
                "classifier": "test",
            }
        },
    )
    monkeypatch.setattr(service, "_semantic_functions", lambda rows: {})

    decision = service.route(
        "Il manque des arguments dans cette section, ajoute plus d'arguments.",
        TargetScope.SECTION,
        "Les essais montrent une différence entre la simulation et les mesures.",
    )

    assert decision.needs_scholar
    assert not decision.needs_diagnostic
    assert decision.specialist_route.value == "scholar"


def test_same_request_keeps_diagnostic_for_an_actual_uncertainty(monkeypatch):
    service = SemanticRoutingService()
    monkeypatch.setattr(
        service,
        "_fastjudge",
        lambda rows: {
            "target": {
                "function": SectionFunction.UNCERTAINTY,
                "confidence": 0.96,
                "classifier": "test",
            }
        },
    )
    monkeypatch.setattr(service, "_semantic_functions", lambda rows: {})

    decision = service.route(
        "Renforce ce verrou et cherche des publications scientifiques pertinentes.",
        TargetScope.SECTION,
        "La représentativité des simulations reste incertaine.",
    )

    assert decision.needs_scholar
    assert decision.needs_diagnostic
    assert decision.specialist_route.value == "diagnostic_scholar"


def test_launch_general_research_uses_native_research_targets(monkeypatch):
    calls = []

    class FakeRepo:
        @staticmethod
        def snapshot(db, session_id):
            return {"selected_sources": []}

        @staticmethod
        def update(db, session_id, **kwargs):
            calls.append(("persist", kwargs))

    class FakeGuidedAgent:
        repository = FakeRepo()

    class FakeGuidedService:
        @staticmethod
        def create_guided_research_session(db, project, **kwargs):
            return {"session_id": "session-direct"}

        @staticmethod
        def get_guided_research_agent():
            return FakeGuidedAgent()

    class FakeScholar:
        @staticmethod
        def run_search(payload):
            calls.append(("payload", payload))
            target = payload["research_targets"][0]
            return {
                "version": "v151-test",
                "results": [
                    {
                        "research_target_id": target["research_target_id"],
                        "research_target_title": target["title"],
                        "research_target_type": target["research_target_type"],
                        "scientific_intent": {
                            "research_target_id": target["research_target_id"],
                            "key_terms_en": ["synthetic SAR data", "measured SAR data"],
                        },
                        "queries": [{"query": "synthetic measured SAR domain gap"}],
                        "articles": [
                            {
                                "title": "Synthetic-to-measured SAR generalization",
                                "year": 2023,
                                "doi": "10.1/direct",
                                "tag": "Direct",
                                "relevance_score": 0.91,
                            }
                        ],
                    }
                ],
            }

    monkeypatch.setattr(ros, "_guided_service", lambda: FakeGuidedService)
    monkeypatch.setattr(ros, "_precise_scholar_agent", lambda: FakeScholar())
    monkeypatch.setattr(ros, "_existing_decided_article_keys", lambda db, project: set())
    monkeypatch.setattr(ros, "_project_year", lambda project, request: 2024)
    monkeypatch.setattr(
        ros,
        "_matched_diagnostic_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("EnnoDiagnostic ne doit pas être appelé")
        ),
    )

    result = ros.launch_targeted_guided_research(
        object(),
        SimpleNamespace(id=1, domain_label="Radar"),
        _request(),
        diagnostic_package={"available": True, "diagnostic_run_id": "diag-unused"},
    )

    payload = next(value for name, value in calls if name == "payload")
    assert "research_targets" in payload
    assert "verrous" not in payload
    assert "diagnostic_context" not in payload
    assert result["research_mode"] == "direct_typed_research_target"
    assert result["diagnostic_required"] is False
    assert result["target_verrous"] == []
    assert result["research_target_ids"] == ["1.3.2"]
    assert result["candidates"][0]["research_target_ids"] == ["1.3.2"]
    assert result["candidates"][0]["target_verrous"] == []


def test_missing_scientific_arguments_offer_research_before_writing(monkeypatch):
    from agents.EnnoAmelioration.application.agent import EnnoAmeliorationAgent

    routing = RoutingDecision(
        intents=[ImprovementIntent.ARGUMENTATION, ImprovementIntent.SCIENTIFIC_ENRICHMENT],
        target_scope=TargetScope.SECTION,
        needs_scholar=True,
        needs_diagnostic=False,
        needs_project_evidence=False,
        section_function=SectionFunction.METHOD,
    )

    class FakeRouting:
        @staticmethod
        def route(*args, **kwargs):
            return routing

    class NeverWriter:
        llm = None

        @staticmethod
        def rewrite(*args, **kwargs):
            raise AssertionError("Le writer doit attendre les sources")

    monkeypatch.setattr(
        EnnoAmeliorationAgent,
        "_evidence_package",
        staticmethod(
            lambda db, project, request, decision: {
                "scholar": {"available": False},
                "gaps": [{"source": "scholar", "reason": "aucune source validée"}],
            }
        ),
    )

    result = EnnoAmeliorationAgent(
        writer=NeverWriter(),
        routing_service=FakeRouting(),
    ).improve(
        object(),
        SimpleNamespace(id=1),
        _request(instruction="Ajoute des arguments scientifiques solides a cette section."),
    )

    assert result.ok
    assert result.state == ImprovementState.AWAITING_EVIDENCE
    assert [row["id"] for row in result.actions] == [ros.RESEARCH_LAUNCH_TARGETED]
    assert "EnnoDiagnostic" not in result.agents_used



class ConversationalRoutingLLM:
    def generate(self, prompt: str, **kwargs):
        if "INTENTION CONVERSATIONNELLE" in prompt:
            if "sans nouvelle recherche" in prompt.casefold():
                return (
                    '{"goal":"scientific_strengthening",'
                    '"evidence_mode":"existing_scientific",'
                    '"wants_argumentation":true,'
                    '"wants_scientific_strengthening":true,'
                    '"wants_new_external_research":false,'
                    '"forbids_external_research":true,'
                    '"forbids_scholar":false,'
                    '"confidence":0.97}'
                )
            return (
                '{"goal":"scientific_strengthening",'
                '"evidence_mode":"new_scientific",'
                '"wants_argumentation":true,'
                '"wants_scientific_strengthening":true,'
                '"wants_new_external_research":true,'
                '"forbids_external_research":false,'
                '"forbids_scholar":false,'
                '"confidence":0.97}'
            )
        if "FONCTION SÉMANTIQUE" in prompt:
            return (
                '{"sections":[{"section_id":"target",'
                '"function":"scientific_landscape","confidence":0.96}]}'
            )
        raise AssertionError("Prompt inattendu")

    def get_last_generation_meta(self):
        return {"provider": "conversation-test"}


def test_natural_scientific_strengthening_does_not_require_magic_keywords(monkeypatch):
    service = SemanticRoutingService(llm=ConversationalRoutingLLM())
    monkeypatch.setattr(service, "_fastjudge", lambda rows: {})

    decision = service.route(
        (
            "Je veux que cette partie ait beaucoup plus de poids scientifique "
            "et qu'elle apporte des arguments plus solides pour mieux la mettre en valeur."
        ),
        TargetScope.SECTION,
        "La littérature actuelle présente plusieurs jeux de données SAR.",
    )

    assert ImprovementIntent.ARGUMENTATION in decision.intents
    assert ImprovementIntent.SCIENTIFIC_ENRICHMENT in decision.intents
    assert ImprovementIntent.RESEARCH in decision.intents
    assert decision.needs_scholar is True
    assert decision.needs_new_research is True
    assert decision.specialist_route.value == "scholar"


def test_explicit_no_new_research_stays_stronger_than_conversation(monkeypatch):
    service = SemanticRoutingService(llm=ConversationalRoutingLLM())
    monkeypatch.setattr(service, "_fastjudge", lambda rows: {})

    decision = service.route(
        (
            "Renforce scientifiquement cette section avec les sources déjà validées, "
            "sans nouvelle recherche."
        ),
        TargetScope.SECTION,
        "La littérature actuelle présente plusieurs jeux de données SAR.",
    )

    assert decision.needs_scholar is True
    assert decision.needs_new_research is False
    assert decision.forbids_new_research is True


def test_pasted_section_remains_one_target_even_with_detected_subsections():
    assert _pasted_section_is_whole_target(
        source_kind="pasted_text",
        scope=TargetScope.SECTION,
        selected_text=None,
        requested_section_id=None,
        requested_section_title=None,
        inferred_section=None,
    ) is True


def test_explicit_subsection_selection_overrides_pasted_whole_section():
    assert _pasted_section_is_whole_target(
        source_kind="pasted_text",
        scope=TargetScope.SECTION,
        selected_text=None,
        requested_section_id="sec-child",
        requested_section_title="Donnée SAR publiquement disponible",
        inferred_section=None,
    ) is False


def test_uploaded_document_is_never_treated_as_one_pasted_section():
    assert _pasted_section_is_whole_target(
        source_kind="document",
        scope=TargetScope.SECTION,
        selected_text=None,
        requested_section_id=None,
        requested_section_title=None,
        inferred_section=None,
    ) is False
