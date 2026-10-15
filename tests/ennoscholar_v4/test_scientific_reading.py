"""One-request scientific reading through the real V4 agent and tools."""
import json
from dataclasses import replace
from unittest.mock import Mock

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.toolkit import Analysis
from tests.ennoscholar_v4.test_finalization import Client, call


def article(title, doi, axis, method):
    abstract = f"{method} addresses the experimental problem."
    chunks = [
        {"section": "Methods", "page": 2, "text": f"{method} uses a documented mechanism and parameter 0.4."},
        {"section": "Results", "page": 4, "text": f"{method} was validated on dataset Q with metric M = 84.7 in Table 2."},
        {"section": "Limitations", "page": 5, "text": f"The authors limit {method} to condition C."},
    ]
    return Article(title=title, doi=doi, year=2024, authors=["Researcher"],
        url=f"https://example.org/{doi.rsplit('/', 1)[-1]}", verified=True,
        abstract=abstract, scientific_families=[axis], fulltext={
            "url": f"https://example.org/{doi.rsplit('/', 1)[-1]}",
            "text": "\n\n".join(chunk["text"] for chunk in chunks), "chunks": chunks,
            "truncated": False, "access_level": "fulltext", "access_status": "fulltext_extracted"})


def analysis_from_packet(packet):
    passages = packet["passages"]
    by_section = {row["section"]: row for row in passages}
    def claim(section):
        row = by_section[section]
        return {"statement": row["exact_quote"], "evidence_ids": [row["evidence_id"]],
            "origin": "source_statement"}
    return {"problem": [claim("Methods")], "objective": [], "method": [claim("Methods")],
        "technical_mechanism": [claim("Methods")], "experimental_setup": [claim("Results")],
        "results": [claim("Results")], "limitations": [claim("Limitations")],
        "contribution": [], "hypotheses": [], "generalization": [], "open_questions": [],
        "relevance": [], "equations": [], "parameters": [claim("Methods")],
        "validation_protocol": [claim("Results")], "datasets_materials": [claim("Results")],
        "tables_figures": [claim("Results")], "comparisons": [], "conditions_of_validity": [claim("Limitations")],
        "method_family": "experimental method", "analysis_limit": "Only targeted text passages were read."}


def test_one_request_reads_two_distinct_methods_and_saves_scientific_cards():
    sources = Mock()
    sources.search.return_value = ([
        article("Mechanism A", "10.1234/method-a", "axis A", "Method A"),
        article("Mechanism B", "10.1234/method-b", "axis B", "Method B")], [])
    sources.verify.side_effect = lambda row, deadline: row.model_copy(update={"verified": True})
    data = {"messages": [{"id": "request_one", "role": "user", "content": "Trouve les articles et rédige un état de l'art scientifique."}],
        "articles": {}}
    saved_packets = []

    def respond(number, kwargs):
        if number == 1:
            return call("search_scientific", {"query": "method A method B experiment", "sources": None,
                "limit_per_source": 10, "offset": 0, "family": "research topic"}, number)
        ids = list(data["articles"])
        if number == 2:
            return call("record_candidate_decisions", {"decisions": [{"article_id": aid,
                "decision": "retain", "justification": "The abstract describes a distinct experimental method."}
                for aid in ids], "finalist_ids": ids, "covered_families": ["axis A", "axis B"],
                "remaining_axes": []}, number)
        memory = json.loads(kwargs["input"][0]["content"].split(" : ", 1)[1])
        if number in (3, 4):
            assert kwargs["tool_choice"] == {"type": "function", "name": "analyze_article"}
            packet = memory["automatic_scientific_reading"]
            saved_packets.append(packet)
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save",
                "research_context": "Comparison of two methods", "passage_query": "",
                "passage_offset": 0, "passages_per_article": 2,
                "analysis": analysis_from_packet(packet)}, number)
        if number == 5:
            assert kwargs["tool_choice"] == {"type": "function", "name": "compare_articles"}
            evidence = [data["articles"][aid]["analysis"]["method"][0]["evidence_ids"][0] for aid in ids]
            return call("compare_articles", {"article_ids": ids, "research_context": "Distinct mechanisms",
                "conclusions": None, "relations": [{"article_ids": ids, "kind": "alternative",
                    "statement": "The methods use different mechanisms under their stated conditions.",
                    "origin": "comparative_inference", "conditions": "Different experimental designs; no global superiority.",
                    "experimental_results": [], "evidence_ids": evidence,
                    "comparable_protocols": None, "uncertainties": ["Protocols differ"]}]}, number)
        if number == 6:
            assert kwargs["tool_choice"] == {"type": "function", "name": "build_argument_blueprint"}
            relation = data["scientific_relations"][0]
            return call("build_argument_blueprint", {"action": "save", "global_question": "Compare mechanisms",
                "narrative_logic": "Present distinct mechanisms and experimentally bounded conclusions.",
                "axes": [{"axis": "scientific methods", "scientific_problem": "Which mechanism addresses the problem?",
                    "section_id": None, "steps": [{"kind": "comparison", "statement": "Two alternatives have documented mechanisms.",
                        "article_ids": ids, "evidence_ids": relation["evidence_ids"], "relation_ids": [relation["id"]],
                        "project_fact_ids": [], "conditions": "Different protocols", "uncertainty": "No superiority claim"}],
                    "transition": "Compare conditions next."}], "remaining_questions": ["Broader coverage"]}, number)
        assert kwargs["tool_choice"] == "auto"
        claims = [data["articles"][aid]["analysis"]["results"][0] for aid in ids]
        answer = "Two methods with distinct mechanisms and conditions: " + " ; ".join(
            f"[{aid}] [{claim['evidence_ids'][0]}]" for aid, claim in zip(ids, claims))
        return call("finalize_response", {"answer": answer, "article_ids": ids,
            "evidence_ids": [claim["evidence_ids"][0] for claim in claims],
            "coverage_sufficient": False, "remaining_uncertainties": ["Broader coverage not assessed"],
            "analyses": None, "displayed_article_ids": ids}, number)

    client = Client(respond)
    config = replace(ScholarV4Config(api_key="mock", web_search_enabled=False), max_tool_rounds=4)
    result = ScholarChatAgent(config, client=client, sources=sources).run(data)
    assert result["publication_accepted"] and not result["partial"], (result["reason"],
        result["exploration_stop_reason"], result["exploration_rounds"],
        result["tool_counts"], result.get("finalization_refusals"),
        [item["tool_choice"] for item in client.calls],
        [(task["article_id"], task["status"], task["attempts"]) for task in data["scientific_reading"]["tasks"]])
    assert len(saved_packets) == 2
    assert {packet["basis"] for packet in saved_packets} == {"fulltext"}
    assert {packet["article_id"] for packet in saved_packets} == set(data["articles"])
    assert all(data["articles"][aid]["analysis"]["results"] for aid in data["articles"])
    assert all(task["status"] == "analyzed" for task in data["scientific_reading"]["tasks"])
    assert data["scientific_reading"]["coverage_by_axis"]["axis A"]["analyzed_fulltext_ids"]
    assert data["scientific_reading"]["coverage_by_axis"]["axis B"]["analyzed_fulltext_ids"]
    assert sources.search.call_count == 1
    from agents.EnnoScholarV4.context import model_context
    from agents.EnnoScholarV4.toolkit import ScholarTools
    ready_toolkit = ScholarTools(data, sources, config)
    ready_memory = json.loads(model_context(data, ready_toolkit, 24000)[0]["content"].split(" : ", 1)[1])
    assert ready_memory["conversation_science"]["relations"]
    assert all(paper["analysis"]["method"] for paper in ready_memory["conversation_science"]["papers"])
    final_memory = json.loads(model_context(data, ready_toolkit, 24000, final=True)[0]["content"].split(" : ", 1)[1])
    assert len(final_memory["conversation_science"]["papers"]) == 2
    assert final_memory["conversation_science"]["relations"]
    assert all(paper["analysis"]["method"] and paper["analysis"]["results"] for paper in final_memory["conversation_science"]["papers"])
    assert "84.7" in json.dumps(final_memory["conversation_science"])

    # The next conversation turn reuses both persisted cards and their proofs.
    data["messages"].append({"id": "request_two", "role": "user", "content": "Précise la comparaison dans cet état de l'art."})
    def followup(number, kwargs):
        assert number == 1 and kwargs["tool_choice"] == "auto"
        ids = list(data["articles"])
        return call("finalize_response", {"answer": "The documented methods remain distinct " +
            " and ".join(f"[{aid}]" for aid in ids), "article_ids": ids,
            "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": [],
            "analyses": None, "displayed_article_ids": ids}, number)
    again = ScholarChatAgent(config, client=Client(followup), sources=sources).run(data)
    assert again["publication_accepted"], (again["reason"], again["tool_counts"], again["rounds"], again["finalization_refusals"])
    assert sources.search.call_count == 1
    assert all(task["status"] == "analyzed" for task in data["scientific_reading"]["tasks"])


def test_abstract_only_priority_is_analyzed_without_invented_body_details(monkeypatch):
    from agents.EnnoScholarV4 import toolkit as toolkit_module
    paper = Article(id="paper_abstract_only", title="Abstract only study", doi="10.1234/abstract",
        year=2024, authors=["Researcher"], url="https://example.org/abstract", verified=True,
        selected=True, abstract="The authors propose a constrained reconstruction method.")
    data = {"messages": [{"id": "request_abstract", "role": "user", "content": "Rédige un état de l'art sur cette méthode."}],
        "articles": {paper.id: paper.durable()}}
    sources = Mock()
    sources.verify.side_effect = lambda row, deadline: row.model_copy(update={"verified": True})
    monkeypatch.setattr(toolkit_module, "get_fulltext", lambda *a, **kw: {
        "url": paper.url, "text": "", "chunks": [], "access_level": "abstract_only",
        "access_status": "abstract_only", "attempts": []})
    seen = {}
    def respond(number, kwargs):
        memory = json.loads(kwargs["input"][0]["content"].split(" : ", 1)[1])
        if number == 1:
            packet = memory["automatic_scientific_reading"]
            assert packet["basis"] == "abstract" and packet["passages"]
            eid = packet["passages"][0]["evidence_id"]
            seen["eid"] = eid
            fields = {name: [] for name in Analysis.model_fields if name not in {"method_family", "analysis_limit"}}
            fields["method"] = [{"statement": "A constrained reconstruction method is proposed",
                "evidence_ids": [eid], "origin": "source_statement"}]
            fields.update(method_family="reconstruction", analysis_limit="Abstract only; body details unavailable")
            return call("analyze_article", {"article_ids": [paper.id], "action": "save",
                "research_context": "reconstruction", "passage_query": "", "passage_offset": 0,
                "passages_per_article": 2, "analysis": fields}, number)
        if number == 2:
            assert kwargs["tool_choice"] == {"type": "function", "name": "build_argument_blueprint"}
            return call("build_argument_blueprint", {"action": "save", "global_question": "Constrained reconstruction",
                "narrative_logic": "Abstract-level method only.", "axes": [{"axis": "reconstruction",
                    "scientific_problem": "How is reconstruction constrained?", "section_id": None,
                    "steps": [{"kind": "method", "statement": "The abstract proposes constrained reconstruction.",
                        "article_ids": [paper.id], "evidence_ids": [seen["eid"]], "relation_ids": [],
                        "project_fact_ids": [], "conditions": "", "uncertainty": "Full text unavailable"}],
                    "transition": ""}], "remaining_questions": ["Body unavailable"]}, number)
        return call("finalize_response", {"answer": f"Abstract-only method [{paper.id}] [{seen['eid']}].",
            "article_ids": [paper.id], "evidence_ids": [seen["eid"]],
            "coverage_sufficient": False, "remaining_uncertainties": ["Full text unavailable"],
            "analyses": None, "displayed_article_ids": [paper.id]}, number)
    config = replace(ScholarV4Config(api_key="mock", web_search_enabled=False), max_tool_rounds=4)
    events = []
    result = ScholarChatAgent(config, client=Client(respond), sources=sources).run(data, emit=events.append)
    assert result["publication_accepted"], (result["reason"], result["tool_counts"], result["rounds"], result["finalization_refusals"], events[-8:])
    stored = data["articles"][paper.id]
    assert stored["analysis_basis"] == "abstract" and stored["analysis"]["method"]
    assert not stored["analysis"]["experimental_setup"] and not stored["analysis"]["results"]
    assert data["scientific_reading"]["tasks"][0]["basis"] == "abstract"
