"""Plan edit vs content revision (block 1, planning).

« Modifie le titre de la section 3 dans le plan » changes the plan: it opens no
drafting and no mission, may change section 3's entry only, and on an approved plan
follows V4's consultant validation (a proposal from Sol, or an applied consultant
edit that withdraws the approval). « Réécris le contenu de la section 3 » stays a
targeted drafting request whose scope forbids any plan change. Copies of the real
lithium session, real V5 tools, no network and no database outside tmp_path.
"""
import copy
import gzip
import json
from unittest.mock import Mock
import pytest
from sqlalchemy import select
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5 import writing_progress as wp
from agents.EnnoScholarV5.persistence import Operation
from agents.EnnoScholarV5.runtime import ScientificAdapter
from agents.EnnoScholarV5.writing_job import wants_full_draft
from experiments.ennoscholar_v5.adapter import Decision
from .test_graph import ARTIFACTS
from .test_live_integration import app_env, call, run_sync, scientific_state

TITLE_REQUEST = "Modifie le titre de la section 3 dans le plan"
TRIAL_REQUEST = ("Dans le plan de l'état de l'art, modifie uniquement le titre de la section 3 pour qu'il mette davantage "
    "l'accent sur les limites des approches existantes. Conserve toutes les autres sections inchangées.")
CONTENT_REQUEST = "Réécris le contenu de la section 3"
NEW_TITLE = "Détecter et quantifier le dépôt : limites des approches existantes"


def lithium(approved=True):
    with gzip.open(ARTIFACTS/"real-lithium-session.json.gz", "rt", encoding="utf-8") as f:
        state = json.load(f)["state"]
    if not approved:
        state["outline"].pop("approval", None)
        state["outline"]["status"] = "proposed"
    # The saved progress belongs to an earlier request: it never applies to a new one.
    return state


def ask(state, request, mid="probe"):
    state["messages"].append({"id": mid, "role": "user", "content": request})
    state["v5_finalization_mode"] = False
    workflow.begin(state, request)  # As the service does when the run opens.
    return state


def ids(state):
    return [s["id"] for s in state["outline"]["sections"]]


def titles(state):
    return [s["title"] for s in state["outline"]["sections"]]


def drafts(state):
    return [copy.deepcopy(s.get("drafts")) for s in state["outline"]["sections"]]


def outline(action, section_id=None, title=None, sections=None):
    return {"action": action, "title": title, "section_id": section_id, "target_section_id": None, "position": None,
        "sections": sections, "article_ids": None}


def adapter():
    return ScientificAdapter(ScholarV4Config(api_key="offline", web_search_enabled=False), Mock(), sources=Mock())


def run(state, tool, args):
    return adapter().execute(state, Decision(tool, args, call_id="o"), remaining_seconds=60)


def plan_revision_goal(state):
    """Sol's saved intent for this request: a plan revision (V4's consultant edit)."""
    request_id = workflow.current_request(state)["id"]
    state["turn_goal"] = {**(state.get("turn_goal") or {}), "deliverable": "plan_revision",
        "references": {**((state.get("turn_goal") or {}).get("references") or {}), "message_id": request_id}}


# --- 1. Detection -----------------------------------------------------------------------------

@pytest.mark.parametrize("request_text", [TITLE_REQUEST, TRIAL_REQUEST, "Renomme la section 3",
    "Change l'intitulé de la troisième section", "Modifie le titre de la 3e section du plan"])
def test_a_plan_edit_never_opens_drafting_nor_a_mission(request_text):
    state = ask(lithium(), request_text)
    assert wp.plan_edit_request(state, request_text)
    assert wp.plan_edit_target(state, request_text) == ids(state)[2]
    assert wp.writing_request_blocked(state, request_text)
    assert wp.begin_writing(copy.deepcopy(state), request_text) is None
    assert workflow.writing_progress(state) is None
    assert not wants_full_draft(state, request_text)


@pytest.mark.parametrize("request_text", [CONTENT_REQUEST, "Réécris la section 3", "Modifie la section 3",
    "Rédige la section 3 du plan", "Modifie le texte de la section 3", "Finalise uniquement la section 1"])
def test_a_content_request_stays_a_targeted_drafting_request(request_text):
    state = ask(lithium(), request_text)
    assert not wp.plan_edit_request(state, request_text)
    progress = workflow.writing_progress(state)
    target = ids(state)[0] if "section 1" in request_text else ids(state)[2]
    assert progress["mode"] == "section" and progress["target_ids"] == [target]
    assert not wants_full_draft(state, request_text)


# --- 2. Tools: unapproved plan -----------------------------------------------------------------

def test_the_title_of_section_3_changes_and_nothing_else_on_an_unapproved_plan():
    state = ask(lithium(approved=False), TRIAL_REQUEST)
    before_titles, before_drafts = titles(state), drafts(state)
    out = run(state, "manage_outline", outline("rename", ids(state)[2], NEW_TITLE))
    assert out.get("saved") is True, out
    assert titles(state) == before_titles[:2] + [NEW_TITLE] + before_titles[3:]
    assert drafts(state) == before_drafts
    assert workflow.writing_progress(state) is None


@pytest.mark.parametrize("args", [
    outline("rename", "SECOND", "Autre titre"),            # another section
    outline("replace", sections=[{"title": "Un seul", "description": "", "article_ids": []}]),
    outline("delete", "THIRD"),
    outline("move", "THIRD"),
])
def test_a_targeted_plan_edit_cannot_change_another_section_or_the_plan(args):
    state = ask(lithium(approved=False), TITLE_REQUEST)
    args = {**args, "section_id": {"SECOND": ids(state)[1], "THIRD": ids(state)[2]}.get(args["section_id"], args["section_id"])}
    before = copy.deepcopy(state["outline"])
    out = run(state, "manage_outline", args)
    assert out.get("saved") is False and out.get("rejection_code") == "plan_edit_out_of_scope", out
    assert state["outline"] == before


def test_a_plan_edit_request_never_saves_a_section_text():
    state = ask(lithium(approved=False), TITLE_REQUEST)
    before = drafts(state)
    out = run(state, "write_section", {"section_id": ids(state)[2], "action": "save", "instructions": "x",
        "text": "Texte.", "article_ids": [], "evidence_ids": []})
    assert not out.get("saved") and "modification du plan" in str(out.get("error")), out
    assert drafts(state) == before


# --- 3. Approved plan: the consultant's validation stays in force ------------------------------

def test_on_an_approved_plan_sol_alone_only_proposes_the_new_title():
    """Without any plan request from the consultant, Sol never changes the approved plan."""
    state = ask(lithium(), "Explique-moi le plan sans rien modifier.")
    before = copy.deepcopy(state["outline"])
    out = run(state, "manage_outline", outline("rename", ids(state)[2], NEW_TITLE))
    assert out.get("saved") is False and out.get("approval_required") is True, out
    assert state["outline"] == before and workflow.plan_approved(state)


def test_the_consultant_title_request_on_an_approved_plan_is_applied_and_withdraws_the_approval():
    """B1: the consultant's explicit request is its own edit (V4 consultant edit), whatever goal Sol declared."""
    state = ask(lithium(), TITLE_REQUEST)
    before_titles, before_drafts = titles(state), drafts(state)
    out = run(state, "manage_outline", outline("rename", ids(state)[2], NEW_TITLE))
    assert out.get("saved") is True, out
    assert titles(state) == before_titles[:2] + [NEW_TITLE] + before_titles[3:] and drafts(state) == before_drafts
    assert not workflow.plan_approved(state) and state["outline_versions"][-1]["approval"]  # Previous version kept.


def test_an_explicit_plan_revision_is_applied_and_withdraws_the_approval():
    state = ask(lithium(), TITLE_REQUEST)
    plan_revision_goal(state)
    before_titles, before_drafts = titles(state), drafts(state)
    assert workflow.plan_approved(state)
    out = run(state, "manage_outline", outline("rename", ids(state)[2], NEW_TITLE))
    assert out.get("saved") is True, out
    assert titles(state) == before_titles[:2] + [NEW_TITLE] + before_titles[3:]
    assert drafts(state) == before_drafts
    # The changed plan must be validated again by the consultant before any drafting.
    assert not workflow.plan_approved(state) and not state["outline"].get("approval")


def test_a_content_revision_still_cannot_change_the_plan():
    state = ask(lithium(approved=False), CONTENT_REQUEST)
    before = copy.deepcopy(state["outline"])
    out = run(state, "manage_outline", outline("rename", ids(state)[2], NEW_TITLE))
    assert not out.get("saved") and "plan doit rester inchangé" in str(out.get("error")), out
    assert state["outline"] == before


# --- 4. Whole run through LangGraph with a scripted Sol ---------------------------------------

def test_a_title_change_run_publishes_without_drafting_or_mission(app_env):
    make, factory, _ = app_env
    state = lithium(approved=False)
    goal = call("set_turn_goal", {"requested_article_count": None, "deliverable": "plan_revision", "lock_ids": [],
        "focus_lock_ids": [], "exact_quote": "", "reading_depth": "metadata", "understanding": "Renommer la section 3 du plan.",
        "comparison_requested": None, "search_needed": False, "article_ids": None, "article_scope": "inclusive",
        "references_ambiguous": False, "constraints": None, "research_need": None}, "g")
    rename = call("manage_outline", outline("rename", ids(state)[2], NEW_TITLE), "o")
    final = call("finalize_response", {"answer": "Le titre de la section 3 a été modifié dans le plan ; le plan est à valider.",
        "article_ids": [], "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": [],
        "analyses": None, "displayed_article_ids": None}, "f")
    svc, sid, tr = make([goal, rename, final, final], state)
    result = run_sync(svc, sid, TRIAL_REQUEST)
    data = scientific_state(factory, sid)
    assert result.get("status") in {"completed", "partial"} and not str(result.get("reason")).startswith("v5_interrupted"), result
    assert titles(data) == titles(state)[:2] + [NEW_TITLE] + titles(state)[3:]
    assert drafts(data) == drafts(state)
    progress = (data.get("document") or {}).get("writing_progress") or {}
    assert progress.get("request_message_id") != workflow.current_request(data).get("id")  # No drafting opened.
    assert not data.get("writing_job")
    with factory() as db:
        tools = [o.request["tool"] for o in db.scalars(select(Operation).where(Operation.kind == "action"))]
    assert "write_section" not in tools and "manage_outline" in tools
