from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
import socket
import time

import pytest
from agents.EnnoScholarV3.config import ScholarV3Config
from agents.EnnoScholarV3.models import Article
from agents.EnnoScholarV3.sources import ScientificSources, V3ScientificSearch, merge_articles
from agents.EnnoScholarV3.tools import ScholarTools, passages, analysis_schema
from agents.EnnoScholarV3.fulltext import get_fulltext
from agents.EnnoScholarV3.network import resolve_public


def paper(**kw):
    return Article(title="Learned mesh generation", doi="10.1234/mesh", url="https://example.org", verified=True, **kw)


def toolkit(rows=None, request=None):
    registry = {}
    merge_articles(registry, rows or [paper(abstract="A sizing field is predicted; connectivity is produced by a classical mesher.")])
    data = {"articles": {a.id: a.durable() for a in registry.values()}}
    return ScholarTools(data, Mock(), request or Mock(), ScholarV3Config(api_key="mock"), lambda: None, lambda *a, **k: None)


def test_bridge_dedup_preserves_sources_selected_and_fulltext():
    registry = {}
    one = paper(authors=["A"], selected=True, sources=["openalex"])
    two = Article(title="Learned mesh generation", arxiv_id="2401.12345v2", sources=["arxiv"], abstract="Abstract", url="https://arxiv.org/abs/2401.12345")
    merge_articles(registry, [one, two])
    assert len(registry) == 1
    row = next(iter(registry.values()))
    assert row.selected and row.arxiv_id == "2401.12345" and set(row.sources) == {"openalex", "arxiv"}
    assert row.abstract and row.access_level == "abstract_only"


def test_transitive_bridge_does_not_return_deleted_article_id():
    registry = {}
    a = Article(title="Version A", doi="10.1234/bridge", url="https://example.org/a", verified=True)
    b = Article(title="Version B", arxiv_id="2401.12345", url="https://example.org/b", selected=True, verified=True)
    bridge = Article(title="Version B", doi="10.1234/bridge", arxiv_id="2401.12345", url="https://example.org/c", verified=True)
    ids = merge_articles(registry, [a, b, bridge])
    assert len(registry) == 1 and len(ids) == 1
    assert registry[ids[0]].selected


def test_doi_real_but_wrong_title_is_blocking():
    source = ScientificSources(client=Mock())
    source.client._get.return_value.json.return_value = {"message": {"DOI": "10.1234/mesh", "title": ["Unrelated mechanics paper"], "author": [{"given": "A", "family": "B"}], "published": {"date-parts": [[2025]]}}}
    result = source.verify(paper(), time.monotonic() + 30)
    assert not result.verified and "DOI/title mismatch" in result.metadata_errors


def test_transient_registry_failure_can_be_retried():
    source = ScientificSources(client=Mock())
    source.client._get.side_effect = [TimeoutError(), NS(json=lambda: {"message": {
        "DOI": "10.1234/mesh", "title": [paper().title], "author": [{"given": "A", "family": "B"}],
        "published": {"date-parts": [[2025]]}}})]
    row = paper()
    row.verified = False
    assert not source.verify(row, time.monotonic() + 30).verified
    assert source.verify(row, time.monotonic() + 30).verified
    assert not row.metadata_errors


def test_arxiv_plain_query_requires_concepts_in_title_or_abstract():
    from agents.EnnoScholarV2.config import ScholarConfig
    client = Mock()
    client.get.return_value = Mock(status_code=200)
    source = V3ScientificSearch(ScholarConfig(), client=client)
    source._get("https://export.arxiv.org/api/query", params={"search_query": "all:(learned sizing field)"})
    query = client.get.call_args.kwargs["params"]["search_query"]
    assert query == "(ti:learned OR abs:learned) AND (ti:sizing OR abs:sizing) AND (ti:field OR abs:field)"
    source._get("https://export.arxiv.org/api/query", params={"search_query": "all:(ti:mesh AND abs:learning)"})
    assert client.get.call_args.kwargs["params"]["search_query"] == "all:(ti:mesh AND abs:learning)"


def test_incomplete_analysis_never_marks_downloaded_text_as_analysed():
    tools = toolkit()
    row = next(iter(tools.registry.values()))
    tools.model_request = Mock(return_value=NS(status="incomplete", output_text='{"method":'))
    result = tools.analyze(row, "mechanics")
    assert "error" in result and not row.analysis and row.analysis_basis is None


def test_paywall_or_missing_pdf_does_not_remove_abstract_or_direct_relevance(monkeypatch):
    row = paper(abstract="A relevant abstract", publisher_access="paywalled")
    monkeypatch.setattr("agents.EnnoScholarV3.fulltext.fetch_public", lambda *a, **k: (_ for _ in ()).throw(ValueError("Blocked")))
    source = Mock()
    source.metadata.return_value = []
    result = get_fulltext(row, source, time.monotonic() + 10)
    assert result["access_level"] == "abstract_only" and not result["text"]
    assert row.abstract == "A relevant abstract" and row.publisher_access == "paywalled"


def test_link_pdf_does_not_mark_fulltext_and_unknown_evidence_is_removed():
    tools = toolkit([paper(abstract="We predict only sizing; mesher generates connectivity.", pdf_url="https://example.org/pdf")])
    row = next(iter(tools.registry.values()))
    assert row.access_level == "abstract_only" and row.fulltext_available is None
    ev = passages(row)[0]["evidence_id"]
    raw = {name: [] for name in analysis_schema()["properties"]}
    raw.update(relevance_tag="DIRECT", method_family="sizing", generation_role="field_prediction", analysis_limit="Abstract only",
        method=[{"statement": "Predicts sizing", "evidence_ids": [ev]}], relevance=[{"statement": "Targets sizing for a mechanical mesh", "evidence_ids": [ev]}],
        results=[{"statement": "Invented result", "evidence_ids": ["made-up"]}])
    import json
    tools.model_request = Mock(return_value=NS(output_text=json.dumps(raw)))
    result = tools.analyze(row, "mechanical mesh sizing")
    assert row.analysis_basis == "abstract" and row.relevance_tag == "DIRECT"
    assert not result["analysis"]["results"] and row.analysis["evidence"][0]["text"] in row.abstract
    assert "evidence" not in result["analysis"]  # Long passages live in durable/UI cards.


def test_redundant_queries_are_not_repeated_and_stagnation_stops():
    tools = toolkit()
    tools.sources.search.return_value = ([paper(abstract="Abstract")], [])
    for q in ["mesh sizing", "mesh sizing", "mesh generalization", "mesh adaptation", "mesh topology", "more mesh"]:
        tools.search(q, None, 5, "sizing", time.monotonic() + 5)
    assert tools.sources.search.call_count == 3


@pytest.mark.parametrize("url", ["http://localhost/a", "http://127.0.0.1/a", "http://169.254.169.254/a", "http://[::1]/a", "file:///secret", "https://user:secret@example.org", "https://example.org:8080"])
def test_private_and_credentials_urls_are_blocked(monkeypatch, url):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))])
    with pytest.raises(ValueError):
        resolve_public(url, time.monotonic() + 5)


def test_dns_result_is_pinned_to_public_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    pinned, host = resolve_public("https://example.org/paper?a=1", time.monotonic() + 5)
    assert pinned == "https://93.184.216.34/paper?a=1" and host == "example.org"


def test_metadata_only_cannot_create_scientific_analysis():
    tools = toolkit([paper()])
    row = next(iter(tools.registry.values()))
    result = tools.analyze(row, "mesh generation")
    assert result["analysis_basis"] == "metadata" and not row.analysis
    tools.model_request.assert_not_called()
