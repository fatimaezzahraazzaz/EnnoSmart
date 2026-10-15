"""Controlled Responses runs for durable multi-section writing."""
import copy
import json
import time
from dataclasses import replace
from unittest.mock import Mock

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from tests.ennoscholar_v4.test_finalization import Client, call
from tests.ennoscholar_v4.test_writing import drafted_document


def test_plan_before_writing_does_not_start_an_unsolicited_seven_section_draft():
    from agents.EnnoScholarV4.writing_progress import begin_writing
    state, _, _ = prepared_six()
    request = ("Je travaille sur un projet de génération automatique de maillages 3D en mécanique avec des modèles d'IA. "
        "Je voudrais préparer un état de l'art scientifique complet pour le CIR 2026. Cherche les articles les plus "
        "pertinents, analyse leurs méthodes, leurs résultats et leurs limites, compare les différentes approches "
        "et construis un plan cohérent avant de rédiger l'état de l'art.")
    assert begin_writing(state, request) is None
    assert not state["document"].get("writing_progress")
    assert begin_writing(state, "Rédige tout l'état de l'art complet en brouillon.")["mode"] == "full"


def prepared_six():
    t, ids = drafted_document()
    for aid in ids:
        t.registry[aid].selected = True
    template = t.state["outline"]["sections"][0]
    t.state["outline"]["sections"] = [
        {**copy.deepcopy(template), "id": f"section_test_{i}", "title": f"Section {i}", "drafts": []}
        for i in range(1, 7)]
    t.state["outline"].pop("approval", None)
    t.state["outline"]["status"] = "proposed"
    eids = [next(e for e in t.shown_evidence if t.evidence[e]["article_id"] == aid) for aid in ids]
    t.execute("build_argument_blueprint", {"action": "save", "global_question": "Methods?",
        "narrative_logic": "Progress from mechanisms to evidence and limits.",
        "axes": [{"axis": "methods", "scientific_problem": "Compare mechanisms",
            "section_id": None, "transition": "Assess experimental evidence.",
            "steps": [{"kind": "method", "statement": "Documented alternatives",
                "article_ids": ids, "evidence_ids": eids, "relation_ids": [],
                "project_fact_ids": [], "conditions": "Different methods", "uncertainty": ""}]}],
        "remaining_questions": []}, time.monotonic()+30)
    t.save()
    t.state["scientific_reading"] = {"tasks": [{"article_id": aid, "axes": ["methods"],
        "status": "analyzed", "source_version": t.registry[aid].source_version,
        "basis": "fulltext", "evidence_ids": eids, "attempts": 0} for aid in ids],
        "coverage_by_axis": {}}
    t.state["messages"] = [{"role": "user", "content": "Rédige tout l'état de l'art complet en six sections."}]
    return t.state, ids, eids


def agent(client):
    sources = Mock()
    sources.search.side_effect = AssertionError("Unexpected search")
    sources.verify.side_effect = lambda article, deadline: article
    config = replace(ScholarV4Config(api_key="mock", web_search_enabled=False), max_tool_rounds=2)
    return ScholarChatAgent(config, client=client, sources=sources)


def draft_call(number, kwargs, ids, eids):
    memory = json.loads(kwargs["input"][0]["content"].split(" : ", 1)[1])
    packet = memory["section_drafting"]["packet"]
    assert packet["argument_blueprint"]["axes"][0]["steps"]
    assert packet["articles"][0]["analysis"]["method"]
    assert packet["evidence"] and packet["table_evidence_index"]
    section_id = memory["section_drafting"]["focus"]["section_id"]
    text = f"Méthode et limites vérifiées, section {section_id} [{ids[1]}] [{eids[1]}]."
    return call("write_section", {"section_id": section_id, "action": "save",
        "instructions": "Evidence-linked scientific writing", "text": text,
        "article_ids": ids, "evidence_ids": eids}, number)


def test_six_sections_saved_and_displayed_from_durable_drafts():
    state, ids, eids = prepared_six()
    client = Client(lambda n, kw: draft_call(n, kw, ids, eids))
    result = agent(client).run(state)
    assert not result["partial"], result["reason"]
    assert result["writing_progress"]["status"] == "draft_complete"
    assert len(state["document_versions"]) >= 6
    assert all(len(section["drafts"]) == 1 for section in state["outline"]["sections"])
    assert all(section["title"] in result["answer"] for section in state["outline"]["sections"])
    assert all(section["drafts"][-1]["text"] not in result["answer"] for section in state["outline"]["sections"])
    assert state["outline"]["status"] == "proposed"
    assert "approval" not in state["outline"]


def test_interrupted_after_two_resumes_without_duplicate_sections():
    state, ids, eids = prepared_six()
    events = []
    def emit(event):
        events.append(event)
    first = agent(Client(lambda n, kw: draft_call(n, kw, ids, eids))).run(state, emit=emit,
        cancelled=lambda: sum(e["tool"] == "writing_progress" and e["status"] == "completed"
            and bool(e.get("section_id")) for e in events) >= 2)
    assert first["partial"] and first["writing_progress"]["status"] != "draft_complete"
    before = [section["drafts"][-1]["id"] for section in state["outline"]["sections"][:2]]
    state["messages"].append({"role": "user", "content": "Continue la rédaction des sections restantes."})
    resumed = agent(Client(lambda n, kw: draft_call(n, kw, ids, eids))).run(state)
    assert not resumed["partial"]
    assert [section["drafts"][-1]["id"] for section in state["outline"]["sections"][:2]] == before
    assert all(len(section["drafts"]) == 1 for section in state["outline"]["sections"])


def test_failed_save_cannot_be_reported_as_complete():
    state, ids, eids = prepared_six()
    def invalid(n, kw):
        output = draft_call(n, kw, ids, eids)
        args = json.loads(output[0]["arguments"])
        args["evidence_ids"] = ["ev_nonexistent"]
        output[0]["arguments"] = json.dumps(args)
        return output
    result = agent(Client(invalid)).run(state)
    assert result["partial"] and result["reason"] == "section_save_failed"
    assert not any(section["drafts"] for section in state["outline"]["sections"])
    assert result["writing_progress"]["section_status"]["section_test_1"] == "failed"


def test_one_section_revision_preserves_other_sections():
    state, ids, eids = prepared_six()
    assert not agent(Client(lambda n, kw: draft_call(n, kw, ids, eids))).run(state)["partial"]
    before = [copy.deepcopy(section) for section in state["outline"]["sections"]]
    state["messages"].append({"role": "user", "content": "Approfondis la section 3 avec les résultats."})
    result = agent(Client(lambda n, kw: draft_call(n, kw, ids, eids))).run(state)
    assert not result["partial"]
    assert all(state["outline"]["sections"][i] == before[i] for i in (0, 1, 3, 4, 5))
    assert len(state["outline"]["sections"][2]["drafts"]) == 2


def test_one_request_can_create_plan_and_save_every_section():
    state, ids, eids = prepared_six()
    state.pop("outline")
    state.pop("argument_blueprint")
    def respond(number, kwargs):
        choice = kwargs["tool_choice"]
        name = choice.get("name") if isinstance(choice, dict) else ""
        if name == "compare_articles":
            return call(name, {"article_ids": ids, "research_context": "methods",
                "conclusions": None, "relations": [{"article_ids": ids, "kind": "alternative",
                "statement": "Different documented methods", "origin": "comparative_inference",
                "conditions": "Protocols differ", "experimental_results": [],
                "evidence_ids": eids, "comparable_protocols": None,
                "uncertainties": ["No direct ranking"]}]}, number)
        if name == "build_argument_blueprint":
            memory = json.loads(kwargs["input"][0]["content"].split(" : ", 1)[1])
            relation = state["scientific_relations"][0]
            return call(name, {"action": "save", "merge": memory["argumentation_focus"]["merge"],
                "global_question": "How do the methods differ?",
                "narrative_logic": "Compare mechanisms before experimental limits.",
                "axes": [{"axis": "methods", "scientific_problem": "Compare methods",
                    "section_id": None, "transition": "Examine the limits.",
                    "steps": [{"kind": "comparison", "statement": "Documented alternatives",
                        "article_ids": ids, "evidence_ids": eids,
                        "relation_ids": [relation["id"]], "project_fact_ids": [],
                        "conditions": "Different protocols", "uncertainty": "No ranking"}]}],
                "remaining_questions": []}, number)
        if name == "manage_outline":
            return call(name, {"action": "create", "title": "État de l’art",
                "section_id": None, "target_section_id": None, "position": None,
                "article_ids": None, "sections": [{"title": f"Section {i}",
                    "description": "Scientific analysis", "article_ids": ids} for i in range(1, 7)]}, number)
        return draft_call(number, kwargs, ids, eids)
    result = agent(Client(respond)).run(state)
    assert not result["partial"], (result["reason"], result["tool_counts"])
    assert len(state["outline"]["sections"]) == 6
    assert all(section["drafts"] for section in state["outline"]["sections"])
    assert state["outline"]["status"] == "proposed"
