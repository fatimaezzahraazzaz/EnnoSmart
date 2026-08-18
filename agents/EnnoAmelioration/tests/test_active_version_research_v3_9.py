from types import SimpleNamespace

from backend_api.services.improvement_service import _working_version
from agents.EnnoAmelioration.application.research_context_bridge_v39 import (
    enrich_direct_research_context,
)
from agents.EnnoScholar.scientific_intent_builder import build_scientific_intent
from agents.EnnoScholar.query_builder import KIND_PRIORITY


def v(vid, number, status, content):
    return SimpleNamespace(
        id=vid,
        version_number=number,
        status=status,
        content=content,
    )


def test_active_version_wins_over_unaccepted_candidate():
    original = v("v1", 1, "original", "PDF ORIGINAL")
    active = v("v2", 2, "accepted", "VERSION AMELIOREE ACTIVE")
    candidate = v("v3", 3, "candidate", "CANDIDATE NON VALIDEE")
    session = SimpleNamespace(
        versions=[original, active, candidate],
        active_version_id="v2",
    )
    assert _working_version(session).id == "v2"


def test_candidate_only_when_explicitly_requested():
    active = v("v2", 2, "accepted", "ACTIVE")
    candidate = v("v3", 3, "candidate", "CANDIDATE")
    session = SimpleNamespace(
        versions=[active, candidate],
        active_version_id="v2",
    )
    assert _working_version(session, prefer_candidate=True).id == "v3"


ACTIVE_SECTION = """
1.3.1.1 Donnée SAR publiquement disponibles
La version active décrit MSTAR pour l'ATR sur images SAR.
Elle décrit aussi SAMPLE avec des données SAR mesurées et simulées.
"""


def direct():
    return {
        "research_context": {
            "research_objective": "MESSAGE INTERDIT",
            "local_context": "PDF ORIGINAL INTERDIT",
        },
        "research_targets": [{
            "research_target_id": "1.3.1.1",
            "title": "Donnée SAR publiquement disponibles",
            "raw_item": {
                "text": "ANCIEN PDF",
                "source_text": "ANCIEN PDF",
                "consultant_instruction": "MESSAGE INTERDIT",
                "supporting_passages": [{"text": "PDF ORIGINAL INTERDIT"}],
            },
        }],
    }


def req(message):
    return SimpleNamespace(
        target_text=ACTIVE_SECTION,
        target_section_title="Donnée SAR publiquement disponibles",
        target_section_id="1.3.1.1",
        instruction=message,
    )


def test_scholar_gets_active_version_section_only():
    out = enrich_direct_research_context(
        req("Renforce scientifiquement cette section."),
        direct(),
        conversation_context={
            "base_version_id": "v2",
            "base_version_number": 2,
            "base_version_status": "accepted",
            "base_version_is_active": True,
        },
    )
    target = out["research_targets"][0]
    assert target["text"] == ACTIVE_SECTION.strip()
    assert target["raw_item"]["source_text"] == ACTIVE_SECTION.strip()
    assert "ANCIEN PDF" not in repr(target)
    assert "MESSAGE INTERDIT" not in repr(target)
    contract = out["v3_9_active_version_contract"]
    assert contract["base_version_id"] == "v2"
    assert contract["base_version_is_active"] is True


def test_message_does_not_change_scientific_payload_for_same_section():
    a = enrich_direct_research_context(req("Renforce cette section."), direct())
    b = enrich_direct_research_context(req("Je n'aime pas la recherche, recommence."), direct())
    assert a["research_targets"] == b["research_targets"]


def test_active_section_entities_reach_intent():
    out = enrich_direct_research_context(req("Renforce."), direct())
    intent = build_scientific_intent(
        out["research_targets"][0],
        domain_detection={},
        diagnostic_context={},
    )
    anchors = set(intent.get("strong_anchors") or [])
    assert "MSTAR" in anchors
    assert "SAMPLE" in anchors


def test_active_section_query_priority_exists():
    assert KIND_PRIORITY["active_section_exact_v3_9"] > KIND_PRIORITY["cir_domain_profile_query"]
