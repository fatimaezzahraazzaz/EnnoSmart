from agents.EnnoAmelioration.application.conversation_scope_v3161 import (
    ACTION_CANCEL_PROGRESSIVE_FOR_TARGET,
    ACTION_NORMAL,
    ACTION_RESUME_PROGRESSIVE,
    ACTION_START_PROGRESSIVE,
    effective_scope_value,
    message_explicitly_requests_full_document,
    progressive_action,
)


def test_section_beats_old_full_document_scope():
    assert effective_scope_value(
        requested_scope="full_document",
        selected_text_present=False,
        resolved_section_present=True,
    ) == "section"


def test_cest_bon_redige_is_not_full_document():
    assert message_explicitly_requests_full_document("c'est bon redige") is False


def test_explicit_full_cir_is_detected():
    assert message_explicitly_requests_full_document(
        "Améliore le CIR complet progressivement paragraphe par paragraphe."
    ) is True


def test_active_progressive_is_cancelled_for_explicit_section():
    assert progressive_action(
        workflow_active=True,
        explicit_target_present=True,
        explicit_full_document_request=False,
        message="non renforce la section 1.3.1.2 avec les articles gardés",
        small_talk=False,
    ) == ACTION_CANCEL_PROGRESSIVE_FOR_TARGET


def test_continue_resumes():
    assert progressive_action(
        workflow_active=True,
        explicit_target_present=False,
        explicit_full_document_request=False,
        message="continue",
        small_talk=False,
    ) == ACTION_RESUME_PROGRESSIVE


def test_random_chat_does_not_resume_progressive():
    assert progressive_action(
        workflow_active=True,
        explicit_target_present=False,
        explicit_full_document_request=False,
        message="c'est bon redige",
        small_talk=False,
    ) == ACTION_NORMAL


def test_new_full_document_starts_only_when_explicit():
    assert progressive_action(
        workflow_active=False,
        explicit_target_present=False,
        explicit_full_document_request=True,
        message="améliore tout le CIR",
        small_talk=False,
    ) == ACTION_START_PROGRESSIVE


def test_small_talk_never_advances():
    assert progressive_action(
        workflow_active=True,
        explicit_target_present=False,
        explicit_full_document_request=False,
        message="salut",
        small_talk=True,
    ) == ACTION_NORMAL
