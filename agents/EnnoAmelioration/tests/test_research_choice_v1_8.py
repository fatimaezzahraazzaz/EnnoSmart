from __future__ import annotations

from types import SimpleNamespace

from EnnoAmelioration_V2_1_research_context_fix.application import research_orchestration_service as svc
from EnnoAmelioration_V2_1_research_context_fix.domain.models import ImprovementRequest, TargetScope


def _request(choice: str | None = None) -> ImprovementRequest:
    return ImprovementRequest(
        instruction="Renforce ce verrou et recherche des preuves scientifiques.",
        full_text="Texte complet",
        target_text="Les données simulées peuvent différer des mesures réelles.",
        target_scope=TargetScope.SECTION,
        target_section_id="1.3.2.3",
        target_section_title="Discordance entre données mesurées et simulées",
        research_choice=choice,
    )


def test_choice_detector_does_not_launch_from_generic_research_request():
    assert svc.detect_research_choice(
        "Recherche des publications scientifiques pertinentes pour ce verrou."
    ) is None


def test_choice_detector_existing_sources():
    assert svc.detect_research_choice(
        "Utiliser uniquement les sources déjà validées, sans nouvelle recherche."
    ) == svc.RESEARCH_USE_EXISTING


def test_choice_detector_launch_targeted_search():
    assert svc.detect_research_choice(
        "Lancer une recherche ciblée pour cette section."
    ) == svc.RESEARCH_LAUNCH_TARGETED


def test_actions_offer_both_choices_when_existing_sources_available():
    actions = svc.research_choice_actions(existing_sources_available=True)
    assert [row["id"] for row in actions] == [
        svc.RESEARCH_USE_EXISTING,
        svc.RESEARCH_LAUNCH_TARGETED,
    ]


def test_actions_offer_only_search_when_no_existing_sources():
    actions = svc.research_choice_actions(existing_sources_available=False)
    assert [row["id"] for row in actions] == [svc.RESEARCH_LAUNCH_TARGETED]


def test_launch_targeted_research_reuses_guided_session_but_precise_core(monkeypatch):
    calls = []

    class FakeRepo:
        def snapshot(self, db, session_id):
            return {"selected_sources": []}
        def update(self, db, session_id, **kwargs):
            calls.append(("persist", kwargs))
            return {}

    class FakeGuidedAgent:
        repository = FakeRepo()

    class FakeGuidedService:
        @staticmethod
        def create_guided_research_session(db, project, **kwargs):
            calls.append(("create", kwargs))
            return {"session_id": "session-123"}
        @staticmethod
        def get_guided_research_agent():
            return FakeGuidedAgent()

    class FakeScholar:
        def run_search(self, payload):
            calls.append(("core_search", payload))
            return {
                "version": "v150",
                "results": [{
                    "scientific_intent": {"strong_anchors": ["synthetic", "measured"]},
                    "queries": [{"query": "synthetic measured domain gap"}],
                    "queries_generated": [],
                    "search_status": {},
                    "reranking": {"used": True},
                    "articles": [
                        {
                            "title": "Article A",
                            "year": 2022,
                            "doi": "10.1/a",
                            "tag": "Direct",
                            "relevance_score": 0.9,
                        },
                        {
                            "title": "Article B",
                            "year": 2021,
                            "doi": "10.1/b",
                            "tag": "Connexe",
                            "relevance_score": 0.8,
                        },
                    ],
                }],
            }

    monkeypatch.setattr(svc, "_guided_service", lambda: FakeGuidedService)
    monkeypatch.setattr(svc, "_precise_scholar_agent", lambda: FakeScholar())
    monkeypatch.setattr(
        svc,
        "_matched_diagnostic_context",
        lambda db, project, request: ({"diagnostic_context_text": ""}, [], []),
    )
    monkeypatch.setattr(svc, "_existing_decided_article_keys", lambda db, project: set())
    monkeypatch.setattr(svc, "_project_year", lambda project, request: 2024)

    result = svc.launch_targeted_guided_research(
        object(), SimpleNamespace(id=1), _request(svc.RESEARCH_LAUNCH_TARGETED)
    )
    assert result["session_id"] == "session-123"
    assert result["engine"] == "ennoscholar_core"
    assert result["candidate_count"] == 2
    assert {row["title"] for row in result["candidates"]} == {"Article A", "Article B"}
    assert [name for name, _ in calls[:2]] == ["create", "core_search"]
    assert any(name == "persist" for name, _ in calls)


def test_result_model_exposes_actions_and_research():
    from EnnoAmelioration_V2_1_research_context_fix.domain.models import (
        ImprovementResult,
        ImprovementState,
        RoutingDecision,
    )

    result = ImprovementResult(
        ok=True,
        state=ImprovementState.AWAITING_EVIDENCE,
        assistant_message="Choisissez.",
        routing=RoutingDecision(intents=[], target_scope=TargetScope.SECTION),
        actions=[{"id": svc.RESEARCH_LAUNCH_TARGETED}],
        research={"session_id": "s1"},
    )
    assert result.actions[0]["id"] == svc.RESEARCH_LAUNCH_TARGETED
    assert result.research["session_id"] == "s1"


def test_long_explicit_new_research_prompt_wins_over_negative_existing_source_mentions():
    prompt = """
Je veux maintenant lancer une NOUVELLE recherche scientifique ciblée pour renforcer la section 1.3.2.3.
Ne te limite pas aux sources scientifiques déjà présentes ou déjà validées dans le projet.
Je demande explicitement à EnnoScholar de rechercher de NOUVELLES publications scientifiques pertinentes.
Utilise le texte de la section et les preuves internes du projet uniquement pour construire des requêtes de recherche précises.
IMPORTANT : lance réellement une nouvelle recherche EnnoScholar ; ne réutilise pas simplement les articles déjà validés comme résultat de cette recherche.
"""
    assert svc.detect_research_choice(prompt) == svc.RESEARCH_LAUNCH_TARGETED


def test_negative_reuse_of_existing_sources_is_not_existing_choice():
    assert svc.detect_research_choice(
        "Ne réutilise pas les sources déjà validées ; lance une nouvelle recherche ciblée."
    ) == svc.RESEARCH_LAUNCH_TARGETED


def test_explicit_no_new_research_stays_existing_choice():
    assert svc.detect_research_choice(
        "Utilise uniquement les sources déjà validées, sans nouvelle recherche."
    ) == svc.RESEARCH_USE_EXISTING


def test_negated_launch_does_not_start_research():
    assert svc.detect_research_choice(
        "Ne lance pas de nouvelle recherche. Utilise uniquement les sources déjà validées."
    ) == svc.RESEARCH_USE_EXISTING


def test_agent_explicit_long_prompt_launches_guided_research_instead_of_writing(monkeypatch):
    import sys
    import types

    modules_pkg = types.ModuleType("modules")
    llm_pkg = types.ModuleType("modules.LLM")
    llm_client_mod = types.ModuleType("modules.LLM.llm_client")
    class DummyLLMClient:
        pass
    llm_client_mod.LLMClient = DummyLLMClient
    monkeypatch.setitem(sys.modules, "modules", modules_pkg)
    monkeypatch.setitem(sys.modules, "modules.LLM", llm_pkg)
    monkeypatch.setitem(sys.modules, "modules.LLM.llm_client", llm_client_mod)

    from EnnoAmelioration_V2_1_research_context_fix.application import agent as agent_module
    from EnnoAmelioration_V2_1_research_context_fix.application.agent import EnnoAmeliorationAgent
    from EnnoAmelioration_V2_1_research_context_fix.domain.models import ImprovementState

    prompt = """
Je veux maintenant lancer une NOUVELLE recherche scientifique ciblée pour renforcer la section 1.3.2.3.
Ne te limite pas aux sources scientifiques déjà présentes ou déjà validées dans le projet.
Je demande explicitement à EnnoScholar de rechercher de NOUVELLES publications scientifiques pertinentes.
Utilise le texte de la section et les preuves internes du projet uniquement pour construire des requêtes de recherche précises.
IMPORTANT : lance réellement une nouvelle recherche EnnoScholar ; ne rédige pas encore une nouvelle version de la section ; ne réutilise pas simplement les articles déjà validés comme résultat de cette recherche.
"""

    class NeverWriter:
        llm = None
        def rewrite(self, *args, **kwargs):  # pragma: no cover - doit être impossible
            raise AssertionError("Le writer ne doit pas être appelé avant validation des sources")

    request = ImprovementRequest(
        instruction=prompt,
        full_text="Texte complet",
        target_text="Les données simulées peuvent différer des mesures réelles.",
        target_scope=TargetScope.SECTION,
        target_section_id="1.3.2.3",
        target_section_title="Discordance entre données mesurées et simulées",
    )

    monkeypatch.setattr(
        EnnoAmeliorationAgent,
        "_evidence_package",
        staticmethod(lambda db, project, request, routing: {
            "scholar": {"available": True},
            "diagnostic": {"available": True},
            "gaps": [],
        }),
    )
    launched = []
    monkeypatch.setattr(
        agent_module,
        "launch_targeted_guided_research",
        lambda db, project, request: launched.append(request.instruction) or {
            "ok": True,
            "session_id": "guided-1",
            "state": "waiting_consultant_feedback",
            "assistant_message": "2 sources candidates trouvées.",
            "candidate_count": 2,
            "candidates": [
                {"candidate_id": "n1", "title": "Nouveau papier 1", "year": 2023},
                {"candidate_id": "n2", "title": "Nouveau papier 2", "year": 2022},
            ],
        },
    )

    result = EnnoAmeliorationAgent(writer=NeverWriter()).improve(
        object(), SimpleNamespace(id=1), request
    )
    assert launched, "Guided Research doit être réellement invoqué"
    assert result.state == ImprovementState.AWAITING_EVIDENCE
    assert result.research["session_id"] == "guided-1"
    assert result.improved_target == ""
    assert "Nouveau papier 1" in result.assistant_message
