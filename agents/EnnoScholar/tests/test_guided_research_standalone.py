from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from EnnoScholar.guided_research.application.guided_research_agent import (
    EnnoScholarGuidedResearchAgent,
)
from EnnoScholar.guided_research.application.standalone_state_of_art_writer_service import (
    run_standalone_state_of_art_writer,
)
from EnnoScholar.consultant_plan_service import create_contract
from EnnoScholar.guided_research.lot1.domain.enums import (
    ConsultantIntent,
    GuidedResearchEntryModule,
    GuidedResearchState,
    GuidedResearchTargetMode,
)
from EnnoScholar.guided_research.lot1.domain.models import (
    GuidedResearchSessionData,
)
from modules.LLM.llm_client import LLMClient


class _StateManager:
    def __init__(self, session: GuidedResearchSessionData) -> None:
        self.session = session
        self.messages: list[dict] = []

    def get_session(self, db, session_id):
        return self.session

    def append_message(self, db, session_id, **kwargs):
        self.messages.append(dict(kwargs))

    def update_brief(self, db, session_id, brief):
        self.session = self.session.model_copy(update={"brief": brief})
        return self.session


class _Repository:
    def __init__(self, context: dict) -> None:
        self.context = dict(context)
        self.selected_sources: list[dict] = []
        self.research_plan: dict = {}
        self.writing_contract: dict = {}
        self.draft: dict = {}
        self.state = None

    def update(self, db, session_id, **kwargs):
        self.context.update(kwargs.get("context_updates") or {})
        if kwargs.get("selected_sources") is not None:
            self.selected_sources = list(kwargs["selected_sources"])
        if kwargs.get("research_plan") is not None:
            self.research_plan = dict(kwargs["research_plan"])
        if kwargs.get("writing_contract") is not None:
            self.writing_contract = dict(kwargs["writing_contract"])
        if kwargs.get("draft") is not None:
            self.draft = dict(kwargs["draft"])
        if kwargs.get("state") is not None:
            self.state = kwargs["state"]
        return self.snapshot(db, session_id)

    def snapshot(self, db, session_id):
        return {
            "context": dict(self.context),
            "selected_sources": list(self.selected_sources),
            "research_plan": dict(self.research_plan),
            "writing_contract": dict(self.writing_contract),
            "draft": dict(self.draft),
        }


class _Research:
    def search(self, requests_payload, **kwargs):
        return {
            "ok": True,
            "candidates": [
                {
                    "candidate_id": "candidate-1",
                    "title": "A relevant scientific article",
                    "candidate_kind": "scientific_article",
                    "relevance_role": "direct_evidence",
                    "scientific_evidence_eligible": True,
                    "target_verrous": list(
                        requests_payload[0].get("target_verrous") or []
                    ),
                }
            ],
            "completeness": {},
        }


class _JsonLLM:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {}

    def generate(self, prompt, **kwargs):
        return json.dumps(self.payload, ensure_ascii=False)


class _SequenceJsonLLM:
    def __init__(self, *payloads: dict) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    def generate(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        if not self.payloads:
            raise AssertionError("Appel LLM inattendu.")
        return json.dumps(self.payloads.pop(0), ensure_ascii=False)

    def get_last_generation_meta(self):
        return {"provider": "fake", "request_name": "standalone-test"}


def test_standalone_project_context_is_recorded_without_research() -> None:
    session = GuidedResearchSessionData(
        session_id="standalone-context-session",
        project_id=19,
        entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
        target_mode=GuidedResearchTargetMode.GLOBAL,
        state=GuidedResearchState.BRIEF_IN_PROGRESS,
        context={
            "operating_mode": "standalone_chat",
            "consultant_verrous": [],
            "standalone_project_brief": {},
        },
    )
    state = _StateManager(session)
    repository = _Repository(session.context)

    class _ForbiddenResearch:
        def search(self, *args, **kwargs):
            raise AssertionError("La déclaration ne doit lancer aucune recherche.")

    llm = _SequenceJsonLLM(
        {
            "classification": {
                "intent": "describe_requirements",
                "confidence": 0.99,
                "rationale": "Le consultant demande uniquement l'enregistrement.",
                "requested_actions": ["describe_requirements"],
                "forbidden_actions": ["search_more", "start_writing"],
                "explicit_research_command": False,
                "needs_clarification": False,
                "corrected_message": "",
                "extracted_text": "Enregistre ce contexte sans recherche.",
                "classifier": "fake_semantic_controller",
            },
            "plan_reference": "none",
            "referenced_plan_version": "",
            "plan_generation_mode": "none",
            "plan_document_scope": "none",
            "assistant_message": "J'enregistre uniquement ce contexte.",
            "memory": {
                "project_facts": ["Projet de maintenance prédictive thermique."],
                "last_consultant_goal": "Préparer un état de l'art ciblé.",
            },
        },
        {
            "plan": [],
            "topics": [],
            "constraints": [],
            "project_brief": {
                "project_name": "THERMO-PREDICT",
                "domain": "prédiction de dérives thermiques industrielles",
                "objective": "anticiper une dégradation thermique",
                "additional_context": "données de capteurs hétérogènes",
            },
            "verrous": [
                {
                    "title": (
                        "Fiabilité hors distribution avec des historiques limités "
                        "et des conditions de fonctionnement différentes"
                    ),
                    "justification": "Incertitude scientifique à étudier.",
                    "supporting_context": (
                        "Charges thermiques, profils d'usage et équipements variables."
                    ),
                }
            ],
            "review_scope": "per_verrou",
            "search_requests": [],
        },
    )
    agent = EnnoScholarGuidedResearchAgent(
        state_manager=state,
        repository=repository,
        llm=llm,
        research=_ForbiddenResearch(),
    )
    agent._contract_path = lambda project: Path(
        "C:/EnnoSmart/.nonexistent-tests/contract.json"
    )
    agent._load_contract_snapshot = lambda project: {}
    agent._conversation_project_context = lambda project, current: {
        "project": {"name": "Projet vide"},
        "operating_mode": "standalone_chat",
        "standalone_project_brief": {},
        "current_verrous": [],
    }

    response = agent.handle_message(
        object(),
        SimpleNamespace(
            id=19,
            organisme="Org",
            project_name="Projet vide",
            year=2026,
            domain_label="",
        ),
        session_id=session.session_id,
        consultant_message=(
            "Le projet s'appelle THERMO-PREDICT. Enregistre le domaine, "
            "l'objectif et le verrou. Ne lance pas encore de recherche."
        ),
    )

    assert response.intent == ConsultantIntent.DESCRIBE_REQUIREMENTS
    assert response.state == GuidedResearchState.BRIEF_PARSED
    assert repository.state == GuidedResearchState.BRIEF_PARSED
    assert response.metadata["context_recorded"] is True
    assert response.metadata["research_started"] is False
    assert response.metadata["trigger_state_of_art_generation"] is False
    assert "THERMO-PREDICT" in response.assistant_message
    assert "Aucune recherche ni rédaction" in response.assistant_message
    assert repository.context["standalone_project_brief"]["domain"].startswith(
        "prédiction de dérives"
    )
    assert len(repository.context["consultant_verrous"]) == 1
    assert repository.context["consultant_verrous"][0]["id"].startswith("SV-")
    assert len(llm.calls) == 2


@pytest.mark.parametrize(
    ("titles", "requested_scope", "expected_scope"),
    [
        (["Robustesse hors distribution"], "per_verrou", "per_verrou"),
        (
            ["Robustesse hors distribution", "Validation physique du simulateur"],
            "global",
            "global",
        ),
    ],
)
def test_standalone_verrous_are_session_scoped_and_researched(
    titles, requested_scope, expected_scope
) -> None:
    session = GuidedResearchSessionData(
        session_id="standalone-session",
        project_id=7,
        entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
        target_mode=GuidedResearchTargetMode.GLOBAL,
        state=GuidedResearchState.BRIEF_IN_PROGRESS,
        context={
            "operating_mode": "standalone_chat",
            "consultant_verrous": [],
            "standalone_project_brief": {},
        },
    )
    state = _StateManager(session)
    repository = _Repository(session.context)
    agent = EnnoScholarGuidedResearchAgent(
        state_manager=state,
        repository=repository,
        llm=_JsonLLM(),
        research=_Research(),
    )
    interpretation = {
        "project_brief": {
            "project_name": "Projet radar",
            "domain": "imagerie radar",
            "objective": "améliorer la généralisation",
        },
        "review_scope": requested_scope,
        "verrous": [
            {
                "title": title,
                "justification": "incertitude à défendre",
                "supporting_context": "contexte déclaré par le consultant",
            }
            for title in titles
        ],
        "search_requests": [
            {
                "query": f"{title} experimental evidence",
                "query_kind": "direct_scientific_evidence",
                "require_direct_evidence": True,
            }
            for title in titles
        ],
    }

    response = agent._add_standalone_verrous_and_search(
        object(),
        SimpleNamespace(
            id=7,
            organisme="Org",
            project_name="Projet radar",
            year=2026,
            domain_label="imagerie radar",
        ),
        session,
        "Rédige un état de l'art sur les verrous indiqués.",
        interpretation=interpretation,
        pending_write_requested=True,
    )

    stored = repository.context["consultant_verrous"]
    assert len(stored) == len(titles)
    assert all(str(row["id"]).startswith("SV-") for row in stored)
    assert repository.context["review_scope"] == expected_scope
    assert repository.context["pending_write_request"] is True
    assert response.metadata["operating_mode"] == "standalone_chat"
    assert response.metadata["review_scope"] == expected_scope
    assert response.metadata["candidates"]


def test_standalone_writer_publishes_only_guarded_markdown(tmp_path: Path) -> None:
    llm = _JsonLLM(
        {
            "title": "État de l'art — robustesse",
            "sections": [
                {
                    "title": "Résultats et limites de la littérature",
                    "verrou_ids": ["SV-1"],
                    "paragraphs": [
                        {
                            "text": (
                                "Les travaux sélectionnés décrivent une méthode "
                                "expérimentale et en délimitent les conditions de validité."
                            ),
                            "citations": ["S1"],
                        }
                    ],
                }
            ],
        }
    )
    result = run_standalone_state_of_art_writer(
        llm=llm,
        project_brief={"domain": "radar", "objective": "généralisation"},
        verrous=[{"id": "SV-1", "title": "Robustesse"}],
        review_scope="per_verrou",
        selected_sources=[
            {
                "candidate_id": "candidate-1",
                "consultant_decision": "accepted",
                "fulltext_preparation": {
                    "article_id": 41,
                    "usable_as_scientific_evidence": True,
                },
            }
        ],
        cards_payload={
            "cards": [
                {
                    "article_id": 41,
                    "guided_candidate_id": "candidate-1",
                    "title": "A validated article",
                    "authors": ["A. Author"],
                    "year": 2024,
                    "verrou_ids": ["SV-1"],
                    "methode": "Experimental protocol",
                    "resultats": "Observed result",
                    "limites": "Limited transferability",
                }
            ]
        },
        output_dir=tmp_path,
    )

    assert result["ok"] is True
    assert result["guard"]["missing_verrous"] == []
    assert "[S1]" in result["markdown"]
    assert Path(result["markdown_output_path"]).exists()
    assert not (tmp_path / "state_of_art_rejected.md").exists()


def test_standalone_combined_approval_uses_plan_and_mapping_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = [
        {
            "section_id": "S1",
            "title": "Dégradation hors distribution",
            "objective": "Comparer les pertes de performance.",
            "level": 1,
        },
        {
            "section_id": "S2",
            "title": "Gap scientifique",
            "objective": "Délimiter le verrou restant.",
            "level": 1,
        },
    ]
    llm = _JsonLLM(
        {
            "title": "État de l'art — THERMO-PREDICT",
            "sections": [
                {
                    "section_id": row["section_id"],
                    "title": row["title"],
                    "level": row["level"],
                    "parent_id": None,
                    "verrou_ids": ["SV-THERMO"],
                    "paragraphs": [
                        {
                            "text": (
                                "Les résultats expérimentaux validés délimitent "
                                "la transférabilité de la méthode sous changement de régime."
                            ),
                            "citations": ["S1"],
                        }
                    ],
                }
                for row in plan
            ],
        }
    )
    session = GuidedResearchSessionData(
        session_id="standalone-write-session",
        project_id=23,
        entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
        target_mode=GuidedResearchTargetMode.PER_VERROU,
        state=GuidedResearchState.BRIEF_PARSED,
        context={
            "operating_mode": "standalone_chat",
            "review_scope": "per_verrou",
            "active_verrou_ids": ["SV-THERMO"],
            "consultant_verrous": [
                {"id": "SV-THERMO", "title": "Fiabilité hors distribution"}
            ],
            "standalone_project_brief": {
                "project_name": "THERMO-PREDICT",
                "domain": "maintenance prédictive thermique",
            },
        },
    )
    state = _StateManager(session)
    repository = _Repository(session.context)
    repository.selected_sources = [
        {
            "candidate_id": "candidate-1",
            "consultant_decision": "accepted",
            "fulltext_preparation": {
                "article_id": 41,
                "usable_as_scientific_evidence": True,
            },
        }
    ]
    cards_path = tmp_path / "article_cards_payload.json"
    cards_path.write_text(
        json.dumps(
            {
                "cards": [
                    {
                        "article_id": 41,
                        "guided_candidate_id": "candidate-1",
                        "title": "A validated article",
                        "authors": ["A. Author"],
                        "year": 2025,
                        "verrou_ids": ["SV-THERMO"],
                        "methode": "Adaptation de domaine.",
                        "results": "Dégradation mesurée hors distribution.",
                        # Structure réelle des nouvelles Article Cards : un objet
                        # de provenance, et non une liste directement découpable.
                        "evidence": {
                            "full_text_available": True,
                            "limitation_evidence": {
                                "explicit_limitations": [],
                            },
                        },
                        "key_scientific_passages": [
                            {"text": "Passage expérimental vérifié."}
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    agent = EnnoScholarGuidedResearchAgent(
        state_manager=state,
        repository=repository,
        llm=llm,
        research=_Research(),
    )
    agent._cards_path = lambda project: cards_path
    monkeypatch.setattr(
        "EnnoScholar.guided_research.application.guided_research_agent.state_of_art_root",
        lambda *args: tmp_path,
    )
    monkeypatch.setattr(
        "services.scholar_state_of_art_payload_service.build_state_of_art_selection_payload",
        lambda *args, **kwargs: {
            "ok": True,
            "selection_summary": {"verrous_count": 1},
        },
    )
    monkeypatch.setattr(
        "services.article_card_builder.build_article_cards_for_selected_articles",
        lambda *args, **kwargs: {
            "ok": True,
            "cards_count": 1,
            "writing_ready_cards_count": 1,
        },
    )

    response = agent._start_writing(
        object(),
        SimpleNamespace(
            id=23,
            organisme="Org",
            project_name="THERMO-PREDICT",
            year=2026,
        ),
        session,
        create_contract(plan),
        tmp_path / "consultant_plan_contract.json",
        "Je valide ce plan et lance la rédaction.",
        explicit_plan_approval=True,
        use_current_sources_only=True,
    )

    assert response.state == GuidedResearchState.WRITING_IN_PROGRESS
    assert repository.writing_contract["writing_authorized"] is True
    assert repository.context["plan_approved"] is True
    assert repository.context["standalone_full_pipeline"] is True
    assert response.metadata["trigger_state_of_art_generation"] is True
    assert response.metadata["standalone_full_pipeline"] is True
    assert response.metadata["phase_2"]["writing_ready_cards_count"] == 1


def test_standalone_writer_recovers_without_llm(tmp_path: Path) -> None:
    class _UnavailableLLM:
        def generate(self, *args, **kwargs):
            raise RuntimeError("429 rate limit")

    result = run_standalone_state_of_art_writer(
        llm=_UnavailableLLM(),
        project_brief={"project_name": "Projet autonome", "domain": "radar"},
        verrous=[{"id": "SV-1", "title": "Robustesse au changement de domaine"}],
        review_scope="per_verrou",
        selected_sources=[
            {
                "candidate_id": "candidate-1",
                "consultant_decision": "accepted",
                "fulltext_preparation": {
                    "article_id": 41,
                    "usable_as_scientific_evidence": True,
                },
            }
        ],
        cards_payload={
            "cards": [
                {
                    "article_id": 41,
                    "guided_candidate_id": "candidate-1",
                    "title": "A validated article",
                    "authors": ["A. Author"],
                    "year": 2024,
                    "verrou_ids": ["SV-1"],
                    "methode": "Un protocole expérimental contrôlé.",
                    "resultats": "Une dégradation est observée hors domaine.",
                    "limites": "La transférabilité reste limitée.",
                }
            ]
        },
        output_dir=tmp_path,
    )

    assert result["ok"] is True
    assert result["writer_mode"] == "deterministic_evidence_only"
    assert result["guard"]["passed"] is True
    assert "[S1]" in result["markdown"]
    assert Path(result["markdown_output_path"]).exists()


def test_openai_429_retry_is_shared_by_web_and_writer(monkeypatch) -> None:
    class _Response:
        def __init__(self, status_code, payload, text="", headers=None):
            self.status_code = status_code
            self._payload = payload
            self.text = text
            self.headers = headers or {}

        def json(self):
            return self._payload

    responses = iter(
        [
            _Response(
                429,
                {},
                text="Please try again in 0.01s",
                headers={"Retry-After": "0.01"},
            ),
            _Response(200, {"ok": True}),
        ]
    )
    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        return next(responses)

    monkeypatch.setattr("modules.LLM.llm_client.requests.post", fake_post)
    monkeypatch.setattr("modules.LLM.llm_client.time.sleep", lambda value: None)
    client = object.__new__(LLMClient)
    client.connect_timeout = 1
    client.read_timeout = 1

    result = client._post_with_retry(
        "https://api.openai.test/responses",
        {},
        {},
        0,
        "OpenAI Web Search",
    )

    assert result == {"ok": True}
    assert calls["count"] == 2
