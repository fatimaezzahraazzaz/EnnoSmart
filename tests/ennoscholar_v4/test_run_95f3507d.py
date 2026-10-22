"""Run 95f3507d: « rédige toutes les sections restantes de l'état de l'art ».

Real run: section 3.1 cited ``[ev_c9e88fa7c429da9f7]``, a truncated form of the declared
proof ``ev_c9e88fa7c429da9f7f90cf10``. The refusal said only « Référence ou preuve non
déclarée », the local diagnosis found nothing, and the repair echoed only the previous
patch (``text: null``): the model removed the « 3.1 » of the title three times, the text
was revalidated unchanged and refused three times. Offline: the real session is read in
read-only mode; nothing is written to the base, no provider is called.
"""
import copy
import json
import re

import pytest

from tests.ennoscholar_v4.test_run_dcf5d05b import session, tools

SECTION_31 = "section_3905aabdae56451f8b400c5e49392161"
REFUSED_TASK = "6462fb7b0329c97fea3f93e4b43f304a589be220d5a1cec5366ca036997e1e35"
TRUNCATED, FULL_ID = "ev_c9e88fa7c429da9f7", "ev_c9e88fa7c429da9f7f90cf10"


def refused_proposal():
    state = session()
    # This fixture exercises a historical proposal's validators. A later run's
    # permissions are not its scope; the real agent also clears these turn-local
    # fields before resolving each new request. Scientific cards/proofs stay intact.
    state.pop("turn_goal", None)
    state.pop("conversation_references", None)
    task = state["task_results"].get(REFUSED_TASK)
    if not task:
        pytest.skip("Tâche du run 95f3507d absente")
    return state, task, copy.deepcopy(task["attempts"][-1]["arguments"])


# --- 1. The defect, located exactly ---------------------------------------------------------

def test_the_diagnosis_names_the_paragraph_the_truncated_marker_and_the_proof_it_designates():
    from agents.EnnoScholarV4.local_repair import diagnose, split
    state, task, proposal = refused_proposal()
    assert "[" + TRUNCATED + "]" in proposal["text"] and FULL_ID in proposal["evidence_ids"]
    diagnosis = diagnose(tools(copy.deepcopy(state)), proposal, task["refusal"])
    assert [p["index"] for p in diagnosis["paragraphs"]] == [1] and diagnosis["kept_paragraphs"] == [0, *range(2, 10)]
    problem = diagnosis["paragraphs"][0]["problems"][0]
    assert problem["code"] == "truncated_identifier" and problem["marker"] == TRUNCATED
    assert problem["candidates"] == [FULL_ID] and TRUNCATED in problem["excerpt"]
    assert TRUNCATED in split(proposal["text"])[1]


def test_the_publication_refusal_names_the_undeclared_markers():
    from types import SimpleNamespace as NS
    from agents.EnnoScholarV4.publication import publish
    state, _, proposal = refused_proposal()
    toolkit = tools(copy.deepcopy(state))
    toolkit.finalization_only, toolkit.saving_section = True, True
    result = publish(toolkit, NS(answer=proposal["text"], article_ids=proposal["article_ids"],
        evidence_ids=proposal["evidence_ids"], analyses=[], displayed_article_ids=None, coverage_sufficient=False,
        remaining_uncertainties=[]), float("inf"))
    assert result["rejection_code"] == "unknown_marker" and result["unknown_markers"] == [TRUNCATED]
    assert TRUNCATED in result["error"]


# --- 2. The deterministic correction, then every check ---------------------------------------

def test_the_real_refused_section_is_saved_after_completing_the_identifier_and_passing_every_check():
    state, _, proposal = refused_proposal()
    local = copy.deepcopy(state)
    result = tools(local).execute("write_section", copy.deepcopy(proposal), float("inf"))
    assert result["saved"], result
    assert result["marker_corrections"] == [{"paragraph": 1, "written": TRUNCATED, "corrected_to": FULL_ID,
        "problem": "truncated_identifier"}]
    draft = next(s for s in local["outline"]["sections"] if s["id"] == SECTION_31)["drafts"][-1]
    assert "[" + FULL_ID + "]" in draft["raw_text"] and "[" + TRUNCATED + "]" not in draft["raw_text"]
    # Only the identifier changed: every other character of the proposal is kept.
    assert draft["raw_text"] == proposal["text"].replace("[" + TRUNCATED + "]", "[" + FULL_ID + "]")
    # The checks ran on the corrected text: provenance typed, numbers traced, proof attached.
    assert draft["numeric_trace"] is not None and FULL_ID in draft["evidence_ids"]
    assert any(FULL_ID in c["evidence_ids"] for c in draft["claims"])
    # Nothing else of the real session changed in this copy, and the base was opened read-only.
    assert [s["id"] for s in local["outline"]["sections"]] == [s["id"] for s in state["outline"]["sections"]]


def test_the_merged_proposal_is_what_a_repair_sees_not_the_last_patch():
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.task_results import TaskResults
    state, task, proposal = refused_proposal()
    results = TaskResults(state, ScholarV4Config(api_key="mock"), dict(input_tokens=0, output_tokens=0,
        estimated_cost_usd=0., unreported_reserved_cost_usd=0.), [])
    raw = json.loads(next(c for c in task["attempts"][-1]["content"] if c.get("type") == "function_call")["arguments"])
    assert raw["text"] is None and raw["paragraph_patches"]                     # what the real repair was shown
    call, output = results.recovery_exchange(task)
    echoed = json.loads(call["arguments"])
    assert echoed["text"] == proposal["text"] and len(echoed["text"]) > 9000      # the whole proposal
    assert set(echoed) <= {"action", "section_id", "instructions", "text", "article_ids", "evidence_ids",
        "project_evidence_ids"}                                                      # no duplicated claims or summary
    assert json.loads(output["output"])["rejection_code"] == "unknown_marker"


# --- 3. What is never corrected automatically -------------------------------------------------

def corpus():
    from tests.ennoscholar_v4.test_context_memory import rich_corpus, seven_section_plan
    t, ids = rich_corpus(2)
    seven_section_plan(t, ids)
    section = t.state["outline"]["sections"][0]
    proofs = {aid: [e for e in t.shown_evidence if t.evidence[e]["article_id"] == aid] for aid in ids}
    return t, ids, section, proofs


def save(t, section, text, article_ids, evidence_ids):
    return t.execute("write_section", {"section_id": section["id"], "action": "save", "instructions": "x", "text": text,
        "article_ids": article_ids, "evidence_ids": evidence_ids}, float("inf"))


def first_sentence(t, eid):
    return t.evidence[eid]["exact_quote"].split(" The surrounding")[0]


def test_an_ambiguous_or_too_short_prefix_is_refused_with_its_paragraph_not_guessed():
    from agents.EnnoScholarV4.local_repair import marker_issues
    t, ids, section, proofs = corpus()
    a = proofs[ids[0]][0]
    # Two proofs sharing a 14-character prefix: no unique completion.
    ambiguous = a[:3+14]
    twin = ambiguous + ("0" if a[3+14] != "0" else "1") * (len(a)-3-14)
    t.evidence[twin] = {**t.evidence[a], "evidence_id": twin}
    t.shown_evidence.append(twin)
    text = f"La publication [{ids[0]}] décrit sa méthode [{a}].\n\nElle précise ses réglages [{ambiguous}]."
    issues = marker_issues(t, {"text": text, "article_ids": [ids[0]], "evidence_ids": [a, twin]})
    assert issues[0]["paragraph"] == 1 and issues[0]["problem"] == "truncated_identifier" and not issues[0]["fixable"]
    assert set(issues[0]["candidates"]) == {a, twin}
    result = save(t, section, text, [ids[0]], [a])
    assert not result.get("saved") and result["unknown_markers"] == [ambiguous]
    short = a[:3+8]
    result = save(t, section, f"La publication [{ids[0]}] décrit sa méthode [{short}].", [ids[0]], [a])
    assert not result.get("saved") and result["rejection_code"] == "unknown_marker" and short in result["unknown_markers"]


def test_a_prefix_of_another_works_proof_or_an_unknown_id_is_never_completed():
    t, ids, section, proofs = corpus()
    other = proofs[ids[1]][0]
    # Only ids[0] is declared: a proof of ids[1] is not admissible for completion.
    result = save(t, section, f"La publication [{ids[0]}] décrit sa méthode [{other[:20]}].", [ids[0]], [proofs[ids[0]][0]])
    assert not result.get("saved") and result["unknown_markers"] == [other[:20]]
    result = save(t, section, f"La publication [{ids[0]}] décrit sa méthode [ev_000000000000000000000000].",
        [ids[0]], [proofs[ids[0]][0]])
    assert not result.get("saved") and "ev_000000000000000000000000" in result["error"]


def test_a_valid_proof_of_a_declared_work_cited_without_declaration_is_declared_and_still_checked():
    t, ids, section, proofs = corpus()
    a, b = proofs[ids[0]][:2]
    text = f"La publication [{ids[0]}] décrit sa méthode [{a}]. Elle détaille aussi ce point [{b}]."
    result = save(t, section, text, [ids[0]], [a])
    assert result["saved"] and result["marker_corrections"][0]["problem"] == "undeclared_evidence"
    assert set(section["drafts"][-1]["evidence_ids"]) == {a, b}


def test_citation_and_attribution_controls_still_refuse_after_a_correction():
    t, ids, section, proofs = corpus()
    a = proofs[ids[0]][0]
    # Completed identifier, but a name no cited passage supports: attribution still refuses.
    unsupported = f"La publication [{ids[0]}] surpasse HiSup sur les Pays-Bas [{a[:20]}]."
    with pytest.raises(ValueError, match="Attribution"):
        save(t, section, unsupported, [ids[0]], [a])
    # Completed identifier, but a value absent from the cited passage: numeric trace still refuses.
    with pytest.raises(ValueError, match="Valeurs chiffrées"):
        save(t, section, f"La publication [{ids[0]}] atteint 97,3 % d'exactitude [{a[:20]}].", [ids[0]], [a])
    # A declared proof of a work that is not cited stays refused.
    other = proofs[ids[1]][0]
    with pytest.raises(ValueError, match="sans preuve"):
        save(t, section, f"La publication [{ids[0]}] décrit sa méthode [{a}].", [ids[0]], [a, other])


# --- 4. Through the agent: corrected once, no repair call ------------------------------------

def test_the_agent_saves_a_section_with_a_truncated_proof_in_one_call_without_a_repair():
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.config import ScholarV4Config
    from tests.ennoscholar_v4.replay_b52f582a import sources
    from tests.ennoscholar_v4.test_run_dcf5d05b import Model, SECTION_1, before_run
    state, proposals, goal = before_run()
    proposal = copy.deepcopy(proposals[-1])
    cited = [e for e in re.findall(r"\[(ev_[0-9a-f]+)\]", proposal["text"]) if e in proposal["evidence_ids"]]
    full = cited[0]
    proposal["text"] = proposal["text"].replace("[" + full + "]", "[" + full[:20] + "]", 1)
    model, events = Model(goal, proposal), []
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False),
        client=model, sources=sources()).run(state, emit=events.append)
    progress = state["document"]["writing_progress"]
    assert SECTION_1 in progress["saved_draft_ids"] and not result["partial"]
    assert model.sections == [SECTION_1]                                         # one proposal, no repair request
    corrections = [e for e in events if e.get("tool") == "writing_progress" and e.get("status") == "marker_corrections"]
    assert corrections and corrections[0]["corrections"][0]["corrected_to"] == full
    draft = state["outline"]["sections"][0]["drafts"][-1]
    assert "[" + full[:20] + "]" not in draft["raw_text"]


def test_a_prefix_shared_with_another_known_proof_is_never_completed_even_if_only_one_is_admissible():
    """Codex P2: uniqueness was checked among admissible proofs only; a proof of another,
    undeclared work sharing the prefix was hidden and the completion accepted."""
    from agents.EnnoScholarV4.local_repair import marker_issues
    t, ids, section, proofs = corpus()
    a = proofs[ids[0]][0]
    prefix = a[:3+14]
    other = prefix + ("0" if a[3+14] != "0" else "1") * (len(a)-3-14)
    t.evidence[other] = {**t.evidence[proofs[ids[1]][0]], "evidence_id": other}     # a proof of the other work
    t.shown_evidence.append(other)
    text = f"La publication [{ids[0]}] décrit sa méthode [{prefix}]."
    issue = marker_issues(t, {"text": text, "article_ids": [ids[0]], "evidence_ids": [a]})[0]
    assert issue["candidates"] == [a] and issue["known_matches"] == 2 and not issue["fixable"]
    result = save(t, section, text, [ids[0]], [a])
    assert not result.get("saved") and result["unknown_markers"] == [prefix]
