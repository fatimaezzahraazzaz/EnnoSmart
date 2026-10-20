import copy
import json
import time
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.publication import fallback
from agents.EnnoScholarV4.toolkit import ScholarTools, FinalArgs
from tests.ennoscholar_v4.test_tools import analysis, tool_inputs


class Stream:
    def __init__(self, response): self.response = response
    def __iter__(self): return iter([NS(type="response.completed", response=self.response)])
    def close(self): pass


class Client:
    def __init__(self, callback): self.callback, self.calls = callback, []
    def with_options(self, **kw): return self
    @property
    def responses(self): return self
    def create(self, **kw):
        if kw.get("tool_choice") == {"type": "function", "name": "set_turn_goal"}:
            # The understanding call: this fake declares no goal, so the agent applies
            # its documented fallback (the earlier behaviour these tests describe).
            self.understanding = getattr(self, "understanding", 0)+1
            return Stream(NS(output=[], output_text="", status="completed", usage={"input_tokens": 10, "output_tokens": 5}))
        self.calls.append(copy.deepcopy(kw))
        output = self.callback(len(self.calls), kw)
        return Stream(NS(output=output, output_text="", status="completed", usage={"input_tokens": 100, "output_tokens": 100}))


def call(name, args, number):
    return [{"type": "function_call", "name": name, "call_id": "call_"+str(number), "arguments": json.dumps(args)}]


def state():
    a = Article(id="paper_verified", title="MeshingNet3D: Verified volumetric method", authors=["Author"], year=2025,
        doi="10.1234/verified", url="https://example.org/verified", verified=True)
    extra = Article(id="paper_extra", title="Complementary method", authors=["Author"], year=2025,
        doi="10.1234/extra", url="https://example.org/extra", verified=True)
    return {"messages": [{"role": "user", "content": "Present the best scientific works"}],
        "articles": {a.id: a.durable(), extra.id: extra.durable()}, "evaluation": {"recommended_article_ids": [a.id]}}


def final(answer="| Publication | Contribution | Acces |\n|---|---|---|\n| [paper_verified] | Metadata only; contribution not yet checked | Metadata only |"):
    return {"answer": answer, "article_ids": ["paper_verified"], "evidence_ids": [], "coverage_sufficient": False,
        "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": ["paper_verified"]}


def sources():
    s = Mock()
    s.search.side_effect = AssertionError("No new search permitted")
    s.metadata.side_effect = AssertionError("No new metadata call permitted")
    s.verify.side_effect = AssertionError("Use saved validated metadata during publication")
    return s


def run(callback, **overrides):
    s, data, client = sources(), state(), Client(callback)
    config = replace(ScholarV4Config(api_key="mock", web_search_enabled=False, max_tool_rounds=1), **overrides)
    result = ScholarChatAgent(config, client=client, sources=s).run(data)
    return result, data, client, s


def read_outline(number):
    return call("manage_outline", {"action": "read", "title": None, "section_id": None, "target_section_id": None,
        "position": None, "sections": None, "article_ids": None}, number)


def test_exploration_budget_exhaustion_reserves_a_real_publication_request():
    result, data, client, source = run(lambda n, kw: read_outline(n) if n == 1 else call("finalize_response", final(), n))
    assert not result["partial"] and not result["reason"]
    assert result["exploration_rounds"] == 1 and result["finalization_rounds"] == 1
    assert "| Publication |" in result["answer"]
    assert data["displayed_article_ids"] == ["paper_verified"]
    assert len(data["articles"]) == 2 and result["complementary_count"] == 0
    assert result["library_counts"]["unexamined"] == 1
    assert client.calls[1]["tool_choice"] == {"type": "function", "name": "finalize_response"}
    assert [t["name"] for t in client.calls[1]["tools"]] == ["finalize_response"]
    assert not source.mock_calls


def test_refused_first_finalization_is_repaired_without_research():
    def respond(n, kw):
        if n == 1: return read_outline(n)
        if n == 2: return call("finalize_response", final("[wrong](https://unknown.invalid/not-discovered)"), n)
        return call("finalize_response", final("[MeshingNet3D](https://doi.org/10.1234/verified)"), n)
    result, data, client, source = run(respond)
    assert not result["partial"] and result["finalization_attempt_count"] == 2
    assert result["finalization_refusals"][0]["rejection_code"] == "unknown_url"
    assert data["finalization_attempts"][0]["draft"].startswith("[wrong]")
    assert data["finalization_attempts"][0]["error"]
    assert data["finalization_attempts"][1]["published"]
    assert "unknown_url" in json.dumps(client.calls[2]["input"])
    assert not source.mock_calls


def test_failed_finalization_returns_only_verified_stored_references_not_88_urls():
    result, data, client, source = run(lambda n, kw: read_outline(n) if n == 1 else call("finalize_response", final("10.9999/invented"), n), max_finalization_attempts=2)
    assert result["partial"] and result["fallback"]
    assert result["reason"] == "finalization_failed" and "round_budget" not in result["answer"]
    assert "10.9999/invented" not in result["answer"]
    assert "10.1234/verified" in result["answer"] and "| N° |" in result["answer"]
    assert len(client.calls) == 3 and len(result["citations"]) == 1
    assert not source.mock_calls


def test_fallback_works_after_timeout_without_reentering_deadline_checks():
    t = ScholarTools(state(), sources(), ScholarV4Config(api_key="mock"), check=lambda: (_ for _ in ()).throw(TimeoutError()))
    result = fallback(t, "timeout")
    assert "10.1234/verified" in result["answer"]
    assert result["partial"] and result["reason"] == "timeout"


def test_fallback_does_not_promote_unverified_or_rejected_notices():
    data = state()
    data["articles"]["paper_verified"]["verified"] = False
    t = ScholarTools(data, sources(), ScholarV4Config(api_key="mock"))
    result = fallback(t, "finalization_failed")
    assert not result["primary_article_ids"] and not result["citations"]
    assert "10.1234/verified" not in result["answer"]


@pytest.mark.parametrize("answer,code", [
    ("[Different scientific paper](https://doi.org/10.1234/verified)", "doi_label_mismatch"),
    ("[MeshingNet3D](https://doi.org/10.1234/invented)", "unknown_url"),
    ("[paper_invented]", "unknown_marker"),
    ("Voir https://unknown.invalid/paper", "unknown_url"),
])
def test_abbreviated_label_support_does_not_disable_identity_checks(answer, code):
    t = ScholarTools(state(), sources(), ScholarV4Config(api_key="mock"))
    t.finalization_only = True
    result = t.finalize(FinalArgs.model_validate(final(answer)), float("inf"))
    assert result["rejection_code"] == code and not t.finished


def test_read_payload_is_deduplicated_but_durable_evidence_remains_exact():
    data = state(); a = data["articles"]["paper_verified"]
    a["abstract"] = "A trained model produces volumetric elements."
    t = ScholarTools(data, sources(), ScholarV4Config(api_key="mock"))
    args = {"article_ids": ["paper_verified"], "action": "read", "research_context": "mechanics", "passage_query": "",
        "passage_offset": 0, "passages_per_article": 8, "analysis": None}
    first = t.execute("analyze_article", args, float("inf"))
    e = first["reading"][0]["passages"][0]
    assert "text" not in e and e["exact_quote"]
    assert t.evidence[e["evidence_id"]]["text"] == e["exact_quote"]
    t.delivered([w["window_id"] for w in [*t.store.windows, *t.reading_windows]], inputs=tool_inputs(first))
    repeated = t.execute("analyze_article", args, float("inf"))
    assert not repeated["reading"][0]["passages"]
    assert repeated["reading"][0]["already_provided_evidence_ids"] == [e["evidence_id"]]
    revisited = t.execute("analyze_article", {**args, "revisit": True}, float("inf"))
    assert revisited["reading"][0]["passages"][0]["evidence_id"] == e["evidence_id"]


def test_failed_publication_does_not_commit_its_pending_analysis():
    data = state(); data["articles"]["paper_verified"]["abstract"] = "Verified method on abstract."
    t = ScholarTools(data, sources(), ScholarV4Config(api_key="mock"))
    result = t.execute("analyze_article", {"article_ids": ["paper_verified"], "action": "read", "research_context": "",
        "passage_query": "", "passage_offset": 0, "passages_per_article": 8, "analysis": None}, float("inf"))
    eid = result["reading"][0]["passages"][0]["evidence_id"]
    t.delivered([w["window_id"] for w in [*t.store.windows, *t.reading_windows]], inputs=tool_inputs(result))
    t.finalization_only = True
    payload = {**final("[wrong](https://unknown.invalid)"), "analyses": [{"article_id": "paper_verified", "analysis": analysis(eid)}]}
    rejected = t.finalize(FinalArgs.model_validate(payload), float("inf"))
    assert not rejected["published"] and not t.registry["paper_verified"].analysis


def test_followup_citation_does_not_replace_the_saved_numbered_order():
    data = state(); data["displayed_article_ids"] = ["paper_extra", "paper_verified"]
    t = ScholarTools(data, sources(), ScholarV4Config(api_key="mock")); t.finalization_only = True
    t.finalize(FinalArgs.model_validate({**final(), "displayed_article_ids": None}), float("inf"))
    assert data["displayed_article_ids"] == ["paper_extra", "paper_verified"]
    t.finalization_only = False
    result = t.execute("get_article", {"identifiers": ["2"]}, float("inf"))
    assert result["articles"][0]["id"] == "paper_verified"


def test_large_last_batch_is_not_reintroduced_beyond_context_limit():
    data = state(); data["messages"].insert(0, {"role": "assistant", "content": "Historical exact material. "*5000})
    source = sources()
    client = Client(lambda n, kw: call("read_conversation", {"offset": 0, "limit": 2}, n) if n == 1 else read_outline(n) if n == 2 else call("finalize_response", final(), n))
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False, max_tool_rounds=2,
        max_context_chars=7000, finalization_context_chars=5000), client=client, sources=source).run(data)
    assert not result["partial"]
    assert all(len(json.dumps(request["input"], ensure_ascii=False)) <= 7000 for request in client.calls)
    assert len(data["messages"][0]["content"]) > 50000
    assert client.calls[1]["tool_choice"] == "auto"
    assert not source.mock_calls


def test_current_refusal_is_reported_when_history_has_reached_its_bound():
    data = state()
    data["finalization_attempts"] = [{"published": True} for _ in range(20)]
    client = Client(lambda n, kw: read_outline(n) if n == 1 else call("finalize_response", final("[wrong](https://unknown.invalid)" if n == 2 else "[MeshingNet3D](https://doi.org/10.1234/verified)"), n))
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False, max_tool_rounds=1), client=client, sources=sources()).run(data)
    assert not result["partial"]
    assert result["finalization_refusals"][0]["rejection_code"] == "unknown_url"
    assert len(data["finalization_attempts"]) == 20


def test_canonical_projection_preserves_hypotheses_and_generalization_without_aliases():
    from agents.EnnoScholarV4.context import analysis_projection
    claim = {"statement": "A documented hypothesis.", "evidence_ids": ["ev_existing"], "origin": "source_statement"}
    result = analysis_projection({"hypotheses": [claim], "generalization": [claim], "method_family": "learned mesh",
        "method": [claim], "proposed_method": [claim], "evidence": [{"text": "Exact original", "exact_quote": "Exact original"}]})
    assert result["hypotheses"] and result["generalization"] and result["method_family"]
    assert "proposed_method" not in result and "evidence" not in result


@pytest.mark.parametrize("doi", ["`10.1234/verified`", "**10.1234/verified**"])
def test_markdown_emphasis_does_not_become_part_of_the_doi(doi):
    t = ScholarTools(state(), sources(), ScholarV4Config(api_key="mock")); t.finalization_only = True
    assert t.finalize(FinalArgs.model_validate(final("DOI vérifié : " + doi)), float("inf"))["published"]


def test_active_question_survives_oversized_scientific_memory():
    from agents.EnnoScholarV4.context import model_context
    data = state()
    claim = {"statement": "A long saved scientific statement. "*50, "evidence_ids": ["ev_existing"]}
    data["articles"]["paper_verified"]["analysis"] = {field: [claim] for field in
        ("problem", "objective", "method", "technical_mechanism", "experimental_setup", "contribution", "results", "limitations")}
    data["research_state"] = {"topic": "A long scientific topic "*150}
    data["messages"] = [{"role": "assistant", "content": "Old answer "*1000}, {"role": "user", "content": "Approfondis l’article 4."}]
    t = ScholarTools(data, sources(), ScholarV4Config(api_key="mock"))
    for finalizing in (False, True):
        context = model_context(data, t, 6500, final=finalizing)
        assert context[-1] == {"role": "user", "content": "Approfondis l’article 4."}
        assert len(json.dumps(context, ensure_ascii=False)) < 7000


def seventy_notices():
    # Beyond the first window of 8 but within one search's share of the new-candidate limit.
    names = {12: ("DL-Polycube", "Deep learning identifies polycube structures and generates hexahedral meshes."),
        19: ("DDPM-Polycube", "A diffusion model constructs polycubes for volumetric hexahedral mesh generation."),
        25: ("3DMeshNet", "A neural mapping generates structured three-dimensional hexahedral cells.")}
    return [Article(title=names.get(i, (f"Fixture unrelated classifier {i}", "This fixture predicts labels, without generating volumetric meshes."))[0],
        abstract=names.get(i, ("", "This fixture predicts labels, without generating volumetric meshes. "*18))[1] if i < 68 else "",
        authors=["Fixture Author"], year=2025, doi=f"10.1234/fixture{i}", verified=True) for i in range(70)]


def test_seventy_notices_68_abstracts_without_finalists_reach_model_and_support_selection():
    from agents.EnnoScholarV4.context import article_projection
    assert len(json.dumps([article_projection(a, 1600) for a in seventy_notices()], ensure_ascii=False)) > 85000
    data = {"messages": [{"role": "user", "content": "Find trained models generating volumetric 3D meshes."}], "articles": {}}
    source = Mock(); source.search.return_value = (seventy_notices(), [])
    source.verify.side_effect = lambda article, deadline: article
    seen = {}
    chosen = ["DL-Polycube", "DDPM-Polycube", "3DMeshNet"]
    proofs = {}
    def respond(n, kwargs):
        for item in kwargs["input"]:
            if item.get("type") == "function_call_output":
                result = json.loads(item["output"])
                seen.update({a["title"]: a for a in result.get("articles", [])})
                for reading in result.get("reading", []):
                    proofs[reading["article"]["id"]] = reading["passages"][0]["evidence_id"]
        if n == 1:
            return call("search_scientific", {"query": "fixed regression query", "sources": None,
                "limit_per_source": 35, "offset": 0, "family": "volumetric"}, n)
        if n == 2:
            assert kwargs["tool_choice"] == "auto"
            # Every result of the search is presented with a short abstract (24 per window).
            assert len(seen) == 24 and all(a.get("abstract") for a in seen.values())
            assert all(name in json.dumps(kwargs["input"]) for name in chosen)
            memory = json.loads(kwargs["input"][0]["content"].split(" : ", 1)[1])
            index = {row["title"]: row["id"] for row in memory["candidate_index"]}
            return call("get_article", {"identifiers": [index[name] for name in chosen]}, n)
        ids = [seen[name]["id"] for name in chosen]
        assert all(seen[name].get("abstract") for name in chosen)
        if n == 3:
            return [*call("record_candidate_decisions", {"decisions": [{"article_id": aid, "decision": "retain", "justification": "The supplied abstract describes volumetric mesh generation."} for aid in ids],
                "finalist_ids": ids, "covered_families": ["volumetric"], "remaining_axes": []}, n),
                *call("analyze_article", {"article_ids": ids, "action": "read", "research_context": "volumetric meshes",
                    "passage_query": "", "passage_offset": 0, "passages_per_article": 2, "analysis": None, "revisit": False}, str(n)+"_read")]
        assert kwargs["tool_choice"] == {"type": "function", "name": "finalize_response"}
        analyses = []
        for name, aid in zip(chosen, ids):
            value = analysis(proofs[aid])
            for category in ("method", "contribution"):
                value[category] = [{"statement": seen[name]["abstract"], "evidence_ids": [proofs[aid]], "origin": "source_statement"}]
            analyses.append({"article_id": aid, "analysis": value})
        answer = "Relevant learned volumetric mesh generators, based on abstracts only.\n\n| Publication | Contribution | Read |\n|---|---|---|\n" + "\n".join(
            f"| [{aid}] | {seen[name]['abstract']} [{proofs[aid]}] | Abstract only |" for name, aid in zip(chosen, ids))
        return call("finalize_response", {"answer": answer, "article_ids": ids, "evidence_ids": list(proofs.values()),
            "coverage_sufficient": True, "remaining_uncertainties": [], "analyses": analyses, "displayed_article_ids": ids}, n)
    client = Client(respond)
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False, max_tool_rounds=3), client=client, sources=source).run(data)
    assert not result["partial"] and not result["fallback"]
    assert result["exploration_stop_reason"] != "context_budget"
    # One broad search keeps half of the unchanged 80-candidate limit (40); the rest is reported as not stored.
    assert len(data["articles"]) == 40 and all(a["abstract"] for a in data["articles"].values())
    assert len(result["primary_article_ids"]) == 3 and all(name in result["answer"] for name in chosen)
    assert all(data["articles"][aid]["analysis_basis"] == "abstract" for aid in result["primary_article_ids"])
    assert all(len(json.dumps(kw["input"], ensure_ascii=False)) <= 85000 for kw in client.calls)
    assert result["publication_accepted"] and result["research_status"] == "sufficient"


def test_successive_windows_can_deliver_all_68_abstracts_without_collecting_again():
    from agents.EnnoScholarV4.models import Article
    t = ScholarTools({"articles": {}}, sources(), ScholarV4Config(api_key="mock"))
    t.store.insert(seventy_notices())
    offered = set()
    for _ in range(10):
        result = t.execute("search_collected_articles", {"query": "", "limit": 12, "exclude_presented": True, "family": None}, float("inf"))
        offered.update(a["id"] for a in result["articles"] if a.get("abstract"))
        assert len(result["articles"]) <= 12
        t.delivered([result["window_id"]])
    assert len(t.registry) == 70 and len(t.store.exposed) == 70 and len(offered) == 68
    assert not t.sources.mock_calls


def test_budget_final_context_without_finalists_contains_real_abstracts():
    from agents.EnnoScholarV4.context import model_context
    t = ScholarTools({"articles": {}, "messages": [{"role": "user", "content": "Generate scientific synthesis."}]}, sources(), ScholarV4Config(api_key="mock"))
    t.store.insert(seventy_notices())
    context = model_context(t.state, t, 24000, final=True)
    memory = json.loads(context[0]["content"].split(" : ", 1)[1])
    assert memory["candidate_articles"] and all(a.get("abstract") for a in memory["candidate_articles"])
    assert not memory["selected_articles"]
    response = fallback(t, "token_budget")
    assert response["fallback"] and "Extrait du résumé conservé" in response["answer"]
    assert "ne constituent pas un classement" in response["answer"]


def test_recovery_context_preserves_titles_beyond_the_first_window_and_marks_actual_exposure():
    from agents.EnnoScholarV4.context import model_context
    from agents.EnnoScholarV4.agent import window_ids, abstract_deliveries
    t = ScholarTools({"articles": {}, "messages": [{"role": "user", "content": "Continue using existing abstracts."}]}, sources(), ScholarV4Config(api_key="mock"))
    t.store.insert(seventy_notices())
    context = model_context(t.state, t, 18000)
    memory = json.loads(context[0]["content"].split(" : ", 1)[1])
    assert len(memory["candidate_index"]) == 70
    assert "3DMeshNet" in [row["title"] for row in memory["candidate_index"]]
    offered = abstract_deliveries(context)
    assert offered and all(value["chars"] > 0 for value in offered.values())
    t.delivered(window_ids(context))
    assert set(t.store.exposed) == set(offered)
    assert not t.sources.mock_calls


def test_synthesis_balances_already_read_body_proofs_without_claiming_saved_analysis():
    from agents.EnnoScholarV4.context import model_context
    data = state(); data["evaluation"]["recommended_article_ids"] = ["paper_verified", "paper_extra"]
    t = ScholarTools(data, sources(), ScholarV4Config(api_key="mock"))
    for article in t.registry.values():
        article.fulltext = {"text": "Documented source body. "*700,
            "chunks": [{"text": "Documented source body. "*700, "section": "Method", "page": 2}], "url": article.url}
    result = t.execute("analyze_article", {"article_ids": list(t.registry), "action": "read", "research_context": "",
        "passage_query": "", "passage_offset": 0, "passages_per_article": 8, "analysis": None, "revisit": False}, float("inf"))
    t.delivered([identifier for row in result["reading"] for identifier in [row["window_id"], row["reading_window_id"]]],
        inputs=tool_inputs(result))
    context = model_context(data, t, 9000, final=True)
    memory = json.loads(context[0]["content"].split(" : ", 1)[1])
    assert {row["article_id"] for row in memory["evidence"]} == set(t.registry)
    assert all(row["body_passages_shown"] == 8 and not row["structured_analysis_saved"] for row in memory["source_material"])


def test_native_web_markup_is_rendered_without_bypassing_reference_validation():
    t = ScholarTools(state(), sources(), ScholarV4Config(api_key="mock")); t.finalization_only = True
    args = FinalArgs.model_validate(final("Documented publication [paper_verified]. citeturn0view0"))
    assert t.finalize(args, float("inf"))["published"]
    assert "" not in t.finished["answer"] and t.finished["citations"][0]["url"] == "https://doi.org/10.1234/verified"
    bad = FinalArgs.model_validate(final("https://unknown.invalid/paper citeturn0view0"))
    assert t.finalize(bad, float("inf"))["rejection_code"] == "unknown_url"
