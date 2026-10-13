import json
import time
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV31.agent import ScholarV31ChatAgent
from agents.EnnoScholarV31.config import ScholarV31Config, ConfigurationError
from agents.EnnoScholarV31.evidence import passages
from agents.EnnoScholarV31.models import Article
from agents.EnnoScholarV31.store import ArticleStore
from agents.EnnoScholarV31.tools import ScholarTools, definitions


def config(**kw):
    return replace(ScholarV31Config(api_key="mock", paid_calls_authorized=True), **kw)


def paper(title="Scientific method", doi="10.1234/science", **kw):
    return Article(title=title, doi=doi, url="https://example.org/" + doi, authors=["Scientist"], year=2025,
        verified=True, sources=["openalex"], **kw)


def tools(rows=None):
    toolkit = ScholarTools({"messages": [], "articles": {}}, Mock(), config())
    toolkit.sources.verify.side_effect = lambda row, deadline: row.model_copy(update={"verified": True})
    toolkit.store.insert(rows or [paper(abstract="We demonstrate a solution under controlled experimental conditions.")])
    return toolkit


def execute(toolkit, name, **kw):
    return toolkit.execute(name, kw, time.monotonic()+30)


def expose(toolkit):
    toolkit.delivered([w["window_id"] for w in [*toolkit.store.windows, *toolkit.reading_windows] if w["status"] == "queued"])


def analysis(eid, **kw):
    result = {name: [] for name in ("problem", "objective", "method", "contribution", "hypotheses", "results", "limitations", "generalization", "open_questions", "relevance")}
    result.update(method=[{"statement": "Uses a learned model.", "evidence_ids": [eid], "origin": "source_statement"}],
        relevance_tag="DIRECT", method_family="trained model", analysis_limit="Limited to supplied passages.", **kw)
    return result


def reading(toolkit, aid, query="", offset=0):
    return execute(toolkit, "analyze_articles", article_ids=[aid], action="read", research_context="Scientific problem",
        passage_query=query, passage_offset=offset, passages_per_article=8, analysis=None)


def test_arbitrarily_late_candidate_is_retrieved_from_full_corpus():
    rows = [paper(f"Unrelated record {i}", f"10.1234/{i}", abstract="A generic astronomical measurement") for i in range(1000)]
    rows[960] = paper("Unique target method", "10.1234/late", abstract="Learned volumetric discretization for continuum simulation")
    store = ArticleStore({})
    ids, dropped = store.insert(rows)
    found, info = store.search("learned volumetric discretization continuum", 8)
    assert len(ids) == 1000 and not dropped
    assert found[0].doi == "10.1234/late"
    assert not info["scores_are_scientific_ranking"]


@pytest.mark.parametrize("query,target,distractor", [
    ("protein folding amino acid structure", "Neural protein folding from amino acid sequences", "Quantum spin tomography"),
    ("quantum mixed density tomography", "Quantum mixed density matrix tomography", "Protein folding prediction"),
])
def test_generic_retrieval_in_two_other_domains(query, target, distractor):
    store = ArticleStore({})
    store.insert([paper(distractor, "10.1234/other"), paper(target, "10.1234/target")])
    found, _ = store.search(query)
    assert found[0].doi == "10.1234/target"


def test_multiple_windows_in_same_batch_are_cumulative_and_not_examined_implicitly():
    toolkit = tools([paper(f"Scientific method {i}", f"10.1234/{i}", abstract="Learning experiments") for i in range(20)])
    first = execute(toolkit, "search_collected_articles", query="learning", limit=5, exclude_presented=True, family=None)
    second = execute(toolkit, "search_collected_articles", query="experiments", limit=5, exclude_presented=True, family=None)
    assert not set(a["id"] for a in first["articles"]) & set(a["id"] for a in second["articles"])
    assert not toolkit.store.exposed and not toolkit.store.examined
    expose(toolkit)
    assert len(toolkit.store.exposed) == 10 and not toolkit.store.examined
    assert len(toolkit.state["candidate_windows"]) == 2


def test_exact_identifier_retrieval_and_revisiting_finalist():
    toolkit = tools()
    aid = next(iter(toolkit.registry))
    execute(toolkit, "get_articles", identifiers=[aid]); expose(toolkit)
    default = execute(toolkit, "search_collected_articles", query="10.1234/science", limit=3, exclude_presented=True, family=None)
    revisit = execute(toolkit, "search_collected_articles", query="10.1234/science", limit=3, exclude_presented=False, family=None)
    assert not default["articles"] and revisit["articles"][0]["id"] == aid


def test_late_paywalled_candidate_can_replace_read_open_access_finalist():
    toolkit = tools([paper("Early accessible paper", "10.1234/early", abstract="Related work", fulltext_available=True),
        paper("Late direct contribution", "10.1234/late", abstract="Direct method", publisher_access="paywalled")])
    early, late = list(toolkit.registry)
    execute(toolkit, "get_articles", identifiers=[early, late]); expose(toolkit)
    for aid in (early, late):
        execute(toolkit, "record_candidate_decisions", decisions=[{"article_id": aid, "decision": "retain", "relevance_tag": "DIRECT", "justification": "Scientific contribution fits the user objective."}], finalist_ids=[aid], covered_families=["method"], remaining_axes=[])
    assert toolkit.state["evaluation"]["recommended_article_ids"] == [late]
    assert toolkit.registry[late].publisher_access == "paywalled"
    assert len(toolkit.state["candidate_decisions"]) == 2


def test_source_versions_remain_distinct_and_are_linked_only_by_identifiers():
    store = ArticleStore({})
    store.insert([paper("Same title", "10.1234/journal", arxiv_id="2501.12345"),
        paper("Same title", "10.48550/arxiv.2501.12345", arxiv_id="2501.12345"),
        paper("Same title", "10.1234/another")])
    assert len(store.registry) == 3
    records = list(store.registry.values())
    assert records[0].canonical_work_id == records[1].canonical_work_id
    assert records[2].canonical_work_id != records[0].canonical_work_id


def test_analysis_is_done_by_conversation_model_and_requires_presented_exact_evidence():
    toolkit = tools()
    aid = next(iter(toolkit.registry))
    result = reading(toolkit, aid)
    eid = result["reading"][0]["passages"][0]["evidence_id"]
    args = dict(article_ids=[aid], action="save", research_context="Research", passage_query="", passage_offset=0, passages_per_article=8, analysis=analysis(eid))
    with pytest.raises(ValueError, match="présentés"):
        execute(toolkit, "analyze_articles", **args)
    expose(toolkit)
    saved = execute(toolkit, "analyze_articles", **args)
    assert saved["article"]["analysis_basis"] == "abstract"
    assert toolkit.registry[aid].analysis["evidence"][0]["page_number"] is None
    assert not toolkit.sources.mock_calls  # No hidden LLM or external calls for analysis.


def test_late_fulltext_passage_and_source_version_cannot_be_fabricated():
    toolkit = tools([paper(fulltext={"text": "early later", "url": "https://example.org/version2", "chunks": [
        {"text": "Introductory background "*500, "page": 1, "section": "Introduction"},
        {"text": "Rare failure constraint found only on the late page.", "page": 33, "section": "Limitations"}]})])
    aid = next(iter(toolkit.registry))
    result = reading(toolkit, aid, "rare failure constraint")
    evidence = result["reading"][0]["passages"][0]
    assert evidence["page_number"] == 33
    assert evidence["exact_quote"] == "Rare failure constraint found only on the late page."
    a = toolkit.registry[aid]
    old = passages(a)[0]["evidence_id"]
    a.fulltext["url"] = "https://example.org/version3"
    assert old != passages(a)[0]["evidence_id"]


def test_gaps_separate_corpus_absence_from_comparative_inference():
    toolkit = tools()
    aid = next(iter(toolkit.registry))
    execute(toolkit, "identify_research_gaps", article_ids=[aid], research_context="Research", gaps=[{
        "statement": "No work found in this corpus", "origin": "corpus_absence", "article_ids": [aid], "evidence_ids": [], "scope": "Only this session and these queries"}])
    assert toolkit.state["research_gaps"][0]["cir_eligibility"] == "not_established"
    with pytest.raises(ValueError, match="preuves"):
        execute(toolkit, "identify_research_gaps", article_ids=[aid], research_context="Research", gaps=[{
            "statement": "Claimed global gap", "origin": "comparative_inference", "article_ids": [aid], "evidence_ids": [], "scope": "Consulted works"}])


def test_bibliography_blocks_invented_doi_and_retains_access_level():
    toolkit = tools()
    aid = next(iter(toolkit.registry))
    common = dict(article_ids=[aid], evidence_ids=[], coverage_sufficient=False, remaining_uncertainties=["Abstract only"])
    blocked = execute(toolkit, "finalize_response", answer="https://doi.org/10.1234/invented", **common)
    assert "error" in blocked and toolkit.finished is None
    execute(toolkit, "finalize_response", answer=f"Relevant contribution [{aid}].", **common)
    assert "10.1234/science" in toolkit.finished["answer"]
    assert "abstract_only" in toolkit.finished["answer"]


def test_strict_tool_schemas_have_no_optional_missing_fields():
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node.get("properties", {})) == set(node.get("required", []))
            for value in node.values(): check(value)
        elif isinstance(node, list):
            for value in node: check(value)
    for definition in definitions(): check(definition["parameters"])


def test_paid_execution_requires_explicit_budget_authorization():
    with pytest.raises(ConfigurationError, match="non autorisés"):
        ScholarV31Config(api_key="mock").validate()


def test_changed_quote_or_page_is_blocked_against_immutable_source_snapshot():
    toolkit = tools()
    aid = next(iter(toolkit.registry))
    read = reading(toolkit, aid); expose(toolkit)
    eid = read["reading"][0]["passages"][0]["evidence_id"]
    toolkit.evidence[eid]["page_number"] = 999
    with pytest.raises(ValueError, match="localisation"):
        execute(toolkit, "finalize_response", answer=f"Conclusion [{aid}] [{eid}]", article_ids=[aid], evidence_ids=[eid], coverage_sufficient=False, remaining_uncertainties=[])


def test_doi_title_association_in_markdown_cannot_be_changed_by_the_model():
    toolkit = tools()
    aid = next(iter(toolkit.registry))
    result = execute(toolkit, "finalize_response", answer="[Invented title](https://doi.org/10.1234/science)", article_ids=[aid], evidence_ids=[], coverage_sufficient=False, remaining_uncertainties=[])
    assert "titre–DOI" in result["error"]


def test_arxiv_notice_with_journal_doi_still_requires_registry_verification():
    toolkit = tools([paper(doi="10.1234/journal", arxiv_id="2501.12345", verification_sources=["https://arxiv.org/abs/2501.12345"])])
    toolkit.sources.verify.side_effect = lambda row, deadline: row.model_copy(update={"verified": False, "metadata_errors": ["DOI/title mismatch"]})
    result = execute(toolkit, "verify_bibliography", article_ids=list(toolkit.registry))
    assert not result["valid"] and not next(iter(toolkit.registry.values())).verified


def test_optional_multilingual_semantic_retrieval_uses_local_vectors_only():
    def encoder(texts):
        return [[1,0] if "protein" in text.casefold() or "protéine" in text.casefold() else [0,1] for text in texts]
    store = ArticleStore({}, encoder=encoder)
    store.insert([paper("Protein structure", "10.1234/protein"), paper("Quantum experiment", "10.1234/quantum")])
    found, info = store.search("protéine")
    assert found[0].doi == "10.1234/protein" and info["method"] == "bm25+local_embeddings"


def test_failing_embeddings_degrade_to_lexical_search_without_losing_candidates():
    store = ArticleStore({}, encoder=lambda texts: (_ for _ in ()).throw(RuntimeError("unavailable")))
    store.insert([paper("Protein structure", "10.1234/protein")])
    found, info = store.search("protein")
    assert found and info["method"] == "bm25" and info["semantic_warning"] == "RuntimeError"


def test_metrics_separate_discovery_exposure_selection_and_final_answer():
    from importlib.util import spec_from_file_location, module_from_spec
    from pathlib import Path
    spec = spec_from_file_location("v31_evaluation", Path(__file__).parent/"research"/"evaluate.py")
    module = module_from_spec(spec); spec.loader.exec_module(module)
    measure = module.measure
    articles = {str(i): {"id": str(i), "doi": f"10.1234/{i}"} for i in range(4)}
    case = {"case": "mock", "references": [{"label": str(i), "doi": f"10.1234/{i}", "bibliographic_status": "verified"} for i in range(5)]}
    payload = {"live": False, "state": {"articles": articles, "exposed_article_ids": ["1", "2", "3"], "evaluation": {"recommended_article_ids": ["2", "3"]}}, "result": {"cited_article_ids": ["3"]}}
    metrics = measure(payload, case)
    assert [a["failure_stage"] for a in metrics["reference_audit"]] == ["present_not_exposed", "exposed_not_selected", "selected_not_returned", "returned", "absent_from_corpus"]
    assert metrics["precision_independent"] is None
    assert metrics["exposure"] == .6 and metrics["selection"] == .4


def call(name, args, cid):
    return {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": cid, "id": "fc_"+cid}


def response(*items, text="", status="completed"):
    return NS(output=list(items), output_text=text, usage={"input_tokens": 100, "output_tokens": 100}, status=status)


def client(*responses):
    mock = Mock(); mock.with_options.return_value = mock
    queue = list(responses)
    def create(**kwargs):
        stream = Mock()
        stream.__iter__ = Mock(return_value=iter([NS(type="response.completed", response=queue.pop(0))]))
        return stream
    mock.responses.create.side_effect = create
    return mock


def test_one_responses_loop_preserves_all_batch_windows_call_ids_and_no_private_reasoning():
    store = ArticleStore({})
    store.insert([paper(f"Method {i}", f"10.1234/{i}", abstract="Learning experiment") for i in range(12)])
    state = store.state
    state["messages"] = [{"role": "user", "content": "Research"}]
    args = dict(query="learning", limit=3, exclude_presented=True, family=None)
    reasoning = {"type": "reasoning", "encrypted_content": "private-opaque", "summary": []}
    mock = client(response(reasoning, call("search_collected_articles", args, "first"), call("search_collected_articles", args, "second")),
        response(call("finalize_response", dict(answer="Corpus collected, classification remains incomplete.", article_ids=[], evidence_ids=[], coverage_sufficient=False, remaining_uncertainties=["Not yet compared"]), "final")))
    events = []
    result = ScholarV31ChatAgent(config(), mock, Mock()).run(state, emit=events.append)
    assert len(result["presented_article_ids"]) == 6
    assert not result["examined_article_ids"]
    submitted = mock.responses.create.call_args_list[1].kwargs["input"]
    assert {i["call_id"] for i in submitted if i.get("type") == "function_call_output"} == {"first", "second"}
    assert "private-opaque" not in json.dumps(state)+json.dumps(result)+json.dumps(events)
    assert all(request.kwargs["model"] == "gpt-5.6-sol" for request in mock.responses.create.call_args_list)
    assert len(mock.responses.create.call_args_list) == 2


def test_failed_upstream_never_marks_pending_windows_as_presented_and_is_costed():
    store = ArticleStore({}); store.insert([paper(abstract="Learning experiment")])
    state = store.state; state["messages"] = [{"role": "user", "content": "Research"}]
    mock = client(response(call("search_collected_articles", dict(query="learning", limit=3, exclude_presented=True, family=None), "first")))
    result = ScholarV31ChatAgent(config(), mock, Mock()).run(state)
    assert result["partial"] and not result["presented_article_ids"]
    assert result["usage"]["unreported_reserved_cost_usd"] > 0


def test_native_web_sources_are_preserved_without_turning_them_into_verified_articles():
    native = {"type": "web_search_call", "id": "ws1", "status": "completed", "action": {"type": "search", "sources": [{"url": "https://example.org/publication", "title": "Source"}]}}
    mock = client(response(native, text="Bonjour."))
    result = ScholarV31ChatAgent(config(), mock, Mock()).run({"messages": [{"role": "user", "content": "Bonjour"}], "articles": {}})
    assert result["web_searches"] == 1 and not result["articles"]
    assert result["citations"][0]["url"] == "https://example.org/publication"


def test_context_compaction_preserves_complete_batch_and_pin_without_full_corpus_index():
    store = ArticleStore({}); store.insert([paper(f"Method {i}", f"10.1234/{i}", abstract="Learning experiment "*300) for i in range(30)])
    state = store.state; state["messages"] = [{"role": "user", "content": "Find research"}]
    args = dict(query="learning", limit=12, exclude_presented=True, family=None)
    mock = client(response(call("search_collected_articles", args, "one"), call("search_collected_articles", args, "two")),
        response(call("finalize_response", dict(answer="Incomplete research.", article_ids=[], evidence_ids=[], coverage_sufficient=False, remaining_uncertainties=[]), "final")))
    result = ScholarV31ChatAgent(config(max_context_chars=5000), mock, Mock()).run(state)
    second = mock.responses.create.call_args_list[1].kwargs["input"]
    assert {i["call_id"] for i in second if i.get("type") == "function_call_output"} == {"one", "two"}
    assert len(result["presented_article_ids"]) == 24
    assert "candidate_index" not in json.dumps(second[0])
