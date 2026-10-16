"""Offline regression of progressive source coverage, independent of synthesis."""
import copy
import json
from unittest.mock import Mock

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.context import model_context
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.scientific_reading import reading_tasks, next_task, prepare_reading, completed, reading_context
from agents.EnnoScholarV4.toolkit import ScholarTools, Analysis


def fixture(*, abstract=False, limitations=True):
    chunks = [{"section": section, "page": i+1, "text": f"{section} scientific method result metric parameter protocol {i}."}
        for i, section in enumerate(["Methods", "Experiments", "Results"]*4 + (["Limitations"]*3 if limitations else []))]
    article = Article(id="paper_depth", title="Synthetic depth study", doi="10.1234/depth", verified=True, authors=["Synthetic author"], year=2024,
        selected=True, abstract="Scientific method described by the authors.",
        fulltext={} if abstract else {"text": "\n".join(c["text"] for c in chunks), "chunks": chunks, "url": "https://example.org/depth"})
    state = {"articles": {article.id: article.durable()}, "messages": [{"role": "user", "content": "Analyse les méthodes et résultats."}]}
    sources = Mock()
    sources.verify.side_effect = lambda a, deadline: a.model_copy(update={"verified": True})
    toolkit = ScholarTools(state, sources, ScholarV4Config(api_key="mock", web_search_enabled=False))
    task = reading_tasks(state, toolkit.registry)["tasks"][0]
    if abstract:
        task["access_attempted"] = True
    return state, toolkit, task


def prepare(state, toolkit, task):
    prepare_reading(state, toolkit, task, lambda n, a: toolkit.execute(n, a, float("inf")))
    inputs = model_context(state, toolkit, 18000)
    toolkit.delivered(state["automatic_reading_focus"]["reading_window_ids"], inputs=inputs)
    return reading_context(state, toolkit)


def save(state, toolkit, packet):
    fields = {f: [] for f in Analysis.model_fields if f not in {"method_family", "analysis_limit"}}
    for row in packet["passages"]:
        key = {"Methods": "method", "Experiments": "experimental_setup", "Results": "results", "Limitations": "limitations", "Abstract": "method"}[row["section"]]
        fields[key].append({"statement": row["exact_quote"], "evidence_ids": [row["evidence_id"]], "origin": "source_statement"})
    toolkit.execute("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "analysis": {**fields,
        "method_family": "synthetic", "analysis_limit": "Targeted passages"}, "research_context": "test",
        "passage_query": "", "passage_offset": 0, "passages_per_article": 6}, float("inf"))
    completed(state, toolkit.registry[packet["article_id"]])


def test_one_save_is_partial_and_checkpoint_resume_accumulates_all_proofs():
    state, toolkit, task = fixture()
    first = prepare(state, toolkit, task)
    save(state, toolkit, first)
    assert task["status"] == "partial" and task["remaining_passages"] > 0
    assert not task["coverage_sufficient"]
    original = set(task["evidence_ids"])
    seen = set(original)
    toolkit.save()
    state = json.loads(json.dumps(state))
    toolkit = ScholarTools(state, toolkit.sources, toolkit.config)
    reading_tasks(state, toolkit.registry)
    while next_task(state, toolkit.registry):
        task = next_task(state, toolkit.registry)
        packet = prepare(state, toolkit, task)
        ids = {p["evidence_id"] for p in packet["passages"]}
        assert not ids & seen
        seen.update(ids)
        save(state, toolkit, packet)
    task = state["scientific_reading"]["tasks"][0]
    assert task["coverage_sufficient"] and task["exhausted"]
    assert len(seen) == 15 and original <= set(task["evidence_ids"])
    assert len(toolkit.registry["paper_depth"].analysis["results"]) == 4


def test_abstract_only_cannot_finish_a_full_scientific_reading():
    state, toolkit, task = fixture(abstract=True)
    save(state, toolkit, prepare(state, toolkit, task))
    assert task["status"] == "partial" and not task["coverage_sufficient"]
    assert task["dimensions"]["limits"]["status"] == "inaccessible"
    assert not toolkit.registry["paper_depth"].analysis["results"]
    assert next_task(state, toolkit.registry) is None


def test_absent_limitations_remain_empty_and_explicitly_searched_without_result():
    state, toolkit, task = fixture(limitations=False)
    while next_task(state, toolkit.registry):
        save(state, toolkit, prepare(state, toolkit, task))
    assert task["status"] == "partial" and task["exhausted"]
    assert task["field_status"]["limitations"] == "not_found_in_examined_sections"
    assert not toolkit.registry["paper_depth"].analysis["limitations"]


def test_prepared_window_survives_interruption_and_foreign_save_does_not_pop_focus():
    state, toolkit, task = fixture()
    packet = prepare(state, toolkit, task)
    pending = copy.deepcopy(state["automatic_reading_focus"])
    completed(state, Article(id="paper_other", title="Other"))
    assert state["automatic_reading_focus"] == pending and task["cursor"] == 0
    assert prepare_reading(state, toolkit, task, lambda *a: (_ for _ in ()).throw(AssertionError("Repeated read"))) == pending
    save(state, toolkit, packet)
    assert task["cursor"] == 6


def test_changed_source_restarts_cursor_without_deleting_previous_analysis():
    state, toolkit, task = fixture()
    save(state, toolkit, prepare(state, toolkit, task))
    previous = copy.deepcopy(toolkit.registry["paper_depth"].analysis)
    toolkit.registry["paper_depth"].fulltext["chunks"][0]["text"] += " Additional scientific condition."
    reading_tasks(state, toolkit.registry)
    assert task["cursor"] == 0 and not task["coverage_sufficient"]
    assert toolkit.registry["paper_depth"].analysis == previous


def test_incomplete_body_never_certifies_full_scientific_coverage():
    state, toolkit, task = fixture()
    toolkit.registry["paper_depth"].fulltext["truncated"] = True
    while next_task(state, toolkit.registry):
        save(state, toolkit, prepare(state, toolkit, task))
    assert task["exhausted"] and not task["coverage_sufficient"]
    assert task["status"] == "partial"
    assert task["dimensions"]["results"]["status"] == "documented"
    assert task["dimensions"]["metrics"]["status"] == "examined_without_result"
