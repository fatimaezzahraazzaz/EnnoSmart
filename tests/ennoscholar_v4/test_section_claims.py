"""Seven-section drafting where claim/text mismatches are corrected instead of stopping the run."""
import copy
import json
import time

import pytest

from tests.ennoscholar_v4.test_document_orchestration import agent, prepared_six
from tests.ennoscholar_v4.test_finalization import Client, call


def prepared_seven():
    state, ids, eids = prepared_six()
    template = state["outline"]["sections"][0]
    state["outline"]["sections"] = [{**copy.deepcopy(template), "id": f"section_test_{i}", "title": f"Section {i}",
        "drafts": []} for i in range(1, 8)]
    state["messages"] = [{"role": "user", "content": "Construis le plan puis rédige tout l'état de l'art complet en sept sections."}]
    return state, ids, eids


def last_output(kw):
    outputs = [item for item in kw["input"] if item.get("type") == "function_call_output"]
    return json.loads(outputs[-1]["output"]) if outputs else {}


def writer(ids, eids, log):
    attempts = {}

    def respond(n, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        focus = memory["section_drafting"]["focus"]["section_id"]
        number = int(focus.rsplit("_", 1)[1])
        attempts[number] = attempts.get(number, 0)+1
        sentence = f"Le mailleur d’hexaèdres de la section {number} conserve la topologie  documentée [{ids[1]}] [{eids[1]}]."
        text = f"## Section {number}\n\n{sentence} Les limites restent partielles."
        claim = f"Le mailleur d'hexaèdres de la section {number} conserve la topologie documentée [{eids[1]}] [{ids[1]}]."
        if number == 3 and attempts[number] == 1:
            log.append("read")
            return call("write_section", {"section_id": "3", "action": "read", "instructions": "Lire le contexte",
                "text": None, "article_ids": [], "evidence_ids": []}, n)
        if number == 4 and attempts[number] == 1:
            claim = f"Une reformulation absente du texte pour la section {number}."
        elif number == 4:
            error = last_output(kw).get("error", "")
            log.append(error)
            claim = sentence  # corrected: copied from the text itself
        return call("write_section", {"section_id": focus, "action": "save", "instructions": "Rédaction étayée",
            "text": text, "article_ids": ids, "evidence_ids": eids,
            "claims": [{"statement": claim, "evidence_ids": [eids[1]], "origin": "source_statement"}]}, n)
    return respond


def test_seven_sections_are_written_in_one_request_with_corrected_claims():
    state, ids, eids = prepared_seven()
    log = []
    result = agent(Client(writer(ids, eids, log))).run(state)
    assert not result["partial"], (result["reason"], result["writing_progress"].get("errors"))
    assert result["writing_progress"]["status"] == "draft_complete"
    sections = state["outline"]["sections"]
    assert all(len(section["drafts"]) == 1 for section in sections)
    for section in sections:
        draft = section["drafts"][-1]
        assert draft["claims"] and all(claim["statement"] in draft["raw_text"] for claim in draft["claims"])
        assert eids[1] in draft["evidence_ids"] and ids[1] in draft["article_ids"]
    # A truly absent declaration must be corrected explicitly before saving.
    assert "read" in log and any("absentes du texte" in message for message in log)
    fourth = sections[3]["drafts"][-1]
    assert not any("reformulation absente" in claim["statement"] for claim in fourth["claims"])
    assert any(eids[1] in claim["evidence_ids"] for claim in fourth["claims"])


def test_a_claim_absent_from_the_text_is_never_saved():
    state, ids, eids = prepared_seven()
    from tests.ennoscholar_v4.test_writing import drafted_document
    t, _ = drafted_document()
    section = t.state["outline"]["sections"][0]
    eid = t.shown_evidence[0]
    aid = t.evidence[eid]["article_id"]
    result = t.execute("write_section", {"section_id": section["id"], "action": "save", "instructions": "x",
        "text": f"Texte rédigé [{aid}] [{eid}].", "article_ids": [aid], "evidence_ids": [eid],
        "claims": [{"statement": "Une affirmation inventée qui ne figure pas dans le texte.", "evidence_ids": [eid], "origin": "source_statement"}]},
        time.monotonic()+30)
    assert not result["saved"] and result["claims_not_in_text"] == ["Une affirmation inventée qui ne figure pas dans le texte."]
    assert not section["drafts"]


def test_interrupted_drafting_reports_its_cause_and_resumes_at_the_next_section():
    state, ids, eids = prepared_seven()
    log, events = [], []
    first = agent(Client(writer(ids, eids, log))).run(state, emit=events.append,
        cancelled=lambda: sum(e["tool"] == "writing_progress" and e["status"] == "completed"
            and bool(e.get("section_id")) for e in events) >= 3)
    assert first["partial"] and first["reason"] == "cancelled"
    saved = {section["id"]: section["drafts"][-1]["id"] for section in state["outline"]["sections"] if section["drafts"]}
    # Completed siblings are settled at interruption rather than discarded.
    assert 3 <= len(saved) < 7
    assert f"{len(saved)}/7" in first["answer"] and "interruption" in first["answer"].lower()
    state["messages"].append({"role": "user", "content": "Continue la rédaction des sections restantes."})
    resumed = agent(Client(writer(ids, eids, log))).run(state)
    assert not resumed["partial"], resumed["reason"]
    assert all(len(section["drafts"]) == 1 for section in state["outline"]["sections"])
    assert {sid: next(s for s in state["outline"]["sections"] if s["id"] == sid)["drafts"][-1]["id"] for sid in saved} == saved
