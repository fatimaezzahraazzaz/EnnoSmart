import json
import time
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import pytest

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config, ConfigurationError
from agents.EnnoScholarV4.models import Article, normalize_doi
from agents.EnnoScholarV4.store import ArticleStore
from agents.EnnoScholarV4.toolkit import ScholarTools, definitions
from agents.EnnoScholarV4.tools import fulltext


def toolkit():
    sources = Mock()
    sources.verify.side_effect = lambda article, deadline: article.model_copy(update={"verified": True})
    sources.metadata.return_value = []
    result = ScholarTools({"messages": [], "articles": {}}, sources, ScholarV4Config(api_key="mock"))
    result.store.insert([Article(title="Verified study", doi="10.1234/study", authors=["Author"], year=2025,
        verified=True, url="https://example.org/study", abstract="The authors generate volumetric meshes using a trained model. Validation remains limited.")])
    return result


def execute(t, name, **args):
    return t.execute(name, args, time.monotonic()+30)


def tool_inputs(result):
    return [{"type": "function_call_output", "output": json.dumps(result)}]


def expose(t, result):
    t.delivered([w["window_id"] for w in [*t.store.windows, *t.reading_windows] if w["status"] == "queued"],
        inputs=tool_inputs(result))


def outline(t, action, **kw):
    args = {"action": action, "title": None, "section_id": None, "target_section_id": None,
        "position": None, "sections": None, "article_ids": None, **kw}
    return execute(t, "manage_outline", **args)


def create_outline(t):
    return outline(t, "create", sections=[{"title": title, "description": "Scientific section", "article_ids": []}
        for title in ["Classical methods", "Learned approaches", "Hybrid methods"]])["outline"]


def read(t, aid):
    return execute(t, "analyze_article", article_ids=[aid], action="read", research_context="3D volumetric meshes",
        passage_query="", passage_offset=0, passages_per_article=8, analysis=None)


def analysis(eid):
    fields = ["problem", "objective", "method", "contribution", "hypotheses", "results", "limitations",
        "generalization", "open_questions", "relevance", "technical_mechanism", "experimental_setup"]
    return {**{name: [] for name in fields}, "method": [{"statement": "Uses a trained model", "evidence_ids": [eid],
        "origin": "source_statement"}], "method_family": "learning", "analysis_limit": "Available passages only"}


def test_title_edit_preserves_ids_other_sections_and_never_searches():
    t = toolkit(); initial = create_outline(t)
    updated = outline(t, "rename", section_id="2", title="Neural approaches")["outline"]
    assert updated["sections"][1]["id"] == initial["sections"][1]["id"]
    assert updated["sections"][0] == initial["sections"][0]
    assert updated["sections"][2] == initial["sections"][2]
    assert t.state["outline_versions"][-1] == initial
    assert t.state["focus"]["section_id"] == updated["sections"][1]["id"]
    t.sources.search.assert_not_called()


def test_targeted_section_search_uses_focus_and_does_not_associate_distractors():
    t = toolkit(); create_outline(t); outline(t, "rename", section_id="2", title="Neural approaches")
    t.sources.search.return_value = ([Article(title="Unrelated result", doi="10.1234/unrelated")], [])
    result = execute(t, "search_for_section", section_id=None, query="neural volumetric mesh generation", limit_per_source=5)
    assert result["section"]["title"] == "Neural approaches"
    assert result["section"]["article_ids"] == []
    assert t.sources.search.call_args.args[0] == "neural volumetric mesh generation"


def test_unknown_article_association_fails_without_changing_outline():
    t = toolkit(); original = create_outline(t)
    with pytest.raises(ValueError):
        outline(t, "associate", section_id="2", article_ids=["invented"])
    assert t.state["outline"] == original


def test_second_article_and_current_reference_are_resolved_locally():
    t = toolkit()
    ids, _ = t.store.insert([Article(title="Second study", doi="10.1234/second")])
    first = next(iter(t.registry)); second = ids[0]
    t.state["last_response"] = {"cited_article_ids": [first, second]}
    assert execute(t, "get_article", identifiers=["2"])["articles"][0]["id"] == second
    assert execute(t, "get_article", identifiers=["cet article"])["articles"][0]["id"] == second
    t.sources.metadata.assert_not_called()


def test_analysis_cannot_be_saved_without_read_evidence():
    t = toolkit(); aid = next(iter(t.registry))
    result = read(t, aid); eid = result["reading"][0]["passages"][0]["evidence_id"]
    with pytest.raises(ValueError):
        execute(t, "analyze_article", article_ids=[aid], action="save", research_context="meshing", passage_query="",
            passage_offset=0, passages_per_article=8, analysis=analysis(eid))
    expose(t, result)
    execute(t, "analyze_article", article_ids=[aid], action="save", research_context="meshing", passage_query="",
        passage_offset=0, passages_per_article=8, analysis=analysis(eid))
    assert t.registry[aid].analysis_basis == "abstract"
    assert t.registry[aid].access_status != "fulltext_analyzed"
    assert t.registry[aid].analysis["proposed_method"]


def test_fulltext_analyzed_only_after_saving_verified_analysis():
    t = toolkit(); aid = next(iter(t.registry)); a = t.registry[aid]
    a.fulltext = {"text": a.abstract, "chunks": [{"text": a.abstract, "page": 3, "section": "Method"}], "url": a.url}
    a.access_status = "fulltext_extracted"
    result = read(t, aid); expose(t, result)
    eid = result["reading"][0]["passages"][0]["evidence_id"]
    assert a.access_status == "fulltext_extracted"
    execute(t, "analyze_article", article_ids=[aid], action="save", research_context="meshing", passage_query="",
        passage_offset=0, passages_per_article=8, analysis=analysis(eid))
    assert a.access_status == "fulltext_analyzed"
    assert a.analysis["evidence"][0]["page_number"] == 3


def test_targeted_fulltext_read_enriches_abstract_card_without_losing_proofs():
    t = toolkit(); aid = next(iter(t.registry)); a = t.registry[aid]
    first = read(t, aid); expose(t, first)
    abstract_eid = first["reading"][0]["passages"][0]["evidence_id"]
    execute(t, "analyze_article", article_ids=[aid], action="save", research_context="meshing",
        passage_query="", passage_offset=0, passages_per_article=8, analysis=analysis(abstract_eid))
    a.fulltext = {"text": "The measured accuracy is 84.7 percent on the held-out set.",
        "chunks": [{"text": "The measured accuracy is 84.7 percent on the held-out set.",
            "page": 5, "section": "Results"}], "url": a.url}
    second = read(t, aid); expose(t, second)
    result_eid = second["reading"][0]["passages"][0]["evidence_id"]
    deeper = analysis(result_eid)
    deeper["method"] = []
    deeper["results"] = [{"statement": "Accuracy was 84.7 percent on the held-out set",
        "evidence_ids": [result_eid], "origin": "source_statement"}]
    execute(t, "analyze_article", article_ids=[aid], action="save", research_context="meshing",
        passage_query="", passage_offset=0, passages_per_article=8, analysis=deeper)
    assert a.analysis["method"][0]["evidence_ids"] == [abstract_eid]
    assert a.analysis["results"][0]["evidence_ids"] == [result_eid]
    assert {e["evidence_id"] for e in a.analysis["evidence"]} == {abstract_eid, result_eid}
    assert a.analysis_basis == "abstract"  # The cumulative card includes abstract-only claims.
    assert a.access_status == "fulltext_analyzed"
    assert a.analysis["reading_coverage"] == "targeted_passages"
    execute(t, "analyze_article", article_ids=[aid], action="save", research_context="meshing",
        passage_query="", passage_offset=0, passages_per_article=8, analysis=deeper)
    assert len(a.analysis["results"]) == 1


@pytest.mark.parametrize("status", [403, 404, 429])
def test_http_failure_never_claims_fulltext(monkeypatch, status):
    t = toolkit(); a = next(iter(t.registry.values()))
    def failure(*args, **kwargs):
        r = httpx.Response(status, request=httpx.Request("GET", a.url))
        raise httpx.HTTPStatusError("blocked", request=r.request, response=r)
    monkeypatch.setattr(fulltext, "fetch_public", failure)
    result = fulltext.get_fulltext(a, t.sources, time.monotonic()+5)
    assert not result["text"] and result["access_level"] == "abstract_only"
    assert result["access_status"] == ("access_blocked" if status == 403 else "abstract_only")


def test_wrong_pdf_identity_and_corrupt_pdf_never_get_analyzed(monkeypatch):
    import fitz
    t = toolkit(); a = next(iter(t.registry.values()))
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((30, 30), "Different publication\n" + "Astronomical measurements. "*40)
        pdf = document.tobytes()
    monkeypatch.setattr(fulltext, "fetch_public", lambda *a, **k: (pdf, "application/pdf", "https://example.org/wrong"))
    result = fulltext.get_fulltext(a, t.sources, time.monotonic()+5)
    assert result["access_status"] == "extraction_failed" and not result["text"]
    monkeypatch.setattr(fulltext, "fetch_public", lambda *a, **k: (b"%PDF-broken", "application/pdf", "https://example.org/broken"))
    assert fulltext.get_fulltext(a, t.sources, time.monotonic()+5)["access_status"] == "extraction_failed"


def test_conservative_deduplication_and_missing_doi():
    store = ArticleStore({})
    ids, _ = store.insert([Article(title="Similar title", authors=["A"], year=2023, url="https://example.org/a"),
        Article(title="Similar title", authors=["B"], year=2025, url="https://example.org/b"),
        Article(title="Canonical", doi="https://doi.org/10.1234/REAL"), Article(title="Canonical", doi="10.1234/real")])
    assert len(ids) == 3
    assert normalize_doi("not a doi") == ""
    assert normalize_doi("") == ""
    assert all(a.doi == "" for a in list(store.registry.values())[:2])


def test_unverified_links_and_invented_dois_are_rejected():
    t = toolkit(); aid = next(iter(t.registry))
    for answer in ["[fake](https://evil.example/fake)", "10.1234/fake"]:
        result = execute(t, "finalize_response", answer=answer, article_ids=[aid], evidence_ids=[], coverage_sufficient=False, remaining_uncertainties=[])
        assert "error" in result


def test_search_budget_and_cache_apply_to_real_provider_calls():
    t = toolkit(); t.config = replace(t.config, max_search_results=2)
    t.sources.search.return_value = ([Article(title=f"Study {i}", doi=f"10.1234/{i}") for i in range(4)], [])
    args = dict(query="volumetric mesh", sources=None, limit_per_source=5, offset=0, family="generation")
    first = execute(t, "search_scientific", **args)
    assert first["collected"] == 2
    execute(t, "search_scientific", **args)
    assert t.sources.search.call_count == 1


def test_tool_schemas_are_strict_and_have_no_classification_tags():
    for definition in definitions():
        assert definition["strict"]
        assert definition["parameters"]["additionalProperties"] is False
        assert set(definition["parameters"]["required"]) == set(definition["parameters"]["properties"])
        assert "relevance_tag" not in json.dumps(definition)


def test_config_requires_key_without_silent_model_substitution(monkeypatch):
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        ScholarV4Config().validate()
    monkeypatch.setenv("ENNOSCHOLAR_V4_MAX_TOOL_ROUNDS", "bad")
    with pytest.raises(ConfigurationError):
        ScholarV4Config.from_env()
    assert ScholarV4Config().model == "gpt-6-luna"


def test_function_call_result_is_returned_with_matching_call_id():
    calls = []
    class Stream:
        def __init__(self, response): self.response = response
        def __iter__(self): return iter([NS(type="response.completed", response=self.response)])
        def close(self): pass
    class Client:
        def with_options(self, **kw): return self
        @property
        def responses(self): return self
        def create(self, **kw):
            calls.append(kw)
            output = [{"type": "function_call", "name": "manage_outline", "call_id": "unique_call", "arguments": json.dumps({"action": "read", "title": None, "section_id": None, "target_section_id": None, "position": None, "sections": None, "article_ids": None})}] if len(calls)==1 else []
            return Stream(NS(output=output, output_text="Votre plan est vide.", status="completed", usage={"input_tokens": 100, "output_tokens": 30}))
    state = {"messages": [{"role": "user", "content": "Reprends notre plan"}], "articles": {}}
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=Client(), sources=Mock()).run(state)
    assert not result["partial"]
    outputs = [item for item in calls[1]["input"] if item.get("type")=="function_call_output"]
    assert outputs[0]["call_id"] == "unique_call"
    assert all(t.get("type") != "web_search" for t in calls[0]["tools"])


def test_final_synthesis_can_save_model_analysis_with_exact_evidence():
    t = toolkit(); aid = next(iter(t.registry))
    a = t.registry[aid]
    a.fulltext = {"text": a.abstract, "chunks": [{"text": a.abstract, "page": 2, "section": "Method"}], "url": a.url}
    a.access_status = "fulltext_extracted"
    result = read(t, aid); expose(t, result)
    eid = result["reading"][0]["passages"][0]["evidence_id"]
    result = execute(t, "finalize_response", answer=f"Méthode étudiée [{aid}] [{eid}]", article_ids=[aid], evidence_ids=[eid],
        coverage_sufficient=False, remaining_uncertainties=[], analyses=[{"article_id": aid, "analysis": analysis(eid)}])
    assert result["published"]
    assert t.registry[aid].access_status == "fulltext_analyzed"
    assert t.state["articles"][aid]["analysis"]["analysis_basis"] == "fulltext"


def test_extraction_keeps_late_method_pages_and_html_has_no_fictional_pages():
    import fitz
    from agents.EnnoScholarV4.tools.extraction import pdf_chunks, html_document
    with fitz.open() as document:
        for i in range(45):
            page = document.new_page()
            page.insert_text((30,30), f"Method page {i+1}. Important technical mechanism.")
        data = document.tobytes()
    chunks, truncated = pdf_chunks(data)
    assert chunks[-1].page == 45 and not truncated
    body = '<html><head><meta name="citation_title" content="Scientific paper"></head><body><article><h2>Method</h2><p>'+"Verified method. "*80+'</p><h2>Results</h2><p>'+"Verified result. "*80+'</p></article></body></html>'
    _, chunks, _ = html_document(body.encode(), "https://example.org/paper")
    assert chunks and all(chunk.page is None for chunk in chunks)


def test_writing_section_uses_saved_analysis_and_versioned_provenance():
    t = toolkit(); create_outline(t); aid = next(iter(t.registry))
    outline(t, "associate", section_id="2", article_ids=[aid])
    result = read(t, aid); expose(t, result)
    eid = result["reading"][0]["passages"][0]["evidence_id"]
    execute(t, "analyze_article", article_ids=[aid], action="save", research_context="meshing", passage_query="",
        passage_offset=0, passages_per_article=8, analysis=analysis(eid))
    version = t.state["outline"]["version"]
    execute(t, "write_section", section_id="2", action="save", instructions="Compare contributions and limits",
        text=f"La méthode étudiée utilise un modèle entraîné [{aid}], sur abstract seulement [{eid}].",
        article_ids=[aid], evidence_ids=[eid])
    section = t.state["outline"]["sections"][1]
    assert section["drafts"][0]["evidence_ids"] == [eid]
    assert t.state["outline"]["version"] == version+1
    assert not t.state["outline_versions"][-1]["sections"][1]["drafts"]
    assert t.finished is None


def test_outline_move_merge_and_delete_keep_surviving_identifiers():
    t = toolkit(); initial = create_outline(t)
    ids = [s["id"] for s in initial["sections"]]
    outline(t, "move", section_id=ids[2], position=1)
    assert [s["id"] for s in t.state["outline"]["sections"]] == [ids[2],ids[0],ids[1]]
    outline(t, "merge", section_id=ids[0], target_section_id=ids[1])
    assert [s["id"] for s in t.state["outline"]["sections"]] == [ids[2],ids[1]]
    outline(t, "delete", section_id=ids[2])
    assert t.state["outline"]["sections"][0]["id"] == ids[1]
