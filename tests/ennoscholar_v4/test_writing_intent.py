"""Writing intents: one section, a full draft, the remaining sections, or a plan followed by a full draft."""
import pytest

from agents.EnnoScholarV4.writing_progress import begin_writing
from tests.ennoscholar_v4.test_finalization import Client
from tests.ennoscholar_v4.test_document_orchestration import agent
from tests.ennoscholar_v4.test_section_claims import prepared_seven, writer

EXACT_REQUEST = ("Continue la rédaction de mon état de l'art. La section 1 est déjà rédigée. "
    "Reprends à la section 2 et rédige toutes les sections restantes jusqu'à la section 7.")
ONLY_SECTION_2 = "rédige uniquement la section 2"


def two_saved_sections():
    """Seven-section plan whose sections 1 and 2 were saved by an interrupted full draft."""
    state, ids, eids = prepared_seven()
    log, events = [], []
    agent(Client(writer(ids, eids, log))).run(state, emit=events.append,
        cancelled=lambda: sum(e["tool"] == "writing_progress" and e["status"] == "completed"
            and bool(e.get("section_id")) for e in events) >= 2)
    saved = {s["id"]: s["drafts"][-1]["id"] for s in state["outline"]["sections"] if s["drafts"]}
    assert list(saved) == ["section_test_1", "section_test_2"]
    return state, ids, eids, saved


def section_ids(state, numbers):
    return [state["outline"]["sections"][n-1]["id"] for n in numbers]


def test_exact_request_targets_every_remaining_section():
    state, _, _, saved = two_saved_sections()
    progress = begin_writing(state, EXACT_REQUEST)
    assert progress["mode"] == "full"
    assert progress["target_ids"] == section_ids(state, range(2, 8))
    # Section 2 already has a draft: it counts as saved, drafting restarts at section 3.
    assert progress["saved_draft_ids"] == {"section_test_2": saved["section_test_2"]}
    assert [sid for sid, status in progress["section_status"].items() if status == "pending"] == section_ids(state, range(3, 8))


def test_exact_request_also_resumes_after_a_single_section_request():
    # Previous message wrote section 2 alone: its progress is complete, so the
    # plain "resume" rule no longer applies; the global wording must still win.
    state, _, _, saved = two_saved_sections()
    begin_writing(state, ONLY_SECTION_2)["status"] = "draft_complete"
    progress = begin_writing(state, EXACT_REQUEST)
    assert progress["mode"] == "full" and progress["target_ids"] == section_ids(state, range(2, 8))


def test_only_section_two_stays_a_single_section_request():
    state, _, _, saved = two_saved_sections()
    progress = begin_writing(state, ONLY_SECTION_2)
    assert progress["mode"] == "section" and progress["target_ids"] == ["section_test_2"]
    # A new version of section 2 is expected: the existing draft is the baseline, not a save.
    assert progress["baseline_draft_ids"] == {"section_test_2": saved["section_test_2"]}
    assert progress["saved_draft_ids"] == {}


@pytest.mark.parametrize("request_text, numbers", [
    ("Rédige la section 3.", [3]),
    ("Peux-tu rédiger seulement la section 4 ?", [4]),
    ("Rédige toutes les sections restantes.", range(1, 8)),
    ("Rédige les autres sections.", range(1, 8)),
    ("Rédige tout l'état de l'art complet.", range(1, 8)),
    ("Rédige les sections 3 à 5.", range(3, 6)),
    ("Rédige toutes les sections à partir de la section 4.", range(4, 8)),
    ("Continue la rédaction des sections restantes.", range(1, 8)),
])
def test_intent_table(request_text, numbers):
    state, _, _ = prepared_seven()
    progress = begin_writing(state, request_text)
    assert progress["target_ids"] == section_ids(state, numbers), request_text
    assert progress["mode"] == ("section" if len(list(numbers)) == 1 else "full")


def test_plan_then_full_draft_waits_for_the_plan_then_targets_all_sections():
    state, _, _ = prepared_seven()
    sections = state["outline"].pop("sections")
    progress = begin_writing(state, "Construis le plan en 7 sections puis rédige toutes les sections.")
    assert progress["mode"] == "full" and progress["status"] == "awaiting_plan"
    state["outline"]["sections"] = sections
    from agents.EnnoScholarV4.writing_progress import refresh_writing
    assert refresh_writing(state)["target_ids"] == section_ids(state, range(1, 8))
    # Planning only, writing explicitly postponed: no draft request.
    assert begin_writing({"outline": {"sections": sections}},
        "Propose le plan des sections avant de rédiger toutes les sections.") is None


def test_exact_request_writes_sections_three_to_seven_in_one_run_and_keeps_saved_ones():
    state, ids, eids, saved = two_saved_sections()
    before = {s["id"]: list(s["drafts"]) for s in state["outline"]["sections"] if s["drafts"]}
    state["messages"].append({"role": "user", "content": EXACT_REQUEST})
    result = agent(Client(writer(ids, eids, []))).run(state)
    assert not result["partial"], (result["reason"], result["writing_progress"].get("errors"))
    assert result["writing_progress"]["status"] == "draft_complete"
    sections = state["outline"]["sections"]
    assert all(len(section["drafts"]) == 1 for section in sections)
    for sid, drafts in before.items():
        current = next(s for s in sections if s["id"] == sid)
        assert current["drafts"] == drafts  # same draft, evidence and version
    for section in sections[2:]:
        assert eids[1] in section["drafts"][-1]["evidence_ids"]
    assert "Brouillon complet" in result["answer"]


def test_only_section_two_writes_a_new_version_of_section_two_only():
    state, ids, eids, saved = two_saved_sections()
    state["messages"].append({"role": "user", "content": ONLY_SECTION_2})
    result = agent(Client(writer(ids, eids, []))).run(state)
    assert result["writing_progress"]["mode"] == "section"
    sections = state["outline"]["sections"]
    assert len(sections[1]["drafts"]) == 2 and sections[1]["drafts"][0]["id"] == saved["section_test_2"]
    assert sections[0]["drafts"][-1]["id"] == saved["section_test_1"]
    assert all(not section["drafts"] for section in sections[2:])
