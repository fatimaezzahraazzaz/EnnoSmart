"""Planning block, last two problems (2026-10-29), offline.

1. « Modifie le plan : supprime la section 4 » : V4 plan_operation read « modifie » as a
   revision of section 4 and refused its removal.
2. « Ajoute une section … puis rédige l'état de l'art complet » : the plan edit first,
   its validation by the consultant, then the drafting asked, never a premature mission.
"""
import copy
import pytest
from sqlalchemy import select
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.manuscript import approve_outline
from agents.EnnoScholarV5.persistence import Operation
from agents.EnnoScholarV5.writing_job import wants_full_draft
from .test_live_integration import app_env, call, run_sync, scientific_state
from .test_planning_block import (lithium, ask, S, ids, titles, drafts_by_id, outline, new_section, run, goal,
    unchanged_except, goal_call, final_call)

DELETE = "Modifie le plan : supprime la section 4."
COMBINED = "Ajoute une section sur les limites scientifiques puis rédige l'état de l'art complet."
CONTINUE = "Continue la rédaction."


# --- 1. « Modifie le plan : supprime la section 4 » -------------------------------------

@pytest.mark.parametrize("approved", [True, False])
@pytest.mark.parametrize("deliverable", ["plan_revision", "plan"])
def test_delete_requested_after_modifie_le_plan_is_applied(approved, deliverable):
    state = ask(lithium(approved), DELETE)
    goal(state, deliverable)
    before = copy.deepcopy(state)
    removed = S(state, 4)
    out = run(state, "manage_outline", outline("delete", removed))
    assert out.get("saved") is True, out
    assert ids(state) == [sid for sid in ids(before) if sid != removed] and unchanged_except(before, state, {removed})
    # The previous plan, with section 4, stays among the versions; the plan must be validated again.
    assert removed in [s["id"] for s in state["outline_versions"][-1]["sections"]]
    assert not workflow.plan_approved(state)
    assert workflow.plan_note(state) == (workflow.PLAN_REVALIDATION if approved else workflow.PLAN_TO_VALIDATE)


def test_after_the_removal_section_4_never_designates_another_section():
    state = ask(lithium(False), DELETE)
    goal(state, "plan_revision")
    assert run(state, "manage_outline", outline("delete", S(state, 4))).get("saved") is True
    after = copy.deepcopy(state["outline"])
    for section_id in ("4", after["sections"][3]["id"]):  # The former section 5, now 4th.
        out = run(state, "manage_outline", outline("delete", section_id))
        assert out.get("saved") is False and out.get("rejection_code") == "plan_edit_out_of_scope", out
    assert state["outline"] == after


@pytest.mark.parametrize("request_text,section", [("Modifie le plan : renomme la section 4.", 4),
    ("Modifie le plan : supprime la section 4.", 3), ("Modifie le titre de la section 4 dans le plan.", 4)])
def test_no_unrequested_removal(request_text, section):
    state = ask(lithium(False), request_text)
    goal(state, "plan_revision")
    before = copy.deepcopy(state["outline"])
    out = run(state, "manage_outline", outline("delete", S(state, section)))
    assert out.get("saved") is False, out
    assert state["outline"] == before


@pytest.mark.parametrize("approved", [True, False])
def test_a_drafted_section_named_for_removal_keeps_its_versions(approved):
    state = ask(lithium(approved), "Modifie le plan : supprime la section 2.")
    goal(state, "plan_revision")
    drafted = copy.deepcopy(state["outline"]["sections"][1])
    assert drafted["drafts"]
    out = run(state, "manage_outline", outline("delete", drafted["id"]))
    assert out.get("saved") is True, out
    kept = next(s for s in state["outline_versions"][-1]["sections"] if s["id"] == drafted["id"])
    assert kept["drafts"] == drafted["drafts"] and drafts_by_id(state) == {sid: d for sid, d in drafts_by_id(
        {"outline": state["outline_versions"][-1]}).items() if sid != drafted["id"]}


def test_a_drafted_section_not_named_is_not_removed():
    state = ask(lithium(False), DELETE)
    goal(state, "plan_revision")
    before = copy.deepcopy(state["outline"])
    out = run(state, "manage_outline", outline("delete", S(state, 2)))
    assert out.get("saved") is False and state["outline"] == before, out


# --- 2. Plan edit, then the drafting asked ----------------------------------------------

@pytest.mark.parametrize("approved", [True, False])
@pytest.mark.parametrize("request_text", [COMBINED, "Renomme la section 3 puis rédige-la.",
    "Ajoute une section sur les perspectives, puis rédige-la.",
    "Ajoute une section sur les limites scientifiques avant de rédiger l'état de l'art complet."])
def test_combined_request_drafts_nothing_now_and_keeps_the_intention(approved, request_text):
    state = ask(lithium(approved), request_text, mid="m1")
    assert workflow.writing_progress(state) is None and not wants_full_draft(state, request_text)
    pending = state["v5_pending_draft"]
    assert pending["request_id"] == "m1" and pending["plan_ids"] == ids(state)
    assert workflow.explicit_plan_request(state)  # Applied as the consultant's edit, even on an approved plan.


def combined_then_validated(approved, request_text=COMBINED, edit=None):
    state = ask(lithium(approved), request_text, mid="m1")
    goal(state, "draft")
    before = copy.deepcopy(state)
    out = run(state, "manage_outline", (edit or (lambda s: outline("add", sections=[new_section("6. Limites scientifiques")])))(state))
    assert out.get("saved") is True, out
    return state, before


@pytest.mark.parametrize("approved", [True, False])
def test_the_plan_is_changed_first_and_validation_is_asked(approved):
    state, before = combined_then_validated(approved)
    assert len(ids(state)) == 6 and unchanged_except(before, state, set()) and not workflow.plan_approved(state)
    note = workflow.plan_note(state)
    assert (workflow.PLAN_REVALIDATION if approved else workflow.PLAN_TO_VALIDATE) in note and "reprendra" in note
    save = run(state, "write_section", {"section_id": S(state, 3), "action": "save", "instructions": "x", "text": "Texte.",
        "article_ids": [], "evidence_ids": []})
    assert save.get("rejection_code") == "plan_edit_request" and "validation du plan" in save["error"]
    assert drafts_by_id(state) == {**drafts_by_id(before), ids(state)[-1]: []}


@pytest.mark.parametrize("approved", [True, False])
def test_continue_before_validation_drafts_nothing_and_opens_no_mission(approved):
    state, _ = combined_then_validated(approved)
    assert not wants_full_draft(state, CONTINUE)
    ask(state, CONTINUE, mid="m2")
    assert workflow.writing_progress(state) is None and state["v5_pending_draft"]["request_id"] == "m1"
    assert "Plan à valider" in workflow.plan_note(state)


@pytest.mark.parametrize("approved", [True, False])
def test_after_validation_continue_drafts_the_requested_manuscript(approved):
    state, before = combined_then_validated(approved)
    approve_outline(state, 1)
    assert wants_full_draft(state, CONTINUE)  # The mission is allowed only now.
    ask(state, CONTINUE, mid="m2")
    progress = workflow.writing_progress(state)
    assert progress["mode"] == "full" and progress["target_ids"] == ids(state) and len(ids(state)) == 6
    assert progress["include_conclusion"] and not progress["approval_required"]
    assert workflow.next_writing_section(state)["id"] == S(state, 3)  # Sections 1-2 stay saved as they are.
    assert "v5_pending_draft" not in state
    assert drafts_by_id(state) == {**drafts_by_id(before), ids(state)[-1]: []}


@pytest.mark.parametrize("request_text,edit,expected", [
    ("Renomme la section 3 puis rédige-la.", lambda s: outline("rename", S(s, 3), "3. Observabilité"), [3]),
    ("Ajoute une section sur les perspectives, puis rédige-la.",
     lambda s: outline("add", sections=[new_section("6. Perspectives")]), [6]),
])
def test_after_validation_a_pronoun_drafts_the_section_of_the_plan_edit(request_text, edit, expected):
    state, _ = combined_then_validated(False, request_text, edit)
    approve_outline(state, 1)
    assert not wants_full_draft(state, CONTINUE)  # A single section: no mission.
    ask(state, CONTINUE, mid="m2")
    progress = workflow.writing_progress(state)
    assert progress["mode"] == "section" and progress["target_ids"] == [S(state, n) for n in expected]


def test_a_new_drafting_request_replaces_the_kept_intention():
    state, _ = combined_then_validated(True)
    approve_outline(state, 1)
    ask(state, "Rédige la section 2.", mid="m2")
    progress = workflow.writing_progress(state)
    assert progress["mode"] == "section" and progress["target_ids"] == [S(state, 2)] and "v5_pending_draft" not in state


def test_if_the_plan_was_not_changed_nothing_is_drafted():
    state = ask(lithium(False), COMBINED, mid="m1")
    goal(state, "draft")
    assert workflow.plan_required(state)  # The plan edit comes first.
    assert "n'est pas lancée" in workflow.plan_note(state)


# --- Whole runs through LangGraph, then the consultant's validation ----------------------

@pytest.mark.parametrize("approved", [True, False])
def test_run_combined_then_validation_then_continue(app_env, approved):
    from agents.EnnoScholarV5.manuscript import DocumentAction
    make, factory, _ = app_env
    state = lithium(approved)
    script = [goal_call("draft", COMBINED), call("manage_outline", outline("add", sections=[new_section(
        "6. Limites scientifiques")]), "o"), final_call("Section ajoutée au plan.")] + [final_call()] * 3
    svc, sid, tr = make(script, state)
    result = run_sync(svc, sid, COMBINED)
    data = scientific_state(factory, sid)
    assert len(tr.requests) == 2 and len(ids(data)) == 6 and not workflow.plan_approved(data)
    assert unchanged_except(state, data, set()) and not data.get("writing_job")
    assert workflow.writing_progress(data) is None and data["v5_pending_draft"]["clause"].startswith("rédige l'état de l'art")
    assert "reprendra après votre validation" in result["answer"]
    with factory() as db:
        tools = [o.request["tool"] for o in db.scalars(select(Operation).where(Operation.kind == "action"))]
    assert "write_section" not in tools
    assert not wants_full_draft(data, CONTINUE)  # No mission before the validation.
    svc.document_action(1, sid, DocumentAction(action="approve_outline", expected_version=data["outline"]["version"]))
    data = scientific_state(factory, sid)
    assert workflow.plan_approved(data) and wants_full_draft(data, CONTINUE)
    tr.choices[:] = [goal_call("draft", CONTINUE, "g2")] + [final_call("x", f"f{i}") for i in range(4)]
    run_sync(svc, sid, CONTINUE)
    data = scientific_state(factory, sid)
    progress = data["document"]["writing_progress"]
    assert progress["mode"] == "full" and progress["target_ids"] == ids(data) and len(ids(data)) == 6
    assert progress["request_message_id"] == workflow.current_request(data)["id"]
    assert drafts_by_id(data) == {**drafts_by_id(state), ids(data)[-1]: []}


@pytest.mark.parametrize("approved", [True, False])
def test_run_modifie_le_plan_supprime_la_section_4(app_env, approved):
    make, factory, _ = app_env
    state = lithium(approved)
    script = [goal_call("plan_revision", DELETE), call("manage_outline", outline("delete", S(state, 4)), "o"),
        final_call("Section 4 supprimée.")] + [final_call()] * 3
    svc, sid, tr = make(script, state)
    result = run_sync(svc, sid, DELETE)
    data = scientific_state(factory, sid)
    assert len(tr.requests) == 2 and ids(data) == [sid_ for sid_ in ids(state) if sid_ != S(state, 4)]
    assert unchanged_except(state, data, {S(state, 4)}) and not workflow.plan_approved(data)
    assert (workflow.PLAN_REVALIDATION if approved else workflow.PLAN_TO_VALIDATE) in result["answer"]
