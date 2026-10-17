"""Offline regression of run 9dff3e0c: reads without ever saving section 5."""
import copy
import json
from types import SimpleNamespace

import pytest

from agents.EnnoScholarV4.attribution import check_attribution
from agents.EnnoScholarV4.writing_progress import begin_writing
from tests.ennoscholar_v4.test_writing import call as execute
from tests.ennoscholar_v4.test_context_memory import (
    CONFIG, Client, agent, call, memory_of, rich_corpus, save_section, seven_section_plan,
)

REQUEST = ("Révise uniquement la section 5 de mon état de l'art. Vérifie la cohérence scientifique des méthodes, "
    "auteurs et résultats avec les quatre publications retenues et leurs preuves. "
    "Corrige les erreurs d'attribution et conserve les autres sections sans modification.")


def revision_case():
    t, ids = rich_corpus(4)
    seven_section_plan(t, ids)
    for n in range(1, 8):
        save_section(t, n, ids)
    target = t.state["outline"]["sections"][4]["drafts"][-1]
    # ~6k characters, with an important final paragraph beyond the old previews.
    target["raw_text"] = ("Argument scientifique existant à conserver. " * 120 + "\n\n" + target["raw_text"])
    target["text"] = target["raw_text"]
    t.state["outline"]["sections"][1]["drafts"][-1]["raw_text"] += " ForeignNet reste à vérifier."
    t.state["messages"] = [{"id": "revision", "role": "user", "content": REQUEST}]
    return t, ids


def save_from_revision(n, kw):
    packet = memory_of(kw)["section_drafting"]["packet"]
    revision = packet["revision"]
    assert not revision["text_truncated"]
    return call("write_section", {"section_id": packet["section"]["id"], "action": "save", "instructions": "Révision ciblée",
        "text": revision["raw_text"] + "\n\nPortée des conclusions explicitée.",
        "article_ids": revision["article_ids"], "evidence_ids": revision["evidence_ids"],
        "numeric_claims": revision["numeric_claims"], "calculations": revision["calculations"]}, n)


def test_targeted_revision_gets_full_draft_and_saves_without_reading_all_cards():
    t, ids = revision_case()
    before = copy.deepcopy(t.state)

    def model(n, kw):
        packet = memory_of(kw)["section_drafting"]["packet"]
        assert packet["revision"]["raw_text"] == before["outline"]["sections"][4]["drafts"][-1]["raw_text"]
        assert {a["id"] for a in packet["articles"]} == set(ids)
        assert packet["evidence"]
        assert all("ForeignNet" not in str(issue) for issue in packet["revision"]["attribution_issues"])
        return save_from_revision(n, kw)

    result = agent(Client(model)).run(t.state)
    assert not result["partial"], result["reason"]
    assert result["rounds"] == 1 and result["tool_counts"]["write_section"] == 1
    assert not any(result["tool_counts"].get(k) for k in ("get_analysis_claims", "compare_articles", "build_argument_blueprint", "analyze_article"))
    assert "ForeignNet" in result["answer"]  # Reported afterwards, never revised here.
    assert [s for i, s in enumerate(t.state["outline"]["sections"]) if i != 4] == [
        s for i, s in enumerate(before["outline"]["sections"]) if i != 4]
    assert t.state["outline"]["sections"][4]["drafts"][:-1] == before["outline"]["sections"][4]["drafts"]
    assert t.state["document_versions"][:-1] == before["document_versions"]
    assert t.state["articles"] == before["articles"] and t.state["evidence"] == before["evidence"]


def test_a_targeted_revision_reserves_its_last_existing_round_for_saving():
    t, ids = revision_case()
    receipts = []

    def model(n, kw):
        focus = memory_of(kw)["section_drafting"]["focus"]
        if n > 1:
            receipts.extend(focus["retrieval_progress"])
        if kw["tool_choice"] == {"type": "function", "name": "write_section"}:
            assert kw["tools"][0]["parameters"]["properties"]["action"]["enum"] == ["save"]
            return save_from_revision(n, kw)
        return call("get_analysis_claims", {"article_id": ids[(n-1) % 4], "fields": ["method"], "offset": 0}, n)

    result = agent(Client(model)).run(t.state)
    assert not result["partial"], result["reason"]
    assert result["rounds"] <= CONFIG.max_tool_rounds + 7  # Existing one-section allowance.
    assert receipts and result["tool_counts"]["write_section"] == 1


def test_explicit_read_returns_the_exact_saved_draft_without_rewriting_it():
    t, ids = revision_case()
    before = copy.deepcopy(t.state["outline"])
    begin_writing(t.state, REQUEST)
    result = execute(t, "write_section", section_id="5", action="read", instructions="Révision",
        text=None, article_ids=ids, evidence_ids=[])
    assert result["current_draft"]["raw_text"] == before["sections"][4]["drafts"][-1]["raw_text"]
    assert not result["current_draft"]["text_truncated"]
    assert t.state["outline"] == before


def test_stale_reading_focus_and_stale_blueprint_cannot_preempt_a_targeted_revision():
    t, ids = revision_case()
    t.state["automatic_reading_focus"] = {"article_id": ids[0], "evidence_ids": ["ev_stale"]}
    t.state["reading_recovery_focus"] = True
    t.state["argument_blueprint"] = {"id": "stale", "source_signature": "old", "axes": [], "version": 1}
    result = agent(Client(save_from_revision)).run(t.state)
    assert not result["partial"], result["reason"]
    assert result["rounds"] == 1
    assert not any(result["tool_counts"].get(k) for k in ("compare_articles", "build_argument_blueprint", "analyze_article"))


def test_last_round_save_still_rejects_unverified_evidence_and_preserves_all_versions():
    t, ids = revision_case()
    before = copy.deepcopy(t.state)

    def model(n, kw):
        if kw["tool_choice"] == {"type": "function", "name": "write_section"}:
            output = save_from_revision(n, kw)
            args = json.loads(output[0]["arguments"])
            args["evidence_ids"] = ["ev_unknown"]
            output[0]["arguments"] = json.dumps(args)
            return output
        return call("get_analysis_claims", {"article_id": ids[(n-1) % 4], "fields": ["method"], "offset": 0}, n)

    events = []
    result = agent(Client(model)).run(t.state, emit=events.append)
    assert result["partial"] and result["writing_progress"]["errors"]
    assert t.state["outline"] == before["outline"]
    assert t.state["document_versions"] == before["document_versions"]
    pages = [e for e in events if e["tool"] == "get_analysis_claims" and e["status"] == "running"]
    assert pages and all(e["fields"] == ["method"] and e["offset"] == 0 for e in pages)


@pytest.mark.parametrize("paper, component", [("DL-Polycube", "GCN-Polycube"), ("ShapeFlow", "XYZ-Lattice")])
def test_own_component_with_exact_paragraph_citation_is_not_a_foreign_work(paper, component):
    proof = {"article_id": "paper_a", "section": "Methods", "exact_quote": f"Our {component} model predicts a structure."}
    claim = {"statement": f"{paper} uses {component}.", "evidence_ids": ["ev_own"], "origin": "source_statement"}
    article = SimpleNamespace(id="paper_a", title=f"{paper}: structure prediction", authors=["A. Author"],
        year=2025, analysis={"method": [claim]})
    toolkit = SimpleNamespace(registry={"paper_a": article}, evidence={"ev_own": proof})
    text = f"{paper} utilise le modèle {component}. La structure sert au maillage [paper_a] [ev_own]."
    assert not check_attribution(text, ["paper_a"], toolkit)
    wrong = {**proof, "exact_quote": "An unrelated result.", "section": "Results"}
    toolkit.evidence["ev_other"] = wrong
    assert check_attribution(text.replace("ev_own", "ev_other"), ["paper_a"], toolkit)
