from __future__ import annotations

from types import SimpleNamespace

from EnnoAmelioration_V2_1_research_context_fix.application import research_orchestration_service as svc
from EnnoAmelioration_V2_1_research_context_fix.domain.models import ImprovementRequest, TargetScope


def _request() -> ImprovementRequest:
    return ImprovementRequest(
        instruction=(
            "Lancer une nouvelle recherche scientifique ciblée. Ne rédige pas encore."
        ),
        full_text="Texte complet",
        target_text=(
            "Les données SAR simulées avec MOCEM présentent un écart avec les mesures MSTAR. "
            "Les fonds simulés et mesurés peuvent créer des biais d'apprentissage et limiter "
            "la généralisation des modèles ATR."
        ),
        target_scope=TargetScope.SECTION,
        target_section_id="1.3.2.3",
        target_section_title="Discordance entre données radar mesurées et simulées",
        project_name="AI-RADAR CIR 2024",
        project_domain="radar SAR et apprentissage automatique",
    )


class FakeRepository:
    def __init__(self):
        self.snapshot_value = {"selected_sources": []}
        self.last_update = None

    def snapshot(self, db, session_id):
        return dict(self.snapshot_value)

    def update(self, db, session_id, **kwargs):
        self.last_update = dict(kwargs)
        self.snapshot_value["selected_sources"] = list(kwargs.get("selected_sources") or [])
        return self.snapshot_value


class FakeGuidedAgent:
    def __init__(self):
        self.repository = FakeRepository()


class FakeGuidedService:
    agent = FakeGuidedAgent()
    accepted_plan_called = False

    @staticmethod
    def create_guided_research_session(db, project, **kwargs):
        return {"session_id": "guided-v2"}

    @classmethod
    def get_guided_research_agent(cls):
        return cls.agent

    @classmethod
    def accept_guided_research_plan(cls, *args, **kwargs):  # pragma: no cover
        cls.accepted_plan_called = True
        raise AssertionError("Le moteur Guided WebResearch ne doit pas être appelé")


class FakePreciseScholar:
    def __init__(self):
        self.payload = None

    def run_search(self, payload):
        self.payload = payload
        return {
            "version": "v150_problem_evidence_year_cutoff",
            "search_elapsed_seconds": 0.1,
            "results": [
                {
                    "scientific_intent": {
                        "verrou_title": "Discordance SAR synthetic-to-real",
                        "strong_anchors": ["SAR", "ATR", "MSTAR", "MOCEM"],
                    },
                    "queries": [
                        {"query": "SAR ATR synthetic measured domain gap"}
                    ],
                    "queries_generated": [],
                    "search_status": {
                        "precision_tag_counts": {"Direct": 2, "Connexe": 0}
                    },
                    "reranking": {"used": True},
                    "articles": [
                        {
                            "title": "Bridging the synthetic-to-measured gap in SAR ATR",
                            "year": 2024,
                            "doi": "10.1000/sar.2024.1",
                            "abstract": "Synthetic SAR to measured MSTAR ATR domain gap.",
                            "authors": ["A. Author"],
                            "tag": "Direct",
                            "relevance_score": 0.94,
                            "source": "semantic_scholar",
                        },
                        {
                            "title": "Radar precipitation simulation over mountains",
                            "year": 2024,
                            "doi": "10.1000/weather",
                            "tag": "Hors sujet",
                            "relevance_score": 0.20,
                        },
                        {
                            "title": "Future SAR ATR paper",
                            "year": 2025,
                            "doi": "10.1000/future",
                            "tag": "Direct",
                            "relevance_score": 0.90,
                        },
                    ],
                }
            ],
        }


def test_project_year_is_resolved_from_project_name():
    assert svc._project_year(SimpleNamespace(id=1), _request()) == 2024


def test_precise_flow_bypasses_guided_generic_research(monkeypatch):
    precise = FakePreciseScholar()
    monkeypatch.setattr(svc, "_guided_service", lambda: FakeGuidedService)
    monkeypatch.setattr(svc, "_precise_scholar_agent", lambda: precise)
    monkeypatch.setattr(
        svc,
        "_matched_diagnostic_context",
        lambda db, project, request: (
            {
                "diagnostic_context_text": "MSTAR MOCEM SAR ATR synthetic measured generalization",
                "matched_verrou_ids": ["700", "701"],
            },
            ["700", "701"],
            [
                {
                    "evidence_id": "D:verrou:700",
                    "title": "Représentativité SAR synthétique",
                    "text": "MSTAR MOCEM synthetic measured SAR ATR",
                }
            ],
        ),
    )
    monkeypatch.setattr(svc, "_existing_decided_article_keys", lambda db, project: set())

    result = svc.launch_targeted_guided_research(
        object(), SimpleNamespace(id=1), _request()
    )

    assert result["engine"] == "ennoscholar_core"
    assert result["generic_web_research_bypassed"] is True
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["title"].startswith("Bridging")
    assert result["candidates"][0]["tag"] == "Direct"
    assert result["project_year_cutoff"] == 2024
    assert precise.payload["force_refresh"] is True
    assert precise.payload["year"] == 2024
    assert precise.payload["verrous"][0]["title"] == "Représentativité SAR synthétique"
    assert precise.payload["verrous"][0]["raw_item"]["source_text"].startswith("Les données SAR")
    assert FakeGuidedService.accepted_plan_called is False

    update = FakeGuidedService.agent.repository.last_update
    assert update is not None
    assert update["state"] == "waiting_consultant_feedback"
    assert update["research_plan"]["engine"] == "ennoscholar_core"
    assert update["context_updates"]["generic_web_research_bypassed"] is True


def test_existing_decided_and_future_articles_are_removed():
    report = {
        "results": [
            {
                "articles": [
                    {
                        "title": "Already accepted paper",
                        "year": 2023,
                        "doi": "10.1/already",
                        "tag": "Direct",
                        "relevance_score": 0.95,
                    },
                    {
                        "title": "New relevant paper",
                        "year": 2024,
                        "doi": "10.1/new",
                        "tag": "Direct",
                        "relevance_score": 0.90,
                    },
                    {
                        "title": "Future relevant paper",
                        "year": 2025,
                        "doi": "10.1/future",
                        "tag": "Direct",
                        "relevance_score": 0.89,
                    },
                ],
                "scientific_intent": {},
                "queries": [],
                "search_status": {},
                "reranking": {},
            }
        ]
    }
    candidates, meta = svc._extract_precise_candidates(
        report,
        request=_request(),
        target_verrous=["700"],
        excluded_keys={"doi:10.1/already"},
        project_year=2024,
        limit=10,
    )
    assert [row["title"] for row in candidates] == ["New relevant paper"]
    assert meta["removed_existing_decided"] == 1
    assert meta["removed_after_project_year"] == 1


def test_format_message_explains_core_and_year_cutoff():
    text = svc.format_research_candidates_message(
        {
            "project_year_cutoff": 2024,
            "candidates": [
                {
                    "title": "SAR ATR paper",
                    "year": 2023,
                    "tag": "Direct",
                    "relevance_score": 0.91,
                }
            ],
        }
    )
    assert "moteur scientifique principal" in text
    assert "postérieures à 2024" in text
    assert "Direct" in text
