"""The argument is durable, evidence-linked, and keeps project observations separate."""
import json
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.argumentation import blueprint_status, comparison_batches
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.context import model_context
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.toolkit import ScholarTools
from agents.EnnoScholarV4.writing_context import writing_context
from tests.ennoscholar_v4.test_finalization import Client, call
from tests.ennoscholar_v4.test_scientific_reading import article, analysis_from_packet


def prepared_project(count=2):
    papers = [article(f"Mechanism {i}", f"10.1234/project-{i}", f"axis {i}", f"Method {i}")
        for i in range(count)]
    for index, paper in enumerate(papers):
        paper.id = f"paper_project_{index}"
        paper.selected = True
    state = {"messages": [{"id": "project_request", "role": "user",
        "content": "Notre projet a mesuré 71.2 % sur le jeu Q. Rédige un état de l'art de ces méthodes."}],
        "articles": {paper.id: paper.durable() for paper in papers}}
    config = replace(ScholarV4Config(api_key="mock"), web_search_enabled=False)
    toolkit = ScholarTools(state, Mock(), config)
    deadline = time.monotonic()+60
    for paper in papers:
        reading = toolkit.execute("analyze_article", {"article_ids": [paper.id], "action": "read",
            "research_context": "mechanisms", "passage_query": "", "passage_offset": 0,
            "passages_per_article": 8, "analysis": None}, deadline)["reading"][0]
        toolkit.delivered({reading["reading_window_id"]})
        packet = {"passages": reading["passages"]}
        toolkit.execute("analyze_article", {"article_ids": [paper.id], "action": "save",
            "research_context": "mechanisms", "passage_query": "", "passage_offset": 0,
            "passages_per_article": 8, "analysis": analysis_from_packet(packet)}, deadline)
    return state, toolkit, config, papers


def relation_arguments(state, ids):
    evidence = [state["articles"][aid]["analysis"]["method"][0]["evidence_ids"][0] for aid in ids]
    return {"article_ids": ids, "research_context": "mechanisms", "conclusions": None,
        "relations": [{"article_ids": ids, "kind": "alternative", "statement": "Distinct methods",
            "origin": "comparative_inference", "conditions": "Different protocols; no superiority",
            "experimental_results": [], "evidence_ids": evidence,
            "comparable_protocols": None, "uncertainties": ["No comparable protocol"]}]}


def blueprint_arguments(ids, relation, fact_id):
    return {"action": "save", "global_question": "Which mechanism?", "narrative_logic": "Compare before confrontation.",
        "axes": [{"axis": "methods", "scientific_problem": "How do the methods differ?", "section_id": None,
            "steps": [{"kind": "comparison", "statement": "Two documented alternatives.",
                "article_ids": ids, "evidence_ids": relation["evidence_ids"], "relation_ids": [relation["id"]],
                "project_fact_ids": [], "conditions": "Protocols differ", "uncertainty": "No ranking"},
                {"kind": "project_confrontation", "statement": "Project observation is lower than the published result, but protocols differ.",
                "article_ids": [ids[0]], "evidence_ids": [relation["evidence_ids"][0]],
                "relation_ids": [], "project_fact_ids": [fact_id], "conditions": "Not directly comparable",
                "uncertainty": "No causal conclusion"}], "transition": "Determine comparable validation conditions."}],
        "remaining_questions": ["Project protocol comparability"]}


def test_project_facts_and_blueprint_validate_provenance_and_staleness():
    state, toolkit, _, papers = prepared_project()
    ids = [paper.id for paper in papers]
    deadline = time.monotonic()+60
    with pytest.raises(ValueError, match="absent du message"):
        toolkit.execute("record_project_facts", {"facts": [{"category": "result", "statement": "invented",
            "exact_quote": "91.2 %", "source_type": "consultant_message", "source_id": "project_request"}]}, deadline)
    assert state.get("project_facts", []) == []
    saved_fact = toolkit.execute("record_project_facts", {"facts": [{"category": "result",
        "statement": "Le projet a mesuré 71.2 % sur Q", "exact_quote": "Notre projet a mesuré 71.2 % sur le jeu Q.",
        "source_type": "consultant_message", "source_id": "project_request", "value": "71.2", "unit": "%"}]}, deadline)
    fid = saved_fact["project_fact_ids"][0]
    assert fid.startswith("project_fact_") and state["project_facts"][0]["source_version"] == "message:project_request"
    assert not any("71.2" in json.dumps(p.analysis) for p in toolkit.registry.values())
    with pytest.raises(ValueError, match="at least 2 items|preuves de chaque publication"):
        bad = relation_arguments(state, ids)
        bad["relations"][0]["evidence_ids"] = bad["relations"][0]["evidence_ids"][:1]
        toolkit.execute("compare_articles", bad, deadline)
    toolkit.execute("compare_articles", relation_arguments(state, ids), deadline)
    relation = state["scientific_relations"][0]
    args = blueprint_arguments(ids, relation, fid)
    invalid = json.loads(json.dumps(args))
    invalid["axes"][0]["steps"][0]["relation_ids"] = ["relation_nonexistent"]
    with pytest.raises(ValueError, match="non enregistrée"):
        toolkit.execute("build_argument_blueprint", invalid, deadline)
    result = toolkit.execute("build_argument_blueprint", args, deadline)
    assert result["saved"] and result["status"]["stale"] is False
    assert state["argument_blueprint"]["cir_eligibility"] == "not_established"
    assert state["argument_blueprint"]["axes"][0]["steps"][1]["project_fact_ids"] == [fid]
    packet = toolkit.execute("build_argument_blueprint", {"action": "read"}, deadline)["packet"]
    assert packet["relations"][0]["id"] == relation["id"] and packet["project_facts"][0]["id"] == fid
    assert writing_context(state, toolkit)["argument_blueprint"]["id"] == state["argument_blueprint"]["id"]
    toolkit.registry[ids[0]].source_version = "changed-source-version"
    assert blueprint_status(state, toolkit.registry)["stale"]
    assert "argument_blueprint" not in writing_context(state, toolkit)


def test_direct_state_of_art_captures_project_result_before_comparison():
    state, toolkit, config, papers = prepared_project()
    ids = [paper.id for paper in papers]
    # Simulate the persistent store loaded for a new conversation turn.
    toolkit.save()
    state["evaluation"] = {"recommended_article_ids": ids}
    seen = []
    def respond(number, kwargs):
        memory = json.loads(kwargs["input"][0]["content"].split(" : ", 1)[1])
        choice = kwargs["tool_choice"]
        seen.append(choice)
        if number == 1:
            assert choice == {"type": "function", "name": "record_project_facts"}
            assert memory["argumentation_focus"]["stage"] == "project_facts"
            assert memory["user_message_sources"][0]["id"] == "project_request"
            return call("record_project_facts", {"facts": [{"category": "result",
                "statement": "Project result 71.2 %", "exact_quote": "Notre projet a mesuré 71.2 % sur le jeu Q.",
                "source_type": "consultant_message", "source_id": "project_request", "value": "71.2", "unit": "%"}]}, number)
        if number == 2:
            assert choice == {"type": "function", "name": "compare_articles"}
            return call("compare_articles", relation_arguments(state, ids), number)
        if number == 3:
            assert choice == {"type": "function", "name": "build_argument_blueprint"}
            args = blueprint_arguments(ids, state["scientific_relations"][0], state["project_facts"][0]["id"])
            return call("build_argument_blueprint", args, number)
        assert choice == "auto"
        return call("finalize_response", {"answer": f"Distinct documented methods [{ids[0]}] [{ids[1]}].",
            "article_ids": ids, "evidence_ids": [], "coverage_sufficient": False,
            "remaining_uncertainties": ["Project protocol comparability"],
            "analyses": None, "displayed_article_ids": ids}, number)
    sources = Mock()
    sources.verify.side_effect = lambda row, deadline: row.model_copy(update={"verified": True})
    result = ScholarChatAgent(replace(config, max_tool_rounds=4), client=Client(respond),
        sources=sources).run(state)
    assert result["publication_accepted"], (result["reason"], result["tool_counts"], seen)
    assert len(state["project_facts"]) == 1 and len(state["scientific_relations"]) == 1
    assert state["argument_blueprint"]["axes"][0]["steps"][1]["kind"] == "project_confrontation"
    assert state["argument_blueprint"]["cir_eligibility"] == "not_established"


def test_five_priority_articles_merge_overlapping_argument_windows():
    state, toolkit, _, papers = prepared_project(5)
    ids = [paper.id for paper in papers]
    deadline = time.monotonic()+60
    batches = comparison_batches(state, toolkit.registry)
    assert batches == [ids[:4], ids[3:]]
    state["argumentation_focus"] = {"stage": "comparison", "article_ids": batches[0]}
    context = model_context(state, toolkit, 18000)
    assert len(json.dumps(context, ensure_ascii=False)) <= 18000
    assert len(json.loads(context[0]["content"].split(" : ", 1)[1])["argumentation_focus"]["packet"]["papers"]) == 4
    state.pop("argumentation_focus")
    for batch in batches:
        toolkit.execute("compare_articles", relation_arguments(state, batch), deadline)
    for batch_index, batch in enumerate(batches):
        relation = state["scientific_relations"][batch_index]
        steps = [{"kind": "method", "statement": f"Documented method in {aid}",
            "article_ids": [aid], "evidence_ids": [state["articles"][aid]["analysis"]["method"][0]["evidence_ids"][0]],
            "relation_ids": [], "project_fact_ids": [], "conditions": "", "uncertainty": ""} for aid in batch]
        steps.append({"kind": "comparison", "statement": f"Conditional alternatives in batch {batch_index}",
            "article_ids": batch, "evidence_ids": relation["evidence_ids"], "relation_ids": [relation["id"]],
            "project_fact_ids": [], "conditions": "Protocols differ", "uncertainty": "No superiority"})
        toolkit.execute("build_argument_blueprint", {"action": "save", "merge": batch_index > 0,
            "global_question": "Methods", "narrative_logic": f"Scientific comparison window {batch_index}",
            "axes": [{"axis": "methods", "scientific_problem": "Which mechanism?", "section_id": None,
                "steps": steps, "transition": "Compare validation conditions"}],
            "remaining_questions": []}, deadline)
    status = blueprint_status(state, toolkit.registry)
    assert status["complete"] and not status["stale"] and not status["uncovered_article_ids"]
    assert state["argument_blueprint"]["version"] == 2
    assert len(state["argument_blueprint"]["axes"]) == 1
    assert len(state["argument_blueprint_versions"]) == 1
    assert len(state["scientific_relations"]) == 2
    assert {aid for step in state["argument_blueprint"]["axes"][0]["steps"]
        for aid in step["article_ids"]} == set(ids)


def test_uploaded_project_fact_requires_current_document_version():
    state, toolkit, _, papers = prepared_project()
    project = Article(id="project_document", title="Project observations", source_version="sha256:initial",
        fulltext={"source_kind": "user_uploaded_document", "text": "Observed drift was 0.6 mm.", "chunks": []})
    toolkit.registry[project.id] = project
    toolkit.save()
    fact = toolkit.execute("record_project_facts", {"facts": [{"category": "result",
        "statement": "Observed drift was 0.6 mm", "exact_quote": "Observed drift was 0.6 mm.",
        "source_type": "uploaded_project_document", "source_id": project.id,
        "value": "0.6", "unit": "mm"}]}, time.monotonic()+60)
    assert fact["saved"] and state["project_facts"][0]["source_version"] == "sha256:initial"
    paper = papers[0]
    eid = state["articles"][paper.id]["analysis"]["method"][0]["evidence_ids"][0]
    args = {"action": "save", "global_question": "Project gap", "narrative_logic": "Confront literature and project.",
        "axes": [{"axis": "drift", "scientific_problem": "What drift?", "section_id": None,
            "steps": [{"kind": "project_confrontation", "statement": "Project drift has separate provenance.",
                "article_ids": [paper.id], "evidence_ids": [eid], "relation_ids": [],
                "project_fact_ids": fact["project_fact_ids"], "conditions": "", "uncertainty": ""}],
            "transition": ""}], "remaining_questions": []}
    toolkit.execute("build_argument_blueprint", args, time.monotonic()+60)
    project.source_version = "sha256:replaced"
    assert blueprint_status(state, toolkit.registry)["stale"]
    with pytest.raises(ValueError, match="source d'un fait projet a changé"):
        toolkit.execute("build_argument_blueprint", args, time.monotonic()+60)
