# -*- coding: utf-8 -*-
from __future__ import annotations

from agents.EnnoScholar.guided_research.lot1.domain.enums import ConsultantIntent
from agents.EnnoScholar.guided_research.lot1.domain.models import IntentClassification
from agents.EnnoScholar.guided_research.lot1.grounded_request_resolver import repair_contextual_classification


def _classification(intent=ConsultantIntent.ACCEPT_PLAN, actions=None):
    return IntentClassification(
        intent=intent,
        confidence=0.8,
        rationale="test",
        requested_actions=list(actions or []),
        forbidden_actions=[],
        needs_clarification=False,
        classifier="test",
    )


def _verrous():
    return [
        {"id": 1, "title": "Généralisation synthétique vers réel"},
        {"id": 2, "title": "Calage réaliste des simulations SAR"},
        {"id": 3, "title": "Densité des rayons en radar bistatique"},
    ]


def _plan():
    return [{"section_id": "s1", "title": "Introduction", "level": 1}]


def test_keep_plan_and_write_is_one_compound_action():
    result = repair_contextual_classification(
        _classification(
            ConsultantIntent.ACCEPT_PLAN,
            [ConsultantIntent.ACCEPT_PLAN, ConsultantIntent.START_WRITING],
        ),
        consultant_message=(
            "Très bien on peut garder le plan maintenant lance la rédaction complète, "
            "tu dois bien défendre les verrous à partir des articles existants."
        ),
        current_verrous=_verrous(),
        current_plan=_plan(),
        session_context={"review_scope": "global"},
    )
    assert result.intent == ConsultantIntent.START_WRITING
    assert result.explicit_plan_approval is True
    assert result.explicit_write_command is True
    assert result.needs_clarification is False
    assert result.use_current_sources_only is True
    assert result.writing_source_scope == "all_validated"
    assert result.requested_actions[:2] == [
        ConsultantIntent.ACCEPT_PLAN,
        ConsultantIntent.START_WRITING,
    ]


def test_keep_same_plan_and_write_detected_even_if_llm_only_chose_accept():
    result = repair_contextual_classification(
        _classification(ConsultantIntent.ACCEPT_PLAN, [ConsultantIntent.ACCEPT_PLAN]),
        consultant_message="Garde le même plan et rédige maintenant avec les articles déjà trouvés.",
        current_verrous=_verrous(),
        current_plan=_plan(),
        session_context={"review_scope": "global"},
    )
    assert result.intent == ConsultantIntent.START_WRITING
    assert result.explicit_plan_approval is True
    assert result.writing_source_scope == "all_validated"


def test_approval_without_write_does_not_start_writing():
    result = repair_contextual_classification(
        _classification(ConsultantIntent.ACCEPT_PLAN, [ConsultantIntent.ACCEPT_PLAN]),
        consultant_message="Ce plan me convient.",
        current_verrous=_verrous(),
        current_plan=_plan(),
        session_context={"review_scope": "global"},
    )
    assert result.intent == ConsultantIntent.ACCEPT_PLAN
    assert result.explicit_write_command is False


def test_existing_articles_without_write_does_not_trigger_action():
    result = repair_contextual_classification(
        _classification(ConsultantIntent.CONVERSE, []),
        consultant_message="Quels sont les articles existants ?",
        current_verrous=_verrous(),
        current_plan=_plan(),
        session_context={"review_scope": "global"},
    )
    assert result.intent == ConsultantIntent.CONVERSE
