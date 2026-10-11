import json
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from agents.EnnoScholarV3.agent import ScholarV3ChatAgent
from agents.EnnoScholarV3.config import ScholarV3Config
from agents.EnnoScholarV3.models import Article


def output(text, annotations=None):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": annotations or []}]}


def call(name, args, cid):
    return {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": cid, "id": "fc_" + cid}


def response(*items, status="completed"):
    texts = [c["text"] for item in items if item["type"] == "message" for c in item["content"] if c["type"] == "output_text"]
    return NS(output=list(items), output_text="\n".join(texts), usage={"input_tokens": 100, "output_tokens": 100}, status=status)


def mock_client(*responses):
    client = Mock()
    client.with_options.return_value = client
    def create(**kwargs):
        r = queue.pop(0)
        if not kwargs["stream"]:
            return r
        stream = Mock()
        stream.__iter__ = Mock(return_value=iter([NS(type="response.completed", response=r)]))
        return stream
    queue = list(responses)
    client.responses.create.side_effect = create
    return client


def config(**kwargs):
    return replace(ScholarV3Config(api_key="test-only"), **kwargs)


def state(message="Recherche de littérature scientifique"):
    return {"messages": [{"role": "user", "content": message}], "articles": {}, "research_state": {}}


class Sources:
    def __init__(self):
        self.queries = []
    def search(self, query, sources, limit, deadline, check):
        self.queries.append(query)
        return [Article(title="Finite element refinement with learned sizing", doi="10.1234/mesh", authors=["Scientist"], year=2025,
            abstract="We predict a sizing field. A classical mesher produces connectivity. Generalization to complex topology is not evaluated.",
            url="https://example.org/paper", verified=True, sources=["openalex"])], []


def test_native_web_and_successive_function_calls_preserve_call_ids_and_encrypted_items():
    native = {"type": "web_search_call", "id": "ws1", "status": "completed", "action": {"type": "search", "query": "FEM learned sizing", "sources": [{"url": "https://example.org/paper"}]}}
    encrypted = {"type": "reasoning", "id": "rs1", "summary": [], "encrypted_content": "opaque-ephemeral"}
    args = {"query": "FEM learned sizing", "sources": None, "limit": 5, "family": "adaptation"}
    client = mock_client(response(native, encrypted, call("search_scientific", args, "one")),
        response(call("search_scientific", {**args, "query": "topology mesh generalization", "family": "topology"}, "two")),
        response(output("Sélection vérifiable.")))
    data, events, sources = state(), [], Sources()
    result = ScholarV3ChatAgent(config(), client, sources).run(data, emit=events.append)
    assert result["tool_counts"]["web_search"] == 1 and result["scientific_searches"] == 2
    assert len(data["articles"]) == 1
    assert any(row["url"] == "https://example.org/paper" for row in result["citations"])
    req = client.responses.create.call_args_list
    assert {"type": "web_search"} in req[0].kwargs["tools"]
    assert req[0].kwargs["store"] is False and req[0].kwargs["max_tool_calls"] == 1
    assert any(item.get("encrypted_content") == "opaque-ephemeral" for item in req[1].kwargs["input"])
    assert any(item.get("call_id") == "one" and item["type"] == "function_call_output" for item in req[1].kwargs["input"])
    assert "opaque-ephemeral" not in json.dumps(data) + json.dumps(result) + json.dumps(events)
    assert set(result["covered_families"]) == {"adaptation", "topology"}


def test_answer_without_search_and_long_multiturn_context():
    data = state("Parmi les articles précédents, lesquels ont des limites ?")
    data["messages"].insert(0, {"role": "assistant", "content": "Publication précédente"})
    client = mock_client(response(output("Les limites sont explicitées.")))
    result = ScholarV3ChatAgent(config(), client, Sources()).run(data)
    assert result["tool_calls"] == 0
    assert any("Publication précédente" in m.get("content", "") for m in client.responses.create.call_args.kwargs["input"])


def test_citations_offsets_are_preserved_across_output_parts():
    r = response(output("Premier."), output("Texte source", [{"type": "url_citation", "url": "https://example.org", "title": "Publication", "start_index": 6, "end_index": 12}]))
    text, cites = ScholarV3ChatAgent.answer(r)
    assert text == "Premier.\nTexte source" and cites[0]["start_index"] == 15
    assert cites[0]["cited"]


def test_cost_budget_stops_before_paid_request():
    client = mock_client()
    result = ScholarV3ChatAgent(config(max_cost_usd=0.00001), client, Sources()).run(state())
    assert result["partial"] and result["reason"] == "cost_budget"
    client.responses.create.assert_not_called()


def test_unresolved_doi_in_final_answer_is_blocked():
    client = mock_client(response(output("Fabricated reference https://doi.org/10.1234/unresolved")))
    result = ScholarV3ChatAgent(config(), client, Sources()).run(state())
    assert result["reason"] == "unverified_text_references"
    assert "10.1234/unresolved" not in result["answer"]
    assert "bloquée" in result["answer"]


def test_tool_budget_and_iteration_budget_return_explicit_partial():
    args = {"query": "FEM", "sources": None, "limit": 5, "family": "sizing"}
    client = mock_client(response(call("search_scientific", args, "one"), call("search_scientific", args, "two")), response(output("Recherche limitée.")))
    result = ScholarV3ChatAgent(config(max_tool_calls=1), client, Sources()).run(state())
    assert result["partial"] and result["reason"] == "tool_budget" and result["tool_calls"] == 1
    client = mock_client(response(call("search_scientific", args, "one")), response(output("Recherche partielle.")))
    result = ScholarV3ChatAgent(config(max_response_rounds=1), client, Sources()).run(state())
    assert result["reason"] == "iteration_budget"


def test_cancel_and_timeout_have_no_unhandled_tool_final():
    client = mock_client()
    result = ScholarV3ChatAgent(config(), client, Sources()).run(state(), cancelled=lambda: True)
    assert result["reason"] == "cancelled"
    client.responses.create.assert_not_called()


def test_failed_function_returns_error_for_original_call_id_then_continues():
    client = mock_client(response(call("get_article_metadata", {"article_id_or_url": "x"}, "one")), response(output("Précisez la référence.")))
    result = ScholarV3ChatAgent(config(), client, Sources()).run(state())
    assert result["answer"] == "Précisez la référence."
    outputs = [i for i in client.responses.create.call_args.kwargs["input"] if i.get("type") == "function_call_output"]
    assert outputs[0]["call_id"] == "one" and "error" in outputs[0]["output"]


def test_extractive_history_summary_preserves_consultant_decisions():
    data = state()
    data["messages"] = [{"role": "user", "content": f"Décision consultant {i}"} for i in range(50)]
    ScholarV3ChatAgent.compact_history(data)
    assert len(data["messages"]) == 30 and "Décision consultant 0" in data["summary"]
