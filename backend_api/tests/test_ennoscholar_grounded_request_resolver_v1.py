# -*- coding: utf-8 -*-
from __future__ import annotations

from agents.EnnoScholar.guided_research.lot1.domain.enums import ConsultantIntent
from agents.EnnoScholar.guided_research.lot1.domain.models import IntentClassification
from agents.EnnoScholar.guided_research.lot1.grounded_request_resolver import (
    repair_contextual_classification,
)


def _classification(
    intent: ConsultantIntent = ConsultantIntent.UNKNOWN,
    *,
    needs_clarification: bool = True,
    verrou_scope: str = "unchanged",
) -> IntentClassification:
    return IntentClassification(
        intent=intent,
        confidence=0.4,
        rationale="test",
        requested_actions=[],
        forbidden_actions=[],
        needs_clarification=needs_clarification,
        verrou_scope=verrou_scope,
        classifier="test",
    )


def _verrous(count: int = 3) -> list[dict]:
    return [
        {"id": 100 + index, "title": f"Verrou scientifique {index}"}
        for index in range(1, count + 1)
    ]


def test_write_all_verrous_without_plan_becomes_plan_then_write() -> None:
    result = repair_contextual_classification(
        _classification(),
        consultant_message="Je veux rédiger l'état de l'art pour tous les verrous.",
        current_verrous=_verrous(3),
        current_plan=[],
        session_context={"review_scope": "global"},
    )
    assert result.intent == ConsultantIntent.PROPOSE_PLAN
    assert result.verrou_scope == "global"
    assert result.needs_clarification is False
    assert result.explicit_write_command is True
    assert result.requested_actions[:2] == [
        ConsultantIntent.PROPOSE_PLAN,
        ConsultantIntent.START_WRITING,
    ]


def test_write_all_verrous_with_plan_starts_writing() -> None:
    result = repair_contextual_classification(
        _classification(),
        consultant_message="Rédige l'état de l'art pour les trois verrous.",
        current_verrous=_verrous(3),
        current_plan=[{"section_id": "s1", "title": "Méthodes", "level": 1}],
        session_context={"review_scope": "global"},
    )
    assert result.intent == ConsultantIntent.START_WRITING
    assert result.verrou_scope == "global"
    assert result.needs_clarification is False


def test_write_now_reuses_existing_scope() -> None:
    result = repair_contextual_classification(
        _classification(),
        consultant_message="Très bien, rédige maintenant.",
        current_verrous=_verrous(2),
        current_plan=[{"section_id": "s1", "title": "Contexte", "level": 1}],
        session_context={"review_scope": "global"},
    )
    assert result.intent == ConsultantIntent.START_WRITING
    assert result.needs_clarification is False
    assert result.verrou_scope == "global"


def test_global_action_does_not_ask_again_when_catalogue_is_known() -> None:
    result = repair_contextual_classification(
        _classification(
            ConsultantIntent.PROPOSE_PLAN,
            needs_clarification=True,
            verrou_scope="global",
        ),
        consultant_message="Prépare un plan global pour tous les verrous.",
        current_verrous=_verrous(4),
        current_plan=[],
        session_context={},
    )
    assert result.intent == ConsultantIntent.PROPOSE_PLAN
    assert result.needs_clarification is False
    assert result.verrou_scope == "global"


def test_ambiguous_message_remains_unknown() -> None:
    result = repair_contextual_classification(
        _classification(),
        consultant_message="Tu peux m'aider ?",
        current_verrous=_verrous(3),
        current_plan=[],
        session_context={},
    )
    assert result.intent == ConsultantIntent.UNKNOWN
    assert result.needs_clarification is True


def test_new_paragraph_research_stays_on_existing_verrou() -> None:
    wrong_llm_decision = _classification(
        ConsultantIntent.ADD_VERROU_AND_SEARCH,
        needs_clarification=False,
    )
    wrong_llm_decision.requested_actions = [
        ConsultantIntent.ADD_VERROU_AND_SEARCH
    ]
    wrong_llm_decision.explicit_new_verrou_declaration = True
    wrong_llm_decision.explicit_research_command = True

    result = repair_contextual_classification(
        wrong_llm_decision,
        consultant_message=(
            "Dans la section Analyse critique des approches existantes, "
            "je veux ajouter un nouveau paragraphe après le passage sur "
            "l'apprentissage faiblement supervisé pour parler de DINOv2. "
            "Fait des rechreche pour trouver les article et source."
        ),
        current_verrous=[
            {
                "id": "SV-INITIAL",
                "title": "Détection robuste avec peu de données",
            }
        ],
        current_plan=[
            {
                "section_id": "analyse",
                "title": "Analyse critique des approches existantes",
                "level": 1,
            }
        ],
        session_context={
            "review_scope": "per_verrou",
            "active_verrou_ids": ["SV-INITIAL"],
        },
    )

    assert result.intent == ConsultantIntent.SEARCH_MORE
    assert result.requested_actions == [ConsultantIntent.SEARCH_MORE]
    assert result.explicit_research_command is True
    assert result.explicit_new_verrou_declaration is False
    assert result.verrou_scope == "per_verrou"
    assert result.target_verrou_ids == ["SV-INITIAL"]
    assert ConsultantIntent.ADD_VERROU_AND_SEARCH in result.forbidden_actions
    assert "existing_scope_research_repair_v3" in result.classifier


def test_argumentative_addition_with_small_wording_variations_is_not_a_lock() -> None:
    variants = [
        "Cherche des articles sur DINOv2 pour argumenter le verrou initial.",
        "Trouve des sources DINOv2 afin de renforcer le verrou existant.",
        "Ce n'est pas un verrou, juste un plus pour compléter l'argumentation : cherche des publications DINOv2.",
    ]
    for message in variants:
        result = repair_contextual_classification(
            _classification(
                ConsultantIntent.ADD_VERROU_AND_SEARCH,
                needs_clarification=False,
            ),
            consultant_message=message,
            current_verrous=[
                {"id": "SV-1", "title": "Verrou scientifique initial"}
            ],
            current_plan=[],
            session_context={"active_verrou_ids": ["SV-1"]},
        )
        assert result.intent == ConsultantIntent.SEARCH_MORE, message
        assert result.target_verrou_ids == ["SV-1"], message
        assert result.explicit_new_verrou_declaration is False, message


def test_explicit_new_verrou_declaration_is_preserved() -> None:
    classification = _classification(
        ConsultantIntent.ADD_VERROU_AND_SEARCH,
        needs_clarification=False,
    )
    classification.explicit_new_verrou_declaration = True
    classification.explicit_research_command = True
    classification.requested_actions = [
        ConsultantIntent.ADD_VERROU_AND_SEARCH
    ]

    result = repair_contextual_classification(
        classification,
        consultant_message=(
            "Ajoute un nouveau verrou sur la dérive thermique et cherche des "
            "articles scientifiques pour le documenter."
        ),
        current_verrous=[{"id": "SV-1", "title": "Verrou initial"}],
        current_plan=[],
        session_context={"active_verrou_ids": ["SV-1"]},
    )

    assert result.intent == ConsultantIntent.ADD_VERROU_AND_SEARCH
    assert result.explicit_new_verrou_declaration is True


def test_semantic_relation_handles_free_language_without_keyword_dependency() -> None:
    classification = _classification(
        ConsultantIntent.ADD_VERROU_AND_SEARCH,
        needs_clarification=False,
    )
    classification.explicit_research_command = True
    classification.explicit_new_verrou_declaration = False
    classification.scientific_scope_relation = "supports_existing_verrou"
    classification.content_target = "existing_verrou"

    result = repair_contextual_classification(
        classification,
        consultant_message=(
            "I would like a literature sweep around DINOv2 as contextual "
            "evidence for what we are already defending."
        ),
        current_verrous=[{"id": "SV-1", "title": "Existing challenge"}],
        current_plan=[],
        session_context={"active_verrou_ids": ["SV-1"]},
    )

    assert result.intent == ConsultantIntent.SEARCH_MORE
    assert result.target_verrou_ids == ["SV-1"]
    assert result.scientific_scope_relation == "supports_existing_verrou"
