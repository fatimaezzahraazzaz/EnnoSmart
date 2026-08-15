from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from EnnoScholar.guided_research.application.guided_research_agent import (
    EnnoScholarGuidedResearchAgent,
    _approve_for_combined_write,
    _apply_verrou_scope_to_plan,
    _merge_additive_plan_update,
    _plan_candidate_covers_current,
    _plan_history_from_session,
    _plan_materially_changed,
    _resolve_candidate_display_identifiers,
    _resolve_effective_writing_source_identifiers,
    _resolve_routed_intent,
    _resolve_targeted_verrou_scope,
)
from EnnoScholar.guided_research.lot1.conversation_understanding_service import (
    ConversationUnderstandingService,
    _ground_writing_source_policy,
)
from EnnoScholar.guided_research.lot1.domain.enums import (
    ConsultantIntent,
    ConversationRole,
    GuidedResearchEntryModule,
    GuidedResearchState,
    GuidedResearchTargetMode,
)
from EnnoScholar.guided_research.lot1.domain.models import (
    ConsultantBrief,
    ConversationTurn,
    GuidedResearchSessionData,
    IntentClassification,
)
from EnnoScholar.guided_research.lot1.session_state_manager import (
    GuidedResearchSessionNotFoundError,
    GuidedResearchSessionStateManager,
)
from EnnoScholar.consultant_plan_service import authorize_writing, create_contract
from EnnoScholar.guided_research.application.web_research_service import (
    WebResearchService,
    _looks_like_scientific_web_source,
)
from EnnoScholar.state_of_art.phase_5_state_of_art_writer_service import (
    extract_supplemental_source_cards,
)
from modules.LLM.llm_client import LLMClient


def _decision(
    intent: ConsultantIntent | str,
    assistant_message: str,
    *,
    requested_actions: list[ConsultantIntent | str] | None = None,
    forbidden_actions: list[ConsultantIntent | str] | None = None,
    explicit_research_command: bool = False,
    replace_current_plan: bool = False,
    needs_clarification: bool = False,
    plan_reference: str = "none",
    referenced_plan_version: str | None = "",
    plan_generation_mode: str = "none",
    plan_document_scope: str = "none",
    use_current_sources_only: bool = False,
    writing_source_scope: str = "unspecified",
    writing_source_identifiers: list[str] | None = None,
    requested_source_count: int | None = None,
    target_verrou_ids: list[str] | None = None,
    verrou_scope: str = "unchanged",
    explicit_new_verrou_declaration: bool = False,
) -> dict:
    return {
        "classification": {
            "intent": (
                intent.value
                if isinstance(intent, ConsultantIntent)
                else intent
            ),
            "confidence": 0.98,
            "rationale": "Décision sémantique du tour courant.",
            "requested_actions": [
                action.value if isinstance(action, ConsultantIntent) else action
                for action in (requested_actions or [])
            ],
            "forbidden_actions": [
                action.value if isinstance(action, ConsultantIntent) else action
                for action in (forbidden_actions or [])
            ],
            "explicit_research_command": explicit_research_command,
            "replace_current_plan": replace_current_plan,
            "use_current_sources_only": use_current_sources_only,
            "writing_source_scope": writing_source_scope,
            "writing_source_identifiers": writing_source_identifiers or [],
            "requested_source_count": requested_source_count,
            "target_verrou_ids": target_verrou_ids or [],
            "verrou_scope": verrou_scope,
            "explicit_new_verrou_declaration": (
                explicit_new_verrou_declaration
            ),
            "needs_clarification": needs_clarification,
            "corrected_message": "",
            "extracted_text": "",
            "classifier": "fake_semantic_controller",
        },
        "plan_reference": plan_reference,
        "referenced_plan_version": referenced_plan_version,
        "plan_generation_mode": plan_generation_mode,
        "plan_document_scope": plan_document_scope,
        "assistant_message": assistant_message,
        "memory": {},
    }


class SequenceLLM:
    """LLM déterministe qui expose aussi les appels décision/action/réparation."""

    def __init__(self, *outputs) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def generate(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        index = len(self.calls) - 1
        if index >= len(self.outputs):
            raise AssertionError("Appel LLM inattendu.")
        output = self.outputs[index]
        if isinstance(output, BaseException):
            raise output
        return (
            output
            if isinstance(output, str)
            else json.dumps(output, ensure_ascii=False)
        )

    def get_last_generation_meta(self):
        return {
            "provider": "fake",
            "request_name": self.calls[-1].get("request_name"),
        }


class GuidedResearchConversationTests(unittest.TestCase):
    def test_new_conversation_does_not_inherit_previous_document(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        manager = GuidedResearchSessionStateManager()
        manager.ensure_schema(engine)
        db = sessionmaker(bind=engine)()
        try:
            agent = EnnoScholarGuidedResearchAgent(
                state_manager=manager,
                llm=SequenceLLM(),
            )
            agent._contract_path = lambda project: Path(
                "C:/EnnoSmart/.nonexistent-tests/consultant_plan_contract.json"
            )
            # Même si un contrat projet historique existe, une nouvelle
            # conversation ne doit jamais le copier.
            agent._load_or_create_contract = lambda project: create_contract([
                {"section_id": "old", "title": "Ancien document"},
            ])
            project = SimpleNamespace(
                id=42,
                organisme="Organisation",
                project_name="Projet",
                year=2026,
                domain_label="Radar",
            )

            created = agent.create_session(
                db,
                project,
                created_by_user_id=7,
            )
            snapshot = agent.repository.snapshot(db, created.session_id)

            self.assertEqual(snapshot["writing_contract"], {})
            self.assertEqual(snapshot["draft"], {})
            self.assertFalse(snapshot["ready_to_write"])
            self.assertEqual(snapshot["state"], GuidedResearchState.BRIEF_IN_PROGRESS.value)
            self.assertEqual(
                snapshot["context"]["conversation_memory"],
                {},
            )
            self.assertTrue(
                snapshot["context"]["conversation_storage_isolated"]
            )
            self.assertIn(
                created.session_id,
                snapshot["context"]["contract_path"],
            )
        finally:
            db.close()
            engine.dispose()

    def test_new_conversation_snapshots_project_verrou_catalog_only(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        manager = GuidedResearchSessionStateManager()
        manager.ensure_schema(engine)
        db = sessionmaker(bind=engine)()
        known_verrous = [
            {"id": 101, "title": "Robustesse hors distribution"},
            {"id": 205, "title": "Traçabilité des données"},
            {"id": 309, "title": "Validation en conditions réelles"},
        ]
        try:
            agent = EnnoScholarGuidedResearchAgent(
                state_manager=manager,
                llm=SequenceLLM(),
            )
            agent._contract_path = lambda project: Path(
                "C:/EnnoSmart/.nonexistent-tests/consultant_plan_contract.json"
            )
            project = SimpleNamespace(
                id=42,
                organisme="Organisation",
                project_name="Projet",
                year=2026,
                domain_label="Domaine scientifique",
            )

            with patch.object(
                agent,
                "_diagnostic_project_context",
                return_value=(True, known_verrous),
            ):
                created = agent.create_session(
                    db,
                    project,
                    created_by_user_id=7,
                )

            snapshot = agent.repository.snapshot(db, created.session_id)
            self.assertEqual(
                snapshot["context"]["project_verrous"],
                known_verrous,
            )
            self.assertEqual(snapshot["writing_contract"], {})
            self.assertEqual(snapshot["draft"], {})
            self.assertEqual(snapshot["context"]["conversation_memory"], {})
        finally:
            db.close()
            engine.dispose()

    def test_saved_conversation_can_be_deleted(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        manager = GuidedResearchSessionStateManager()
        manager.ensure_schema(engine)
        db = sessionmaker(bind=engine)()
        try:
            created = manager.create_session(
                db,
                project_id=42,
                created_by_user_id=7,
                entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
                target_mode=GuidedResearchTargetMode.GLOBAL,
            )
            manager.delete_session(db, created.session_id)

            with self.assertRaises(GuidedResearchSessionNotFoundError):
                manager.get_session(db, created.session_id)
        finally:
            db.close()
            engine.dispose()

    def test_legacy_brief_metadata_does_not_hide_saved_conversation(self) -> None:
        now = datetime.now(timezone.utc)
        legacy_session = SimpleNamespace(
            id="legacy-session",
            project_id=1,
            created_by_user_id=1,
            entry_module=GuidedResearchEntryModule.ENNOSCHOLAR.value,
            target_mode=GuidedResearchTargetMode.GLOBAL.value,
            state=GuidedResearchState.BRIEF_PARSED.value,
            brief_json={
                "raw_request": "Conserver ce plan historique.",
                "requested_sections": [
                    {
                        "section_id": "section-1",
                        "title": "Introduction",
                        "order": 1,
                        "required": True,
                        "section_mode": "scientific_evidence",
                        "consultant_wording": "Ancienne métadonnée.",
                    }
                ],
            },
            context_json={},
            messages=[],
            ready_to_write=False,
            version=1,
            created_at=now,
            updated_at=now,
        )

        restored = GuidedResearchSessionStateManager()._to_domain(
            legacy_session,
            include_messages=False,
        )

        self.assertEqual(restored.brief.requested_sections[0].title, "Introduction")

    def test_write_command_keeps_complete_stored_corpus_when_only_subset_is_mentioned(
        self,
    ) -> None:
        stored = [f"A{index}" for index in range(1, 13)]

        resolved = _resolve_effective_writing_source_identifiers(
            ["A18", "C9"],
            stored,
            requested_count=12,
        )

        self.assertEqual(resolved, stored)

    def test_exact_new_selection_replaces_stored_corpus(self) -> None:
        resolved = _resolve_effective_writing_source_identifiers(
            ["A18", "C9"],
            ["A1", "A2", "A3"],
            requested_count=2,
        )

        self.assertEqual(resolved, ["A18", "C9"])

    def test_structured_write_authorization_routes_to_writer(self) -> None:
        classification = IntentClassification(
            intent=ConsultantIntent.START_WRITING,
            confidence=0.96,
            rationale="Le consultant ordonne explicitement de rédiger maintenant.",
            requested_actions=[ConsultantIntent.START_WRITING],
            explicit_write_command=True,
            corrected_message="C'est bon, rédige maintenant avec ce plan.",
        )
        self.assertEqual(
            _resolve_routed_intent(classification),
            ConsultantIntent.START_WRITING,
        )

    def test_writer_understands_baseline_verrou_corpus_without_research_additions(
        self,
    ) -> None:
        llm = SequenceLLM(
            _decision(
                ConsultantIntent.START_WRITING,
                (
                    "Je rédige avec les 11 articles initiaux liés aux verrous, "
                    "sans les ajouts de recherche."
                ),
                requested_actions=[ConsultantIntent.START_WRITING],
                use_current_sources_only=True,
                writing_source_scope="baseline_verrou_corpus",
                requested_source_count=11,
            )
        )
        result = ConversationUnderstandingService(llm).understand(
            session=GuidedResearchSessionData(
                project_id=1,
                entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
                target_mode=GuidedResearchTargetMode.GLOBAL,
            ),
            consultant_message=(
                "travaille juste avec les 11 articles qu'on avait pour les "
                "verrous, sans les articles de recherche, et rédige"
            ),
            project_context={"project": {"name": "AI-RADAR"}},
            current_plan=[],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result.classification.intent,
            ConsultantIntent.START_WRITING,
        )
        self.assertTrue(result.classification.use_current_sources_only)
        self.assertEqual(
            result.classification.writing_source_scope,
            "baseline_verrou_corpus",
        )
        self.assertEqual(result.classification.requested_source_count, 11)
        self.assertEqual(len(llm.calls), 1)

    def test_partial_plan_change_is_detected_as_delta_not_replacement(self) -> None:
        current = [
            {"section_id": "intro", "title": "Introduction", "level": 1},
            {"section_id": "methods", "title": "Méthodes", "level": 1},
            {"section_id": "limits", "title": "Limites", "level": 1},
        ]
        addition = [{
            "section_id": "insuffisances",
            "title": "Insuffisances des solutions existantes",
            "level": 1,
        }]
        self.assertFalse(
            _plan_candidate_covers_current(addition, current)
        )
        merged = _merge_additive_plan_update(current, addition)
        self.assertEqual(
            [row["title"] for row in merged],
            [
                "Introduction",
                "Méthodes",
                "Limites",
                "Insuffisances des solutions existantes",
            ],
        )
        self.assertTrue(
            _plan_candidate_covers_current(
                [*current, *addition],
                current,
            )
        )

    def test_contextual_intent_keeps_forbidden_writing_out_of_search(self) -> None:
        classification = IntentClassification(
            intent=ConsultantIntent.SEARCH_MORE,
            confidence=0.98,
            rationale="Recherche demandée, rédaction explicitement interdite.",
            requested_actions=[ConsultantIntent.SEARCH_MORE],
            forbidden_actions=[ConsultantIntent.START_WRITING],
            explicit_research_command=True,
            corrected_message=(
                "Recherche les preuves et propose des sections sans rédiger."
            ),
        )
        self.assertEqual(
            _resolve_routed_intent(classification),
            ConsultantIntent.SEARCH_MORE,
        )

    def test_converse_turns_never_request_an_action_payload(self) -> None:
        session = GuidedResearchSessionData(
            project_id=1,
            entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
            target_mode=GuidedResearchTargetMode.GLOBAL,
        )

        for message, answer in (
            ("bONJOUR", "Bonjour ! Comment allez-vous ?"),
            ("cAVA ?", "Ça va bien, merci. Et vous ?"),
        ):
            with self.subTest(message=message):
                llm = SequenceLLM(
                    _decision(ConsultantIntent.CONVERSE, answer),
                )
                result = ConversationUnderstandingService(llm).understand(
                    session=session,
                    consultant_message=message,
                    project_context={
                        "project": {"name": "Projet générique"},
                        "validated_article_cards": [
                            {"title": "Une ancienne source sans rapport"}
                        ],
                    },
                    current_plan=[{
                        "section_id": "introduction",
                        "title": "Introduction",
                    }],
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(
                    result.classification.intent,
                    ConsultantIntent.CONVERSE,
                )
                self.assertEqual(result.classification.requested_actions, [])
                self.assertEqual(result.assistant_message, answer)
                self.assertEqual(result.plan, [])
                self.assertEqual(result.topics, [])
                self.assertEqual(result.constraints, [])
                self.assertEqual(result.search_requests, [])
                self.assertEqual(len(llm.calls), 1)
                self.assertEqual(
                    llm.calls[0]["request_name"],
                    "ennoscholar:guided_research:conversation_decision",
                )
                self.assertNotIn(
                    "action_payload",
                    llm.calls[0]["request_name"],
                )

    def test_detailed_plan_request_uses_decision_then_structured_action(self) -> None:
        decision = _decision(
            ConsultantIntent.PROPOSE_PLAN,
            "Je vous propose un plan détaillé pour validation.",
            requested_actions=[ConsultantIntent.PROPOSE_PLAN],
            replace_current_plan=True,
            plan_generation_mode="initial",
            plan_document_scope="unspecified",
        )
        action = {
            "plan": [
                {
                    "section_id": "introduction",
                    "title": "Introduction générale",
                    "objective": "Poser le contexte et les enjeux.",
                    "parent_id": None,
                    "level": 1,
                },
                {
                    "section_id": "methodes",
                    "title": "Méthodes",
                    "objective": "Comparer les familles de méthodes.",
                    "parent_id": None,
                    "level": 1,
                },
            ],
            "topics": [],
            "constraints": [],
            "search_requests": [],
        }
        llm = SequenceLLM(decision, action)
        result = ConversationUnderstandingService(llm).understand(
            session=GuidedResearchSessionData(
                project_id=1,
                entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
                target_mode=GuidedResearchTargetMode.GLOBAL,
            ),
            consultant_message="non je veut un plan detailler",
            project_context={"project": {"name": "Projet générique"}},
            current_plan=[],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result.classification.intent,
            ConsultantIntent.PROPOSE_PLAN,
        )
        self.assertTrue(result.classification.replace_current_plan)
        self.assertEqual(
            [section["title"] for section in result.plan],
            ["Introduction générale", "Méthodes"],
        )
        self.assertEqual(result.search_requests, [])
        self.assertEqual(
            [call["request_name"] for call in llm.calls],
            [
                "ennoscholar:guided_research:conversation_decision",
                "ennoscholar:guided_research:action_payload",
            ],
        )

    def test_first_plan_reference_restores_snapshot_before_local_change(self) -> None:
        first_plan = [
            {
                "section_id": "original-a",
                "title": "Famille originale A",
                "objective": "Comparer la première famille.",
                "parent_id": None,
                "level": 1,
            },
            {
                "section_id": "original-b",
                "title": "Famille originale B",
                "objective": "Comparer la seconde famille.",
                "parent_id": None,
                "level": 1,
            },
        ]
        llm = SequenceLLM(
            _decision(
                ConsultantIntent.CHANGE_PLAN,
                "Je restaure le premier plan et j'ajoute la section.",
                requested_actions=[ConsultantIntent.CHANGE_PLAN],
                replace_current_plan=True,
                plan_reference="first",
                referenced_plan_version=None,
            ),
            {
                "plan": [{
                    "section_id": "insuffisances",
                    "title": "Insuffisances des solutions existantes",
                    "objective": "Mettre en valeur les verrous.",
                    "parent_id": None,
                    "level": 1,
                }],
                "topics": [],
                "constraints": [],
                "search_requests": [],
            },
            {
                "plan": [
                    *first_plan,
                    {
                        "section_id": "insuffisances",
                        "title": "Insuffisances des solutions existantes",
                        "objective": "Mettre en valeur les verrous.",
                        "parent_id": None,
                        "level": 1,
                    },
                ],
                "topics": [],
                "constraints": [],
                "search_requests": [],
            },
        )
        session = GuidedResearchSessionData(
            project_id=1,
            entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
            target_mode=GuidedResearchTargetMode.GLOBAL,
            messages=[
                ConversationTurn(
                    role=ConversationRole.ASSISTANT,
                    content="Voici le premier plan.",
                    metadata={
                        "contract": {
                            "plan_version": 1,
                            "consultant_edited_plan": first_plan,
                        }
                    },
                ),
                ConversationTurn(
                    role=ConversationRole.ASSISTANT,
                    content="Voici le plan courant.",
                    metadata={
                        "contract": {
                            "plan_version": 2,
                            "consultant_edited_plan": [{
                                "section_id": "generic",
                                "title": "Plan générique courant",
                            }],
                        }
                    },
                ),
            ],
        )

        result = ConversationUnderstandingService(llm).understand(
            session=session,
            consultant_message="non, le premier plan avec la section insuffisances",
            project_context={"project": {"name": "Projet générique"}},
            current_plan=[{
                "section_id": "generic",
                "title": "Plan générique courant",
            }],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            [row["title"] for row in result.plan],
            [
                "Famille originale A",
                "Famille originale B",
                "Insuffisances des solutions existantes",
            ],
        )
        self.assertIn("Famille originale A", llm.calls[1]["prompt"])
        self.assertEqual(
            [row["valid"] for row in result.interpreter["action_attempts"]],
            [False, True],
        )
        self.assertEqual(len(_plan_history_from_session(session)), 2)

    def test_contextual_understanding_exposes_fallback_cause(self) -> None:
        class FailingLLM:
            def generate(self, *args, **kwargs):
                raise TimeoutError("provider timeout")

        service = ConversationUnderstandingService(FailingLLM())
        session = GuidedResearchSessionData(
            project_id=1,
            entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
            target_mode=GuidedResearchTargetMode.GLOBAL,
        )

        result = service.understand(
            session=session,
            consultant_message="donne-moi un autre plan totalement différent",
            project_context={"project": {"name": "Projet générique"}},
            current_plan=[],
        )

        self.assertIsNone(result)
        self.assertEqual(
            service.get_last_failure()["error_type"],
            "TimeoutError",
        )
        self.assertEqual(
            service.get_last_failure()["stage"],
            "structured_conversation",
        )

    def test_natural_targeted_plan_ignores_payloads_outside_selected_intent(
        self,
    ) -> None:
        session = GuidedResearchSessionData(
            project_id=1,
            entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
            target_mode=GuidedResearchTargetMode.GLOBAL,
            context={"operating_mode": "diagnostic_backed"},
        )
        titles = [
            "Contexte scientifique et recours aux données SAR synthétiques",
            "Écart entre données synthétiques et données réelles",
            "Limites des approches existantes et incertitude scientifique",
            "Synthèse critique et positionnement du verrou",
        ]
        llm = SequenceLLM(
            _decision(
                ConsultantIntent.PROPOSE_PLAN,
                "J'ai compris : ce plan portera uniquement sur le verrou 1.",
                requested_actions=[
                    ConsultantIntent.PROPOSE_PLAN,
                    ConsultantIntent.START_WRITING,
                ],
                replace_current_plan=True,
                plan_generation_mode="initial",
                plan_document_scope="state_of_art",
                target_verrou_ids=["803"],
                verrou_scope="per_verrou",
            ),
            {
                "plan": [
                    {
                        "section_id": f"sec{index}",
                        "title": title,
                        "objective": f"Analyser {title.casefold()}.",
                        "parent_id": None,
                        "level": 1,
                    }
                    for index, title in enumerate(titles, start=1)
                ],
                # Reproduit le défaut réel observé : le fournisseur avait rempli
                # un champ secondaire malgré l'intention de plan sélectionnée.
                "constraints": ["Rédiger uniquement en français."],
                "project_brief": {
                    "project_name": "AI-RADAR",
                    "domain": "SAR ATR",
                },
            },
        )

        result = ConversationUnderstandingService(llm).understand(
            session=session,
            consultant_message=(
                "je veut rediger une etat de l'art mais seulement du verrou 1 "
                "et voici le plan que je veut\n" + "\n".join(titles)
            ),
            project_context={
                "project": {"name": "AI-RADAR"},
                "operating_mode": "diagnostic_backed",
                "current_verrous": [{
                    "id": 803,
                    "title": (
                        "Incertitude sur la généralisation des modèles ATR "
                        "entraînés sur données SAR synthétiques aux données réelles"
                    ),
                }],
            },
            current_plan=[],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.classification.intent, ConsultantIntent.PROPOSE_PLAN)
        self.assertEqual(result.classification.target_verrou_ids, ["803"])
        self.assertEqual([row["title"] for row in result.plan], titles)
        self.assertEqual(result.constraints, [])
        self.assertEqual(result.project_brief, {})
        self.assertEqual(
            [row["valid"] for row in result.interpreter["action_attempts"]],
            [True],
        )

    def test_global_existing_verrous_are_not_treated_as_new_verrous(self) -> None:
        session = GuidedResearchSessionData(
            project_id=1,
            entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
            target_mode=GuidedResearchTargetMode.GLOBAL,
            context={"operating_mode": "diagnostic_backed"},
        )
        known_verrous = [
            {"id": 101, "title": "Robustesse hors distribution"},
            {"id": 205, "title": "Traçabilité des données"},
            {"id": 309, "title": "Validation en conditions réelles"},
        ]
        llm = SequenceLLM(
            _decision(
                ConsultantIntent.ADD_VERROU_AND_SEARCH,
                "Indiquez le verrou à ajouter.",
                requested_actions=[ConsultantIntent.ADD_VERROU_AND_SEARCH],
                explicit_research_command=True,
                verrou_scope="global",
            ),
            _decision(
                ConsultantIntent.PROPOSE_PLAN,
                "Je prépare un plan global couvrant les verrous retenus.",
                requested_actions=[
                    ConsultantIntent.PROPOSE_PLAN,
                    ConsultantIntent.START_WRITING,
                ],
                replace_current_plan=True,
                plan_generation_mode="initial",
                plan_document_scope="state_of_art",
                verrou_scope="global",
            ),
            {
                "plan": [
                    {
                        "section_id": "synthese-transversale",
                        "title": "Synthèse transversale des verrous",
                        "objective": (
                            "Comparer les preuves, limites et incertitudes "
                            "associées aux verrous retenus."
                        ),
                        "parent_id": None,
                        "level": 1,
                    }
                ]
            },
        )

        result = ConversationUnderstandingService(llm).understand(
            session=session,
            consultant_message=(
                "je veux rédiger l'état de l'art global de tous les verrous "
                "déjà retenus pour ce projet"
            ),
            project_context={
                "project": {"name": "Projet générique"},
                "operating_mode": "diagnostic_backed",
                "current_verrous": known_verrous,
            },
            current_plan=[],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.classification.intent, ConsultantIntent.PROPOSE_PLAN)
        self.assertEqual(result.classification.verrou_scope, "global")
        self.assertEqual(result.verrous, [])
        self.assertEqual(result.search_requests, [])
        self.assertEqual(
            [row["valid"] for row in result.interpreter["decision_attempts"]],
            [False, True],
        )
        self.assertEqual(len(result.plan), 1)

    def test_targeted_verrou_scope_is_verified_and_applied_to_every_section(
        self,
    ) -> None:
        classification = IntentClassification.model_validate(
            _decision(
                ConsultantIntent.DESCRIBE_REQUIREMENTS,
                "Je me limite au verrou demandé.",
                target_verrou_ids=["1"],
                verrou_scope="per_verrou",
            )["classification"]
        )
        known = [
            {"id": 803, "title": "Généralisation SAR synthétique-réel"},
            {"id": 804, "title": "Calage des simulations SAR"},
        ]

        scope = _resolve_targeted_verrou_scope(classification, known)
        scoped_plan = _apply_verrou_scope_to_plan(
            [
                {"section_id": "s1", "title": "Contexte", "verrou_ids": []},
                {"section_id": "s2", "title": "Limites", "verrou_ids": ["804"]},
            ],
            scope["active_verrou_ids"],
        )

        self.assertEqual(scope["review_scope"], "per_verrou")
        self.assertEqual(scope["active_verrou_ids"], ["803"])
        self.assertEqual(
            [row["verrou_ids"] for row in scoped_plan],
            [["803"], ["803"]],
        )

    def test_global_scope_exposes_catalog_without_narrowing_ids(self) -> None:
        classification = IntentClassification.model_validate(
            _decision(
                ConsultantIntent.PROPOSE_PLAN,
                "Je couvre tous les verrous retenus.",
                verrou_scope="global",
            )["classification"]
        )
        known = [
            {"id": 101, "title": "Robustesse"},
            {"id": 205, "title": "Traçabilité"},
        ]

        scope = _resolve_targeted_verrou_scope(classification, known)

        self.assertEqual(scope["review_scope"], "global")
        self.assertEqual(scope["active_verrou_ids"], [])
        self.assertEqual(scope["active_verrous"], known)

    def test_conversation_contract_paths_are_isolated(self) -> None:
        agent = object.__new__(EnnoScholarGuidedResearchAgent)
        agent._contract_path = lambda project: Path(
            "C:/EnnoSmart/.nonexistent-tests/consultant_plan_contract.json"
        )

        first = agent._session_contract_path(
            SimpleNamespace(id=1),
            "11111111-1111-1111-1111-111111111111",
        )
        second = agent._session_contract_path(
            SimpleNamespace(id=1),
            "22222222-2222-2222-2222-222222222222",
        )

        self.assertNotEqual(first, second)
        self.assertIn("11111111-1111-1111-1111-111111111111", str(first))
        self.assertIn("22222222-2222-2222-2222-222222222222", str(second))

    def test_empty_memory_delta_cannot_erase_existing_project_memory(self) -> None:
        session = GuidedResearchSessionData(
            project_id=1,
            entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
            target_mode=GuidedResearchTargetMode.GLOBAL,
            context={
                "conversation_memory": {
                    "project_facts": ["Le radar opère en bande X."],
                    "consultant_preferences": ["Réponse en français."],
                    "validated_decisions": [],
                    "rejected_options": [],
                    "open_questions": [],
                    "current_focus": "Plan scientifique",
                    "last_consultant_goal": "Construire l'état de l'art",
                }
            },
        )
        llm = SequenceLLM(
            _decision(
                ConsultantIntent.DESCRIBE_REQUIREMENTS,
                "J'intègre cette contrainte.",
                requested_actions=[
                    ConsultantIntent.DESCRIBE_REQUIREMENTS
                ],
            ),
            {
                "plan": [],
                "topics": [],
                "constraints": ["Le document doit rester en français."],
                "search_requests": [],
            },
        )

        result = ConversationUnderstandingService(llm).understand(
            session=session,
            consultant_message="le document doit rester en français",
            project_context={"project": {"name": "Projet générique"}},
            current_plan=[],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result.memory.project_facts,
            ["Le radar opère en bande X."],
        )
        self.assertEqual(
            result.memory.consultant_preferences,
            ["Réponse en français."],
        )
        self.assertEqual(result.memory.current_focus, "Plan scientifique")

    def test_multidimensional_search_is_repaired_into_concise_query_portfolio(self) -> None:
        session = GuidedResearchSessionData(
            project_id=3,
            entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
            target_mode=GuidedResearchTargetMode.PER_VERROU,
            context={"operating_mode": "standalone_chat"},
        )
        decision = _decision(
            ConsultantIntent.SEARCH_MORE,
            "Je lance une recherche ciblée.",
            requested_actions=[ConsultantIntent.SEARCH_MORE],
            explicit_research_command=True,
        )
        common = {
            "query_kind": "direct_scientific_evidence",
            "entity_name": "predictive reliability under distribution shift",
            "entity_type": "other",
            "required_terms": ["predictive reliability", "distribution shift"],
            "target_verrous": ["SV-1"],
            "requested_dimensions": [
                "experimental results",
                "validation protocols",
                "limitations",
            ],
            "target_context_dimensions": [
                "industrial equipment",
                "changing operating conditions",
            ],
            "require_direct_evidence": True,
            "source_preferences": ["scientific_articles"],
        }
        llm = SequenceLLM(
            decision,
            {
                "search_requests": [{
                    **common,
                    "query": (
                        "out-of-distribution reliability predictive models limited "
                        "historical data changing operating regimes thermal loads "
                        "usage profiles equipment characteristics experimental "
                        "evidence validation protocols limitations"
                    ),
                }],
            },
            {
                "search_requests": [
                    {
                        **common,
                        "query": "industrial time series distribution shift",
                    },
                    {
                        **common,
                        "query": "remaining useful life domain adaptation",
                    },
                    {
                        **common,
                        "query": "thermal anomaly transfer learning",
                    },
                ],
            },
        )

        result = ConversationUnderstandingService(llm).understand(
            session=session,
            consultant_message=(
                "Recherche les preuves, méthodes, protocoles et limites pour "
                "le verrou enregistré, sans rédiger."
            ),
            project_context={
                "project": {"name": "Projet autonome"},
                "operating_mode": "standalone_chat",
                "current_verrous": [{"id": "SV-1", "title": "Robustesse"}],
            },
            current_plan=[],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(len(result.search_requests), 3)
        self.assertEqual(
            [row["valid"] for row in result.interpreter["action_attempts"]],
            [False, True],
        )

    def test_schema_error_is_repaired_by_the_llm_without_lexical_coercion(self) -> None:
        invalid = _decision(
            "request_for_plan_completion",
            "Réponse invalide.",
            requested_actions=["CONTINUE_PLAN_LIST"],
        )
        repaired = _decision(
            ConsultantIntent.CONVERSE,
            "Bonjour ! Comment puis-je vous aider ?",
        )
        llm = SequenceLLM(invalid, repaired)
        service = ConversationUnderstandingService(llm)
        session = GuidedResearchSessionData(
            project_id=1,
            entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
            target_mode=GuidedResearchTargetMode.GLOBAL,
        )
        result = service.understand(
            session=session,
            consultant_message="Bonjour",
            project_context={"project": {"name": "Projet générique"}},
            current_plan=[],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result.classification.intent,
            ConsultantIntent.CONVERSE,
        )
        self.assertEqual(result.classification.requested_actions, [])
        self.assertEqual(len(llm.calls), 2)
        self.assertTrue(llm.calls[1]["request_name"].endswith(":schema_repair"))
        self.assertEqual(
            [row["valid"] for row in result.interpreter["decision_attempts"]],
            [False, True],
        )
        self.assertEqual(
            result.interpreter["action_attempts"],
            [],
        )
        self.assertEqual(service.get_last_failure(), {})

    def test_action_schema_error_is_repaired_before_plan_is_exposed(self) -> None:
        llm = SequenceLLM(
            _decision(
                ConsultantIntent.CHANGE_PLAN,
                "Je prépare le plan détaillé.",
                requested_actions=[ConsultantIntent.CHANGE_PLAN],
                plan_reference="current",
            ),
            {
                "plan": [{
                    "section_id": "introduction",
                    "title": "Introduction",
                }],
                "topics": ["chaîne interdite"],
                "constraints": [],
                "search_requests": [],
            },
            {
                "plan": [{
                    "section_id": "introduction",
                    "title": "Introduction générale",
                    "objective": "Présenter le projet.",
                    "parent_id": None,
                    "level": 1,
                }],
                "topics": [],
                "constraints": [],
                "search_requests": [],
            },
        )
        service = ConversationUnderstandingService(llm)
        result = service.understand(
            session=GuidedResearchSessionData(
                project_id=1,
                entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
                target_mode=GuidedResearchTargetMode.GLOBAL,
            ),
            consultant_message="non je veut un plan detailler",
            project_context={"project": {"name": "Projet générique"}},
            current_plan=[],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.plan[0]["title"], "Introduction générale")
        self.assertEqual(
            [row["valid"] for row in result.interpreter["action_attempts"]],
            [False, True],
        )
        self.assertEqual(
            len(llm.calls),
            3,
        )
        self.assertTrue(llm.calls[-1]["request_name"].endswith(":schema_repair"))

    def test_structured_search_request_reaches_domain_model_as_dict(self) -> None:
        llm = SequenceLLM(
            _decision(
                ConsultantIntent.SEARCH_MORE,
                "Je lance la recherche ciblée.",
                requested_actions=[ConsultantIntent.SEARCH_MORE],
                explicit_research_command=True,
            ),
            {
                "plan": [],
                "topics": [],
                "constraints": [],
                "search_requests": [{
                    "query": "differentiable ray tracing SAR evidence",
                    "query_kind": "scientific_evidence",
                    "entity_name": "differentiable ray tracing",
                    "entity_type": "scientific_method",
                    "target_context_dimensions": ["SAR"],
                    "require_direct_evidence": False,
                }],
            },
        )
        result = ConversationUnderstandingService(llm).understand(
            session=GuidedResearchSessionData(
                project_id=1,
                entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
                target_mode=GuidedResearchTargetMode.GLOBAL,
            ),
            consultant_message="cherche des articles sur ce sujet",
            project_context={"project": {"name": "Projet générique"}},
            current_plan=[],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result.search_requests[0]["query_kind"],
            "scientific_evidence",
        )
        self.assertIsInstance(result.search_requests[0], dict)

    def test_agent_converse_turn_preserves_project_state_and_brief(self) -> None:
        brief = ConsultantBrief(
            raw_request="État de l'art validé.",
            general_constraints=["Conserver la structure validée."],
        )
        session = GuidedResearchSessionData(
            session_id="social-session",
            project_id=1,
            entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
            target_mode=GuidedResearchTargetMode.GLOBAL,
            state=GuidedResearchState.READY_TO_WRITE,
            brief=brief,
            ready_to_write=True,
        )

        class RecordingStateManager:
            def __init__(self):
                self.messages = []
                self.brief_updates = []

            def get_session(self, db, session_id):
                return session

            def append_message(self, db, session_id, **kwargs):
                self.messages.append(kwargs)

            def update_brief(self, db, session_id, value):
                self.brief_updates.append(value)

        class RecordingRepository:
            def __init__(self):
                self.updates = []

            def update(self, db, session_id, **kwargs):
                self.updates.append(kwargs)

        class ForbiddenResearch:
            def search(self, *args, **kwargs):
                raise AssertionError(
                    "Un tour CONVERSE ne doit lancer aucune recherche."
                )

        state_manager = RecordingStateManager()
        repository = RecordingRepository()
        llm = SequenceLLM(
            _decision(
                ConsultantIntent.CONVERSE,
                "Bonjour ! Comment allez-vous ?",
            )
        )
        agent = EnnoScholarGuidedResearchAgent(
            state_manager=state_manager,
            repository=repository,
            llm=llm,
            research=ForbiddenResearch(),
        )
        agent._contract_path = lambda project: Path(
            "C:/EnnoSmart/.nonexistent-tests/contract.json"
        )
        agent._load_contract_snapshot = lambda project: {}
        agent._conversation_project_context = lambda project, current: {
            "project": {"name": "Projet générique"}
        }
        response = agent.handle_message(
            object(),
            SimpleNamespace(id=1),
            session_id=session.session_id,
            consultant_message="Bonjour",
        )

        self.assertEqual(
            response.intent,
            ConsultantIntent.CONVERSE,
        )
        self.assertEqual(
            response.assistant_message,
            "Bonjour ! Comment allez-vous ?",
        )
        self.assertEqual(response.state, GuidedResearchState.READY_TO_WRITE)
        self.assertTrue(response.ready_to_write)
        self.assertEqual(response.brief, brief)
        self.assertNotIn(
            "parties restent peu couvertes",
            response.assistant_message.casefold(),
        )
        self.assertEqual(len(state_manager.messages), 2)
        self.assertEqual(state_manager.brief_updates, [])
        self.assertEqual(len(llm.calls), 1)
        self.assertTrue(repository.updates)
        for update in repository.updates:
            self.assertNotIn("state", update)
            self.assertNotIn("coverage", update)
            self.assertNotIn("writing_contract", update)
            self.assertNotIn("ready_to_write", update)

    def test_two_invalid_decisions_fall_back_to_unknown_without_action(self) -> None:
        session = GuidedResearchSessionData(
            session_id="invalid-decision-session",
            project_id=1,
            entry_module=GuidedResearchEntryModule.ENNOSCHOLAR,
            target_mode=GuidedResearchTargetMode.GLOBAL,
            state=GuidedResearchState.BRIEF_IN_PROGRESS,
        )

        class RecordingStateManager:
            def __init__(self):
                self.messages = []
                self.brief_updates = []

            def get_session(self, db, session_id):
                return session

            def append_message(self, db, session_id, **kwargs):
                self.messages.append(kwargs)

            def update_brief(self, db, session_id, value):
                self.brief_updates.append(value)

        class RecordingRepository:
            def __init__(self):
                self.updates = []

            def update(self, db, session_id, **kwargs):
                self.updates.append(kwargs)

        class ForbiddenResearch:
            def search(self, *args, **kwargs):
                raise AssertionError(
                    "Une décision invalide ne doit lancer aucune recherche."
                )

        llm = SequenceLLM(
            {"classification": {"intent": "invented"}},
            {"classification": {"intent": "still_invented"}},
        )
        state_manager = RecordingStateManager()
        repository = RecordingRepository()
        agent = EnnoScholarGuidedResearchAgent(
            state_manager=state_manager,
            repository=repository,
            llm=llm,
            research=ForbiddenResearch(),
        )
        agent._contract_path = lambda project: Path(
            "C:/EnnoSmart/.nonexistent-tests/contract.json"
        )
        agent._load_contract_snapshot = lambda project: {}
        agent._conversation_project_context = lambda project, current: {
            "project": {"name": "Projet générique"}
        }

        response = agent.handle_message(
            object(),
            SimpleNamespace(id=1),
            session_id=session.session_id,
            consultant_message="???",
        )

        self.assertEqual(response.intent, ConsultantIntent.UNKNOWN)
        self.assertEqual(response.state, GuidedResearchState.BRIEF_IN_PROGRESS)
        self.assertFalse(response.ready_to_write)
        self.assertEqual(state_manager.brief_updates, [])
        self.assertEqual(len(llm.calls), 2)
        for update in repository.updates:
            self.assertNotIn("coverage", update)
            self.assertNotIn("writing_contract", update)

    def test_structured_search_intent_is_authoritative(self) -> None:
        classification = IntentClassification(
            intent=ConsultantIntent.SEARCH_MORE,
            confidence=0.62,
            rationale="Le message court a été interprété comme une recherche.",
            requested_actions=[ConsultantIntent.SEARCH_MORE],
            explicit_research_command=False,
        )
        self.assertEqual(
            _resolve_routed_intent(classification),
            ConsultantIntent.SEARCH_MORE,
        )

    def test_forbidden_write_cannot_bypass_research_confirmation(self) -> None:
        classification = IntentClassification(
            intent=ConsultantIntent.START_WRITING,
            confidence=0.58,
            rationale="Classification contradictoire.",
            requested_actions=[
                ConsultantIntent.START_WRITING,
                ConsultantIntent.SEARCH_MORE,
            ],
            forbidden_actions=[ConsultantIntent.START_WRITING],
            explicit_write_command=False,
            explicit_research_command=False,
        )
        self.assertEqual(
            _resolve_routed_intent(classification),
            ConsultantIntent.UNKNOWN,
        )

    def test_partial_additive_plan_update_preserves_existing_sections(self) -> None:
        current = [
            {
                "section_id": "introduction",
                "title": "Introduction",
                "objective": "Présenter le problème.",
            },
            {
                "section_id": "methodes",
                "title": "Familles de méthodes",
                "objective": "Comparer les méthodes.",
            },
            {
                "section_id": "limites",
                "title": "Limites",
                "objective": "Analyser les limites.",
            },
        ]
        partial_update = [
            {
                "section_id": "methodes",
                "title": "Familles de méthodes",
                "objective": "Comparer les méthodes et le rendu différentiable.",
            },
            {
                "section_id": "rendu_differentiel",
                "title": "Rendu différentiable générique",
                "objective": "Étudier les problèmes inverses.",
                "parent_id": "methodes",
                "level": 2,
            },
        ]
        merged = _merge_additive_plan_update(current, partial_update)
        self.assertEqual(
            [row["section_id"] for row in merged],
            ["introduction", "methodes", "limites", "rendu_differentiel"],
        )
        self.assertEqual(
            merged[1]["objective"],
            "Comparer les méthodes et le rendu différentiable.",
        )

    def test_additive_generic_ids_do_not_overwrite_existing_sections(self) -> None:
        current = [
            {
                "section_id": "section_1",
                "title": "Introduction",
                "objective": "",
            },
            {
                "section_id": "section_2",
                "title": "Méthodes",
                "objective": "",
            },
        ]
        additions = [
            {
                "section_id": "section_1",
                "title": "Protocole de validation",
                "objective": "",
            },
            {
                "section_id": "section_2",
                "title": "Conclusion",
                "objective": "",
            },
        ]
        merged = _merge_additive_plan_update(current, additions)
        self.assertEqual(
            [row["title"] for row in merged],
            ["Introduction", "Méthodes", "Protocole de validation", "Conclusion"],
        )
        self.assertEqual(len({row["section_id"] for row in merged}), 4)

    def test_combined_approve_and_write_command_approves_contract(self) -> None:
        contract = create_contract([{
            "section_id": "section_1",
            "title": "Problème scientifique",
            "objective": "Expliquer l'incertitude.",
            "verrou_ids": [],
        }])
        message = "aprouve le plan et lance la redaction"
        approved = _approve_for_combined_write(
            contract,
            message,
            approved_by="consultant",
            explicit_approval=True,
        )
        self.assertTrue(approved["approved_plan"])
        self.assertTrue(authorize_writing(approved)["writing_authorized"])

    def test_identical_plan_does_not_cancel_existing_approval(self) -> None:
        plan = [{
            "section_id": "section_1",
            "title": "Problème scientifique",
            "objective": "Expliquer l'incertitude.",
            "verrou_ids": [],
        }]
        self.assertFalse(_plan_materially_changed(plan, plan))

    def test_zenodo_software_is_not_tagged_as_scientific_article(self) -> None:
        service = WebResearchService()
        candidate = service._normalize_scientific(
            {
                "title": "A reproducible software package",
                "abstract": "Implementation and configuration details.",
                "publication_types": ["software"],
                "url": "https://example.test/tool",
            },
            provider="zenodo",
            request={"query": "reproducible package"},
            rank=1,
        )
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate["candidate_kind"], "research_output")
        self.assertFalse(candidate["scientific_evidence_eligible"])
        self.assertNotIn("result", candidate["evidence_scope"])

    def test_documentation_only_request_does_not_query_scientific_indexes(self) -> None:
        request = {
            "query": "Dr.Jit automatic differentiation",
            "entity_name": "Dr.Jit",
            "entity_type": "documentation",
            "query_kind": "official_documentation",
            "source_preferences": ["documentation officielle"],
        }
        self.assertTrue(WebResearchService._wants_documentation(request))
        self.assertFalse(WebResearchService._wants_scientific(request))

    def test_combined_request_requires_articles_and_official_documentation(self) -> None:
        request = {
            "query": "Mitsuba 3 inverse rendering",
            "entity_name": "Mitsuba 3",
            "entity_type": "tool",
            "source_preferences": [
                "articles scientifiques",
                "documentation officielle",
            ],
        }
        self.assertTrue(WebResearchService._wants_documentation(request))
        self.assertTrue(WebResearchService._wants_scientific(request))
        completeness = WebResearchService._research_completeness(
            [request],
            [{
                "candidate_kind": "software_repository",
                "official_source": False,
            }],
        )
        self.assertIn(
            "scientific_articles",
            completeness["missing_source_types"],
        )
        self.assertIn(
            "official_documentation_pages",
            completeness["missing_source_types"],
        )

    def test_combined_documentation_entities_are_searched_separately(self) -> None:
        expanded = WebResearchService._expand_documentation_entities([{
            "query": "Mitsuba 3 Dr.Jit inverse rendering",
            "entity_name": "Mitsuba 3 et Dr.Jit",
            "entity_type": "tool",
            "requested_dimensions": [
                "inverse rendering",
                "automatic differentiation",
            ],
            "source_preferences": ["documentation officielle"],
        }])
        self.assertEqual(
            [row["entity_name"] for row in expanded],
            ["Mitsuba 3", "Dr.Jit"],
        )

    def test_provider_queries_are_short_and_split_by_source_type(self) -> None:
        planned = WebResearchService._plan_provider_requests([{
            "query": (
                "NVIDIA OptiX ray tracing performance radar SAR "
                "transferability limitations scientific validation"
            ),
            "entity_name": "NVIDIA OptiX",
            "entity_type": "tool",
            "requested_dimensions": [
                "ray tracing",
                "performance",
                "radar SAR",
                "transferability",
                "limitations",
                "scientific validation",
            ],
            "source_preferences": [
                "articles scientifiques",
                "documentation officielle",
            ],
        }])
        self.assertEqual(len(planned), 2)
        by_kind = {row["query_kind"]: row for row in planned}
        self.assertEqual(
            by_kind["official_documentation"]["query"],
            "NVIDIA OptiX official documentation",
        )
        self.assertEqual(
            by_kind["scientific_evidence"]["query"],
            "NVIDIA OptiX ray tracing radar SAR",
        )
        self.assertNotIn(
            "transferability",
            by_kind["scientific_evidence"]["query"],
        )

    def test_openai_web_discovery_returns_direct_and_official_sources(self) -> None:
        class FakeWebLLM:
            def web_search(self, *args, **kwargs):
                return {
                    "ok": True,
                    "model": "fake-web",
                    "answer": "Two grounded sources were found.",
                    "sources": [
                        {
                            "url": "https://developer.nvidia.com/rtx/ray-tracing/optix",
                            "title": "NVIDIA OptiX Ray Tracing Engine",
                            "snippet": "Official NVIDIA OptiX documentation.",
                            "cited": True,
                        },
                        {
                            "url": "https://arxiv.org/abs/2005.09736",
                            "title": (
                                "NVIDIA OptiX Hardware-Accelerated "
                                "SAR Simulation"
                            ),
                            "snippet": (
                                "OptiX computes SAR phase histories and "
                                "is validated on point targets."
                            ),
                            "cited": True,
                        },
                    ],
                }

        class OpenAIOnlyResearchService(WebResearchService):
            def _run_job(self, provider, request):
                if provider == "openai_web":
                    return super()._run_job(provider, request)
                return []

        service = OpenAIOnlyResearchService(
            llm=FakeWebLLM(),
            enable_llm_rerank=False,
        )
        result = service.search([{
            "query": "NVIDIA OptiX radar SAR",
            "entity_name": "NVIDIA OptiX",
            "entity_type": "tool",
            "requested_dimensions": ["ray tracing", "radar SAR"],
            "target_context_dimensions": ["radar SAR"],
            "required_terms": ["NVIDIA OptiX"],
            "source_preferences": [
                "articles scientifiques",
                "documentation officielle",
            ],
        }])
        roles = {
            row["title"]: row["relevance_role"]
            for row in result["candidates"]
        }
        self.assertEqual(
            roles["NVIDIA OptiX Ray Tracing Engine"],
            "official_documentation",
        )
        self.assertEqual(
            roles["NVIDIA OptiX Hardware-Accelerated SAR Simulation"],
            "direct_evidence",
        )
        self.assertTrue(result["completeness"]["complete"], result)

    def test_openai_response_parser_collects_all_consulted_sources(self) -> None:
        parsed = LLMClient._parse_openai_web_search_response({
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {
                                "type": "url",
                                "url": "https://developer.example/docs",
                            },
                            {
                                "type": "url",
                                "url": "https://example.org/paper?utm_source=search",
                            },
                        ],
                    },
                },
                {
                    "type": "message",
                    "content": [{
                        "type": "output_text",
                        "text": "The direct scientific result is available here.",
                        "annotations": [{
                            "type": "url_citation",
                            "url": "https://example.org/paper",
                            "title": "Direct scientific paper",
                            "start_index": 4,
                            "end_index": 38,
                        }],
                    }],
                },
            ],
        })
        self.assertEqual(len(parsed["sources"]), 2)
        cited = next(
            row
            for row in parsed["sources"]
            if row["url"].startswith("https://example.org/paper")
        )
        self.assertTrue(cited["cited"])
        self.assertEqual(cited["title"], "Direct scientific paper")

    def test_web_publications_are_not_misclassified_as_documentation(self) -> None:
        self.assertTrue(
            _looks_like_scientific_web_source(
                "https://research.nvidia.com/sites/default/files/pubs/2006-07_OptiX-A-General/optix.pdf",
                "OptiX: A General Purpose Ray Tracing Engine",
            )
        )
        self.assertTrue(
            _looks_like_scientific_web_source(
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC9999999/",
                "A scientific study using OptiX",
            )
        )

    def test_direct_evidence_requires_target_context_not_only_method(self) -> None:
        request = [{
            "entity_name": "NVIDIA OptiX",
            "target_context_dimensions": ["SAR", "radar", "ray tracing"],
        }]
        connected = {
            "candidate_kind": "scientific_article",
            "title": "Domain-specific compilation in NVIDIA OptiX ray tracing",
            "abstract": "A compiler architecture for GPU ray tracing.",
        }
        direct = {
            "candidate_kind": "scientific_article",
            "title": "Hardware-accelerated SAR simulation with NVIDIA OptiX",
            "abstract": "Ray tracing generates synthetic aperture radar data.",
        }
        self.assertEqual(
            WebResearchService._deterministic_relevance_role(
                connected,
                request,
            )[0],
            "connected_evidence",
        )
        self.assertEqual(
            WebResearchService._deterministic_relevance_role(
                direct,
                request,
            )[0],
            "direct_evidence",
        )

    def test_web_citation_comment_cannot_create_false_direct_evidence(self) -> None:
        request = [{
            "entity_name": "Generic Engine",
            "target_context_dimensions": ["radar SAR"],
        }]
        medical = {
            "candidate_kind": "scientific_article",
            "title": "Generic Engine for medical imaging systems",
            "abstract": None,
            "web_citation_context": (
                "This related source may be transferable to radar SAR."
            ),
        }
        sar = {
            "candidate_kind": "scientific_article",
            "title": "Hardware-accelerated SAR simulation",
            "abstract": None,
            "web_citation_context": (
                "The article implements Generic Engine."
            ),
        }
        self.assertEqual(
            WebResearchService._deterministic_relevance_role(
                medical,
                request,
            )[0],
            "connected_evidence",
        )
        self.assertEqual(
            WebResearchService._deterministic_relevance_role(
                sar,
                request,
            )[0],
            "direct_evidence",
        )

    def test_missing_direct_evidence_triggers_targeted_refinement(self) -> None:
        class DirectRefinementService(WebResearchService):
            @staticmethod
            def _candidate_matches_request(candidate):
                return True

            def _run_job(self, provider, request):
                if provider != "arxiv":
                    return []
                if (
                    request.get("refinement_reason")
                    == "missing_direct_scientific_evidence"
                ):
                    return [{
                        "candidate_id": "DIRECT-1",
                        "candidate_kind": "scientific_article",
                        "title": "Tool applied to SAR simulation",
                        "abstract": "Direct validation for synthetic aperture radar.",
                        "url": "https://arxiv.test/direct",
                        "entity_name": "Tool",
                        "target_context_dimensions": ["SAR"],
                        "source_authority": 0.9,
                        "relevance_score": 0.9,
                    }]
                return [{
                    "candidate_id": "CONNECTED-1",
                    "candidate_kind": "scientific_article",
                    "title": "Tool for optical rendering",
                    "abstract": "A scientific optical rendering application.",
                    "url": "https://arxiv.test/connected",
                    "entity_name": "Tool",
                    "target_context_dimensions": ["SAR"],
                    "source_authority": 0.9,
                    "relevance_score": 0.9,
                }]

        result = DirectRefinementService().search([{
            "query": "Tool SAR simulation",
            "entity_name": "Tool",
            "entity_type": "tool",
            "requested_dimensions": ["ray tracing", "SAR"],
            "target_context_dimensions": ["SAR"],
            "require_direct_evidence": True,
            "source_preferences": ["articles scientifiques"],
        }])
        self.assertTrue(result["completeness"]["complete"], result)
        self.assertEqual(
            result["completeness"]["found"]["direct_scientific_evidence"],
            1,
        )
        self.assertEqual(len(result["refinement_rounds"]), 1)
        self.assertEqual(
            result["refinement_rounds"][0]["queries"][0][
                "refinement_reason"
            ],
            "missing_direct_scientific_evidence",
        )

    def test_llm_cannot_reject_or_downgrade_deterministic_direct_evidence(self) -> None:
        class WrongReranker:
            def generate(self, *args, **kwargs):
                return json.dumps({
                    "decisions": [{
                        "candidate_id": "DIRECT-1",
                        "role": "irrelevant",
                        "confidence": 0.99,
                        "reason": "Incorrect model decision.",
                    }]
                })

        service = WebResearchService(llm=WrongReranker())
        rows = service._classify_and_filter_candidates(
            [{
                "candidate_id": "DIRECT-1",
                "candidate_kind": "scientific_article",
                "title": "Tool applied to SAR simulation",
                "abstract": "Ray tracing validation for SAR observations.",
                "url": "https://example.test/direct",
                "entity_name": "Tool",
                "source_authority": 0.9,
                "relevance_score": 0.9,
            }],
            [{
                "entity_name": "Tool",
                "target_context_dimensions": ["SAR"],
            }],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["relevance_role"], "direct_evidence")
        self.assertGreater(rows[0]["selection_priority_score"], 0.9)

    def test_llm_can_reject_scientific_article_from_unrelated_domain(self) -> None:
        class ContextualReranker:
            def generate(self, *args, **kwargs):
                return json.dumps({
                    "decisions": [{
                        "candidate_id": "MEDICAL-VIT",
                        "role": "irrelevant",
                        "confidence": 0.97,
                        "reason": (
                            "L'article applique ViT à l'imagerie médicale "
                            "sans donnée ni validation SAR."
                        ),
                    }]
                })

        service = WebResearchService(llm=ContextualReranker())
        rows = service._classify_and_filter_candidates(
            [{
                "candidate_id": "MEDICAL-VIT",
                "candidate_kind": "scientific_article",
                "title": "COVID-19 Detection Using Vision Transformers",
                "abstract": "Classification of CT and X-ray medical images.",
                "url": "https://example.test/medical-vit",
                "entity_name": "Vision Transformers",
                "source_authority": 0.9,
                "relevance_score": 0.7,
            }],
            [{
                "entity_name": "Vision Transformers",
                "target_context_dimensions": [
                    "synthetic aperture radar",
                    "automatic target recognition",
                ],
            }],
        )

        self.assertEqual(rows, [])

    def test_direct_search_rejects_method_without_target_context(self) -> None:
        medical_candidate = {
            "candidate_kind": "scientific_article",
            "title": "Vision Transformers for CT image classification",
            "abstract": "A medical imaging study on chest X-rays.",
            "venue": "Medical Imaging",
            "url": "https://example.test/medical",
            "query": "vision transformers SAR target recognition",
            "entity_name": "Vision Transformers",
            "required_terms": ["Vision Transformers"],
            "excluded_terms": [],
            "target_context_dimensions": [
                "synthetic aperture radar",
                "automatic target recognition",
            ],
            "require_direct_evidence": True,
        }
        sar_candidate = {
            **medical_candidate,
            "title": "Vision Transformers for SAR target recognition",
            "abstract": (
                "The method classifies synthetic aperture radar targets."
            ),
            "url": "https://example.test/sar",
        }

        self.assertFalse(
            WebResearchService._candidate_matches_request(medical_candidate)
        )
        self.assertTrue(
            WebResearchService._candidate_matches_request(sar_candidate)
        )

    def test_synthesis_renderer_never_mixes_documentation_and_direct_evidence(self) -> None:
        class WrongSynthesis:
            def generate(self, *args, **kwargs):
                return json.dumps({
                    "direct_evidence": [{
                        "source_refs": ["C1", "C2"],
                        "analysis": "C1 and C2 are direct proof.",
                    }],
                    "official_documentation": [{
                        "source_refs": ["C2", "C1"],
                        "analysis": "C2 and C1 document the API.",
                    }],
                    "connected_evidence": [],
                    "implementation": [],
                    "transferability_conditions": [],
                    "limitations": [],
                    "plan_suggestions": ["Modify the plan."],
                })

        agent = object.__new__(EnnoScholarGuidedResearchAgent)
        agent.llm = WrongSynthesis()
        response = agent._synthesize_research_response(
            project=None,
            contract={},
            consultant_message="Trouve les preuves directes sans rédiger.",
            candidates=[
                {
                    "candidate_id": "DOC-1",
                    "candidate_kind": "documentation",
                    "title": "Official API guide",
                    "relevance_role": "official_documentation",
                    "official_source": True,
                },
                {
                    "candidate_id": "DIRECT-1",
                    "candidate_kind": "scientific_article",
                    "title": "Direct SAR validation",
                    "relevance_role": "direct_evidence",
                },
            ],
            research_completeness={"missing_source_types": []},
        )
        direct_section = response.split(
            "Preuves scientifiques directes",
            1,
        )[1].split("Documentation officielle", 1)[0]
        documentation_section = response.split(
            "Documentation officielle",
            1,
        )[1].split("Sources scientifiques connexes", 1)[0]
        self.assertIn("C2", direct_section)
        self.assertNotIn("C1", direct_section)
        self.assertIn("C1", documentation_section)
        self.assertNotIn("C2", documentation_section)
        self.assertNotIn("Modify the plan", response)

    def test_arxiv_pdf_and_abstract_are_deduplicated_even_with_doi(self) -> None:
        rows = WebResearchService._deduplicate([
            {
                "candidate_id": "A",
                "title": "Hardware-accelerated SAR simulation",
                "url": "https://arxiv.org/abs/2005.09736",
                "doi": "10.1109/example",
                "source_providers": ["openalex"],
            },
            {
                "candidate_id": "B",
                "title": "Hardware-accelerated SAR simulation",
                "url": "https://arxiv.org/pdf/2005.09736.pdf",
                "source_providers": ["openai_web_search"],
            },
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            set(rows[0]["source_providers"]),
            {"openalex", "openai_web_search"},
        )

    def test_same_scientific_title_is_merged_across_aggregator_and_arxiv(self) -> None:
        rows = WebResearchService._deduplicate([
            {
                "candidate_id": "AGGREGATOR",
                "candidate_kind": "scientific_article",
                "title": (
                    "Hardware-accelerated SAR simulation with "
                    "NVIDIA-RTX technology | Request PDF"
                ),
                "url": "https://www.researchgate.net/publication/123",
                "source_providers": ["openai_web_search"],
            },
            {
                "candidate_id": "ORIGINAL",
                "candidate_kind": "scientific_article",
                "title": (
                    "Hardware-accelerated SAR simulation with "
                    "NVIDIA-RTX technology"
                ),
                "url": "https://arxiv.org/pdf/2005.09736",
                "source_providers": ["arxiv"],
            },
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["url"],
            "https://arxiv.org/pdf/2005.09736",
        )

    def test_official_documentation_page_satisfies_completeness(self) -> None:
        request = {
            "query": "Mitsuba 3 inverse rendering",
            "entity_name": "Mitsuba 3",
            "entity_type": "documentation",
            "source_preferences": ["documentation officielle"],
        }
        completeness = WebResearchService._research_completeness(
            [request],
            [{
                "candidate_kind": "documentation",
                "official_source": True,
                "url": "https://mitsuba.readthedocs.io/",
            }],
        )
        self.assertTrue(completeness["complete"])
        self.assertEqual(completeness["missing_source_types"], [])

    def test_search_auto_refines_missing_official_documentation_in_same_turn(self) -> None:
        class FakeResearchService(WebResearchService):
            @staticmethod
            def _candidate_matches_request(candidate):
                return True

            def _run_job(self, provider, request):
                if (
                    provider == "arxiv"
                    and request.get("query_kind") != "official_documentation"
                ):
                    return [{
                        "candidate_id": "SCI-1",
                        "candidate_kind": "scientific_article",
                        "title": "Scientific inverse rendering study",
                        "url": "https://arxiv.test/1",
                        "open_access": True,
                        "source_authority": 0.9,
                        "relevance_score": 0.9,
                    }]
                if (
                    provider == "github"
                ):
                    return [{
                        "candidate_id": "REPO-1",
                        "candidate_kind": "software_repository",
                        "title": "Official project repository",
                        "url": "https://github.test/project",
                        "official_source": False,
                        "open_access": True,
                        "source_authority": 0.8,
                        "relevance_score": 0.8,
                    }]
                if (
                    provider == "readthedocs"
                    and request.get("query_kind") == "official_documentation"
                    and request.get("refinement_reason")
                    == "missing_official_documentation"
                ):
                    return [{
                        "candidate_id": "DOC-1",
                        "candidate_kind": "documentation",
                        "title": "Official inverse rendering documentation",
                        "url": "https://tool.readthedocs.test/inverse",
                        "official_source": True,
                        "open_access": True,
                        "source_authority": 0.85,
                        "relevance_score": 0.85,
                    }]
                return []

        result = FakeResearchService().search([{
            "query": "Tool inverse rendering",
            "entity_name": "Tool",
            "entity_type": "tool",
            "requested_dimensions": ["inverse rendering"],
            "source_preferences": [
                "articles scientifiques",
                "documentation officielle",
            ],
        }])
        self.assertTrue(result["completeness"]["complete"])
        self.assertEqual(len(result["refinement_rounds"]), 1)
        self.assertEqual(
            {
                row["candidate_kind"]
                for row in result["candidates"]
            },
            {
                "scientific_article",
                "software_repository",
                "documentation",
            },
        )

    def test_search_enforces_explicit_publication_year_ceiling(self) -> None:
        class DatedResearchService(WebResearchService):
            @staticmethod
            def _candidate_matches_request(candidate):
                return True

            def _classify_and_filter_candidates(self, candidates, requests):
                return list(candidates)

            def _run_job(self, provider, request):
                if provider != "arxiv":
                    return []
                return [
                    {
                        "candidate_id": "BEFORE",
                        "candidate_kind": "scientific_article",
                        "title": "Valid study",
                        "year": 2024,
                        "relevance_score": 0.9,
                    },
                    {
                        "candidate_id": "AFTER",
                        "candidate_kind": "scientific_article",
                        "title": "Too recent study",
                        "year": 2025,
                        "relevance_score": 0.95,
                    },
                    {
                        "candidate_id": "UNKNOWN",
                        "candidate_kind": "scientific_article",
                        "title": "Undated study",
                        "relevance_score": 0.8,
                    },
                ]

        result = DatedResearchService().search(
            [{
                "query": "experimental validation",
                "query_kind": "scientific_evidence",
                "publication_year_max": 2024,
            }],
            auto_refine=False,
        )

        self.assertEqual(
            [row["candidate_id"] for row in result["candidates"]],
            ["BEFORE"],
        )

    def test_relevance_filter_rejects_out_of_domain_homonym(self) -> None:
        candidate = {
            "title": "Metal artifact reduction in X-ray tomography",
            "abstract": (
                "Industrial computed tomography for dense material inspection."
            ),
            "venue": "Imaging",
            "url": "https://example.test/xray",
            "query": (
                "differentiable ray tracing inverse physical parameter calibration"
            ),
            "required_terms": [],
            "excluded_terms": [],
        }
        self.assertFalse(
            WebResearchService._candidate_matches_request(candidate)
        )

    def test_relevance_filter_keeps_partial_multiword_entity_match(self) -> None:
        candidate = {
            "title": "OptiX programming guide",
            "abstract": "API reference and ray tracing pipeline documentation.",
            "venue": "Technical documentation",
            "url": "https://optix.example.test/guide",
            "query": "NVIDIA OptiX official ray tracing documentation",
            "entity_name": "NVIDIA OptiX",
            "required_terms": ["NVIDIA OptiX"],
            "excluded_terms": [],
        }
        self.assertTrue(
            WebResearchService._candidate_matches_request(candidate)
        )

    def test_relevance_filter_splits_vendor_tokens_in_domain(self) -> None:
        candidate = {
            "title": "OptiX ray tracing documentation",
            "abstract": "Programming guide and API reference.",
            "venue": "Official documentation",
            "url": "https://raytracing-docs.nvidia.com/optix/guide/index.html",
            "query": "NVIDIA OptiX official documentation",
            "entity_name": "NVIDIA OptiX",
            "required_terms": ["NVIDIA OptiX"],
            "excluded_terms": [],
        }
        self.assertTrue(
            WebResearchService._candidate_matches_request(candidate)
        )

    def test_relevance_filter_still_rejects_missing_entity_identity(self) -> None:
        candidate = {
            "title": "Generic ray tracing documentation",
            "abstract": "Programming guide for a different rendering engine.",
            "venue": "Technical documentation",
            "url": "https://example.test/ray-tracing",
            "query": "NVIDIA OptiX ray tracing documentation",
            "entity_name": "NVIDIA OptiX",
            "required_terms": ["NVIDIA OptiX"],
            "excluded_terms": [],
        }
        self.assertFalse(
            WebResearchService._candidate_matches_request(candidate)
        )

    def test_relevance_filter_keeps_required_technical_concept(self) -> None:
        candidate = {
            "title": (
                "Differentiable ray tracing for inverse material calibration"
            ),
            "abstract": (
                "Gradient-based calibration reduces discrepancies between "
                "simulated and measured propagation."
            ),
            "venue": "Scientific venue",
            "url": "https://example.test/relevant",
            "query": (
                "differentiable ray tracing inverse material calibration measurements"
            ),
            "required_terms": ["differentiable ray tracing"],
            "excluded_terms": [],
        }
        self.assertTrue(
            WebResearchService._candidate_matches_request(candidate)
        )

    def test_relevance_filter_keeps_sar_parameter_recovery_synonym(self) -> None:
        candidate = {
            "title": (
                "Recovering Geometric Parameters from SAR Images "
                "Using Differentiable Ray Tracing"
            ),
            "abstract": (
                "The method estimates geometric parameters directly from "
                "synthetic aperture radar observations."
            ),
            "venue": "Scientific venue",
            "url": "https://example.test/direct",
            "query": (
                "inverse calibration physical parameters ray tracing SAR"
            ),
            "required_terms": [
                "inverse calibration",
                "ray tracing",
                "SAR",
            ],
            "excluded_terms": [],
        }
        self.assertTrue(
            WebResearchService._candidate_matches_request(candidate)
        )

    def test_relevance_filter_rejects_single_anchor_outside_target_domain(self) -> None:
        candidate = {
            "title": "Optical energy yield modeling with ray tracing",
            "abstract": (
                "A photovoltaic simulation predicts irradiance and energy."
            ),
            "venue": "Solar energy",
            "url": "https://example.test/peripheral",
            "query": (
                "inverse calibration physical parameters ray tracing SAR"
            ),
            "required_terms": [
                "inverse calibration",
                "ray tracing",
                "SAR",
            ],
            "excluded_terms": [],
        }
        self.assertFalse(
            WebResearchService._candidate_matches_request(candidate)
        )

    def test_relevance_filter_preserves_broad_predictive_reliability_evidence(self) -> None:
        candidate = {
            "candidate_kind": "scientific_article",
            "title": (
                "Conditional reliability diagnostics for bearing remaining "
                "useful life under operating-regime shift"
            ),
            "abstract": (
                "A predictive model is evaluated under changing operating "
                "conditions with strict train validation and test separation."
            ),
            "venue": "Reliability Engineering",
            "url": "https://example.test/rul-regime-shift",
            "query": (
                "out-of-distribution reliability predictive models limited "
                "historical data changing operating regimes validation limitations"
            ),
            "entity_name": "predictive model robustness under varying conditions",
            "entity_type": "other",
            "required_terms": [
                "predictive model",
                "limited historical data",
                "out-of-distribution reliability",
                "validation protocol",
            ],
            "target_context_dimensions": [
                "changing operating regimes",
                "thermal loads",
                "equipment characteristics",
            ],
            "require_direct_evidence": True,
        }

        self.assertTrue(
            WebResearchService._candidate_matches_request(candidate)
        )

    def test_predictive_control_is_not_promoted_to_direct_ood_evidence(self) -> None:
        request = {
            "query": "predictive model reliability operating regime shift",
            "entity_name": "predictive model robustness under varying conditions",
            "entity_type": "other",
            "required_terms": [
                "predictive model",
                "limited historical data",
                "out-of-distribution reliability",
                "validation protocol",
            ],
            "target_context_dimensions": [
                "changing operating regimes",
                "thermal loads",
                "equipment characteristics",
            ],
            "require_direct_evidence": True,
        }
        candidate = {
            "candidate_kind": "scientific_article",
            "title": "Model predictive control of electric motor drives",
            "abstract": (
                "The controller operates under fast-changing operating "
                "conditions and limits thermal stress. Hardware-in-the-loop "
                "validation demonstrates control feasibility."
            ),
            "query": request["query"],
        }

        role = WebResearchService._deterministic_relevance_role(
            candidate,
            [request],
        )[0]

        self.assertEqual(role, "connected_evidence")

    def test_documentation_is_limited_to_contextual_evidence(self) -> None:
        cards = extract_supplemental_source_cards(
            {
                "sources": [{
                    "candidate_id": "DOC-1",
                    "candidate_kind": "documentation",
                    "consultant_decision": "accepted",
                    "title": "Documentation de l'outil",
                    "url": "https://example.test/docs",
                    "content_excerpt": (
                        "Cette documentation décrit l'architecture, "
                        "l'installation et la procédure de configuration."
                    ),
                    "evidence_scope": [
                        "definition",
                        "architecture",
                        "procedure",
                        "configuration",
                    ],
                }]
            },
            [],
        )
        self.assertEqual(len(cards), 1)
        self.assertTrue(cards[0]["documentation_scope_only"])
        self.assertFalse(cards[0]["scientific_evidence_eligible"])

    def test_selected_articles_do_not_reuse_historical_identifiers(self) -> None:
        classification = IntentClassification.model_validate(
            _decision(
                ConsultantIntent.START_WRITING,
                "Je lance la rédaction.",
            )["classification"]
        )
        classification.writing_source_scope = "explicit_selection"
        classification.writing_source_identifiers = [
            "A1",
            "A2",
            "A30",
            "C1",
            "C3",
        ]
        classification.requested_source_count = 22

        grounded = _ground_writing_source_policy(
            (
                "Lance la rédaction complète en utilisant uniquement les "
                "articles sélectionnés."
            ),
            classification,
        )

        self.assertEqual(grounded.writing_source_scope, "all_validated")
        self.assertEqual(grounded.writing_source_identifiers, [])
        self.assertIsNone(grounded.requested_source_count)

    def test_exact_source_policy_uses_only_ids_from_current_message(self) -> None:
        classification = IntentClassification.model_validate(
            _decision(
                ConsultantIntent.START_WRITING,
                "Je lance la rédaction.",
            )["classification"]
        )
        classification.writing_source_scope = "explicit_selection"
        classification.writing_source_identifiers = ["A30", "C9"]
        classification.requested_source_count = 22

        grounded = _ground_writing_source_policy(
            "Rédige uniquement avec A1, A7, C1 et C3.",
            classification,
        )

        self.assertEqual(grounded.writing_source_scope, "explicit_selection")
        self.assertEqual(
            grounded.writing_source_identifiers,
            ["A1", "A7", "C1", "C3"],
        )
        self.assertIsNone(grounded.requested_source_count)

    def test_candidate_display_ids_resolve_to_persistent_candidate_ids(self) -> None:
        resolved = _resolve_candidate_display_identifiers(
            ["C1", "C3", "A7"],
            ["WEB-111", "SRC-222", "SRC-333"],
        )
        self.assertEqual(resolved, ["WEB-111", "SRC-333", "A7"])


if __name__ == "__main__":
    unittest.main()
