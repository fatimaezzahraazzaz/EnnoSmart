# -*- coding: utf-8 -*-
from services.ennoscholar_conversation_state_service import (
    apply_verrou_scope_lock,
    build_conversation_phase1_payload,
)
from types import SimpleNamespace


def test_virtual_chat_verrou_keeps_matching_article_card_without_db_verrou_id():
    scoped = apply_verrou_scope_lock(
        selection_payload={"verrous": []},
        article_cards_payload={
            "cards": [
                {
                    "article_id": 10,
                    "guided_candidate_id": "SRC-CHAT-1",
                    "title": "Article du chat",
                    "verrou_ids": [],
                },
                {
                    "article_id": 11,
                    "guided_candidate_id": "SRC-OTHER",
                    "title": "Article d'une autre portée",
                    "verrou_ids": ["SV-other"],
                },
            ]
        },
        session_sources=[
            {
                "candidate_id": "SRC-CHAT-1",
                "consultant_decision": "accepted",
                "target_verrous": ["SV-chat-lock"],
                "fulltext_preparation": {"article_id": 10},
            }
        ],
        scope={"identifiers": ["sv-chat-lock"]},
    )

    cards = scoped["article_cards_payload"]["cards"]
    assert [card["article_id"] for card in cards] == [10]
    assert scoped["scope_manifest"]["kept_cards_count"] == 1


def test_virtual_chat_verrou_is_materialized_for_phases_4_to_5():
    scoped = apply_verrou_scope_lock(
        selection_payload={"verrous": []},
        article_cards_payload={
            "cards": [
                {
                    "article_id": 10,
                    "citation_id": "A1",
                    "target_verrous": ["SV-chat-lock"],
                }
            ]
        },
        session_sources=[],
        scope={
            "identifiers": ["sv-chat-lock"],
            "consultant_verrous": [
                {
                    "id": "SV-chat-lock",
                    "title": "Robustesse avec peu de données",
                    "justification": "Évaluer la robustesse de la méthode.",
                    "supporting_context": "Le projet est nouveau.",
                },
                {
                    "id": "SV-chat-lock",
                    "title": "Robustesse avec peu de données",
                },
            ],
        },
    )

    assert scoped["selection_payload"]["verrous"] == [
        {
            "id": "SV-chat-lock",
            "title": "Robustesse avec peu de données",
            "justification": "Évaluer la robustesse de la méthode.",
            "supporting_context": "Le projet est nouveau.",
            "verrou_id": "SV-chat-lock",
            "verrou_title": "Robustesse avec peu de données",
            "objectif_rd": "Évaluer la robustesse de la méthode.",
            "contexte_projet": "Le projet est nouveau.",
            "conversation_confirmed": True,
            "contract_origin": "guided_conversation_consultant_verrou",
        }
    ]
    assert scoped["selection_payload"]["verrous_count"] == 1


def test_standalone_conversation_materializes_phase1_from_all_ready_cards():
    session_id = "63233936-b9f7-4d00-afbc-0a09e918fe6d"
    project = SimpleNamespace(
        id=6,
        project_name="ZZZZ",
        organisme="YLE Architecte",
        year="2026",
        domain_label="Formulation cosmétique",
    )
    context = {
        "session_id": session_id,
        "corpus_scope_id": session_id,
        "scholar_run_id": 61,
        "snapshot": {
            "context": {
                "operating_mode": "standalone_chat",
                "standalone_project_brief": {
                    "project_name": "Émulsion au rétinol",
                    "domain": "Formulation cosmétique",
                    "objective": "Stabiliser le rétinol sans empêcher sa libération.",
                    "additional_context": "Émulsion huile-dans-eau.",
                },
            }
        },
        "scope": {
            "identifiers": ["sv-retinol"],
            "consultant_verrous": [
                {
                    "id": "SV-retinol",
                    "title": "Stabilité durable du rétinol",
                    "justification": "Résister à l'oxydation, la lumière et la température.",
                }
            ],
        },
        "sources": [],
    }
    cards_payload = {
        "cards": [
            {"article_id": 15712, "citation_id": "A1", "title": "Article 1"},
            {"article_id": 15710, "citation_id": "A2", "title": "Article 2"},
            {"article_id": 15711, "citation_id": "A3", "title": "Article 3"},
        ],
        "excluded_from_writing_count": 4,
    }

    selection = build_conversation_phase1_payload(
        project=project,
        conversation_context=context,
        article_cards_payload=cards_payload,
    )

    assert selection["payload_version"] == "conversation_runtime_handoff_v1"
    assert selection["guided_session_id"] == session_id
    assert selection["selected_articles_count"] == 3
    assert selection["selection_summary"]["excluded_by_limit_total"] == 0
    assert selection["selection_summary"]["excluded_articles_count"] == 4
    assert selection["project_context_structured"]["objectif_technique"].startswith(
        "Stabiliser le rétinol"
    )
    assert selection["verrous"][0]["verrou_id"] == "SV-retinol"
    assert [
        row["article_id"]
        for row in selection["verrous"][0]["selected_articles"]
    ] == [15712, 15710, 15711]

    scoped = apply_verrou_scope_lock(
        selection_payload=selection,
        article_cards_payload=cards_payload,
        session_sources=[],
        scope=context["scope"],
    )
    assert [
        row["article_id"]
        for row in scoped["article_cards_payload"]["cards"]
    ] == [15712, 15710, 15711]
