"""Offline records, progressive windows and exact scientific transmission."""
import copy
import json
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.argumentation import argument_packet, bounded_argument_packet, claim_entries
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.context import model_context, analysis_projection
from agents.EnnoScholarV4.evidence import exact_source_valid, snapshot
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.scientific_reading import reading_tasks, next_task, prepare_reading, completed, reading_context
from agents.EnnoScholarV4.toolkit import Analysis, ScholarTools
from tests.ennoscholar_v4.test_finalization import Client, call


def corpus(count=4):
    papers = []
    for i in range(count):
        chunks = [{"section": "Introduction", "page": 1, "text": "Background only. "*200}]
        for j in range(3):
            chunks.extend([
                {"section": "Methods", "page": 2+j, "text": f"Method {i}.{j} uses mechanism K{j} under condition C{j}."},
                {"section": "Experiments", "page": 5+j, "text": f"Experiment {i}.{j}: dataset D{j}, protocol P{j}, seed {j}, metric M{j}, unit ms."},
                {"section": "Results", "page": 8+j, "text": f"Result {i}.{j}: M{j} = {i*10+j}.7 ms on D{j} with protocol P{j} and condition C{j}."},
                {"section": "Limitations", "page": 11+j, "text": f"Limitation {i}.{j}: method K{j} excludes condition X{j}."},
            ])
        from agents.EnnoScholarV4.tools.tables import structure, table_text
        table = structure(["Method", "M0 (ms)"], [[f"K{i}", f"{i}.7"]],
            f"Table 1: M0 in ms, dataset D0, protocol P0, condition C0.", "1")
        chunks.append({"section": "Results", "page": 15, "text": table_text(table), "table": table})
        chunks.append({"section": "References", "page": 16, "text": "Unneeded references. "*200})
        p = Article(id=f"paper_progress_{i}", title=f"Synthetic study {i}", doi=f"10.1234/progress-{i}",
            year=2024, authors=[f"Author {i}"], verified=True, selected=True, scientific_families=["methods"],
            url=f"https://example.org/{i}", fulltext={"url": f"https://example.org/{i}",
                "text": "\n\n".join(c["text"] for c in chunks), "chunks": chunks, "truncated": False})
        papers.append(p)
    state = {"messages": [{"role": "user", "content": "Compare les méthodes, résultats et limites des articles."}],
        "articles": {p.id: p.durable() for p in papers}}
    config = replace(ScholarV4Config(api_key="mock", web_search_enabled=False), max_tool_rounds=6)
    sources = Mock()
    sources.verify.side_effect = lambda p, deadline: p.model_copy(update={"verified": True})
    return state, ScholarTools(state, sources, config), config


def analysis_for(packet):
    fields = {key: [] for key in Analysis.model_fields if key not in {"method_family", "analysis_limit"}}
    for row in packet["passages"]:
        field = {"Methods": "method", "Experiments": "experimental_setup", "Results": "results", "Limitations": "limitations"}[row["section"]]
        claim = {"statement": row["exact_quote"], "evidence_ids": [row["evidence_id"]], "origin": "source_statement"}
        fields[field].append(claim)
        if row.get("table"):
            fields["tables_figures"].append(copy.deepcopy(claim))
        if field == "experimental_setup":
            fields["validation_protocol"].append(copy.deepcopy(claim))
    return {**fields, "method_family": "synthetic mechanism", "analysis_limit": "Available scientific sections only"}


def prepare(state, toolkit, task):
    def run(name, args):
        result = toolkit.execute(name, args, time.monotonic()+30)
        toolkit.save()
        return result
    focus = prepare_reading(state, toolkit, task, run)
    # A mocked model receipt must include the actual source packet, as required
    # by the step-1 transmission guard; window IDs alone never authorize a save.
    toolkit.delivered(focus["reading_window_ids"], inputs=model_context(state, toolkit, 18000))
    return reading_context(state, toolkit)


def save(state, toolkit, packet):
    result = toolkit.execute("analyze_article", {"article_ids": [packet["article_id"]], "action": "save",
        "research_context": "scientific comparison", "passage_query": "", "passage_offset": 0,
        "passages_per_article": 6, "analysis": analysis_for(packet)}, time.monotonic()+30)
    completed(state, toolkit.registry[packet["article_id"]])
    toolkit.save()
    return result


def analyzed_corpus():
    state, toolkit, config = corpus()
    reading_tasks(state, toolkit.registry)
    seen, windows = set(), {}
    while (task := next_task(state, toolkit.registry)) is not None:
        packet = prepare(state, toolkit, task)
        ids = {p["evidence_id"] for p in packet["passages"]}
        assert not seen & ids
        seen.update(ids)
        aid = task["article_id"]
        windows[aid] = windows.get(aid, 0)+1
        save(state, toolkit, packet)
        if task["remaining_passages"]:
            assert task["status"] == "partial" and not task["coverage_sufficient"]
        # Real checkpoint serialization and reconstruction between every window.
        state = json.loads(json.dumps(state))
        toolkit = ScholarTools(state, toolkit.sources, config)
        reading_tasks(state, toolkit.registry)
    return state, toolkit, config, windows


def test_four_articles_multiple_windows_merge_and_checkpoint_resume():
    state, toolkit, _, windows = analyzed_corpus()
    assert len(windows) == 4 and min(windows.values()) >= 3
    for p in toolkit.registry.values():
        assert len(p.analysis["method"]) == 3
        assert len(p.analysis["experimental_setup"]) == 3
        assert len(p.analysis["results"]) == 4
        assert len(p.analysis["limitations"]) == 3
        assert len(p.analysis["tables_figures"]) == 1
        assert all(exact_source_valid(e, toolkit.snapshots) for e in p.analysis["evidence"])
        assert len({e["evidence_id"] for e in p.analysis["evidence"]}) == 13
        assert p.doi == f"10.1234/progress-{p.id.rsplit('_', 1)[-1]}"
        assert p.authors and p.year == 2024 and p.access_status == "fulltext_analyzed"
        for field, claim in claim_entries(p.analysis):
            assert claim["statement"] == toolkit.evidence[claim["evidence_ids"][0]]["exact_quote"]
        task = next(t for t in state["scientific_reading"]["tasks"] if t["article_id"] == p.id)
        assert task["status"] == "analyzed" and task["coverage_sufficient"]
        assert task["cursor"] == 13 and task["exhausted"]
    assert all(e["section"] not in {"Introduction", "References"} for e in toolkit.evidence.values())


def test_pending_window_survives_interruption_before_save_without_skipping():
    state, toolkit, config = corpus(1)
    task = reading_tasks(state, toolkit.registry)["tasks"][0]
    packet = prepare(state, toolkit, task)
    pending = copy.deepcopy(state["automatic_reading_focus"])
    assert task["cursor"] == 0 and task["query_offsets"] == [0, 0, 0]
    state = json.loads(json.dumps(state))
    resumed = ScholarTools(state, toolkit.sources, config)
    assert reading_context(state, resumed)["passages"] == packet["passages"]
    assert state["automatic_reading_focus"] == pending
    save(state, resumed, packet)
    following = prepare(state, resumed, state["scientific_reading"]["tasks"][0])
    assert not {p["evidence_id"] for p in packet["passages"]} & {p["evidence_id"] for p in following["passages"]}


def test_legacy_card_and_changed_source_do_not_imply_sufficient_coverage():
    state, toolkit, _ = corpus(1)
    task = reading_tasks(state, toolkit.registry)["tasks"][0]
    save(state, toolkit, prepare(state, toolkit, task))
    previous = copy.deepcopy(toolkit.registry[task["article_id"]].analysis)
    task.pop("source_fingerprint")
    reading_tasks(state, toolkit.registry)
    assert task["remaining_passages"] > 0 and not task["coverage_sufficient"]
    assert toolkit.registry[task["article_id"]].analysis == previous
    p = toolkit.registry[task["article_id"]]
    p.fulltext["chunks"][1]["text"] += " Changed protocol."
    reading_tasks(state, toolkit.registry)
    assert task["cursor"] == 0 and task["status"] == "queued"
    assert p.analysis == previous


def test_argument_windows_are_lossless_and_keep_exact_conditions_and_proofs():
    state, toolkit, _, _ = analyzed_corpus()
    ids = list(toolkit.registry)
    complete = argument_packet(state, toolkit, ids)
    assert len(complete["papers"]) == 4
    expected = {(p["id"], field, c["statement"], tuple(c["evidence_ids"]))
        for p in complete["papers"] for field, c in claim_entries(p["analysis"])}
    seen, offsets = set(), {}
    for _ in range(30):
        window = bounded_argument_packet(argument_packet(state, toolkit, ids, claim_offsets=offsets), 9000)
        assert len(json.dumps(window, ensure_ascii=False)) <= 9000
        for p in window["papers"]:
            proofs = {e["evidence_id"]: e for e in p["proofs"]}
            for field, claim in claim_entries(p["analysis"]):
                key = (p["id"], field, claim["statement"], tuple(claim["evidence_ids"]))
                assert key not in seen
                seen.add(key)
                for eid in claim["evidence_ids"]:
                    assert proofs[eid]["exact_quote"] == toolkit.evidence[eid]["exact_quote"]
        if not window["transmission_partial"]:
            break
        assert window["next_claim_offsets"] != offsets
        offsets = window["next_claim_offsets"]
    else:
        pytest.fail("Comparison windows made no bounded progress")
    assert seen == expected


def test_final_context_includes_every_priority_and_multiple_methods_results():
    state, toolkit, _, _ = analyzed_corpus()
    context = model_context(state, toolkit, 24000, final=True)
    memory = json.loads(context[0]["content"].split(" : ", 1)[1])
    packet = memory["conversation_science"]
    from agents.EnnoScholarV4.context import FINAL_REST_CHARS
    # The essential dossier is protected (run 53292dc4); the rest keeps a fixed share.
    assert len(json.dumps(context, ensure_ascii=False)) <= max(24500, len(json.dumps(packet, ensure_ascii=False)) + FINAL_REST_CHARS)
    assert packet["transmission"]["complete"]
    assert {p["id"] for p in packet["papers"]} == set(toolkit.registry)
    for p in packet["papers"]:
        assert len(p["analysis"]["method"]) >= 2
        assert len(p["analysis"]["results"]) >= 2
        assert len(p["analysis"]["limitations"]) >= 2
        assert all(eid in {e["evidence_id"] for e in p["proofs"]}
            for _, c in claim_entries(p["analysis"]) for eid in c["evidence_ids"])
    assert "not_yet_examined" not in json.dumps(memory.get("scientific_reading_status", {}))


def test_budget_stop_preserves_partial_progress_and_defers_comparison():
    state, toolkit, config = corpus(4)
    seen = []
    def respond(number, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        seen.append(kw["tool_choice"])
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            packet = memory["automatic_scientific_reading"]
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save",
                "research_context": "methods", "passage_query": "", "passage_offset": 0,
                "passages_per_article": 6, "analysis": analysis_for(packet)}, number)
        assert kw["tool_choice"] == {"type": "function", "name": "finalize_response"}
        remaining = memory["scientific_reading_status"]["remaining_article_ids"]
        assert remaining
        return call("finalize_response", {"answer": "Analyses partielles : " + " ".join(f"[{aid}]" for aid in remaining),
            "article_ids": remaining, "evidence_ids": [], "coverage_sufficient": False,
            "remaining_uncertainties": ["Budget de lecture atteint"], "analyses": None,
            "displayed_article_ids": remaining}, number)
    result = ScholarChatAgent(replace(config, max_tool_calls=9), client=Client(respond), sources=toolkit.sources).run(state)
    assert result["publication_accepted"], (result["reason"], result["tool_counts"], seen, result.get("finalization_refusals"))
    tasks = state["scientific_reading"]["tasks"]
    assert any(t["status"] == "partial" and t["cursor"] > 0 for t in tasks)
    assert state["scientific_reading"]["remaining_article_ids"]
    assert {"type": "function", "name": "compare_articles"} not in seen
    assert sum(result["tool_counts"].values()) <= 9


def test_missing_fields_remain_unknown_until_relevant_text_is_examined():
    state, toolkit, _ = corpus(1)
    p = next(iter(toolkit.registry.values()))
    p.fulltext["chunks"] = [c for c in p.fulltext["chunks"] if c["section"] != "Limitations"]
    p.fulltext["text"] = "\n\n".join(c["text"] for c in p.fulltext["chunks"])
    task = reading_tasks(state, toolkit.registry)["tasks"][0]
    save(state, toolkit, prepare(state, toolkit, task))
    assert task["status"] == "partial"
    assert task["field_status"]["limitations"] == "not_yet_examined"
    while next_task(state, toolkit.registry):
        save(state, toolkit, prepare(state, toolkit, task))
    assert task["status"] == "partial" and task["exhausted"]
    assert task["field_status"]["limitations"] == "not_found_in_examined_sections"
    assert not p.analysis["limitations"]


def test_resaving_old_claims_cannot_consume_a_new_window():
    state, toolkit, _ = corpus(1)
    task = reading_tasks(state, toolkit.registry)["tasks"][0]
    old_packet = prepare(state, toolkit, task)
    save(state, toolkit, old_packet)
    cursor = task["cursor"]
    prepare(state, toolkit, task)
    save(state, toolkit, old_packet)
    assert task["cursor"] == cursor and task["status"] == "needs_review"
    assert next_task(state, toolkit.registry) is None


def test_more_than_four_priority_cards_are_transmitted_and_never_lost():
    state, toolkit, _ = corpus(6)
    reading_tasks(state, toolkit.registry)
    while (task := next_task(state, toolkit.registry)) is not None:
        save(state, toolkit, prepare(state, toolkit, task))
    memory = json.loads(model_context(state, toolkit, 24000, final=True)[0]["content"].split(" : ", 1)[1])
    assert {p["id"] for p in memory["conversation_science"]["papers"]} == set(toolkit.registry)
    assert all(p["analysis"]["method"] and p["analysis"]["results"] for p in memory["conversation_science"]["papers"])
    assert memory["conversation_science"]["transmission_partial"]
    assert all(memory["conversation_science"]["omitted_claims"].values())


def test_source_and_comparison_guards_reject_invented_or_cross_paper_proof():
    state, toolkit, _, _ = analyzed_corpus()
    ids = list(toolkit.registry)
    original = copy.deepcopy(toolkit.registry[ids[0]].analysis)
    eid = toolkit.registry[ids[1]].analysis["results"][0]["evidence_ids"][0]
    bad = analysis_for({"passages": []})
    bad["results"] = [{"statement": "Unsupported result 999.9 ms", "evidence_ids": [eid], "origin": "source_statement"}]
    with pytest.raises(ValueError):
        toolkit.execute("analyze_article", {"article_ids": [ids[0]], "action": "save", "research_context": "test",
            "passage_query": "", "passage_offset": 0, "passages_per_article": 2, "analysis": bad}, time.monotonic()+30)
    assert toolkit.registry[ids[0]].analysis == original
    proof_ids = [toolkit.registry[aid].analysis["results"][0]["evidence_ids"][0] for aid in ids]
    relation = {"article_ids": ids, "kind": "conditional_superiority", "statement": "Unsupported superiority",
        "origin": "comparative_inference", "conditions": "Distinct protocols", "experimental_results": [],
        "evidence_ids": proof_ids, "comparable_protocols": False, "uncertainties": ["Different metrics"]}
    with pytest.raises(ValueError, match="protocoles comparables"):
        toolkit.execute("compare_articles", {"article_ids": ids, "research_context": "test",
            "conclusions": None, "relations": [relation]}, time.monotonic()+30)
    assert not state.get("scientific_relations")
    assert "999.9" not in json.dumps(argument_packet(state, toolkit))


def test_comparator_consumes_successive_windows_and_saves_four_paper_proofs():
    state, toolkit, config, _ = analyzed_corpus()
    expected = {(p.id, field, c["statement"]) for p in toolkit.registry.values()
        for field, c in claim_entries(analysis_projection(p.analysis))}
    received = set()
    comparisons = []
    def respond(number, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        focus = memory.get("argumentation_focus", {})
        if focus.get("stage") == "comparison":
            packet = focus["packet"]
            evidence = []
            for p in packet["papers"]:
                for field, claim in claim_entries(p["analysis"]):
                    received.add((p["id"], field, claim["statement"]))
                if p["proofs"]:
                    evidence.append(p["proofs"][0]["evidence_id"])
            comparisons.append(packet)
            assert len(evidence) == 4
            return call("compare_articles", {"article_ids": list(toolkit.registry), "research_context": "conditions",
                "conclusions": None, "relations": [{"article_ids": list(toolkit.registry), "kind": "alternative",
                    "statement": "Distinct documented mechanisms", "origin": "comparative_inference",
                    "conditions": "Metrics M0/M1/M2 and protocols P0/P1/P2 remain separate",
                    "experimental_results": [], "evidence_ids": evidence, "comparable_protocols": False,
                    "uncertainties": ["No experimental ranking"]}]}, number)
        if focus.get("stage") == "blueprint":
            relations = state["scientific_relations"]
            return call("build_argument_blueprint", {"action": "save", "global_question": "Mechanisms?",
                "narrative_logic": "Separate protocols", "axes": [{"axis": "methods", "scientific_problem": "Mechanisms?",
                    "section_id": None, "transition": "", "steps": [{"kind": "comparison", "statement": "Documented alternatives",
                        "article_ids": list(toolkit.registry), "evidence_ids": relations[0]["evidence_ids"],
                        "relation_ids": [relations[0]["id"]], "project_fact_ids": [], "conditions": "Distinct protocols",
                        "uncertainty": "No superiority"}]}], "remaining_questions": []}, number)
        ids = list(toolkit.registry)
        return call("finalize_response", {"answer": "Documented alternatives " + " ".join(f"[{aid}]" for aid in ids),
            "article_ids": ids, "evidence_ids": [], "coverage_sufficient": False,
            "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": ids}, number)
    result = ScholarChatAgent(config, client=Client(respond), sources=toolkit.sources).run(state)
    assert result["publication_accepted"], (result["reason"], result["tool_counts"], len(comparisons), result.get("finalization_refusals"))
    assert len(comparisons) >= 2
    assert received == expected
    assert all(entry["complete"] for entry in state["comparison_reading"].values())
    assert all(len({toolkit.evidence[eid]["article_id"] for eid in relation["evidence_ids"]}) == 4
        for relation in state["scientific_relations"])


def test_a_single_claim_per_window_does_not_certify_all_scientific_passages():
    state, toolkit, _ = corpus(1)
    task = reading_tasks(state, toolkit.registry)["tasks"][0]
    while next_task(state, toolkit.registry):
        packet = prepare(state, toolkit, task)
        # Simulate an under-reading model which omits scientific facts in each window.
        reduced = {**packet, "passages": packet["passages"][:1]}
        save(state, toolkit, reduced)
    assert task["status"] == "partial" and not task["coverage_sufficient"]
    assert task["unincorporated_passages"] > 0
    assert task["unincorporated_evidence_ids"]


def test_non_writing_request_can_read_six_priority_articles():
    from tests.ennoscholar_v4.test_scientific_reading import article, analysis_from_packet
    state, toolkit, config = corpus(0)
    papers = [article(f"Brief study {i}", f"10.1234/brief-{i}", "methods", f"Mechanism {i}") for i in range(6)]
    for i, p in enumerate(papers):
        p.id, p.selected = f"paper_brief_{i}", True
    state["articles"] = {p.id: p.durable() for p in papers}
    state["messages"] = [{"role": "user", "content": "Explique les méthodes, résultats et limites."}]
    seen = []
    def respond(number, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if memory.get("automatic_scientific_reading") and kw["tool_choice"] != {"type": "function", "name": "finalize_response"}:
            packet = memory["automatic_scientific_reading"]
            seen.append(packet["article_id"])
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save",
                "research_context": "methods", "passage_query": "", "passage_offset": 0,
                "passages_per_article": 6, "analysis": analysis_from_packet(packet)}, number)
        ids = list(state["articles"])
        return call("finalize_response", {"answer": "Méthodes documentées " + " ".join(f"[{aid}]" for aid in ids),
            "article_ids": ids, "evidence_ids": [], "coverage_sufficient": False,
            "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": ids}, number)
    result = ScholarChatAgent(config, client=Client(respond), sources=toolkit.sources).run(state)
    assert result["publication_accepted"], (result["reason"], result["tool_counts"])
    assert len(seen) == 6 and set(seen) == set(state["articles"])
    assert all(t["status"] == "analyzed" for t in state["scientific_reading"]["tasks"])
