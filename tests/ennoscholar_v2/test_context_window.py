from __future__ import annotations

import json

import pytest

from agents.EnnoScholarV2.agent import StoredArticle
from agents.EnnoScholarV2.context import ActiveSources, estimate_tokens, serialized_bytes
from agents.EnnoScholarV2.models import Article
from context_reproduction import run_reproduction


def active_notices(request):
    return json.JSONDecoder().raw_decode(request["instructions"].rsplit(":\n", 1)[-1])[0]


def test_289_articles_four_searches_then_fulltext_and_analysis():
    run = run_reproduction()
    assert run.report["backend_articles"] == 289
    assert [name for name, _ in run.tool_results[:4]] == ["search_scientific", "find_similar_articles", "find_similar_articles", "search_scientific"]
    assert all(len(result["articles"]) <= 8 for _, result in run.tool_results[:4])
    assert not run.result.partial and run.result.reason is None
    assert run.result.tool_counts == {"search_scientific": 2, "find_similar_articles": 2, "get_fulltext": 1, "analyze_articles": 1}
    assert len(run.retrieved) == 5 and run.report["fulltexts"] == 5 and run.report["analyses"] == 3
    assert all(record.article.abstract.endswith("BACKEND_ONLY_ABSTRACT_END") for record in run.registry.values())
    for request in run.requests:
        assert request["tool_choice"] == "auto" and request["tools"]
        notices = active_notices(request)
        assert len(notices) <= 12 and len({notice["id"] for notice in notices}) == len(notices)
        assert "BACKEND_ONLY_ABSTRACT_END" not in request["instructions"]
        assert not any(row.get("title") for item in request["input"] if item.get("type") == "function_call_output"
            for row in json.loads(item["output"]).get("articles", []))
    assert len(active_notices(run.requests[2])) == 12


def test_native_compaction_prunes_old_pairs_and_keeps_recent_pairs():
    run = run_reproduction(native=True)
    next_input = run.requests[2]["input"]
    assert next_input[0]["type"] == "compaction" and next_input[0]["id"] == "cmp_native"
    assert not any(item.get("id") == "rs_old" or item.get("call_id") in {"c1", "c2"} for item in next_input)
    assert {item["call_id"] for item in next_input if item.get("type") == "function_call"} == {"c3", "c4"}
    assert {item["call_id"] for item in next_input if item.get("type") == "function_call_output"} == {"c3", "c4"}
    assert run.report["fulltexts"] == 5 and not run.result.partial
    assert run.requests[0]["context_management"]


def test_reported_token_pressure_compacts_before_continuation():
    run = run_reproduction(pressure=True)
    run.client.responses.compact.assert_called_once()
    assert run.requests[2]["input"][0]["id"] == "cmp_standalone"
    assert run.requests[2]["tool_choice"] == "auto"
    assert run.report["fulltexts"] == 5 and not run.result.partial


def test_failed_standalone_compaction_still_allows_native_continuation(monkeypatch):
    from unittest.mock import Mock
    original = Mock
    # Use the same scenario, changing only the compaction endpoint's outcome.
    import context_reproduction
    def client_mock(*args, **kwargs):
        client = original(*args, **kwargs)
        client.responses.compact.side_effect = RuntimeError("Compaction unavailable")
        return client
    monkeypatch.setattr(context_reproduction, "Mock", client_mock)
    run = run_reproduction(pressure=True)
    assert run.report["fulltexts"] == 5 and not run.result.partial
    assert all(request["context_management"] for request in run.requests)


@pytest.mark.parametrize("identity", ["id", "doi", "arxiv", "title"])
def test_active_selection_deduplicates_all_article_identities(identity):
    left = Article(id="a", title="Neural meshing", doi="10.1234/one", arxiv_id="2401.00001")
    right = Article(id="b", title="Different article", doi="10.1234/two", arxiv_id="2401.00002")
    if identity == "id":
        right.id = left.id
    elif identity == "doi":
        right.doi = "https://doi.org/10.1234/ONE"
    elif identity == "arxiv":
        right.arxiv_id = left.arxiv_id + "v2"
    else:
        right.title = "NÉURAL — Meshing!"
    registry = {"left": StoredArticle(left), "right": StoredArticle(right)}
    active = ActiveSources(registry)
    active.update([{"article_id": "left"}, {"article_id": "right"}, {"article_id": "left"}])
    # A title alone no longer overrides contradictory DOI/arXiv identifiers.
    expected = 2 if identity == "title" else 1
    assert len(active.identifiers) == expected and len(active.render(10000)) == expected
    assert len(registry) == 2


def test_new_search_updates_one_selection_instead_of_accumulating_lists():
    registry = {f"a{i}": StoredArticle(Article(id=f"a{i}", title=f"Paper {i}")) for i in range(40)}
    active = ActiveSources(registry)
    for start in range(0, 32, 8):
        result = active.tool_result("search_scientific", {"articles": [registry[f"a{i}"].article.model_dump() for i in range(start, start + 8)]})
        assert len(active.identifiers) == 12 and len(active.render(10000)) == 12
        assert "articles" not in result and len(result["article_ids"]) == 8
        assert active.identifiers[:8] == [f"a{i}" for i in range(start, start + 8)]


def test_opaque_ciphertext_bytes_are_not_counted_as_rendered_tokens():
    small = {"type": "reasoning", "encrypted_content": "x", "summary": []}
    large = dict(small, encrypted_content="x" * 100000)
    assert estimate_tokens(small) == estimate_tokens(large)
    assert serialized_bytes(large) > 100000


def test_offline_fallback_estimates_tokens_instead_of_utf8_bytes(monkeypatch):
    import agents.EnnoScholarV2.context as context
    monkeypatch.setattr(context, "_tokenizer", lambda: None)
    assert context.estimate_tokens("a" * 400) == 100
    assert context.estimate_tokens("汉" * 100) == 100
    assert context.serialized_bytes("汉" * 100) == 300
