"""Typed provenance of drafted claims, section readiness and repair scheduling.

Offline: fake model, temporary state, no network, no OpenAI call. Mirrors the
refusals of real run b52f582a (session 23236f69).
"""
import copy
import json
import re
import time

import pytest

from agents.EnnoScholarV4.task_results import TaskResults
from tests.ennoscholar_v4.test_attribution import execute, two_papers

DEEPLAB = "Essai 3 — Changement d’architecture (piste SegArchA/SegArchB). Résultat : pipeline pas assez stable à l’entraînement."
ROADMARK = "La base annotée couvre environ 120 km et compile plus de 15 000 images."


def linked(delivered=True, verified=True):
    """Two analysed publications and a linked diagnostic with verified project passages."""
    t, ids, proofs = two_papers()
    t.state["mode"] = "diagnostic_linked"
    t.state["diagnostic_link"] = {"active_lock_ids": ["3"], "selected_lock_ids": ["3"], "revision": 1, "history": [],
        "snapshot": {"project": {"id": 1, "name": "P", "year": "2025"}, "diagnostic_id": 1, "source_version": "v",
            "locks": [{"id": "3", "title": "Stabilité SegArchA/SegArchB", "decision": "garde", "justification": "x",
                "source": {"evidence_ids": ["ev_fast_deeplab"]}}],
            "synthesis": {}, "context_sections": {}, "families": [], "rnd_approach": {}, "temporal_context": {},
            # The project's trial for lock 3: what was tried and what it gave, from the lock's own passage.
            "facts": [{"id": "ent_tried", "kind": "demarche", "statement": "Essai 3 — adaptation de SegArchA et SegArchB aux entrées LiDAR.",
                    "evidence_ids": ["ev_fast_deeplab"], "source_path": "entities[0]", "source": {}},
                {"id": "ent_obtained", "kind": "result", "statement": "Essai 3 — pipeline pas assez stable à l'entraînement.",
                    "evidence_ids": ["ev_fast_deeplab"], "source_path": "entities[1]", "source": {}}],
            "evidence": {
                "ev_fast_deeplab": {"evidence_id": "ev_fast_deeplab", "quote": DEEPLAB, "quote_verified": verified,
                    "document_name": "projet_essais.pdf", "section_title": "Essai 3", "chunk_id": "p67"},
                "ev_fast_roadmark": {"evidence_id": "ev_fast_roadmark", "quote": ROADMARK, "quote_verified": True,
                    "document_name": "dossier_cir.pdf", "section_title": "base annotée interne", "chunk_id": "p4"}}}}
    if delivered:
        t.state["delivered_project_evidence_ids"] = ["ev_fast_deeplab", "ev_fast_roadmark"]
    return t, ids, proofs


def save(t, ids, text, eids, project=(), claims=()):
    section = t.state["outline"]["sections"][0]
    return execute(t, "write_section", section_id=section["id"], action="save", instructions="x", text=text,
        article_ids=ids, evidence_ids=eids, project_evidence_ids=list(project), claims=list(claims))


def draft(t):
    return t.state["outline"]["sections"][0]["drafts"][-1]


def test_a_publication_result_a_project_result_and_their_comparison_are_saved_with_their_own_proofs():
    t, ids, proofs = linked()
    eid = proofs[ids[0]]["Our"]
    text = (f"Le réseau de Qian et al. produit un maillage quadrangulaire de surface [{ids[0]}] [{eid}]. "
        f"Dans le projet, l'adaptation SegArchA/SegArchB n'a pas donné un pipeline stable [projet:ev_fast_deeplab]. "
        f"Cette publication ne traite donc pas l'instabilité observée dans le projet [{ids[0]}] [{eid}] [projet:ev_fast_deeplab].")
    result = save(t, [ids[0]], text, [eid], ["ev_fast_deeplab"])
    assert result["saved"], result
    kinds = {c["statement"][:30]: c["kind"] for c in draft(t)["claims"]}
    assert sorted(kinds.values()) == ["comparison", "project", "scientific"]
    assert draft(t)["project_evidence_ids"] == ["ev_fast_deeplab"] and draft(t)["evidence_ids"] == [eid]
    # Rendered as a documentary reference to the project's passage, not as a publication link.
    assert "(projet : projet_essais.pdf, Essai 3)" in result["text"] and "[projet:" not in result["text"]


def test_diagnostic_ids_given_as_publication_evidence_are_routed_to_the_project():
    """Run b52f582a: ev_fast_… ids passed in evidence_ids were looked up among article
    proofs only (« Comparaison sans preuve réellement lue »)."""
    t, ids, proofs = linked()
    eid = proofs[ids[0]]["Our"]
    text = f"Le réseau produit un maillage quadrangulaire [{ids[0]}] [{eid}]. La base annotée couvre 120 km [ev_fast_roadmark]."
    result = save(t, [ids[0]], text, [eid, "ev_fast_roadmark"])
    assert result["saved"], result
    assert draft(t)["project_evidence_ids"] == ["ev_fast_roadmark"] and draft(t)["evidence_ids"] == [eid]
    assert "[projet:ev_fast_roadmark]" in draft(t)["raw_text"]


def test_a_project_architecture_named_with_its_project_proof_is_not_a_misattributed_publication():
    t, ids, proofs = linked()
    eid = proofs[ids[0]]["Our"]
    base = f"Le réseau produit un maillage quadrangulaire [{ids[0]}] [{eid}]. "
    refused = "Dans le projet, le changement d'architecture vers SegArchA/SegArchB n'a pas produit un pipeline stable."
    with pytest.raises(ValueError, match="Attribution non vérifiable"):
        save(t, [ids[0]], base + refused, [eid])
    assert save(t, [ids[0]], base + refused[:-1] + " [projet:ev_fast_deeplab].", [eid], ["ev_fast_deeplab"])["saved"]


@pytest.mark.parametrize("claim, message", [
    ({"kind": "scientific", "evidence_ids": [], "project_evidence_ids": ["ev_fast_deeplab"]}, "une preuve projet ne l'établit pas"),
    ({"kind": "project", "evidence_ids": ["EID"], "project_evidence_ids": []}, "une publication ne prouve pas"),
    ({"kind": "comparison", "evidence_ids": ["EID"], "project_evidence_ids": []}, "une comparaison cite"),
])
def test_each_claim_is_validated_according_to_its_declared_origin(claim, message):
    t, ids, proofs = linked()
    eid = proofs[ids[0]]["Our"]
    sentence = "Le réseau produit un maillage quadrangulaire de surface."
    text = f"{sentence[:-1]} [{ids[0]}] [{eid}] [projet:ev_fast_deeplab]."
    claim = {**claim, "evidence_ids": [eid if e == "EID" else e for e in claim["evidence_ids"]],
        "statement": f"{sentence[:-1]} [{ids[0]}] [{eid}] [projet:ev_fast_deeplab].", "origin": "source_statement"}
    with pytest.raises(ValueError, match=message):
        save(t, [ids[0]], text, [eid], ["ev_fast_deeplab"], [claim])
    assert not t.state["outline"]["sections"][0].get("drafts")


def test_a_claim_absent_from_the_text_and_unconsulted_or_unverified_project_proofs_are_refused():
    t, ids, proofs = linked()
    eid = proofs[ids[0]]["Our"]
    text = f"Le réseau produit un maillage quadrangulaire [{ids[0]}] [{eid}]."
    result = save(t, [ids[0]], text, [eid], claims=[{"statement": "Une reformulation qui n'est pas dans le texte.",
        "kind": "scientific", "evidence_ids": [eid], "project_evidence_ids": [], "origin": "source_statement"}])
    assert result["saved"] is False and result["claims_not_in_text"] == ["Une reformulation qui n'est pas dans le texte."]
    with pytest.raises(ValueError, match="Preuves projet inconnues"):
        save(t, [ids[0]], text + " Fait [projet:ev_fast_inventee].", [eid])
    t, ids, proofs = linked(delivered=False)
    t.state["delivered_project_evidence_ids"] = []
    with pytest.raises(ValueError, match="non consultées"):
        save(t, [ids[0]], text + " Essai [projet:ev_fast_deeplab].", [eid])
    t, ids, proofs = linked(verified=False)
    with pytest.raises(ValueError, match="non vérifiées"):
        save(t, [ids[0]], text + " Essai [projet:ev_fast_deeplab].", [eid])


def test_a_publication_result_still_needs_its_own_publication_proof():
    """Mis-attribution stays refused: a sentence naming one publication with another's proof."""
    t, ids, proofs = linked()
    other = proofs[ids[1]]["Graphnet"]
    with pytest.raises(ValueError, match="Attribution non vérifiable"):
        save(t, ids, f"Qian et al. prédisent un champ de métrique anisotrope [{ids[1]}] [{other}].", [other])


def test_a_framing_section_saves_project_facts_without_any_publication_result():
    t, ids, proofs = linked()
    text = "Le projet a constitué une base de 120 km [projet:ev_fast_roadmark] ; les publications restent à analyser."
    result = save(t, [], text, [], ["ev_fast_roadmark"])
    assert result["saved"] and draft(t)["provenance"]["project"] == 1 and draft(t)["provenance"]["scientific"] == 0
    with pytest.raises(ValueError, match="sans publication analysée"):
        save(t, [], text + f" Voir [{ids[0]}].", [], ["ev_fast_roadmark"])
    with pytest.raises(ValueError, match="exige du texte et des preuves"):
        save(t, [], "Un texte sans aucune preuve.", [], [])


def test_the_last_scientific_refusal_survives_a_later_interrupted_attempt():
    """recovery_exchange() returned nothing for [refused, refused, failed] (run b52f582a)."""
    state, usage = {}, {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0., "unreported_reserved_cost_usd": 0.}
    from agents.EnnoScholarV4.config import ScholarV4Config
    tasks = TaskResults(state, ScholarV4Config(api_key="mock"), usage, [])
    task = tasks.task("section", "s1", "sig")
    for reason in ("claims absentes", "Attribution non vérifiable : SegArchA"):
        attempt = tasks.begin(task)
        response = type("R", (), {"output": [{"type": "function_call", "name": "write_section", "call_id": f"c_{reason[:4]}",
            "arguments": json.dumps({"action": "save", "text": reason})}], "usage": {"input_tokens": 1, "output_tokens": 1},
            "status": "completed"})()
        tasks.receive(task, attempt, response, seconds=1)
        tasks.reject(task, attempt, {"error": reason})
    attempt = tasks.begin(task)
    tasks.interrupted(task, attempt, 0.1, 3)
    assert [a["status"] for a in task["attempts"]] == ["refused", "refused", "interrupted"]
    exchange = tasks.recovery_exchange(task)
    assert exchange[0]["call_id"] == "c_Attr" and "SegArchA" in exchange[1]["output"]
    # Same after a reload of the session JSON.
    restored = TaskResults(json.loads(json.dumps(state)), ScholarV4Config(api_key="mock"), dict(usage), [])
    assert restored.recovery_exchange(next(iter(restored.rows.values())))[0]["call_id"] == "c_Attr"


# --- Agent 1 -> Agent 2: the project's work per lock, on the real LiDAR dossier -------

def real(session):
    import sqlite3
    from pathlib import Path
    db_path = Path(__file__).resolve().parents[2] / "ennoscholar_v4_test.db"
    if not db_path.is_file():
        pytest.skip("Base locale absente")
    db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    row = db.execute("select data from scholar_v4_sessions where id like ?", (session+"%",)).fetchone()
    db.close()
    if not row:
        pytest.skip("Session absente")
    state = json.loads(row[0])
    # Per-request state left by the last real run (the agent resets it at each request).
    for key in ("argumentation_focus", "writing_focus", "writing_planning", "turn_goal", "automatic_reading_focus", "selection_focus",
            "current_turn_article_ids", "conversation_references", "request_card_pages"):
        state.pop(key, None)
    # As in a real drafting or planning request, the latest message is the consultant's request
    # (the base now ends with a long answer of a later question, which is not this task's input).
    state.setdefault("messages", []).append({"id": "test_request", "role": "user",
        "content": "Continue la rédaction de mon état de l'art CIR."})
    without_defective_readings(state)
    return state




# Run ce08ad31 (execution 31551714…) read fifteen windows of four works the request did not
# target (the defect corrected by test_run_ce08ad31). The corpus is checked as legitimate
# runs built it: claims and proofs coming only from those readings are left out.
DEFECTIVE_EXECUTIONS = {"315517145ec4467ba31cac847bf56006"}


def without_defective_readings(state):
    added = {eid for task in state.get("task_results", {}).values() if task["kind"] == "reading"
        for attempt in task["attempts"] if attempt.get("execution") in DEFECTIVE_EXECUTIONS
        for eid in attempt.get("evidence_ids", [])}
    if not added:
        return
    for article in state.get("articles", {}).values():
        analysis = article.get("analysis") or {}
        for field, value in list(analysis.items()):
            if isinstance(value, list) and value and isinstance(value[0], dict) and field != "evidence":
                analysis[field] = [c for c in value if not (c.get("evidence_ids") and set(c["evidence_ids"]) <= added)]
        if isinstance(analysis.get("evidence"), list):
            analysis["evidence"] = [e for e in analysis["evidence"] if e.get("evidence_id") not in added]
    state["shown_evidence_ids"] = [eid for eid in state.get("shown_evidence_ids", []) if eid not in added]


def test_each_lock_receives_what_the_project_tried_obtained_and_did_not_document():
    from agents.EnnoScholarV4.diagnostic_link import work_units, lock_dossier
    state = real("23236f69")
    snapshot = state["diagnostic_link"]["snapshot"]
    roles = {}
    for lock in snapshot["locks"]:
        units = work_units(state, lock)
        assert units, lock["id"]
        for unit in units:
            assert unit["link"] in {"declared", "lock_evidence_passage", "lock_evidence_passage_partial"}
            for role, facts in unit["roles"].items():
                for fact in facts:
                    # Every fact keeps its own diagnostic proof, verified in the source document.
                    assert fact["evidence_ids"] and all(snapshot["evidence"][e]["quote_verified"] is True for e in fact["evidence_ids"])
            roles[lock["id"]] = {r for u in units for r, v in u["roles"].items() if v}
            missing = {r for u in units for r in u["roles_not_documented"]}
            # A gap is reported, never filled.
            assert not missing & roles[lock["id"]] or len(units) > 1
    assert {"approaches", "results"} <= roles["1"] and "parameters" in roles["1"]
    assert {"approaches", "methods", "parameters", "results"} <= roles["2"]
    # Lock 3 (SegArchA/SegArchB): only an approach and a result are documented; the rest is a declared gap.
    unit3 = work_units(state, next(v for v in snapshot["locks"] if v["id"] == "3"))[0]
    assert {r for r, v in unit3["roles"].items() if v} == {"approaches", "results"} and {"methods", "parameters", "limits"} <= set(unit3["roles_not_documented"])
    # The unit is the lock's own trial (its title read from the base, not written here).
    assert unit3["unit"] and unit3["unit"] in json.dumps(snapshot["evidence"], ensure_ascii=False) or unit3["unit"].startswith("Essai 3")
    # Without a start date in the diagnostic, the dossier says so instead of using the CIR year.
    assert lock_dossier(state, ["3"], 9000)["rnd_reference_date"]["status"] == "unknown"


def test_a_section_compares_one_publication_with_two_project_trials_without_mixing_them():
    """Two project results, each with its own proof; a method name only present in another
    trial's passage is refused (wrong experiment)."""
    t, ids, proofs = linked()
    snapshot = t.state["diagnostic_link"]["snapshot"]
    eid = proofs[ids[0]]["Our"]
    text = (f"Le réseau produit un maillage quadrangulaire de surface [{ids[0]}] [{eid}]. "
        "Le projet a d'abord constitué une base de 120 km [projet:ev_fast_roadmark]. "
        "Il a ensuite adapté SegArchA/SegArchB sans obtenir un pipeline stable [projet:ev_fast_deeplab].")
    assert save(t, [ids[0]], text, [eid], ["ev_fast_roadmark", "ev_fast_deeplab"])["saved"]
    claims = {c["statement"][:30]: c["project_evidence_ids"] for c in draft(t)["claims"] if c["kind"] == "project"}
    assert sorted(claims.values()) == [["ev_fast_deeplab"], ["ev_fast_roadmark"]]
    mixed = (f"Le réseau produit un maillage quadrangulaire de surface [{ids[0]}] [{eid}]. "
        "Le projet a adapté SegArchA/SegArchB sans pipeline stable [projet:ev_fast_roadmark].")
    with pytest.raises(ValueError, match="Attribution non vérifiable"):
        save(t, [ids[0]], mixed, [eid], ["ev_fast_roadmark"])


# --- Scientific content actually sent to a section (LiDAR and 3D meshing) ------------

@pytest.mark.parametrize("session", ["23236f69", "4a7314e4"])
def test_sections_receive_methods_results_and_limits_with_their_exact_passages(session):
    from unittest.mock import Mock
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.context import model_context, transmitted_evidence_ids, SECTION_CONTEXT_CHARS
    from agents.EnnoScholarV4.toolkit import ScholarTools
    from agents.EnnoScholarV4.writing_context import section_article_ids
    state = real(session)
    tools = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    sections = state["outline"]["sections"]
    state.setdefault("document", {})["writing_progress"] = {"id": "w", "mode": "full", "target_ids": [s["id"] for s in sections],
        "plan_revision": state["outline"].get("plan_revision"), "baseline_draft_ids": {}, "saved_draft_ids": {}, "errors": [],
        "section_status": {}, "status": "running", "approval_required": True}
    with_cards = 0
    for section in sections:
        ids = section_article_ids(state, tools.registry, section)
        state["writing_focus"] = {"section_id": section["id"], "article_ids": ids, "retrieval_progress": []}
        inputs = model_context(state, tools, SECTION_CONTEXT_CHARS)
        packet = json.loads(inputs[0]["content"].split(" : ", 1)[1])["section_drafting"]["packet"]
        sent = transmitted_evidence_ids(inputs, tools.evidence)
        assert len(json.dumps(inputs, ensure_ascii=False)) <= SECTION_CONTEXT_CHARS
        claims = packet["proof_backed_claims"]
        # A claim never reaches the model without its exact passage (run b52f582a: 0 passage).
        assert all(set(c["evidence_ids"]) <= sent for c in claims)
        if any(s["status"] == "analysed" for s in packet["sources"]):
            with_cards += 1
            assert claims and sent
            fields = {c["field"] for c in claims}
            # Not a general presentation: what the works do and what they obtain or where they stop.
            # A synthesis builds on results, comparisons and limits (its map covers the methods).
            if re.search(r"synth[eè]se|conclusion|perspectives", section["title"], re.I):
                assert fields & {"results", "limitations", "comparisons"} and packet["manuscript_map"]["sections"]
            else:
                assert fields & {"method", "technical_mechanism", "experimental_setup"} and fields & {"results", "limitations"}
        # Sources without a card are named with their state, never as results.
        assert all(s["status"] in {"analysed", "card_without_proofs", "to_analyse", "unavailable"} for s in packet["sources"])
    assert with_cards >= 3


# --- Replay of run b52f582a, interruption and resumption ------------------------------

def test_the_b52f582a_replay_repairs_without_monopolising_the_run():
    from tests.ennoscholar_v4 import replay_b52f582a as replay
    if not replay.available():
        pytest.skip("Base locale absente")
    out = replay.run()
    summary = replay.summary(out)
    result, state = out["result"], out["state"]
    progress = state["document"]["writing_progress"]
    # Real run: 1 section saved, 5 refusals, 430 s on one section, timeout at 602 s.
    # The sources without card that the sections need are read in the background while other
    # sections are written; the synthesis is written only after the scientific sections.
    # Local processing runs in real time in the hybrid clock: the count varies with the machine load.
    assert len(progress["saved_draft_ids"]) >= 8 and len(progress["target_ids"]) == 14
    assert result["timings"]["wall_seconds"] < 600 and summary["max_calls_one_section"] <= 3
    assert summary["real_proposals_used"] == 6 and result["tool_counts"].get("analyze_article", 0) >= 3
    synthesis = state["outline"]["sections"][-1]
    if synthesis["id"] in progress["saved_draft_ids"]:
        assert all(s["id"] in progress["saved_draft_ids"] for s in state["outline"]["sections"])
    # The contract errors are gone: diagnostic proofs are never looked up as publication proofs.
    assert not any("Comparaison sans preuve" in r for r in summary["refusal_reasons"])
    # Every generated section with analysed sources received exact passages.
    sections = [e for e in out["log"] if e["kind"] == "section"]
    assert all(e["proofs_sent"] or e["project_facts_sent"] for e in sections)
    saved = [s for s in state["outline"]["sections"] if s.get("drafts")]
    assert all(all(state["articles"][aid]["analysis"] for aid in s["drafts"][-1]["article_ids"]) for s in saved)
    assert sum(d["provenance"]["project"] + d["provenance"]["comparison"] for s in saved for d in s["drafts"][-1:]
        if d.get("provenance")) >= 10
    # What the drafts do with the project's work is recorded per section. The real
    # proposals (written before this correction, without any project proof) compare
    # nothing and are flagged; the simulated target writer compares each lock's trial.
    real = set(out["proposals"])
    recorded = {s["id"]: s["drafts"][-1]["confrontation"] for s in saved if s["drafts"][-1].get("confrontation")}
    assert all(c["compared_units"] == 0 for sid, c in recorded.items() if sid in real)
    assert sum(c["compared_units"] > 0 for sid, c in recorded.items() if sid not in real) >= 5


def test_an_interrupted_draft_resumes_with_its_last_refusal_and_keeps_saved_sections():
    from tests.ennoscholar_v4 import replay_b52f582a as replay
    if not replay.available():
        pytest.skip("Base locale absente")
    # Interrupted after the first proposals and refusals (readings come first now).
    first = replay.run(timeout=300)
    state, proposals = first["state"], first["proposals"]
    assert first["result"]["partial"]
    already = dict(state["document"]["writing_progress"]["saved_draft_ids"])
    refused = [t["target"] for t in state["task_results"].values() if t["kind"] == "section" and t["status"] == "refused"]
    assert already and refused
    state["messages"].append({"id": "resume", "role": "user", "content": "Continue la rédaction des sections restantes."})
    second = replay.run(state=state, proposals=proposals)
    progress = state["document"]["writing_progress"]
    assert len(progress["saved_draft_ids"]) == len(progress["target_ids"])
    # Sections saved by the first run are neither generated nor saved again.
    assert not {e["section_id"] for e in second["log"] if e["kind"] == "section"} & set(already)
    assert all(progress["saved_draft_ids"][sid] == draft_id for sid, draft_id in already.items())
    # A section refused in the first run resumes from its last proposal and exact refusal.
    first_call = next(e for e in second["log"] if e["kind"] == "section" and e["section_id"] == refused[0])
    assert first_call["repair"]



# --- What a draft actually does with the project's work (recorded and reviewed) -------

def test_a_draft_records_which_project_work_it_confronts_and_the_review_flags_a_missing_confrontation():
    from agents.EnnoScholarV4.scientific_review import deterministic_review
    t, ids, proofs = linked()
    t.state["outline"]["sections"][0]["lock_ids"] = ["3"]
    eid = proofs[ids[0]]["Our"]
    # A section tied to lock 3 that only presents the publication.
    assert save(t, [ids[0]], f"Le réseau produit un maillage quadrangulaire de surface [{ids[0]}] [{eid}].", [eid])["saved"]
    confrontation = draft(t)["confrontation"]
    assert confrontation["units_available"] == 1 and confrontation["units_cited"] == 0
    codes = {i["code"] for i in deterministic_review(t.state, t)["issues"]}
    assert "project_confrontation_missing" in codes
    # The same section confronting the publication with what the project tried and obtained.
    text = (f"Le réseau produit un maillage quadrangulaire de surface [{ids[0]}] [{eid}]. "
        "Le projet a adapté SegArchA et SegArchB sans obtenir un pipeline stable [projet:ev_fast_deeplab]. "
        f"La publication ne traite pas cette instabilité d'entraînement [{ids[0]}] [{eid}] [projet:ev_fast_deeplab].")
    result = save(t, [ids[0]], text, [eid], ["ev_fast_deeplab"])
    assert result["saved"] and result["confrontation"]["compared_units"] == 1
    relations = draft(t)["project_relations"]
    assert {(r["lock_id"], r["role"]) for r in relations} == {("3", "approaches"), ("3", "results")}
    assert all(r["evidence_id"] == "ev_fast_deeplab" and r["link"] == "lock_evidence_passage" for r in relations)
    codes = {i["code"] for i in deterministic_review(t.state, t)["issues"] if i.get("section_id") == t.state["outline"]["sections"][0]["id"]}
    assert not codes & {"project_confrontation_missing", "project_comparison_missing", "project_fact_outside_section_locks"}
