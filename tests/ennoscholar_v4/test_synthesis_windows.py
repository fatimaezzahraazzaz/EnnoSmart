"""Lossless model-facing scientific windows and recoverable tool access."""
import copy
import json
import pytest

from agents.EnnoScholarV4.argumentation import argument_packet, bounded_argument_packet, claim_entries
from agents.EnnoScholarV4.scientific_reading import READING_QUERIES, reading_context
from tests.ennoscholar_v4.test_progressive_reading import analyzed_corpus
from tests.ennoscholar_v4.test_reading_depth import fixture, prepare, save
from agents.EnnoScholarV4.context import model_context
from agents.EnnoScholarV4.toolkit import Analysis


def test_tool_read_windows_recover_all_claims_and_exact_shared_source_metadata():
    state, toolkit, _, _ = analyzed_corpus()
    original = copy.deepcopy(state)
    complete = argument_packet(state, toolkit)
    expected = {(p["id"], field, c["statement"], tuple(c["evidence_ids"])) for p in complete["papers"] for field, c in claim_entries(p["analysis"])}
    offsets, seen = {}, set()
    for _ in range(30):
        packet = toolkit.execute("build_argument_blueprint", {"action": "read", "claim_offsets": offsets}, float("inf"))["packet"]
        assert len(json.dumps(packet, ensure_ascii=False)) <= 14000
        for p in packet["papers"]:
            by_id = {e["evidence_id"]: e for e in p["proofs"]}
            for field, claim in claim_entries(p["analysis"]):
                key = (p["id"], field, claim["statement"], tuple(claim["evidence_ids"]))
                assert key not in seen
                seen.add(key)
                for eid in claim["evidence_ids"]:
                    proof = by_id[eid]
                    assert proof["exact_quote"] == toolkit.evidence[eid]["exact_quote"]
                    source = p["sources"][proof["source_ref"]]
                    assert source["source_url"] == toolkit.evidence[eid]["source_url"]
        if not packet["transmission_partial"]:
            break
        assert packet["next_claim_offsets"] != offsets
        offsets = packet["next_claim_offsets"]
    assert seen == expected
    assert state == original


def test_tiny_packet_refuses_without_truncating_stored_scientific_statements():
    state, toolkit, _, _ = analyzed_corpus()
    source = argument_packet(state, toolkit)
    original = copy.deepcopy(source)
    packet = bounded_argument_packet(source, 800)
    assert len(json.dumps(packet, ensure_ascii=False)) <= 800
    assert packet["blocked_by_context"] and packet["transmission_partial"]
    assert source == original


def test_model_targeted_extra_read_does_not_skip_the_following_automatic_window():
    state, toolkit, task = fixture()
    prepare(state, toolkit, task)
    result = toolkit.execute("analyze_article", {"article_ids": [task["article_id"]], "action": "read", "research_context": "targeted continuation", "passage_query": " ".join(READING_QUERIES), "passage_offset": 6, "passages_per_article": 6, "analysis": None}, float("inf"))
    focus = state["automatic_reading_focus"]
    focus["evidence_ids"].extend(p["evidence_id"] for p in result["reading"][0]["passages"])
    focus["reading_window_ids"].append(result["reading"][0]["reading_window_id"])
    toolkit.delivered(focus["reading_window_ids"], inputs=model_context(state, toolkit, 18000))
    expanded = reading_context(state, toolkit)
    save(state, toolkit, expanded)
    assert task["cursor"] == 6 and len(task["examined_evidence_ids"]) == 12
    following = prepare(state, toolkit, task)
    assert len(following["passages"]) == 3
    assert not set(focus["evidence_ids"]) & {p["evidence_id"] for p in following["passages"]}
    save(state, toolkit, following)
    assert task["exhausted"] and task["coverage_sufficient"]


def test_metadata_only_priority_can_attempt_fulltext_instead_of_being_skipped(monkeypatch):
    from agents.EnnoScholarV4 import toolkit as module
    from agents.EnnoScholarV4.scientific_reading import reading_tasks, next_task
    state, toolkit, task = fixture(abstract=True)
    toolkit.registry[task["article_id"]].abstract = ""
    task.pop("access_attempted")
    reading_tasks(state, toolkit.registry)
    assert task["exhausted"] and next_task(state, toolkit.registry) is task
    _, body_toolkit, _ = fixture()
    body = copy.deepcopy(next(iter(body_toolkit.registry.values())).fulltext)
    body.update(access_level="fulltext", access_status="fulltext_extracted", source_kind="html")
    monkeypatch.setattr(module, "get_fulltext", lambda *a, **k: copy.deepcopy(body))
    packet = prepare(state, toolkit, task)
    assert packet["basis"] == "fulltext" and packet["passages"]


def review_empty(toolkit, aid, ids):
    fields = {f: [] for f in Analysis.model_fields if f not in {"method_family", "analysis_limit"}}
    return toolkit.execute("analyze_article", {"article_ids": [aid], "action": "save", "research_context": "absence in examined passages", "passage_query": "", "passage_offset": 0, "passages_per_article": 6,
        "reviewed_evidence_ids": ids, "reviewed_fields": ["results", "limitations"],
        "analysis": {**fields, "method_family": "", "analysis_limit": "No result established in these passages; no fact is inferred."}}, float("inf"))


def test_empty_window_review_can_advance_without_inventing_scientific_claims():
    from agents.EnnoScholarV4.scientific_reading import next_task, completed
    state, toolkit, task = fixture()
    while next_task(state, toolkit.registry):
        packet = prepare(state, toolkit, task)
        ids = [p["evidence_id"] for p in packet["passages"]]
        result = review_empty(toolkit, task["article_id"], ids)
        completed(state, toolkit.registry[task["article_id"]], result["reviewed_evidence_ids"], result["reviewed_fields"])
    assert task["exhausted"] and task["status"] == "partial"
    assert not task["coverage_sufficient"] and not task["unincorporated_passages"]
    assert task["field_status"]["results"] == "not_found_in_examined_sections"
    assert not toolkit.registry[task["article_id"]].analysis
    assert len(task["reviewed_without_result_evidence_ids"]) == 15
    assert state["scientific_reading_reviews"]


def test_empty_window_review_cannot_bypass_the_actual_delivery_receipt():
    from agents.EnnoScholarV4.scientific_reading import prepare_reading
    state, toolkit, task = fixture()
    focus = prepare_reading(state, toolkit, task, lambda n,a: toolkit.execute(n,a,float("inf")))
    with pytest.raises(ValueError, match="réellement présentées"):
        review_empty(toolkit, task["article_id"], focus["evidence_ids"])
    assert task["cursor"] == 0 and task["status"] == "awaiting_analysis"
    assert not state.get("scientific_reading_reviews")


def test_deep_reading_keeps_36_passages_20_proofs_and_rich_cumulative_scientific_fields():
    from unittest.mock import Mock
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.models import Article
    from tests.ennoscholar_v4.test_finalization import Client, call
    sections = ["Methods"]*5+["Results"]*5+["Limitations"]*4+["Methods"]+["Experiments"]*2+["Parameters"]*3+["Discussion"]*16
    chunks = [{"section": section, "page": i+1, "text": f"Evidence material #{i}: {section} scientific method mechanism result parameter condition."} for i, section in enumerate(sections)]
    paper = Article(id="paper_synthetic_deep", title="Offline synthetic mechanism study", doi="10.1234/synthetic-deep", authors=["Synthetic author"], year=2024, selected=True, verified=True,
        fulltext={"text": "\n\n".join(c["text"] for c in chunks), "chunks": chunks, "url": "https://example.org/synthetic-deep", "truncated": False})
    state = {"articles": {paper.id: paper.durable()}, "messages": [{"role": "user", "content": "Approfondis Offline synthetic mechanism study, ses mécanismes, résultats et limites."}]}
    shown = set()
    def respond(n, kwargs):
        memory = json.loads(kwargs["input"][0]["content"].split(" : ", 1)[1])
        if kwargs["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            packet = memory["automatic_scientific_reading"]
            fields = {f: [] for f in Analysis.model_fields if f not in {"method_family", "analysis_limit"}}
            reviews = []
            for row in packet["passages"]:
                assert row["evidence_id"] not in shown
                shown.add(row["evidence_id"])
                index = int(row["exact_quote"].split("#",1)[1].split(":",1)[0])
                field = "technical_mechanism" if index < 5 else "results" if index < 10 else "limitations" if index < 14 else "method" if index == 14 else "experimental_setup" if index < 17 else "parameters" if index < 20 else None
                if field:
                    fields[field].append({"statement": row["exact_quote"], "evidence_ids": [row["evidence_id"]], "origin": "source_statement"})
                else:
                    reviews.append(row["evidence_id"])
            return call("analyze_article", {"article_ids": [paper.id], "action": "save", "research_context": "deep synthetic reading", "passage_query": "", "passage_offset": 0, "passages_per_article": 6,
                "reviewed_evidence_ids": reviews, "reviewed_fields": ["results", "limitations"] if reviews else [],
                "analysis": {**fields, "method_family": "Synthetic mechanisms", "analysis_limit": "Contextual passages reviewed without inventing findings."}}, n)
        return call("finalize_response", {"answer": f"Analyse ciblée documentée [{paper.id}].", "article_ids": [paper.id], "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": [paper.id]}, n)
    sources = Mock()
    sources.verify.side_effect = lambda a, deadline: a.model_copy(update={"verified": True})
    config = ScholarV4Config(api_key="mock", web_search_enabled=False)
    result = ScholarChatAgent(config, client=Client(respond), sources=sources).run(state)
    assert result["publication_accepted"], result
    analysis = state["articles"][paper.id]["analysis"]
    assert len(shown) == 36 and len(analysis["evidence"]) == 20
    assert len(analysis["technical_mechanism"]) == 5
    assert len(analysis["results"]) == 5 and len(analysis["limitations"]) == 4
    assert result["tool_calls"] <= config.max_tool_calls
