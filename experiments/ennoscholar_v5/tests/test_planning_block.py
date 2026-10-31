"""Planning block (audit of 2026-10-29): B1-B5 and I1-I5, offline.

Copies of the real lithium session (5 sections, sections 1-2 drafted, plan approved
or not), real V5 tools and LangGraph runs with a scripted Sol through the Responses
SDK on a simulated transport. No network, no database outside tmp_path.
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

PAPER = "paper_a39c5c7db2f6111f9728"  # A source of section 1 only.


def lithium(approved=True):
    with gzip.open(ARTIFACTS/"real-lithium-session.json.gz", "rt", encoding="utf-8") as f:
        state = json.load(f)["state"]
    if not approved:
        state["outline"].pop("approval", None)
        state["outline"]["status"] = "proposed"
    return state


def ask(state, request, mid="probe"):
    state["messages"].append({"id": mid, "role": "user", "content": request})
    state["v5_finalization_mode"] = False
    state["v5_tool_counters"] = {}  # Reset by the service at each request.
    state["v5_request_start"] = {"request_id": mid, "plan_revision": state["outline"].get("plan_revision"),
        "plan_approved": workflow.plan_approved(state)}
    workflow.begin(state, request)
    return state


def S(state, n):
    return state["outline"]["sections"][n-1]["id"]


def ids(state):
    return [s["id"] for s in state["outline"]["sections"]]


def titles(state):
    return [s["title"] for s in state["outline"]["sections"]]


def drafts_by_id(state):
    return {s["id"]: copy.deepcopy(s.get("drafts")) for s in state["outline"]["sections"]}


def outline(action, section_id=None, title=None, sections=None, target=None, position=None, article_ids=None):
    return {"action": action, "title": title, "section_id": section_id, "target_section_id": target, "position": position,
        "sections": sections, "article_ids": article_ids}


def new_section(title, article_ids=()):
    return {"title": title, "description": "Limites scientifiques des approches.", "article_ids": list(article_ids)}


def adapter():
    return ScientificAdapter(ScholarV4Config(api_key="offline", web_search_enabled=False), Mock(), sources=Mock())


def run(state, tool, args):
    return adapter().execute(state, Decision(tool, args, call_id="o"), remaining_seconds=60)


def goal(state, deliverable, search_needed=False):
    """As V4 record_goal stores Sol's set_turn_goal for this request (plan_operation included)."""
    from agents.EnnoScholarV5.turn import plan_operation
    registry = adapter().toolkit(state).registry
    state["turn_goal"] = {"deliverable": deliverable, "references": {"message_id": workflow.current_request(state)["id"]},
        "constraints": [], "search_needed": search_needed}
    if deliverable in {"plan", "plan_revision"}:
        state["turn_goal"]["plan_operation"] = plan_operation(state, workflow.current_request(state)["content"], registry)


def unchanged_except(before, after, changed_ids):
    """Every section other than changed_ids keeps its title, sources and drafts."""
    rows = {s["id"]: (s["title"], s["article_ids"], s.get("drafts")) for s in before["outline"]["sections"]}
    now = {s["id"]: (s["title"], s["article_ids"], s.get("drafts")) for s in after["outline"]["sections"]}
    return all(now.get(sid) == row for sid, row in rows.items() if sid not in changed_ids)


# --- B4: one or several sections named ------------------------------------------------------

@pytest.mark.parametrize("request_text,expected", [
    ("Révise la section 2, pas la section 5.", ("section", [2])),
    ("Rédige la section 3 puis la section 4.", ("section", [3, 4])),
    ("Rédige les sections 3 et 4.", ("section", [3, 4])),
    ("Améliore la section 2 et la section 5.", ("section", [2, 5])),
    ("Rédige la section 3 en t'appuyant sur la section 2.", ("section", [3])),
    ("Rédige la section 3 puis la section 7.", None),       # An unknown section: nothing, never all.
    ("Finalise uniquement la section 1.", ("section", [1])),  # Earlier fix kept.
    ("Rédige les sections 3 à 5.", ("full", [3, 4, 5])),
    ("Termine toutes les sections restantes.", ("full", [1, 2, 3, 4, 5])),
])
def test_b4_the_sections_named_and_only_them(request_text, expected):
    state = ask(lithium(), request_text)
    progress = workflow.writing_progress(state)
    got = progress and (progress["mode"], [ids(state).index(t)+1 for t in progress["target_ids"]])
    assert got == expected
    assert wants_full_draft(state, request_text) == (bool(expected) and expected[0] == "full")


def test_b4_a_listed_revision_writes_only_its_sections_and_names_them():
    state = ask(lithium(), "Rédige la section 3 puis la section 4.")
    wp.check_write_scope(state, S(state, 3))
    wp.check_write_scope(state, S(state, 4))
    with pytest.raises(ValueError):
        wp.check_write_scope(state, S(state, 5))
    with pytest.raises(ValueError):
        wp.check_write_scope(state, plan_change=True)
    answer = wp.writing_answer(state)
    assert titles(state)[2] in answer and titles(state)[3] in answer and "non enregistrées" in answer


# --- B5 / I5: plan edit or content revision -------------------------------------------------

@pytest.mark.parametrize("request_text,section", [("Réécris le contenu de la section 3", 3),
    ("Réécris la section 2 sans modifier le plan.", 2), ("Rédige la section 3 sans modifier le plan.", 3),
    ("Ne modifie pas le plan, améliore seulement la section 2.", 2), ("Modifie la section 3", 3)])
@pytest.mark.parametrize("approved", [True, False])
def test_i5_a_content_request_drafts_its_section_and_never_changes_the_plan(request_text, section, approved):
    state = ask(lithium(approved), request_text)
    goal(state, "plan_revision")  # Even if Sol misreads the request as a plan revision.
    progress = workflow.writing_progress(state)
    assert progress["mode"] == "section" and progress["target_ids"] == [S(state, section)]
    before = copy.deepcopy(state)
    out = run(state, "manage_outline", outline("rename", S(state, section), "Nouveau titre"))
    assert not out.get("saved") and state["outline"] == before["outline"], out


@pytest.mark.parametrize("request_text", ["Quel est le titre de la section 3 ?", "Explique-moi le plan sans rien modifier."])
def test_b5_a_question_about_the_plan_is_not_an_edit(request_text):
    state = ask(lithium(), request_text)
    assert not wp.plan_edit_request(state, request_text) and workflow.writing_progress(state) is None


# --- I1: the named section may move ---------------------------------------------------------

@pytest.mark.parametrize("approved", [True, False])
def test_i1_moving_the_named_section_is_allowed_and_only_it_moves(approved):
    state = ask(lithium(approved), "Déplace la section 5 en deuxième position.")
    before = copy.deepcopy(state)
    out = run(state, "manage_outline", outline("move", S(state, 5), position=2))
    assert out.get("saved") is True, out
    old = ids(before)
    assert ids(state) == [old[0], old[4], old[1], old[2], old[3]] and drafts_by_id(state) == drafts_by_id(before)


@pytest.mark.parametrize("args", [lambda s: outline("move", S(s, 3), position=1), lambda s: outline("rename", S(s, 2), "2. Autre"),
    lambda s: outline("delete", S(s, 3)), lambda s: outline("replace", sections=[new_section("Un seul")])])
def test_i1_a_title_request_still_changes_nothing_but_that_title(args):
    state = ask(lithium(False), "Modifie le titre de la section 3 dans le plan")
    before = copy.deepcopy(state["outline"])
    out = run(state, "manage_outline", args(state))
    assert out.get("saved") is False and out.get("rejection_code") == "plan_edit_out_of_scope", out
    assert state["outline"] == before


# --- I2: replacing a section is a plan edit -------------------------------------------------

@pytest.mark.parametrize("approved", [True, False])
def test_i2_replacing_a_section_updates_that_plan_entry_without_drafting(approved):
    text = "Remplace la section 4 par une section sur la modélisation du vieillissement."
    state = ask(lithium(approved), text)
    assert workflow.writing_progress(state) is None and not wants_full_draft(state, text)
    goal(state, "plan_revision")
    before = copy.deepcopy(state)
    sec4 = state["outline"]["sections"][3]
    out = run(state, "manage_outline", outline("update", S(state, 4), sections=[{**new_section(
        "4. Modélisation du vieillissement"), "article_ids": sec4["article_ids"]}]))
    assert out.get("saved") is True, out
    assert titles(state)[3] == "4. Modélisation du vieillissement" and unchanged_except(before, state, {S(state, 4)})
    save = run(state, "write_section", {"section_id": S(state, 4), "action": "save", "instructions": "x", "text": "Texte.",
        "article_ids": [], "evidence_ids": []})
    assert save.get("rejection_code") == "plan_edit_request"


# --- B2: removals and the « N parts maximum » constraint --------------------------------------

@pytest.mark.parametrize("approved", [True, False])
@pytest.mark.parametrize("request_text,n", [("Supprime la section 4.", 4), ("Supprime la dernière section.", 5)])
def test_b2_an_explicitly_requested_removal_is_applied(approved, request_text, n):
    state = ask(lithium(approved), request_text)
    goal(state, "plan_revision")  # search_needed=False: V4 kept the whole plan's corpus.
    before = copy.deepcopy(state)
    removed = S(state, n)
    out = run(state, "manage_outline", outline("delete", removed))
    assert out.get("saved") is True, out
    assert ids(state) == [sid for sid in ids(before) if sid != removed] and unchanged_except(before, state, {removed})


def test_b2_removing_an_article_from_a_section_is_applied():
    state = ask(lithium(False), f"Retire l'article {PAPER} de la section 1.")
    goal(state, "plan_revision")
    before = copy.deepcopy(state)
    out = run(state, "manage_outline", outline("dissociate", S(state, 1), article_ids=[PAPER]))
    assert out.get("saved") is True, out
    assert PAPER not in state["outline"]["sections"][0]["article_ids"] and unchanged_except(before, state, {S(state, 1)})
    assert state["outline"]["sections"][0]["drafts"] == before["outline"]["sections"][0]["drafts"]


def test_b2_a_plan_reduced_to_four_parts_can_drop_an_undrafted_section():
    state = ask(lithium(False), "Propose un nouveau plan en 4 parties maximum.")
    goal(state, "plan_revision")
    out = run(state, "manage_outline", outline("delete", S(state, 5)))
    assert out.get("saved") is True and len(ids(state)) == 4, out


def test_b2_an_unrequested_removal_still_keeps_the_corpus():
    state = ask(lithium(False), "Ajoute une section sur les limites scientifiques.")
    goal(state, "plan_revision")
    before = copy.deepcopy(state["outline"])
    out = run(state, "manage_outline", outline("delete", S(state, 4)))
    assert not out.get("saved") and "corpus" in str(out.get("error")) and state["outline"] == before, out


# --- I4: a drafted section is protected ------------------------------------------------------

@pytest.mark.parametrize("request_text", ["Modifie le plan.", "Propose un nouveau plan en 4 parties maximum.",
    "Supprime la section 4."])
def test_i4_a_drafted_section_never_leaves_the_plan_unless_named(request_text):
    state = ask(lithium(False), request_text)
    goal(state, "plan")
    before = copy.deepcopy(state["outline"])
    out = run(state, "manage_outline", outline("delete", S(state, 2)))
    assert out.get("saved") is False and out.get("rejection_code") in {"drafted_section_protected", "plan_edit_out_of_scope"}, out
    assert state["outline"] == before


def test_i4_the_consultant_naming_a_drafted_section_removes_it_and_keeps_its_versions():
    state = ask(lithium(False), "Supprime la section 2.")
    goal(state, "plan_revision")
    drafted = copy.deepcopy(state["outline"]["sections"][1])
    out = run(state, "manage_outline", outline("delete", drafted["id"]))
    assert out.get("saved") is True, out
    assert drafted["id"] not in ids(state)
    assert any(s["id"] == drafted["id"] and s["drafts"] == drafted["drafts"] for s in state["outline_versions"][-1]["sections"])


def test_i4_a_drafted_section_is_never_merged_away():
    state = ask(lithium(False), "Fusionne les sections 2 et 3.")
    goal(state, "plan_revision")
    before = copy.deepcopy(state["outline"])
    out = run(state, "manage_outline", outline("merge", S(state, 2), target=S(state, 3)))
    assert out.get("rejection_code") == "drafted_section_protected" and state["outline"] == before, out
    # The other direction keeps section 2's text as its current version.
    out = run(state, "manage_outline", outline("merge", before["sections"][2]["id"], target=before["sections"][1]["id"]))
    assert out.get("saved") is True, out
    assert state["outline"]["sections"][1]["drafts"][-1] == before["sections"][1]["drafts"][-1]


# --- I3: a plan request may end with a question --------------------------------------------

def final(answer="Quelle modification souhaitez-vous apporter au plan ?"):
    return {"answer": answer, "article_ids": [], "evidence_ids": [], "coverage_sufficient": False,
        "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": None}


@pytest.mark.parametrize("approved", [True, False])
def test_i3_an_imprecise_plan_request_can_be_answered_by_a_question(approved):
    state = ask(lithium(approved), "Je veux modifier le plan déjà validé.")
    goal(state, "plan_revision")
    ad = adapter()
    first = ad.execute(state, Decision("finalize_response", final(), call_id="f1"), remaining_seconds=60)
    assert first.get("rejection_code") == "plan_not_saved"
    second = ad.execute(state, Decision("finalize_response", final(), call_id="f2"), remaining_seconds=60)
    assert second.get("rejection_code") != "plan_not_saved"
    assert workflow.plan_note(state) == workflow.PLAN_UNCHANGED and workflow.plan_approved(state) == approved


def test_i3_a_new_plan_must_still_be_saved():
    state = lithium(False)
    state.pop("outline")
    state["messages"].append({"id": "probe", "role": "user", "content": "Propose-moi un plan pour cet état de l'art."})
    state["v5_request_start"] = {"request_id": "probe", "plan_revision": None}
    state["turn_goal"] = {"deliverable": "plan", "references": {"message_id": "probe"}}
    state["v5_tool_counters"] = {}
    ad = adapter()
    for cid in ("f1", "f2"):
        assert ad.execute(state, Decision("finalize_response", final(), call_id=cid),
            remaining_seconds=60).get("rejection_code") == "plan_not_saved"


# --- B1: a consultant's change of an approved plan ------------------------------------------

@pytest.mark.parametrize("deliverable", ["plan", "plan_revision", "answer"])
def test_b1_the_consultant_request_on_an_approved_plan_is_applied_then_needs_validation(deliverable):
    state = ask(lithium(), "Ajoute une section sur les limites scientifiques.")
    goal(state, deliverable)
    before = copy.deepcopy(state)
    out = run(state, "manage_outline", outline("add", sections=[new_section("6. Limites scientifiques")]))
    assert out.get("saved") is True, out
    assert len(ids(state)) == 6 and unchanged_except(before, state, set())
    assert not workflow.plan_approved(state) and workflow.plan_note(state) == workflow.PLAN_REVALIDATION
    for n in (1, 2):  # Nothing is drafted before the new validation.
        save = run(state, "write_section", {"section_id": S(state, n), "action": "save", "instructions": "x",
            "text": "Texte.", "article_ids": [], "evidence_ids": []})
        assert not save.get("saved")
    assert drafts_by_id(state) == {**drafts_by_id(before), ids(state)[-1]: []}


def test_b1_a_plan_goal_on_an_approved_plan_publishes_its_change():
    state = ask(lithium(), "Propose-moi un plan pour cet état de l'art.")
    goal(state, "plan")
    sec4 = state["outline"]["sections"][3]
    ad = adapter()
    out = ad.execute(state, Decision("manage_outline", outline("update", S(state, 4), sections=[{**new_section(
        "4. Gestion thermique"), "article_ids": sec4["article_ids"]}]), call_id="o"), remaining_seconds=60)
    assert out.get("saved") is True and not workflow.plan_required(state), out


# --- B3: drafting resumes on the revalidated plan ----------------------------------------------

def edited_and_revalidated(approved_at_end=True):
    """A full draft opened on revision 1; the consultant renames section 3, then validates."""
    state = ask(lithium(), "Rédige l'état de l'art complet.", mid="m1")
    first = copy.deepcopy(workflow.writing_progress(state))
    from agents.EnnoScholarV5.tools.outline import manage_outline, OutlineArgs
    registry = adapter().toolkit(state).registry
    manage_outline(state, OutlineArgs(action="rename", title="3. Titre du consultant", section_id=S(state, 3),
        target_section_id=None, position=None, sections=None, article_ids=None), registry, consultant=True)
    if approved_at_end:
        from agents.EnnoScholarV5.manuscript import approve_outline
        approve_outline(state, 1)
    return state, first


@pytest.mark.parametrize("approved_at_end", [True, False])
def test_b3_continue_after_a_plan_change_drafts_the_current_plan(approved_at_end):
    state, first = edited_and_revalidated(approved_at_end)
    text = "Continue la rédaction."
    assert wants_full_draft(state, text)
    ask(state, text, mid="m2")
    progress = workflow.writing_progress(state)
    assert progress["id"] != first["id"] and progress["status"] == "running"
    assert progress["target_ids"] == ids(state) and progress["plan_revision"] == state["outline"]["plan_revision"]
    assert workflow.next_writing_section(state)["id"] == S(state, 3)  # Sections 1-2 stay saved.
    assert progress["approval_required"] == (not approved_at_end)


def test_b3_a_section_revision_resumes_the_same_section_after_a_plan_change():
    state = ask(lithium(), "Révise la section 2.", mid="m1")
    first = copy.deepcopy(workflow.writing_progress(state))
    from agents.EnnoScholarV5.tools.outline import manage_outline, OutlineArgs
    registry = adapter().toolkit(state).registry
    manage_outline(state, OutlineArgs(action="rename", title="3. Autre", section_id=S(state, 3), target_section_id=None,
        position=None, sections=None, article_ids=None), registry, consultant=True)
    ask(state, "Continue la rédaction.", mid="m2")
    progress = workflow.writing_progress(state)
    assert progress["mode"] == "section" and progress["target_ids"] == [S(state, 2)]
    assert progress["baseline_draft_ids"] == first["baseline_draft_ids"] and progress["status"] == "running"


def test_b3_a_mission_waiting_for_validation_resumes_on_the_edited_plan(app_env, monkeypatch):
    from agents.EnnoScholarV5.manuscript import DocumentAction
    from agents.EnnoScholarV5 import writing_job
    make, factory, _ = app_env
    state = ask(lithium(False), "Rédige l'état de l'art complet.", mid="m1")
    progress = workflow.writing_progress(state)
    job = writing_job.new_job(ScholarV4Config(api_key="offline", writing_job_budget_usd=5.0), "m1", "r1", {})
    job.update(status="paused", paused_reason=writing_job.APPROVAL_PAUSE, goal={"deliverable": "draft"},
        progress={"id": progress["id"], "target_ids": list(progress["target_ids"]), "plan_revision": progress["plan_revision"],
            "mode": "full", "baseline_draft_ids": {}})
    state["writing_job"] = job
    svc, sid, tr = make([], state)
    dispatched = []
    monkeypatch.setattr(svc, "dispatch_job", lambda owner, s, job_id, force=False: dispatched.append(job_id))
    data = scientific_state(factory, sid)
    svc.document_action(1, sid, DocumentAction(action="outline_edit", expected_version=data["outline"]["version"],
        outline={"action": "rename", "title": "3. Titre du consultant", "section_id": S(state, 3), "target_section_id": None,
            "position": None, "sections": None, "article_ids": None}))
    data = scientific_state(factory, sid)
    svc.document_action(1, sid, DocumentAction(action="approve_outline", expected_version=data["outline"]["version"]))
    data = scientific_state(factory, sid)
    job = data["writing_job"]
    assert job["status"] == "waiting" and dispatched == [job["id"]]
    assert job["progress"]["plan_revision"] == data["outline"]["plan_revision"]
    assert writing_job.base.scope_problem(writing_job.base.segment_directive(job), data) is None
    assert data["document"]["writing_progress"]["request_message_id"] == "m1"
    assert [s["drafts"] for s in data["outline"]["sections"]] == [s["drafts"] for s in state["outline"]["sections"]]


# --- Whole runs through LangGraph with a scripted Sol --------------------------------------

def goal_call(deliverable, understanding, cid="g"):
    return call("set_turn_goal", {"requested_article_count": None, "deliverable": deliverable, "lock_ids": [],
        "focus_lock_ids": [], "exact_quote": "", "reading_depth": "none", "understanding": understanding,
        "comparison_requested": None, "search_needed": False, "article_ids": None, "article_scope": None,
        "references_ambiguous": False, "constraints": None, "research_need": None}, cid)


def final_call(answer="Réponse.", cid="f"):
    return call("finalize_response", final(answer), cid)


def graph_run(app_env, state, request, deliverable, steps):
    make, factory, _ = app_env
    svc, sid, tr = make([goal_call(deliverable, request)] + steps + [final_call()] * 3, state)
    result = run_sync(svc, sid, request)
    data = scientific_state(factory, sid)
    with factory() as db:
        tools = [(o.request["tool"], o.request["arguments"].get("action"), (o.response or {}).get("output", {}))
            for o in db.scalars(select(Operation).where(Operation.kind == "action"))]
    return result, data, tools, len(tr.requests)


@pytest.mark.parametrize("approved", [True, False])
@pytest.mark.parametrize("deliverable", ["plan_revision", "answer"])
def test_run_title_change_publishes_without_drafting(app_env, approved, deliverable):
    state = lithium(approved)
    request = "Modifie le titre de la section 3 dans le plan"
    result, data, tools, calls = graph_run(app_env, state, request, deliverable,
        [call("manage_outline", outline("rename", S(state, 3), "3. Détecter le dépôt : limites"), "o")])
    # Goal + edit; the confirmation is built from the saved plan (block 3, I2), no third call.
    assert calls == 2 and not str(result.get("reason") or "").startswith("v5_interrupted")
    assert titles(data) == titles(state)[:2] + ["3. Détecter le dépôt : limites"] + titles(state)[3:]
    assert drafts_by_id(data) == drafts_by_id(state) and not data.get("writing_job")
    assert "write_section" not in [t for t, *_ in tools]
    assert not workflow.plan_approved(data)
    note = workflow.PLAN_REVALIDATION if approved else workflow.PLAN_TO_VALIDATE
    assert note in result["answer"]


@pytest.mark.parametrize("approved", [True, False])
def test_run_delete_section_publishes(app_env, approved):
    state = lithium(approved)
    result, data, tools, calls = graph_run(app_env, state, "Supprime la section 4.", "plan_revision",
        [call("manage_outline", outline("delete", S(state, 4)), "o")])
    assert calls == 2 and [s for s in ids(data)] == [sid for sid in ids(state) if sid != S(state, 4)]
    assert unchanged_except(state, data, {S(state, 4)}) and not workflow.plan_approved(data)


def test_run_propose_plan_on_approved_plan_publishes_the_change(app_env):
    state = lithium(True)
    sec4 = state["outline"]["sections"][3]
    result, data, tools, calls = graph_run(app_env, state, "Propose-moi un plan pour cet état de l'art.", "plan",
        [call("manage_outline", outline("update", S(state, 4), sections=[{**new_section("4. Gestion thermique"),
            "article_ids": sec4["article_ids"]}]), "o")])
    assert calls == 3 and titles(data)[3] == "4. Gestion thermique" and workflow.PLAN_REVALIDATION in result["answer"]
    assert not any(o.get("rejection_code") == "plan_not_saved" for _, _, o in tools)


def test_run_imprecise_plan_request_ends_with_the_question(app_env):
    state = lithium(True)
    question = "Quelle modification du plan souhaitez-vous : un titre, l'ordre des sections, une section à ajouter ou retirer ?"
    result, data, tools, calls = graph_run(app_env, state, "Je veux modifier le plan déjà validé.", "plan_revision",
        [final_call("Le plan reste à préciser.", "f0"), final_call(question, "f1")])
    # Sol's first answer is refused once (no plan change); its question is then published.
    assert question in result["answer"] and workflow.PLAN_UNCHANGED in result["answer"]
    assert data["outline"] == state["outline"] and calls == 3
    assert [o.get("rejection_code") for t, _, o in tools if t == "finalize_response"][:1] == ["plan_not_saved"]


@pytest.mark.parametrize("approved", [True, False])
def test_run_explain_plan_changes_nothing(app_env, approved):
    state = lithium(approved)
    result, data, tools, calls = graph_run(app_env, state, "Explique-moi le plan sans rien modifier.", "answer",
        [call("manage_outline", outline("read"), "o")])
    assert data["outline"] == state["outline"] and workflow.PLAN_UNCHANGED not in result["answer"]
    assert workflow.PLAN_REVALIDATION not in result["answer"]
